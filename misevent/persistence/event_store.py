"""
Event Store — append-only αποθήκευση events + pub/sub.

Τα events κανονικά ΔΕΝ διαγράφονται και ΔΕΝ τροποποιούνται ποτέ. Διορθώσεις
γίνονται με νέα «αντισταθμιστικά» events (π.χ. νέος υπολογισμός).
Η μόνη εξαίρεση είναι η ρητή, διαχειριστική επαναφορά σε παλιότερο seq,
η οποία διαγράφει ολόκληρο το μεταγενέστερο κλαδί του log.

ΜΙΑ ΒΑΣΗ: το πού γράφονται τα bytes το ξέρει το `Database` (βλ. `database.py`).
Εδώ ζει μόνο η σημασιολογία του log. Κάθε store είναι ένας λογικός χώρος μέσα
στην ίδια βάση και ΚΑΘΕ query φιλτράρει σε `store = ?` — δεν υπάρχει πια
δομική απομόνωση με χωριστό αρχείο να μας σώσει αν το ξεχάσουμε.
"""

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import fields
from datetime import UTC, date, datetime

from misevent.domain.events import (
    EVENT_TYPES,
    SHARED_EVENT_TYPES,
    DomainEvent,
    LeaveType,
)
from misevent.persistence.database import Database

BACKUP_FORMAT = "misevent-event-backup"
BACKUP_VERSION = 1


def _coerce_with(registry: dict, etype: str, payload: dict):
    return _coerce(registry[etype], payload)


def _coerce(cls, payload: dict):
    """Μετατροπή JSON payload πίσω σε dataclass (dates, enums)."""
    kwargs = {}
    for f in fields(cls):
        if f.name not in payload:
            continue
        v = payload[f.name]
        if f.type in (date, "date", date | None, "date | None") and isinstance(v, str):
            v = date.fromisoformat(v)
        if f.type in (LeaveType, "LeaveType") and isinstance(v, str):
            v = LeaveType(v)
        kwargs[f.name] = v
    return cls(**kwargs)


