"""
Η ΜΙΑ ΒΑΣΗ — πολλά λογικά stores σε έναν πίνακα.

Κάθε χώρος (χρήστες, κοινές παράμετροι, κάθε εταιρεία) είναι ένα `store`:
μια τιμή στη στήλη `store` του πίνακα `events`. Η απομόνωση είναι ΛΟΓΙΚΗ —
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
    """CREATE TABLE IF NOT EXISTS events (
        store        TEXT NOT NULL,
        seq          INTEGER NOT NULL,
        event_id     TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        event_type   TEXT NOT NULL,
        occurred_at  TEXT NOT NULL,
        payload      TEXT NOT NULL,
        tx           TEXT,
        PRIMARY KEY (store, seq)
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_id ON events(store, event_id)",
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


def _add_tx_column(conn) -> bool:
    """Migration: η στήλη `tx` (transaction της εντολής που έγραψε το event,
    βλ. `event_store.transaction`) σε βάσεις πριν από αυτήν. True αν υπήρχε."""
    if any(r[1] == "tx" for r in conn.execute("PRAGMA table_info(events)")):
        return True
    conn.execute("ALTER TABLE events ADD COLUMN tx TEXT")
    return False


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
        _add_tx_column(self.conn)
        self.conn.commit()

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
            # backup πριν από τη στήλη `tx` -> NULL (μία γραμμή ανά event στο audit)
            tx = "tx" if _add_tx_column(src) else "NULL"
            rows = src.execute(
                "SELECT store, seq, event_id, aggregate_id, event_type, occurred_at, "
                f"payload, {tx} FROM events"
            ).fetchall()
        except sqlite3.Error:
            raise ValueError("Το αρχείο δεν είναι backup βάσης του misevent") from None
        finally:
            src.close()
        if not any(r[0] == SYSTEM_STORE for r in rows):
            raise ValueError("Το backup δεν περιέχει χρήστες/εταιρείες")
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
