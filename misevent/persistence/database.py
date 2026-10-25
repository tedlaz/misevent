"""
Η ΜΙΑ ΒΑΣΗ — πολλά λογικά stores σε έναν πίνακα.

Κάθε χώρος (χρήστες, κοινές παράμετροι, κάθε εταιρεία) είναι ένα `store`:
μια γραμμή του πίνακα `stores`, και το ακέραιο id της είναι η στήλη `store`
του `events` (όχι το όνομα: το «company:<uuid>» θα επαναλαμβανόταν σε κάθε
event και σε κάθε index). Η απομόνωση είναι ΛΟΓΙΚΗ —
γι' αυτό ΚΑΝΕΝΑ query δεν επιτρέπεται χωρίς `WHERE store = ?`, και γι' αυτό
το connection δεν διαρρέει ποτέ έξω από το `EventStore`.

⚠️ Το `seq` είναι ΑΝΑ STORE, όχι καθολικό: το βλέπει ο χρήστης στο audit,
είναι το όρισμα του `rollback_to`, και ταξιδεύει μέσα στα backups. Κάθε store
κρατά τη δική του αρίθμηση από το 1.

Εδώ μαζεύεται ΟΛΟ το SQLite-specific (connect, DDL, PRAGMA). Το `EventStore`
από πάνω δεν ξέρει τι βάση τρέχει από κάτω.
"""

import sqlite3

MEMORY = ":memory:"

# ---------------------------------------------------------------- ονόματα stores
SYSTEM_STORE = "system"  # χρήστες + μητρώο εταιρειών
SHARED_STORE = "shared"  # ΚΟΙΝΕΣ νομοθετικές παράμετροι
COMPANY_PREFIX = "company:"


def company_store(company_id: str) -> str:
    """Το store μιας εταιρείας. Ένα σημείο ορίζει τη σύμβαση ονομασίας."""
    return f"{COMPANY_PREFIX}{company_id}"


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS stores (
        id   INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE
    )""",
    # ΟΧΙ `WITHOUT ROWID`: μετρημένο, βγαίνει ΜΕΓΑΛΥΤΕΡΟ (τα payloads είναι
    # πολύ μεγάλα για clustered B-tree) παρά το διπλό index του PK.
    """CREATE TABLE IF NOT EXISTS events (
        store        INTEGER NOT NULL REFERENCES stores(id),
        seq          INTEGER NOT NULL,
        event_id     TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        event_type   TEXT NOT NULL,
        occurred_at  TEXT NOT NULL,
        payload      TEXT NOT NULL,
        tx           TEXT,
        PRIMARY KEY (store, seq)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_events_agg ON events(store, aggregate_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events(store, event_type)",
)


def _without_wal(data: bytes) -> bytes:
    """Σβήνει τη σημαία WAL (bytes 18-19 του header: 2 -> 1, όπως το
    `journal_mode=DELETE`). Αλλιώς η SQLite δεν ανοίγει το αρχείο από μνήμη,
    και το backup είναι αυτόνομο αρχείο, χωρίς -wal δίπλα του."""
    if data[18:20] == b"":
        return data[:18] + b"" + data[20:]
    return data


class Database:
    """Μία σύνδεση, ένας πίνακας events, πολλά λογικά stores."""

    def __init__(self, path: str = MEMORY):
        self.path = str(path)
        # check_same_thread=False: ο dev server του Flask είναι threaded και οι
        # εγγραφές σειριοποιούνται ήδη από το καθολικό lock του Registry.
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        if self.path != MEMORY:
            # Ένα αρχείο για ΟΛΕΣ τις εταιρείες σημαίνει περισσότερο ανταγωνισμό
            # γραφής· το WAL κρατά τους αναγνώστες εκτός της ουράς.
            self.conn.execute("PRAGMA journal_mode=WAL")
        for statement in _SCHEMA:
            self.conn.execute(statement)
        self.conn.commit()

    def store_id(self, name: str) -> int:
        """Το id του store στη στήλη `events.store` (δημιουργείται αν λείπει)."""
        self.conn.execute("INSERT OR IGNORE INTO stores (name) VALUES (?)", (name,))
        self.conn.commit()
        return self.conn.execute(
            "SELECT id FROM stores WHERE name = ?", (name,)
        ).fetchone()[0]

    def store(self, name: str, registry: dict | None = None, shared=None):
        """Το `EventStore` ενός λογικού χώρου μέσα σε αυτή τη βάση."""
        from misevent.persistence.event_store import EventStore

        return EventStore(self, name, registry=registry, shared=shared)

    # ------------------------------------------- backup ΟΛΟΥ του συστήματος
    def snapshot(self) -> bytes:
        """Συνεπές αντίγραφο ΟΛΗΣ της βάσης (όλα τα stores) ως αρχείο SQLite.
        Το backup API της SQLite διαβάζει σωστά και με ενεργό WAL."""
        mem = sqlite3.connect(MEMORY)
        try:
            self.conn.backup(mem)
            return _without_wal(mem.serialize())
        finally:
            mem.close()

    def restore_snapshot(self, data: bytes) -> int:
        """Αντικαθιστά ατομικά ΟΛΑ τα events με αυτά ενός `snapshot`.

        Δεν αντικαθιστούμε το αρχείο στον δίσκο: η ζωντανή σύνδεση μένει, και
        η αλλαγή είναι ΜΙΑ συναλλαγή — ή περνά ολόκληρη ή καθόλου. Το replay
        (και άρα ο έλεγχος ότι τα events αποκωδικοποιούνται) είναι του caller.
        """
        src = sqlite3.connect(MEMORY)
        try:
            src.deserialize(_without_wal(data))
            # Με ΟΝΟΜΑ store, όχι id: τα ids του backup δεν είναι τα δικά μας,
            # και τα ζωντανά `EventStore` κρατούν ήδη τα δικά μας.
            rows = src.execute(
                "SELECT s.name, e.seq, e.event_id, e.aggregate_id, e.event_type, "
                "e.occurred_at, e.payload, e.tx FROM events e "
                "JOIN stores s ON s.id = e.store"
            ).fetchall()
        except sqlite3.Error:
            raise ValueError("Το αρχείο δεν είναι backup βάσης του misevent") from None
        finally:
            src.close()
        if not any(r[0] == SYSTEM_STORE for r in rows):
            raise ValueError("Το backup δεν περιέχει χρήστες/εταιρείες")
        # Πριν από τη συναλλαγή: το `store_id` κάνει commit (κενό store αν
        # αποτύχει η επαναφορά — αβλαβές).
        ids = {name: self.store_id(name) for name in {r[0] for r in rows}}
        rows = [(ids[r[0]], *r[1:]) for r in rows]
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute("DELETE FROM events")
            self.conn.executemany(
                "INSERT INTO events (store, seq, event_id, aggregate_id, event_type, "
                "occurred_at, payload, tx) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return len(rows)

    def close(self):
        self.conn.close()
