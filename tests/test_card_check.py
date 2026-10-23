"""
ΕΛΕΓΧΟΣ ΑΡΤΙΟΤΗΤΑΣ ΚΑΡΤΑΣ πριν τη μισθοδοσία («Διάφορα → card_required»).

- χωρίς κανένα χτύπημα στον μήνα -> "empty" (δεν εισήχθη το αρχείο)·
- χτύπημα χωρίς ταίρι -> μετρά με το πρόγραμμα, δεν μπλοκάρει·
- ημέρα χωρίς χτύπημα -> προειδοποίηση, την αποδέχεται η ολοκλήρωση·
- νέο χτύπημα στον μήνα αναιρεί την ολοκλήρωση.

Εκτέλεση: uv run pytest tests/test_card_check.py
"""

from datetime import date

import pytest
from test_closed_period import setup

FEB = [date(2026, 2, d) for d in range(1, 29)]


def test_card_required_gates_attendance(fresh_system):
    svc, proj, eid = setup()  # πρόγραμμα 09-17 Δε–Πα
    svc.set_misc_settings(card_required=True)
    emp = proj.employments[eid]
    assert [i["kind"] for i in svc.card_issues(emp, 2026, 2)] == ["empty"]
    with pytest.raises(ValueError, match="έλεγχος κάρτας"):
        svc.approve_attendance(2026, 2)

    days = [d for d in FEB if emp.day_schedule(d)]
    for d in days[2:]:
        svc.record_attendance(eid, d, ("09:00-17:00",))
    pid = svc.record_card_punch(eid, days[0], "09:00", "in")  # χωρίς έξοδο
    assert [(i["day"], i["kind"], i["blocking"])
            for i in svc.card_issues(emp, 2026, 2)] == [
        (days[0], "open", False), (days[1], "no_punch", False)]
    svc.confirm_card_month(eid, 2026, 2)  # ορφανό = πρόγραμμα, δεν μπλοκάρει
    assert not svc.card_month_pending(emp, 2026, 2)
    svc.cancel_card_punch(pid)  # νέο event στον μήνα -> ξανά έλεγχος
    svc.confirm_card_month(eid, 2026, 2)
    assert not svc.card_month_pending(emp, 2026, 2)
    svc.record_card_punch(eid, days[0], "09:00", "in")  # νέο χτύπημα -> ξανά
    assert svc.card_month_pending(emp, 2026, 2)
    svc.record_card_punch(eid, days[0], "17:00", "out")
    svc.confirm_card_month(eid, 2026, 2)
    svc.approve_attendance(2026, 2)


def test_off_by_default(fresh_system):
    svc, proj, eid = setup()
    assert not svc.card_month_pending(proj.employments[eid], 2026, 2)
