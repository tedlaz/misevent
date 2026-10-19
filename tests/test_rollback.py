"""Στοχευμένοι έλεγχοι για το destructive event-log rollback."""

import copy
import os
import tempfile
import unittest

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.domain.events import CompanyRegistered
from misevent.persistence.database import Database


class EventStoreRollbackTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = Database(self.path).store("test")
        for name in ("ΠΡΩΤΗ", "ΔΕΥΤΕΡΗ", "ΤΡΙΤΗ"):
            self.store.append(
                "COMPANY",
                CompanyRegistered("0000000001", "000000001", name),
            )

    def tearDown(self):
        self.store.conn.close()
        os.remove(self.path)

    def test_rollback_keeps_selected_event_and_removes_all_newer(self):
        log = self.store.load_log()
        selected = log[1]["seq"]

        removed = self.store.rollback_to(selected)

        remaining = self.store.load_log()
        self.assertEqual(removed, 1)
        self.assertEqual([row["seq"] for row in remaining], [log[0]["seq"], selected])
        self.assertEqual(remaining[-1]["event"].legal_name, "ΔΕΥΤΕΡΗ")

    def test_rollback_rejects_unknown_sequence(self):
        with self.assertRaisesRegex(ValueError, "δεν υπάρχει"):
            self.store.rollback_to(999999)
        self.assertEqual(len(self.store.load_log()), 3)

    def test_backup_restore_preserves_all_event_metadata(self):
        backup = self.store.export_backup()
        original = self.store._raw_events()
        self.store.rollback_to(original[0]["seq"])

        restored = self.store.restore_backup(backup)

        self.assertEqual(restored, 3)
        self.assertEqual(self.store._raw_events(), original)

    def test_restore_rejects_tampered_backup_without_changing_store(self):
        backup = self.store.export_backup()
        tampered = copy.deepcopy(backup)
        tampered["events"][0]["payload"]["legal_name"] = "ΑΛΛΟΙΩΜΕΝΟ"
        before = self.store._raw_events()

        with self.assertRaisesRegex(ValueError, "checksum"):
            self.store.restore_backup(tampered)

        self.assertEqual(self.store._raw_events(), before)


if __name__ == "__main__":
    unittest.main()
