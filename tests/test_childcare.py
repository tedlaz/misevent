"""
Tests μειωμένου ωραρίου φροντίδας τέκνου: η σύμβαση μένει πλήρης, το
πρόγραμμα επιτρέπεται να είναι μικρότερο ΜΟΝΟ μέσα στην περίοδο μείωσης.

Εκτέλεση: python test_childcare.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd
import pytest
from test_schedule_ergani import setup

REDUCED = {d: "09:00-16:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}  # 35ω


def test_alternative_periods():
    """2ω × 12 μήνες και μετά 1ω × 6 μήνες, συνεχόμενα."""
    svc, proj, eid = setup()
    svc.set_childcare_reduction(eid, date(2026, 3, 31), "alternative")
    emp = proj.employments[eid]
    assert emp.childcare_reduction == [
        (date(2026, 3, 31), date(2027, 3, 30), 2),
        (date(2027, 3, 31), date(2027, 9, 29), 1),
    ]
    assert emp.childcare_hours_on(date(2026, 3, 30)) == 0
    assert emp.childcare_hours_on(date(2027, 3, 31)) == 1
    assert emp.childcare_hours_on(date(2027, 9, 30)) == 0


def test_reduced_schedule_only_within_period():
    svc, proj, eid = setup()
    with pytest.raises(ValueError):  # χωρίς δήλωση: 35 ≠ 40
        svc.set_schedule(eid, date(2026, 4, 6), REDUCED)
    svc.set_childcare_reduction(eid, date(2026, 4, 1), "classic")
    svc.set_schedule(eid, date(2026, 4, 6), REDUCED)
    assert proj.employments[eid].weekly_hours == 40.0  # η σύμβαση δεν αλλάζει
    with pytest.raises(ValueError):  # 30 < 40 − 1×5
        svc.set_schedule(eid, date(2026, 4, 13), {"MON": "09:00-15:00", **{
            d: "09:00-15:00" for d in ("TUE", "WED", "THU", "FRI")}})
    svc.set_childcare_reduction(eid, date(2026, 4, 1))  # κατάργηση
    assert proj.employments[eid].childcare_hours_on(date(2026, 5, 1)) == 0
