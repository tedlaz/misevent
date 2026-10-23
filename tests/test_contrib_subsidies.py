"""
Επιδοτήσεις εισφορών πέρα από το 102:
- νέοι ≤25 (αρ. 86 ν. 4583/2018, ΑΠΔ κωδ. 107): εργοδοτική 6,66% επί
  ασφαλιστέων, έως τον μήνα που κλείνουν τα 25·
- μητέρα (αρ. 141§2 ν. 3655/2008, ΑΠΔ κωδ. 103): ½ σύνταξης εργαζομένου
  (3,335%) για 12 μήνες απασχόλησης μετά τον μήνα τοκετού — ΔΕΝ σωρεύεται
  με το 102, ισχύει η μεγαλύτερη.

Εκτέλεση: uv run pytest tests/test_contrib_subsidies.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd
import pytest
from test_apd_from_events import setup

from misevent.domain.events import LeaveType


def run(svc, year, *months):
    for m in months:
        svc.approve_attendance(year, m)
        svc.run_monthly_payroll(year, m)


def payroll(proj, month, year=2026):
    return next(p for p in proj.payrolls
                if (p.period_year, p.period_month) == (year, month))


def apd_text(svc, year, month):
    return svc.apd_file_bytes("efka", year, month).decode("cp1253").replace(
        "\r", "").replace("\n", "")


def assert_apd_balances(svc, p, year, month):
    """Η ΑΠΔ δηλώνει ΠΛΗΡΕΙΣ εισφορές + επιδοτήσεις· καταβλητέο = ό,τι
    υπολογίστηκε (κρατήθηκε + εργοδοτικές μειωμένες)."""
    rows = [r for _i, r in svc.build_apd_rows(year, month)["efka"]
            if r.earnings_type in ("001", "008")]
    a = p.analysis
    assert round(sum(r.subsidy_employee for r in rows), 2) == a["subsidy_employee"]
    assert round(sum(r.subsidy_employer for r in rows), 2) == a["subsidy_employer"]
    assert round(sum(r.payable for r in rows), 2) == round(
        p.efka_employee + p.efka_employer, 2)
    return rows


def test_youth_employer_subsidy_in_payroll_and_apd():
    svc, proj, _eid = setup(birth_date=date(2005, 3, 10))
    run(svc, 2026, 1, 2)
    p = payroll(proj, 2)
    sub = round(1500 * 0.0666, 2)
    assert p.analysis["subsidy_employer"] == sub
    assert p.efka_employer == round(round(1500 * 0.2179, 2) - sub, 2)
    assert p.analysis["subsidy_employee"] == 0.0  # η κράτηση ΑΜΕΤΑΒΛΗΤΗ
    rows = assert_apd_balances(svc, p, 2026, 2)
    assert rows[0].contrib_employer == round(1500 * 0.2179, 2), "ΠΛΗΡΗΣ στην ΑΠΔ"
    assert "4107000000" + "0000000000" + "006660" in apd_text(svc, 2026, 2)


def test_youth_ends_on_25th_birthday():
    """Λήγει ΤΗΝ ΗΜΕΡΑ των γενεθλίων: 15/2 → 14 από τις 28 ημέρες του Φεβ."""
    svc, proj, _eid = setup(birth_date=date(2001, 2, 15))
    run(svc, 2026, 1, 2, 3)
    p1 = payroll(proj, 1)  # πρόσληψη 5/1: αναλογικά μικτά
    assert p1.analysis["subsidy_employer"] == round(p1.gross * 0.0666, 2)
    assert payroll(proj, 2).analysis["subsidy_employer"] == round(
        1500 * 0.0666 * 14 / 28, 2)
    assert payroll(proj, 3).analysis["subsidy_employer"] == 0.0


def test_youth_on_christmas_gift():
    svc, proj, _eid = setup(birth_date=date(2005, 3, 10))
    for m in range(1, 13):
        if m == 4:
            svc.apply_minimum_adjustments(2026, 4)
        run(svc, 2026, m)
    svc.run_christmas_gifts(2026)
    g = next(g for g in proj.gifts if g.gift_type == "christmas")
    assert g.subsidy_employer == round(g.gross * 0.0666, 2)
    row = next(r for _i, r in svc.build_apd_rows(2027, 1)["efka"]
               if r.earnings_type == "003")
    assert row.subsidy_employer == g.subsidy_employer


def test_mother_reduction_starts_after_birth_month():
    svc, proj, eid = setup(sex="1")
    svc.record_child_birth(eid, date(2026, 1, 20))
    svc.record_leave(eid, LeaveType.SICK, date(2026, 2, 2), date(2026, 2, 6))
    run(svc, 2026, 1, 2)
    assert payroll(proj, 1).analysis["subsidy_employee"] == 0.0
    p = payroll(proj, 2)
    assert p.analysis["subsidy_code"] == "103"
    assert p.analysis["subsidy_employee"] == round(p.gross * 0.03335, 2)
    assert_apd_balances(svc, p, 2026, 2)
    assert "4103003335" in apd_text(svc, 2026, 2)


def test_mother_twelve_months_of_employment():
    svc, proj, eid = setup(sex="1")
    svc.record_child_birth(eid, date(2025, 12, 5))
    run(svc, 2026, *range(1, 13))
    assert payroll(proj, 12).analysis["subsidy_code"] == "103"
    emp = proj.employments[eid]
    assert not svc.mother_subsidy_on(emp, date(2027, 1, 29)), "12 μήνες εξαντλήθηκαν"


def test_mother_does_not_stack_with_minimum_wage_subsidy():
    svc, proj, eid = setup(monthly_gross=880.0, sex="1")
    svc.record_child_birth(eid, date(2025, 12, 5))
    run(svc, 2026, 1, 2)
    a = payroll(proj, 2).analysis
    assert (a["subsidy_code"], a["subsidy_employee"]) == ("102", 58.70)


def test_child_birth_only_for_women():
    svc, _proj, eid = setup(sex="0")
    with pytest.raises(ValueError):
        svc.record_child_birth(eid, date(2026, 1, 20))


def test_payslip_itemizes_subsidies(monkeypatch):
    """Απόδειξη PDF: πλήρης εισφορά, η επιδότηση ως ΞΕΧΩΡΙΣΤΗ γραμμή, και τα
    σύνολα βγαίνουν ίσα με ό,τι κρατήθηκε/καταβάλλεται."""
    from webapp import pdf_reports as pr

    lines = []
    monkeypatch.setattr(pr, "_line", lambda _pdf, label, _note, amount, sign="",
                        **_k: lines.append((label, sign, amount)))
    monkeypatch.setattr(pr, "_subtotal", lambda *_a: None)
    a = {"efka_detail": [{"label": "ΕΦΚΑ", "employee": 0.1337, "amount": 150.0},
                         {"label": "ΤΕΚΑ", "employee": 0.0, "amount": 10.0}],
         "subsidy_employee": 50.0, "subsidy_code": "103",
         "gross": 1500.0, "efka_employer": 226.85, "subsidy_employer": 99.9}
    pr._efka_deduction_lines(None, a)
    pr._employer_cost_lines(None, a, "")
    assert lines == [
        ("Εισφορές — ΕΦΚΑ", "− ", 200.0),
        ("Εισφορές — ΤΕΚΑ", "− ", 10.0),
        (pr.labels.SUBSIDY_LABELS["103"], "+ ", 50.0),
        ("Ασφαλιστικές εισφορές εργοδότη", "+ ", 326.75),
        (pr.labels.SUBSIDY_LABELS["107"], "− ", 99.9),
    ]
