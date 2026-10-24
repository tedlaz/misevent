"""Κάρτα, παρουσίες και μισθοδοσία: ΤΕΛΟΣ του ημερολογιακού μήνα (30/31, 28/29
τον Φεβρουάριο) — ΟΧΙ στην τελευταία εργάσιμη (π.χ. 29/05/2026, Παρασκευή)."""

from datetime import date

import _bootstrap  # noqa: F401
import pytest

from misevent.rules import obligation_types as ot


@pytest.mark.parametrize("y, m, end", [
    (2026, 5, date(2026, 5, 31)),   # Κυριακή — η τελ. εργάσιμη θα ήταν 29/05
    (2026, 2, date(2026, 2, 28)),   # Σάββατο
    (2028, 2, date(2028, 2, 29)),   # δίσεκτο
    (2026, 4, date(2026, 4, 30)),
    (2026, 12, date(2026, 12, 31)),
])
def test_period_close_due_is_calendar_month_end(y, m, end):
    # μέχρι την τελευταία ημέρα μπορεί να συμβεί μισθολογικό event
    for code in ("CARD_IMPORT", "ATTENDANCE_APPROVAL", "MONTHLY_PAYROLL"):
        assert ot.due_date(code, y, m) == end, code


def test_other_types_keep_last_working_day():
    assert ot.due_date("SALARY_PAYMENT", 2026, 5) == date(2026, 5, 29)


def test_eom_cal_rule_is_valid_and_shifts_months():
    ot.validate_due_rule(("eom_cal", 1))
    with pytest.raises(ValueError):
        ot.validate_due_rule(("eom_cal", 13))
