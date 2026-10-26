"""
Στοιχεία σύνδεσης ΕΡΓΑΝΗ ΙΙ (REST API) ΑΝΑ ΕΤΑΙΡΕΙΑ — ΞΕΧΩΡΙΣΤΗ βάση
`secrets.db`, ΕΚΤΟΣ event log.

Ο κωδικός πρέπει να ξαναδιαβάζεται (στέλνεται στο `/Authentication`), άρα
ΔΕΝ γίνεται hash όπως οι κωδικοί χρηστών· κρυπτογραφείται (Fernet) με το
κλειδί `<data>/secrets.key`. Στα events δεν μπαίνει ποτέ: θα έμενε για πάντα
στο ιστορικό, στο audit και στα backup μιας εταιρείας.

⚠️ Το backup ΟΛΟΥ του συστήματος περιέχει ΚΑΙ τη βάση ΚΑΙ το κλειδί, ώστε
μετά το restore η σύνδεση να δουλεύει χωρίς να ξαναδοθούν κωδικοί — άρα το
αρχείο backup δίνει πρόσβαση στους κωδικούς ΕΡΓΑΝΗ και φυλάγεται ανάλογα.

Ζει στο webapp (εξάρτηση `cryptography`) — ο πυρήνας μένει stdlib-only.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from misevent.integrations import ergani as ergani_mod
from misevent.persistence.database import MEMORY, _without_wal

# «Προσομοίωση» = καμία κλήση δικτύου (το μέχρι τώρα μοναδικό).
ENVIRONMENTS = {
    "simulate": "Προσομοίωση (καμία αποστολή)",
    "trial": "Δοκιμαστικό ΕΡΓΑΝΗ",
    "production": "ΠΑΡΑΓΩΓΙΚΟ ΕΡΓΑΝΗ",
}

_SCHEMA = """CREATE TABLE IF NOT EXISTS ergani_credentials (
    store       TEXT PRIMARY KEY,
    environment TEXT NOT NULL,
    username    TEXT NOT NULL,
    password    BLOB NOT NULL,
    updated_at  TEXT NOT NULL
)"""


class SecretStore:
    def __init__(self, path: str = MEMORY, key_path: Path | None = None):
        self.path = str(path)
        self.key_path = key_path
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.execute(_SCHEMA)
        self.conn.commit()
        self._fernet = Fernet(self._load_key())

    def _load_key(self) -> bytes:
        if self.key_path is None:  # στη μνήμη (tests)
            return Fernet.generate_key()
        if not self.key_path.exists():
            self.key_path.write_bytes(Fernet.generate_key())
            self.key_path.chmod(0o600)
        return self.key_path.read_bytes().strip()

    # ------------------------------------------------------------ ανά εταιρεία
    def get(self, store: str) -> dict:
        """{environment, username, has_password, updated_at} — ΧΩΡΙΣ κωδικό."""
        row = self.conn.execute(
            "SELECT environment, username, updated_at FROM ergani_credentials "
            "WHERE store = ?", (store,)).fetchone()
        if row is None:
            return {"environment": "simulate", "username": "", "has_password": False,
                    "updated_at": ""}
        return {"environment": row[0], "username": row[1], "has_password": True,
                "updated_at": row[2]}

    def set(self, store: str, environment: str, username: str, password: str = ""):
        """Κενός κωδικός = κρατά τον υπάρχοντα (το πεδίο δεν ξαναγεμίζει ποτέ)."""
        if environment not in ENVIRONMENTS:
            raise ValueError("Άγνωστο περιβάλλον ΕΡΓΑΝΗ")
        username = username.strip()
        old = self.conn.execute(
            "SELECT password FROM ergani_credentials WHERE store = ?", (store,)
        ).fetchone()
        token = self._fernet.encrypt(password.encode()) if password else (old and old[0])
        if environment != "simulate" and not (username and token):
            raise ValueError("Για αποστολή στο ΕΡΓΑΝΗ χρειάζονται όνομα χρήστη και κωδικός")
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO ergani_credentials VALUES (?, ?, ?, ?, ?)",
                (store, environment, username, token or b"",
                 datetime.now(UTC).isoformat(timespec="seconds")))

    def delete(self, store: str):
        with self.conn:
            self.conn.execute("DELETE FROM ergani_credentials WHERE store = ?", (store,))

    def client(self, store: str) -> ergani_mod.ErganiClient:
        """Ο client ΕΡΓΑΝΗ της εταιρείας, κατά τις ρυθμίσεις της."""
        row = self.conn.execute(
            "SELECT environment, username, password FROM ergani_credentials "
            "WHERE store = ?", (store,)).fetchone()
        if row is None or row[0] == "simulate":
            return ergani_mod.ErganiClient(simulate=True)
        try:
            password = self._fernet.decrypt(row[2]).decode()
        except InvalidToken:
            # Άλλο κλειδί: ΟΧΙ σιωπηλή προσομοίωση — η υποβολή αποτυγχάνει
            # ορατά (authentication) και καταγράφεται ως failed.
            password = ""
        return ergani_mod.ErganiClient(username=row[1], password=password,
                                       environment=row[0], simulate=False)

    # ------------------------------------------------------------ backup
    def snapshot(self) -> tuple[bytes, bytes]:
        """(βάση, κλειδί) — ταξιδεύουν ΜΑΖΙ, αλλιώς η βάση είναι άχρηστη."""
        mem = sqlite3.connect(MEMORY)
        try:
            self.conn.backup(mem)
            return _without_wal(mem.serialize()), self._key()
        finally:
            mem.close()

    def _key(self) -> bytes:
        return self.key_path.read_bytes().strip() if self.key_path else b""

    def restore_snapshot(self, data: bytes, key: bytes):
        try:
            fernet = Fernet(key)
        except ValueError:
            raise ValueError("Άκυρο κλειδί κωδικών ΕΡΓΑΝΗ στο backup") from None
        src = sqlite3.connect(MEMORY)
        try:
            src.deserialize(_without_wal(data))
            rows = src.execute("SELECT * FROM ergani_credentials").fetchall()
        except sqlite3.Error:
            raise ValueError("Το backup δεν έχει έγκυρους κωδικούς ΕΡΓΑΝΗ") from None
        finally:
            src.close()
        with self.conn:
            self.conn.execute("DELETE FROM ergani_credentials")
            self.conn.executemany(
                "INSERT INTO ergani_credentials VALUES (?, ?, ?, ?, ?)", rows)
        if self.key_path:
            self.key_path.write_bytes(key)
        self._fernet = fernet