class EventStore:
    """Append-only log ενός aggregate-χώρου (μιας εταιρείας ή του συστήματος).

    ➜ ΠΟΛΛΕΣ ΕΤΑΙΡΕΙΕΣ: κάθε εταιρεία έχει ΔΙΚΟ ΤΗΣ store — πλέον μια τιμή
    στη στήλη `store` της κοινής βάσης, όχι χωριστό αρχείο. Οι «νομοθετικές»
    παράμετροι είναι ΚΟΙΝΕΣ για όλες, γι' αυτό δίνεται προαιρετικά ένα
    `shared` store: τα events του `SHARED_EVENT_TYPES` γράφονται ΕΚΕΙ και
    διαβάζονται από ΟΛΕΣ τις εταιρείες (βλ. `load`). Χωρίς `shared` η
    συμπεριφορά είναι ακριβώς η μονο-εταιρική (demo/tests).

    Το `registry` ορίζει ποιο μητρώο τύπων αποκωδικοποιεί τα payloads — έτσι
    το ίδιο store εξυπηρετεί και τον χώρο των χρηστών (`AUTH_EVENT_TYPES`).

    ⚠️ Το `seq` είναι ΑΝΑ STORE και ξεκινά από το 1: το βλέπει ο χρήστης στο
    audit, το δέχεται το `rollback_to`, και ταξιδεύει μέσα στα backups.
    """

    def __init__(
        self,
        db: Database,
        store: str,
        registry: dict | None = None,
        shared: "EventStore | None" = None,
    ):
        self.db = db
        self.name = store
        self._subscribers: list[Callable[[str, DomainEvent], None]] = []
        self.registry = registry if registry is not None else EVENT_TYPES
        self.shared = shared

    @property
    def conn(self):
        return self.db.conn

    def _is_shared(self, event_type: str) -> bool:
        return self.shared is not None and event_type in SHARED_EVENT_TYPES

    def subscribe(self, handler: Callable[[str, DomainEvent], None]):
        self._subscribers.append(handler)

    def append(self, aggregate_id: str, event: DomainEvent) -> str:
        # Κοινή παράμετρος (π.χ. νέα ΓΣΣΕ): γράφεται ΜΙΑ φορά στο κοινό log.
        # Οι subscribers του κοινού store είναι τα projections ΟΛΩΝ των
        # φορτωμένων εταιρειών, οπότε ενημερώνονται όλες — και η δική μας.
        if self._is_shared(type(event).__name__):
            return self.shared.append(aggregate_id, event)
        rec = event.to_record(aggregate_id)
        # Το επόμενο seq υπολογίζεται ΜΕΣΑ στο ίδιο statement: ένα round-trip,
        # και σε κούρσα σκάει το PRIMARY KEY (store, seq) αντί να χαθεί event.
        self.conn.execute(
            "INSERT INTO events "
            "(store, seq, event_id, aggregate_id, event_type, occurred_at, payload) "
            "SELECT :store, COALESCE(MAX(seq), 0) + 1, :event_id, :aggregate_id, "
            ":event_type, :occurred_at, :payload FROM events WHERE store = :store",
            {**rec, "store": self.name},
        )
        self.conn.commit()
        for handler in self._subscribers:
            handler(aggregate_id, event)
        return rec["event_id"]

    def _load_own(
        self, aggregate_id: str | None = None, event_types: Iterable[str] | None = None
    ):
        """Μόνο τα events ΑΥΤΟΥ του store.

        Τα κοινά δεν χρειάζονται φιλτράρισμα: το `append` τα δρομολογεί εξ
        ολοκλήρου στο κοινό store, οπότε δεν πατούν ποτέ εδώ.
        """
        q = "SELECT aggregate_id, event_type, payload FROM events WHERE store = ?"
        params: list = [self.name]
        if aggregate_id:
            q += " AND aggregate_id = ?"
            params.append(aggregate_id)
        if event_types:
            types = list(event_types)
            q += f" AND event_type IN ({_marks(types)})"
            params.extend(types)
        q += " ORDER BY seq"
        out = []
        for agg, etype, payload in self.conn.execute(q, params):
            cls = self.registry[etype]
            out.append((agg, _coerce(cls, json.loads(payload))))
        return out

    def load(
        self, aggregate_id: str | None = None, event_types: Iterable[str] | None = None
    ):
        """Επιστρέφει (aggregate_id, event) με τη σειρά που συνέβησαν.

        ⚠️ ΠΡΩΤΑ οι ΚΟΙΝΕΣ παράμετροι και μετά τα events της εταιρείας: το
        replay της μισθοδοσίας πρέπει να βρίσκει ήδη σε ισχύ τη ΓΣΣΕ και τη
        φορολογική κλίμακα της περιόδου.
        """
        head = []
        if self.shared is not None:
            head = self.shared.load(aggregate_id, event_types)
        return head + self._load_own(aggregate_id, event_types)

    def load_log(self):
        """Επιστρέφει το event log μαζί με το σταθερό sequence metadata."""
        out = []
        q = (
            "SELECT seq, event_id, aggregate_id, event_type, occurred_at, payload "
            "FROM events WHERE store = ? ORDER BY seq"
        )
        for seq, event_id, agg, etype, occurred_at, payload in self.conn.execute(
            q, (self.name,)
        ):
            cls = self.registry[etype]
            out.append(
                {
                    "seq": seq,
                    "event_id": event_id,
                    "aggregate_id": agg,
                    "event_type": etype,
                    "occurred_at": occurred_at,
                    "event": _coerce(cls, json.loads(payload)),
                }
            )
        return out

    # ------------------------------------------------------- στοχευμένα reads
    # Χωρίς αυτά οι callers θα άνοιγαν μόνοι τους το connection — και ένα
    # ξεχασμένο `WHERE store` πλέον ΔΕΝ σκάει, απλώς διαβάζει άλλη εταιρεία.

    def event_count(self) -> int:
        """Πόσα events έχει ΑΥΤΟ το store."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE store = ?", (self.name,)
        ).fetchone()[0]

    def count_by_type(self, event_type: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE store = ? AND event_type = ?",
            (self.name, event_type),
        ).fetchone()[0]

    def max_seq(self, aggregate_id: str, event_type: str) -> int | None:
        """Το seq του ΤΕΛΕΥΤΑΙΟΥ event αυτού του τύπου για το aggregate."""
        row = self.conn.execute(
            "SELECT MAX(seq) FROM events "
            "WHERE store = ? AND aggregate_id = ? AND event_type = ?",
            (self.name, aggregate_id, event_type),
        ).fetchone()
        return row[0] if row else None

    def events_after(
        self, aggregate_id: str, event_types: Iterable[str], seq: int
    ) -> list[tuple[str, str]]:
        """(event_type, payload_json) events του aggregate ΜΕΤΑ από το seq."""
        types = list(event_types)
        q = (
            "SELECT event_type, payload FROM events "
            "WHERE store = ? AND aggregate_id = ? "
            f"AND event_type IN ({_marks(types)}) AND seq > ? ORDER BY seq"
        )
        return list(self.conn.execute(q, [self.name, aggregate_id, *types, seq]))

    def raw_by_type(self, event_type: str) -> list[tuple[str, str]]:
        """(occurred_at, payload_json) για κάθε event του τύπου."""
        return list(
            self.conn.execute(
                "SELECT occurred_at, payload FROM events "
                "WHERE store = ? AND event_type = ? ORDER BY seq",
                (self.name, event_type),
            )
        )

    # ------------------------------------------------------- backup / restore
    def _raw_events(self) -> list[dict]:
        """Σειριοποιήσιμα records χωρίς upcasting ή απώλεια metadata."""
        q = (
            "SELECT seq, event_id, aggregate_id, event_type, occurred_at, payload "
            "FROM events WHERE store = ? ORDER BY seq"
        )
        return [
            {
                "seq": seq,
                "event_id": event_id,
                "aggregate_id": aggregate_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "payload": json.loads(payload),
            }
            for seq, event_id, aggregate_id, event_type, occurred_at, payload in self.conn.execute(
                q, (self.name,)
            )
        ]

    @staticmethod
    def _backup_checksum(events: list[dict]) -> str:
        canonical = json.dumps(
            events, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def export_backup(self) -> dict:
        """Δημιουργεί versioned, checksummed backup ολόκληρου του event log."""
        events = self._raw_events()
        return {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "event_count": len(events),
            "events": events,
            "sha256": self._backup_checksum(events),
        }

    def backup_bytes(self) -> bytes:
        return (
            json.dumps(self.export_backup(), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")

    def validate_backup(self, document: dict) -> list[dict]:
        """Ελέγχει format, checksum και δυνατότητα replay πριν από restore.

        ⚠️ Ο έλεγχος τύπων γίνεται κόντρα στο `self.registry`, ΟΧΙ σε σταθερό
        μητρώο: ο χώρος των χρηστών τρέχει με `AUTH_EVENT_TYPES` και τα δικά
        του events είναι εξίσου έγκυρα.
        """
        if not isinstance(document, dict):
            raise TypeError("Το backup πρέπει να είναι αντικείμενο JSON")
        if document.get("format") != BACKUP_FORMAT:
            raise ValueError("Το αρχείο δεν είναι backup events του misevent")
        if document.get("version") != BACKUP_VERSION:
            raise ValueError("Μη υποστηριζόμενη έκδοση backup")
        events = document.get("events")
        if not isinstance(events, list):
            raise TypeError("Το backup δεν περιέχει έγκυρη λίστα events")
        if document.get("event_count") != len(events):
            raise ValueError("Το πλήθος events του backup δεν συμφωνεί")
        if document.get("sha256") != self._backup_checksum(events):
            raise ValueError("Το checksum του backup δεν είναι έγκυρο")

        seen_ids: set[str] = set()
        previous_seq = 0
        validated = []
        required = {
            "seq",
            "event_id",
            "aggregate_id",
            "event_type",
            "occurred_at",
            "payload",
        }
        for index, rec in enumerate(events, start=1):
            if not isinstance(rec, dict) or not required.issubset(rec):
                raise ValueError(f"Ελλιπή δεδομένα στο event #{index} του backup")
            seq = rec["seq"]
            if not isinstance(seq, int) or isinstance(seq, bool) or seq <= previous_seq:
                raise ValueError("Τα seq του backup δεν είναι αυστηρά αύξοντα")
            previous_seq = seq
            event_id = rec["event_id"]
            if not isinstance(event_id, str) or not event_id or event_id in seen_ids:
                raise ValueError("Διπλό ή κενό event_id στο backup")
            seen_ids.add(event_id)
            if not isinstance(rec["aggregate_id"], str) or not rec["aggregate_id"]:
                raise ValueError(f"Κενό aggregate_id στο event #{seq}")
            event_type = rec["event_type"]
            if event_type not in self.registry:
                raise ValueError(f"Άγνωστος τύπος event: {event_type}")
            if not isinstance(rec["payload"], dict):
                raise TypeError(f"Μη έγκυρο payload στο event #{seq}")
            try:
                datetime.fromisoformat(rec["occurred_at"])
                _coerce(self.registry[event_type], rec["payload"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Μη έγκυρο event #{seq}: {exc}") from None
            validated.append({k: rec[k] for k in required})
        return validated

    def restore_backup(self, document: dict) -> int:
        """Αντικαθιστά ατομικά το log ΑΥΤΟΥ του store με ένα ελεγμένο backup."""
        events = self.validate_backup(document)
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute("DELETE FROM events WHERE store = ?", (self.name,))
            self.conn.executemany(
                """INSERT INTO events
                   (store, seq, event_id, aggregate_id, event_type, occurred_at, payload)
                   VALUES (:store, :seq, :event_id, :aggregate_id, :event_type,
                           :occurred_at, :payload_json)""",
                [
                    {
                        **rec,
                        "store": self.name,
                        "payload_json": json.dumps(
                            rec["payload"], ensure_ascii=False, separators=(",", ":")
                        ),
                    }
                    for rec in events
                ],
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return len(events)

    def rollback_to(self, seq: int) -> int:
        """Διαγράφει όλα τα events μετά το ``seq`` και επιστρέφει το πλήθος.

        Το event-στόχος πρέπει να υπάρχει. Η ανακατασκευή των projections
        γίνεται από τον caller, αφού ολοκληρωθεί επιτυχώς η συναλλαγή.
        """
        row = self.conn.execute(
            "SELECT 1 FROM events WHERE store = ? AND seq = ?", (self.name, seq)
        ).fetchone()
        if row is None:
            raise ValueError("Το επιλεγμένο event δεν υπάρχει πλέον")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            cur = self.conn.execute(
                "DELETE FROM events WHERE store = ? AND seq > ?", (self.name, seq)
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return cur.rowcount


def _marks(values) -> str:
    """Placeholders για `IN (...)` — όσα και τα values."""
    return ",".join("?" * len(values))
