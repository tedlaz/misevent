"""
Tests ΑΠΔ για τις **Αποδοχές ασθενείας (τύπος 008)**.

Ο κανόνας (όπως τον δηλώνει ο e-ΕΦΚΑ):
  - Ό,τι πλήρωσε ο εργοδότης για ημέρες ασθενείας δηλώνεται ΞΕΧΩΡΙΣΤΑ, με
    τύπο αποδοχών **008 «Αποδοχές ασθενείας»** — όχι μέσα στις τακτικές.
  - Στις **ημέρες** (No 43) μπαίνουν οι ημέρες ασθενείας.
  - Στο **διάστημα** (No 40/41) μπαίνουν η έναρξη και η λήξη της ασθένειας
    (και όχι ο πλήρης μήνας, όπως στις τακτικές αποδοχές).

Αναλλοίωτα που ελέγχονται εδώ: 001 + 008 = τα μικτά ΚΑΙ οι ημέρες
ασφάλισης της μισθοδοσίας — η ΑΠΔ δεν αλλάζει σύνολα, μόνο τα αναλύει.

Εκτέλεση: uv run tests/test_apd_sickness.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain.events import LeaveType
from misevent.domain.projections import Projections
from misevent.persistence.database import Database

FULL_WEEK = {
    "MON": "09:00-17:00",
    "TUE": "09:00-17:00",
    "WED": "09:00-17:00",
    "THU": "09:00-17:00",
    "FRI": "09:00-17:00",
}


def setup(monthly_gross=1500.0, hire=date(2024, 4, 8)):
    """Μισθωτός πλήρους απασχόλησης ΜΕ ασφαλιστικό προφίλ (χρειάζεται για
    την παραγωγή γραμμών ΑΠΔ)."""
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
    svc.define_kpk_rates(
        "efka_main", "0101", date(2024, 1, 1), 0.1337, 0.2179,
        "main_pension=10,37/18,79|healthcare=0/0|auxiliary=3/3",
    )
    svc.define_specialty(
        "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ",
        {"MAIN_EPIK": {"kad": "0141", "specialty_code": "411100",
                       "kpk": "0101"}},
    )
    pid = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ", first_name="ΙΩΑΝΝΗΣ", father_name="ΝΙΚΟΛΑΟΣ",
        mother_name="ΜΑΡΙΑ", birth_date=date(1990, 5, 5), afm="111222336",
        amka="05059012343", ama="123456789",
    )
    eid = svc.start_employment(
        pid, hire_date=hire, monthly_gross=monthly_gross,
        weekly_hours=40.0, weekly_pattern=FULL_WEEK,
    )
    svc.assign_specialty(eid, "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ")
    svc.submit_hire_e3(eid)
    svc.submit_hire_wto(eid)
    return svc, proj, eid


def rows(svc, year, month, fund="efka"):
    return [r for _, r in svc.build_apd_rows(year, month)[fund]]


def test_sickness_row_days_period_and_amount():
    """Μία ασθένεια: ξεχωριστή γραμμή 008 με τις ημέρες & το διάστημά της."""
    svc, proj, eid = setup()
    # Δευ 5/1 - Παρ 9/1/2026: 5 εργάσιμες -> 3 μισές + 2 κανονικά, επίδομα 120
    lid = svc.record_leave(eid, LeaveType.SICK, date(2026, 1, 5), date(2026, 1, 9))
    svc.record_sickness_benefit(eid, lid, 120.0)
    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)

    regular = [r for r in rows(svc, 2026, 1) if r.earnings_type == "001"]
    sick = [r for r in rows(svc, 2026, 1) if r.earnings_type == "008"]
    assert len(regular) == 1 and len(sick) == 1

    # Ημερομίσθιο 1500/25 = 60. Πληρωμένο ασθενείας: 3 x 30 + 2 x 60 - 120 = 90
    assert sick[0].gross == 90.0
    assert sick[0].insurance_days == 5            # ημέρες ασθενείας
    assert sick[0].date_from == date(2026, 1, 5)  # έναρξη ασθένειας
    assert sick[0].date_to == date(2026, 1, 9)    # λήξη ασθένειας

    # Οι τακτικές μειώνονται αντίστοιχα — τα σύνολα ΔΕΝ αλλάζουν
    p = next(p for p in proj.payrolls if (p.period_year, p.period_month) == (2026, 1))
    assert regular[0].gross + sick[0].gross == p.gross
    assert regular[0].insurance_days + sick[0].insurance_days == p.insurance_days
    assert regular[0].date_from == date(2026, 1, 1)   # τακτικές: πλήρης μήνας
    assert regular[0].date_to == date(2026, 1, 31)
    print("OK 008: ημέρες, διάστημα ασθενείας και ποσό· τα σύνολα μένουν ίδια")


def test_two_sicknesses_two_rows():
    """Δύο ασθένειες στον ίδιο μήνα: ΜΙΑ γραμμή 008 ανά ασθένεια."""
    svc, proj, eid = setup()
    svc.record_leave(eid, LeaveType.SICK, date(2026, 1, 5), date(2026, 1, 6))
    svc.record_leave(eid, LeaveType.SICK, date(2026, 1, 19), date(2026, 1, 21))
    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)

    sick = sorted(
        (r for r in rows(svc, 2026, 1) if r.earnings_type == "008"),
        key=lambda r: r.date_from,
    )
    assert len(sick) == 2
    assert [(r.date_from, r.date_to, r.insurance_days) for r in sick] == [
        (date(2026, 1, 5), date(2026, 1, 6), 2),
        (date(2026, 1, 19), date(2026, 1, 21), 3),
    ]
    # 1η ασθένεια: 2 μισές (60). 2η: 1 μισή + 2 κανονικά (30 + 120 = 150)
    assert [r.gross for r in sick] == [60.0, 150.0]
    p = next(p for p in proj.payrolls if (p.period_year, p.period_month) == (2026, 1))
    regular = next(r for r in rows(svc, 2026, 1) if r.earnings_type == "001")
    assert round(regular.gross + sum(r.gross for r in sick), 2) == p.gross
    assert regular.insurance_days + sum(r.insurance_days for r in sick) == 25
    print("OK δύο ασθένειες -> δύο γραμμές 008, μία ανά διάστημα")


def test_sickness_across_months_is_clipped():
    """Ασθένεια που περνά τον μήνα: κάθε ΑΠΔ δηλώνει το δικό της κομμάτι."""
    svc, proj, eid = setup()
    # Δευ 26/1 - Παρ 6/2/2026 (10 εργάσιμες, 5 σε κάθε μήνα)
    svc.record_leave(eid, LeaveType.SICK, date(2026, 1, 26), date(2026, 2, 6))
    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)
    svc.approve_attendance(2026, 2)
    svc.run_monthly_payroll(2026, 2)

    jan = next(r for r in rows(svc, 2026, 1) if r.earnings_type == "008")
    feb = next(r for r in rows(svc, 2026, 2) if r.earnings_type == "008")
    assert (jan.date_from, jan.date_to) == (date(2026, 1, 26), date(2026, 1, 31))
    assert (feb.date_from, feb.date_to) == (date(2026, 2, 1), date(2026, 2, 6))
    assert jan.insurance_days == 5 and feb.insurance_days == 5
    print("OK ασθένεια σε δύο μήνες: το διάστημα κόβεται ανά περίοδο")


def test_no_sickness_no_extra_row():
    """Χωρίς ασθένεια: καμία γραμμή 008 — η ΑΠΔ μένει όπως ήταν."""
    svc, proj, eid = setup()
    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)
    all_rows = rows(svc, 2026, 1)
    assert [r.earnings_type for r in all_rows] == ["001"]
    assert all_rows[0].insurance_days == 25
    print("OK χωρίς ασθένεια δεν προστίθεται γραμμή 008")


def test_unpaid_over_limit_days_stay_out():
    """Ημέρες πάνω από το όριο είναι ΑΝΕΥ ΑΠΟΔΟΧΩΝ: δεν μπαίνουν στο 008
    (ούτε στις ημέρες ασφάλισης — τις κόβει ήδη το συγκεντρωτικό)."""
    svc, proj, eid = setup()
    # 30 συνεχόμενες εργάσιμες από Δευ 5/1/2026 -> 26 πληρωμένες, 4 over
    svc.record_leave(eid, LeaveType.SICK, date(2026, 1, 5), date(2026, 2, 13))
    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)
    jan = next(r for r in rows(svc, 2026, 1) if r.earnings_type == "008")
    # Ιανουάριος: 20 εργάσιμες ασθενείας (5/1-31/1), όλες εντός ορίου
    assert jan.insurance_days == 20

    svc.approve_attendance(2026, 2)
    svc.run_monthly_payroll(2026, 2)
    feb_sick = [r for r in rows(svc, 2026, 2) if r.earnings_type == "008"]
    # Φεβρουάριος: 6 εντός ορίου (26 - 20) + 4 over που ΔΕΝ πληρώνονται
    assert feb_sick[0].insurance_days == 6
    p = next(p for p in proj.payrolls if (p.period_year, p.period_month) == (2026, 2))
    regular = next(r for r in rows(svc, 2026, 2) if r.earnings_type == "001")
    assert regular.insurance_days + feb_sick[0].insurance_days == p.insurance_days
    print("OK οι ημέρες άνω του ορίου μένουν εκτός των αποδοχών ασθενείας")
