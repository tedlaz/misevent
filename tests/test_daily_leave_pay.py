"""
Ημερομίσθιος με άδεια/ασθένεια — ΚΑΜΙΑ ημέρα δεν χάνεται δύο φορές.

Πριν: ο ημερομίσθιος πληρωνόταν ΜΟΝΟ τις ημέρες εργασίας. Η κανονική άδεια
έμενε απλήρωτη και στην ασθένεια οι ημέρες ήταν απλήρωτες ΚΑΙ αφαιρούνταν η
περικοπή (διπλή ποινή). Τώρα, όπως στον μισθωτό:
  μικτά = αμοιβή ημέρας × (εργασία + άδεια με αποδοχές)
        + ημέρες ασθενείας × ημερομίσθιο ΕΞΑΗΜΕΡΟΥ − περικοπή ασθενείας.

Σενάριο: ημερομίσθιο 50€, πενθήμερο 8ωρο -> αμοιβή ημέρας 60€ (×6/5).
Μάρτιος 2026: 22 εργάσιμες = 1.320€. Απουσία Δευ 2/3 – Παρ 6/3.
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd
from _bootstrap import PERSON_ID

from misevent.app.services import PayrollService
from misevent.domain.events import LeaveType
from misevent.domain.projections import Projections
from misevent.persistence.database import Database

WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}


def run(kind=None, benefit=0.0):
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
    svc.define_kpk_rates("efka_main", "0101", date(2024, 1, 1), 0.1337, 0.2179,
                         "main_pension=13,37/21,79|healthcare=0/0")
    pid = svc.register_person(
        last_name="ΗΜΕΡΟΜΙΣΘΙΟΥ", first_name="ΝΙΚΟΣ", father_name="Γ",
        mother_name="Δ", birth_date=date(1990, 5, 5), afm="111222336",
        amka="05059012343", ama="123456789", **PERSON_ID)
    eid = svc.start_employment(pid, hire_date=date(2024, 4, 8), pay_type="daily",
                               daily_wage=50.0, weekly_hours=40.0, weekly_pattern=WEEK)
    svc.assign_insurance_profile(
        eid, ergani_specialty_code="419", ergani_specialty_description="ΛΟΙΠΟΙ",
        coverages=({"fund": "efka_main", "kad": "0000",
                    "specialty_code": "000000", "kpk": "0101"},))
    svc.submit_hire_e3(eid)
    svc.submit_hire_wto(eid)
    if kind:
        lid = svc.record_leave(eid, kind, date(2026, 3, 2), date(2026, 3, 6))
        if benefit:
            svc.record_sickness_benefit(eid, lid, benefit)
    line = svc.approve_attendance(2026, 3)[eid]
    return line, svc.run_monthly_payroll(2026, 3)[0][1]


def test_no_leave_baseline():
    line, res = run()
    assert line["worked"] == 22 and res.gross == 1320.0


def test_annual_leave_is_paid_and_insured():
    """Κανονική άδεια: πληρώνεται όπως η εργασία και ασφαλίζεται."""
    base_line, _ = run()
    line, res = run(LeaveType.ANNUAL)
    assert res.gross == 1320.0  # 17 εργασίας + 5 άδειας = 22 × 60
    assert line["efka_days"] == base_line["efka_days"]


def test_sickness_is_not_counted_twice():
    """Ασθένεια Δευ 2/3 – Παρ 6/3 = 5 ημέρες (το Σάββατο 7/3 δεν είναι μέσα
    στην άδεια): 3 μισές + 2 κανονικές, σε ημερομίσθιο εξαημέρου 50€.
    Μικτά = 17 × 60 + 5 × 50 − 3 × 25 = 1.020 + 250 − 75 = 1.195€."""
    base_line, _ = run()
    line, res = run(LeaveType.SICK)
    assert (line["sick_half"], line["sick_full"]) == (3, 2)
    assert res.sick_deduction == 75.0
    assert res.gross == 1195.0
    # οι πληρωμένες ημέρες ασθενείας είναι ασφαλισμένες ημέρες
    assert line["efka_days"] == base_line["efka_days"]


def test_sickness_benefit_is_deducted_once():
    _, res = run(LeaveType.SICK, benefit=60.0)
    assert res.gross == 1135.0  # 1.195 − 60
