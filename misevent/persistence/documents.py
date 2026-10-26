"""
ΕΓΓΡΑΦΑ (blobs) — ΞΕΧΩΡΙΣΤΗ βάση `documents.db`, ΕΚΤΟΣ event log.

Τα PDF που συνοδεύουν την αναγγελία πρόσληψης ΕΡΓΑΝΗ (νομιμοποιητικά τρίτων
χωρών, βιβλιάριο ανηλίκου) δεν είναι γεγονότα: είναι δυαδικά, μεγάλα, και
πρέπει να ΔΙΑΓΡΑΦΟΝΤΑΙ (GDPR) — ενώ τα events είναι αμετάβλητα. Γι' αυτό η
`misevent.db` μένει ένας πίνακας `events` και τα έγγραφα ζουν εδώ.

Ίδια σύμβαση απομόνωσης με τα events: ΚΑΘΕ query έχει `WHERE store = ?`, και
το connection δεν βγαίνει έξω από αυτό το module (`scope()` δίνει τον χώρο
μιας εταιρείας). Το backup όλου του συστήματος παίρνει ΚΑΙ αυτή τη βάση
(`snapshot` / `restore_snapshot`, βλ. webapp/views/backup.py).
"""

import hashlib
import sqlite3
from datetime import UTC, datetime

from misevent.persistence.database import MEMORY, _without_wal

_SCHEMA = """CREATE TABLE IF NOT EXISTS documents (
    store     TEXT NOT NULL,
    person_id TEXT NOT NULL,
    kind      TEXT NOT NULL,
    filename  TEXT NOT NULL,
    content   BLOB NOT NULL,
    sha256    TEXT NOT NULL,
    added_at  TEXT NOT NULL,
    PRIMARY KEY (store, person_id, kind)
)"""
_COLUMNS = "store, person_id, kind, filename, content, sha256, added_at"


class DocumentStore:
    """Μία σύνδεση, ένας πίνακας `documents`, πολλά λογικά stores."""

    def __init__(self, path: str = MEMORY):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        if self.path != MEMORY:
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    def scope(self, store: str) -> "Documents":
        return Documents(self, store)

    def purge(self, store: str) -> int:
        """ΟΡΙΣΤΙΚΗ διαγραφή όλων των εγγράφων ενός store (διαγραφή εταιρείας)."""
        with self.conn:
            return self.conn.execute("DELETE FROM documents WHERE store = ?",
                                     (store,)).rowcount

    def snapshot(self, store: str | None = None) -> bytes:
        """Όλη η βάση — ή, με `store`, μόνο τα έγγραφα μιας εταιρείας."""
        mem = sqlite3.connect(MEMORY)
        try:
            if store is None:
                self.conn.backup(mem)
            else:
                mem.execute(_SCHEMA)
                mem.executemany(
                    f"INSERT INTO documents ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    self.conn.execute(f"SELECT {_COLUMNS} FROM documents "
                                      "WHERE store = ?", (store,)))
                mem.commit()
            return _without_wal(mem.serialize())
        finally:
            mem.close()

    def restore_snapshot(self, data: bytes, store: str | None = None) -> int:
        """Αντικαθιστά ατομικά ΟΛΑ τα έγγραφα με αυτά ενός `snapshot` — ή, με
        `store`, μόνο τα έγγραφα αυτής της εταιρείας, με ΟΛΑ τα έγγραφα του
        snapshot να ανήκουν πλέον σε αυτήν (backup άλλης εταιρείας → εδώ)."""
        src = sqlite3.connect(MEMORY)
        try:
            src.deserialize(_without_wal(data))
            rows = src.execute(f"SELECT {_COLUMNS} FROM documents").fetchall()
        except sqlite3.Error:
            raise ValueError("Το αρχείο δεν είναι backup εγγράφων του misevent") from None
        finally:
            src.close()
        if store is not None:
            rows = [(store, *r[1:]) for r in rows]
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            if store is None:
                self.conn.execute("DELETE FROM documents")
            else:
                self.conn.execute("DELETE FROM documents WHERE store = ?", (store,))
            self.conn.executemany(f"INSERT INTO documents ({_COLUMNS}) VALUES "
                                  "(?, ?, ?, ?, ?, ?, ?)", rows)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return len(rows)

    def close(self):
        self.conn.close()


class Documents:
    """Τα έγγραφα ΜΙΑΣ εταιρείας (ένα store). Ένα έγγραφο ανά (πρόσωπο, είδος):
    νέο ανέβασμα του ίδιου είδους αντικαθιστά το προηγούμενο."""

    def __init__(self, db: DocumentStore, store: str):
        self._db = db
        self.store = store

    def put(self, person_id: str, kind: str, filename: str, content: bytes) -> dict:
        row = (self.store, person_id, kind, filename, content,
               hashlib.sha256(content).hexdigest(),
               datetime.now(UTC).isoformat(timespec="seconds"))
        with self._db.conn:
            self._db.conn.execute(
                f"INSERT OR REPLACE INTO documents ({_COLUMNS}) VALUES "
                "(?, ?, ?, ?, ?, ?, ?)", row)
        return self._meta(row)

    def get(self, person_id: str, kind: str) -> dict | None:
        row = self._db.conn.execute(
            f"SELECT {_COLUMNS} FROM documents WHERE store = ? AND person_id = ? "
            "AND kind = ?", (self.store, person_id, kind)).fetchone()
        return {**self._meta(row), "content": row[4]} if row else None

    def list(self, person_id: str) -> list[dict]:
        rows = self._db.conn.execute(
            f"SELECT {_COLUMNS} FROM documents WHERE store = ? AND person_id = ? "
            "ORDER BY kind", (self.store, person_id)).fetchall()
        return [self._meta(r) for r in rows]

    def delete(self, person_id: str, kind: str) -> bool:
        with self._db.conn:
            cur = self._db.conn.execute(
                "DELETE FROM documents WHERE store = ? AND person_id = ? AND kind = ?",
                (self.store, person_id, kind))
        return cur.rowcount > 0

    @staticmethod
    def _meta(row) -> dict:
        """Χωρίς το περιεχόμενο — για λίστες/εμφάνιση."""
        return {"person_id": row[1], "kind": row[2], "filename": row[3],
                "size": len(row[4]), "sha256": row[5], "added_at": row[6]}
