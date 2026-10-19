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
        PRIMARY KEY (store, seq)
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_id ON events(store, event_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_agg ON events(store, aggregate_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events(store, event_type)",
)


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

    def store(self, name: str, registry: dict | None = None, shared=None):
        """Το `EventStore` ενός λογικού χώρου μέσα σε αυτή τη βάση."""
        from misevent.persistence.event_store import EventStore

        return EventStore(self, name, registry=registry, shared=shared)

    def close(self):
        self.conn.close()
