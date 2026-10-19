"""ΜΙΑ βάση, πολλά stores: οι εγγυήσεις που πριν τις έδινε το filesystem.

Όσο κάθε χώρος ήταν χωριστό αρχείο, η απομόνωση ήταν δωρεάν. Τώρα είναι
συνθήκη `WHERE store = ?` σε κάθε query — ένα ξεχασμένο φίλτρο ΔΕΝ σκάει,
απλώς διαβάζει ή σβήνει δεδομένα άλλης εταιρείας. Αυτά τα tests είναι το
δίχτυ ακριβώς για αυτό.
"""

import os
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.domain.auth_events import AUTH_EVENT_TYPES, UserRegistered
from misevent.domain.events import CompanyRegistered
from misevent.persistence.database import SYSTEM_STORE, Database, company_store


def _register(store, name):
    return store.append(
        "COMPANY", CompanyRegistered("0000000001", "000000001", name)
    )


class StoreIsolationTests(unittest.TestCase):
    """Δύο stores στην ΙΔΙΑ βάση δεν βλέπει ο ένας τον άλλο."""

    def setUp(self):
        self.db = Database()
        self.a = self.db.store("company:a")
        self.b = self.db.store("company:b")
        for name in ("ΑΛΦΑ 1", "ΑΛΦΑ 2", "ΑΛΦΑ 3"):
            _register(self.a, name)
        _register(self.b, "ΒΗΤΑ 1")

    def test_each_store_reads_only_its_own_events(self):
        self.assertEqual(self.a.event_count(), 3)
        self.assertEqual(self.b.event_count(), 1)
        names = [e.legal_name for _, e in self.b.load()]
        self.assertEqual(names, ["ΒΗΤΑ 1"])

    def test_seq_is_per_store_and_starts_at_one(self):
        self.assertEqual([r["seq"] for r in self.a.load_log()], [1, 2, 3])
        self.assertEqual([r["seq"] for r in self.b.load_log()], [1])

    def test_same_connection_is_shared(self):
        """Η απομόνωση είναι λογική, όχι δομική — μία βάση, ένα connection."""
        self.assertIs(self.a.conn, self.b.conn)

    def test_rollback_does_not_touch_other_stores(self):
        removed = self.a.rollback_to(1)
        self.assertEqual(removed, 2)
        self.assertEqual(self.a.event_count(), 1)
        self.assertEqual(self.b.event_count(), 1)

    def test_restore_does_not_touch_other_stores(self):
        backup = self.a.export_backup()
        self.a.rollback_to(1)
        restored = self.a.restore_backup(backup)
        self.assertEqual(restored, 3)
        self.assertEqual(self.a.event_count(), 3)
        self.assertEqual(self.b.event_count(), 1)

    def test_targeted_reads_are_scoped(self):
        self.assertEqual(self.a.count_by_type("CompanyRegistered"), 3)
        self.assertEqual(self.b.count_by_type("CompanyRegistered"), 1)
        self.assertEqual(self.a.max_seq("COMPANY", "CompanyRegistered"), 3)
        self.assertEqual(self.b.max_seq("COMPANY", "CompanyRegistered"), 1)
        self.assertEqual(len(self.a.raw_by_type("CompanyRegistered")), 3)
        self.assertEqual(
            [t for t, _ in self.a.events_after("COMPANY", ("CompanyRegistered",), 1)],
            ["CompanyRegistered", "CompanyRegistered"],
        )

    def test_seq_continues_after_rollback(self):
        self.a.rollback_to(1)
        _register(self.a, "ΑΛΦΑ 4")
        self.assertEqual([r["seq"] for r in self.a.load_log()], [1, 2])


class AuthBackupTests(unittest.TestCase):
    """Ο χώρος των χρηστών τρέχει με ΑΛΛΟ μητρώο τύπων — και πρέπει να
    μπορεί να παίρνει και να επαναφέρει backup σαν κάθε άλλος."""

    def test_backup_roundtrip_with_auth_registry(self):
        db = Database()
        store = db.store(SYSTEM_STORE, registry=AUTH_EVENT_TYPES)
        store.append(
            "USER-1",
            UserRegistered("USER-1", "a@b.gr", "2026-01-01T00:00:00", True),
        )
        backup = store.export_backup()
        store.validate_backup(backup)  # δεν πετά «Άγνωστος τύπος event»
        store.rollback_to(1)
        self.assertEqual(store.restore_backup(backup), 1)
        self.assertEqual(store.event_count(), 1)



class PersistenceTests(unittest.TestCase):
    """Το ίδιο αρχείο ξανανοίγει με τα ίδια stores και τα ίδια seq."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

    def tearDown(self):
        # Το WAL αφήνει και -wal/-shm δίπλα στο αρχείο.
        for suffix in ("", "-wal", "-shm"):
            leftover = Path(self.path + suffix)
            if leftover.exists():
                os.remove(leftover)

    def test_stores_survive_reopen(self):
        db = Database(self.path)
        alpha = db.store(company_store("alpha"))
        beta = db.store(company_store("beta"))
        _register(alpha, "ΑΛΦΑ")
        _register(alpha, "ΑΛΦΑ 2")
        _register(beta, "ΒΗΤΑ")
        db.close()

        reopened = Database(self.path)
        self.assertEqual(reopened.store(company_store("alpha")).event_count(), 2)
        self.assertEqual(reopened.store(company_store("beta")).event_count(), 1)
        self.assertEqual(
            [r["seq"] for r in reopened.store(company_store("alpha")).load_log()],
            [1, 2],
        )
        reopened.close()

    def test_company_store_naming_is_single_sourced(self):
        self.assertEqual(company_store("abc"), "company:abc")


if __name__ == "__main__":
    unittest.main()
