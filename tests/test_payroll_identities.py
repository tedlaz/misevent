"""
ΛΟΓΙΣΤΙΚΕΣ ΤΑΥΤΟΤΗΤΕΣ της μισθοδοσίας — ΑΚΡΙΒΩΣ στο λεπτό, σε κάθε διαδρομή:

  1. Αποδοχές − Εισφορές εργαζομένου − Φόροι = Καθαρό πληρωτέο
  2. Συνολικό κόστος = Αποδοχές + Εισφορές εργοδότη
  3. Συνολικό κόστος = Εισφ. εργαζ. + Εισφ. εργοδ. + Φόροι + Καθαρό

Η (1) επιβάλλεται στο `PayrollResult.__post_init__`· εδώ ελέγχεται ότι καμία
διαδρομή του πυρήνα δεν την παραβιάζει σε πλέγμα εισόδων. Καθαρές συναρτήσεις
— κανένα store (οι ίδιες ταυτότητες στα events: `test_cost_report.py`).
"""

from datetime import date
from itertools import product

import _bootstrap  # noqa: F401  ΠΡΩΤΟ import (ρίζα στο sys.path + chdir)
import pytest

from misevent.domain import payroll_engine as pe

WAGES = (0.0, 333.33, 880.0, 1234.567, 2999.99, 9000.0)  # 9000 > πλαφόν
CHILDREN = (0, 2, 5)
END = date(2026, 3, 31)


def _assert_identities(r):
    cost = pe.r2(r.gross + r.efka_employer)
    assert pe.r2(r.gross - r.efka_employee - r.fmy) == r.net, r
    assert pe.r2(r.efka_employee + r.efka_employer + r.fmy + r.net) == cost, r


def test_monthly():
    for w, ch, unpaid, sick, prem in product(
            WAGES, CHILDREN, (0, 3), (0.0, 17.777), (0.0, 45.555)):
        _assert_identities(pe.calc_monthly(
            w, ch, END, unpaid_days=unpaid, sick=sick, premiums=prem,
            exempt=prem / 3, subsidy=w * 0.0667, mother_pct=0.01, youth_pct=0.02))


def test_daily():
    for d, days, ch, prem in product(
            (0.0, 39.30, 51.8333, 120.0), (0, 13, 26), CHILDREN, (0.0, 12.345)):
        _assert_identities(pe.calc_monthly_daily(d, days, ch, END, premiums=prem))


@pytest.mark.parametrize("fn", [
    pe.calc_christmas_gift, pe.calc_easter_gift, pe.calc_leave_allowance])
def test_gifts(fn):
    for w, ch, hire in product(
            WAGES, CHILDREN, (date(2020, 1, 1), date(2026, 2, 17), date(2026, 9, 3))):
        _assert_identities(fn(w, ch, 2026, hire, subsidy_full=w * 0.0667))


@pytest.mark.parametrize("fn", [
    pe.calc_christmas_gift_daily, pe.calc_easter_gift_daily,
    pe.calc_leave_allowance_daily])
def test_gifts_daily(fn):
    for d, ch, hire in product(
            (39.30, 51.8333), CHILDREN, (date(2020, 1, 1), date(2026, 3, 9))):
        _assert_identities(fn(d, ch, 2026, hire))


def test_guard_rejects_mismatch():
    with pytest.raises(ValueError, match="Ασυμφωνία"):
        pe.PayrollResult(1000.0, 130.0, 220.0, 870.0, 50.0, 821.0)
