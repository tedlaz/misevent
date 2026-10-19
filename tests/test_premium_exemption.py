"""
Απαλλαγή εισφορών προσαυξήσεων (αρ. 41 ν. 5184/2025, Εγκ. e-ΕΦΚΑ 21/2025).

Πλήρης απασχόληση: η ΠΡΟΣΑΥΞΗΣΗ νύχτας/υπερεργασίας/υπερωρίας/αργίας δεν
έχει εισφορές — η απλή αξία της ώρας έχει. Η ΑΠΔ δηλώνει μόνο το ποσό με
εισφορές· ο ΦΜΥ υπολογίζεται στα πλήρη μικτά.

Εκτέλεση: uv run pytest tests/test_premium_exemption.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd
from test_apd_from_events import setup

from misevent.app.services import PayrollService

HOURS = {"overtime": 2.0, "normal_n": 4.0}
# ωρομίσθιο 1500/25×6/40 = 9,00
# υπερωρία: 9×1,40×2 = 25,20 → απλή αξία 18,00 (εισφορές) + 7,20 (απαλλαγή)
# νύχτα εντός ωραρίου: 9×0,25×4 = 9,00 → όλο προσαύξηση (απαλλαγή)
EXEMPT = 16.20


def feb(svc, proj, eid):
    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)
    svc.approve_attendance(2026, 2, {eid: HOURS})
    svc.run_monthly_payroll(2026, 2)
    return next(p for p in proj.payrolls if p.period_month == 2)


def test_premiums_carry_no_contributions_and_stay_out_of_apd():
    svc, proj, eid = setup(monthly_gross=1500.0)
    p = feb(svc, proj, eid)
    assert p.premiums == 34.20 and p.gross == 1534.20
    assert p.analysis["premium_exempt"] == EXEMPT
    base = 1534.20 - EXEMPT  # 1518,00: μισθός + απλή αξία υπερωρίας
    assert p.efka_employee == round(base * 0.1337, 2)
    assert p.taxable == round(p.gross - p.efka_employee, 2), "ο ΦΜΥ στα πλήρη μικτά"

    rows = [r for _i, r in svc.build_apd_rows(2026, 2)["efka"]]
    assert rows[0].gross == base, "η ΑΠΔ δηλώνει μόνο το ποσό με εισφορές"


def test_part_time_and_before_november_2025_get_nothing():
    class Emp:
        weekly_hours = 20.0

    supplement = {"premium": 16.2}
    assert PayrollService.premium_exempt(Emp, supplement, date(2026, 2, 27)) == 0.0
    Emp.weekly_hours = 40.0
    assert PayrollService.premium_exempt(Emp, supplement, date(2025, 10, 31)) == 0.0
    assert PayrollService.premium_exempt(Emp, supplement, date(2025, 11, 28)) == 16.2
