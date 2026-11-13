"""Upcasting: παλιό payload στη βάση -> τρέχουσα μορφή στο replay (ARCH_REF §5.1)."""

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd
import pytest

from misevent.domain import events
from misevent.domain.events import CompanyRegistered
from misevent.persistence.database import Database


def test_old_payload_is_upcast_on_every_read_path(monkeypatch):
    store = Database().store("company:upcast")
    store.append("COMPANY", CompanyRegistered("0000000001", "000000001", "ΑΛΦΑ"))
    backup = store.export_backup()  # v1: χωρίς `_v`

    # «Νέα έκδοση» του event: v1 -> v2 προσθέτει «ΑΕ» στην επωνυμία.
    up = lambda p: {**p, "legal_name": p["legal_name"] + " ΑΕ"}  # noqa: E731
    monkeypatch.setitem(events.UPCASTERS, "CompanyRegistered", [up])

    ((_, ev),) = store.load()
    assert ev.legal_name == "ΑΛΦΑ ΑΕ"
    assert store.load_log()[0]["event"].legal_name == "ΑΛΦΑ ΑΕ"
    assert store.raw_by_type("CompanyRegistered")[0][1]["legal_name"] == "ΑΛΦΑ ΑΕ"
    assert store.restore_backup(backup) == 1  # παλιό backup περνά validation

    # Νέα εγγραφή: σημαδεύεται v2 και ΔΕΝ ξαναπερνά από τον upcaster.
    store.append("COMPANY", CompanyRegistered("0000000002", "000000002", "ΒΗΤΑ ΑΕ"))
    last = store._raw_events()[-1]["payload"]
    assert last["_v"] == 2
    assert store.load()[-1][1].legal_name == "ΒΗΤΑ ΑΕ"


def test_payload_from_newer_program_is_refused():
    with pytest.raises(ValueError, match="νεότερο"):
        events.upcast("CompanyRegistered", {"_v": 2})


def test_unversioned_types_are_untouched():
    payload = {"name": "x"}
    assert events.upcast("CompanyRegistered", payload) is payload
