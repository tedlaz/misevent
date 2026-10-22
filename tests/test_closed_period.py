"""
Tests ΚΛΕΙΔΩΜΑΤΟΣ ΠΕΡΙΟΔΟΥ: μόλις κλείσει η μισθοδοσία ενός μήνα, ο μήνας
ΚΑΙ ΚΑΘΕ ΠΡΟΓΕΝΕΣΤΕΡΟΣ παγώνουν — ούτε πρόσληψη/αποχώρηση, ούτε πρόγραμμα,
ούτε παρουσίες, ούτε άδειες (§5.6). Οτιδήποτε από αυτά θα άλλαζε αναδρομικά
ποσά που έχουν ήδη υπολογιστεί και υποβληθεί (ΑΠΔ, ΦΜΥ, Δώρα).

Ο κανόνας ζει στο command layer (`services._check_open_period` /
`_check_open_days`) — καμία οθόνη δεν τον παρακάμπτει. Το UI (grid
«Πρόγραμμα»/«Άδειες») απλώς χρωματίζει και κλειδώνει ό,τι δηλώνουν τα
`svc.closed_payroll_period()` / `closed_period_end()`.

Εκτέλεση: python tests/test_closed_period.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain.events import LeaveType
from misevent.domain.projections import Projections
from misevent.domain.scheduler import ObligationScheduler
from misevent.persistence.database import Database

FULL_WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}


def setup():
    """Εταιρεία με έναν μισθωτό και ΚΛΕΙΣΜΕΝΗ τη μισθοδοσία 1/2026."""
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    # Ο scheduler είναι που γεννά/κλείνει τις υποχρεώσεις MONTHLY_PAYROLL —
    # χωρίς αυτόν καμία περίοδος δεν «κλείνει» ποτέ.
    sched = ObligationScheduler(store, proj, horizon_months=1)
    store.subscribe(sched.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
    svc.define_kpk_rates(
        "efka_main", "0101", date(2025, 1, 1), 0.1337, 0.2179,
        "main_pension=10,37/18,79|healthcare=0/0|auxiliary=3/3",
    )
    svc.define_specialty(
        "ΥΠΑΛΛΗΛΟΣ",
        {"MAIN_EPIK": {"kad": "0110", "specialty_code": "122100",
                       "kpk": "0101"}},
    )
    pid = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ", first_name="ΙΩΑΝΝΗΣ", father_name="ΝΙΚΟΛΑΟΣ",
        mother_name="ΜΑΡΙΑ", birth_date=date(1990, 5, 5), afm="111222336",
        amka="05059012343", ama="123456789",
    )
    eid = svc.start_employment(
        pid, hire_date=date(2026, 1, 5), monthly_gross=1500.0,
        weekly_hours=40.0, weekly_pattern=FULL_WEEK,
    )
    svc.assign_specialty(eid, "ΥΠΑΛΛΗΛΟΣ")
    svc.submit_hire_e3(eid)
    svc.submit_hire_wto(eid)
    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)
    return svc, proj, eid


def refuses(fn, *args, **kwargs):
    """Εκτελεί και επιστρέφει το μήνυμα του ValueError — ή σκάει."""
    try:
        fn(*args, **kwargs)
    except ValueError as exc:
        return str(exc)
    raise AssertionError("Η ενέργεια έγινε δεκτή σε ΚΛΕΙΣΜΕΝΗ περίοδο")


def test_period_is_reported_as_closed():
    svc, _, _ = setup()
    assert svc.closed_payroll_period() == (2026, 1)
    assert svc.is_period_closed(2026, 1) and svc.is_period_closed(2025, 12)
    assert not svc.is_period_closed(2026, 2)
    # Τελευταία κλειδωμένη ημέρα = τέλος του κλεισμένου μήνα
    assert svc.closed_period_end() == date(2026, 1, 31)
    print("OK η κλεισμένη περίοδος αναφέρεται σωστά (και το όριο ημέρας)")


def test_schedule_is_frozen():
    svc, _, eid = setup()
    msg = refuses(svc.set_daily_schedule, eid, {date(2026, 1, 20): "10:00-18:00"})
    assert "1/2026" in msg and "κλείσει" in msg
    # Και το εβδομαδιαίο με ισχύ μέσα στην κλεισμένη περίοδο
    refuses(svc.set_schedule, eid, date(2026, 1, 26), FULL_WEEK)
    # Ο ΕΠΟΜΕΝΟΣ μήνας παραμένει ανοιχτός
    svc.set_daily_schedule(eid, {date(2026, 2, 10): "10:00-18:00"})
    print("OK πρόγραμμα: κλειδωμένο στην κλεισμένη περίοδο, ανοιχτό μετά")


def test_attendance_is_frozen():
    svc, _, eid = setup()
    msg = refuses(svc.record_attendance, eid, date(2026, 1, 15), ["09:00-17:00"])
    assert "1/2026" in msg
    refuses(svc.approve_attendance, 2026, 1)
    print("OK παρουσίες: ούτε καταγραφή ούτε επανέγκριση σε κλεισμένη περίοδο")


def test_leaves_are_frozen():
    svc, proj, eid = setup()
    refuses(svc.record_leave, eid, LeaveType.ANNUAL, date(2026, 1, 12),
            date(2026, 1, 13))
    # Άδεια που ΞΕΚΙΝΗΣΕ πριν το κλείσιμο δεν ακυρώνεται ούτε μετακινείται
    lid = svc.record_leave(eid, LeaveType.ANNUAL, date(2026, 2, 9), date(2026, 2, 10))
    refuses(svc.reschedule_leave, eid, lid, LeaveType.ANNUAL,
            date(2026, 1, 26), date(2026, 1, 27))
    svc.cancel_leave(eid, lid)  # ανοιχτή περίοδος: επιτρέπεται
    assert not proj.employments[eid].active_leaves()
    print("OK άδειες: καμία καταχώρηση/μεταβολή/ακύρωση σε κλεισμένη περίοδο")


def test_hire_and_termination_are_frozen():
    svc, _, eid = setup()
    refuses(svc.terminate, eid, date(2026, 1, 20), "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
    pid2 = svc.register_person(
        last_name="ΓΕΩΡΓΙΟΥ", first_name="ΜΑΡΙΑ", father_name="ΠΕΤΡΟΣ",
        mother_name="ΕΛΕΝΗ", birth_date=date(1992, 2, 2), afm="044555666",
        amka="02029212343", ama="987654321",
    )
    refuses(svc.start_employment, pid2, hire_date=date(2026, 1, 12),
            monthly_gross=1500.0, weekly_hours=40.0, weekly_pattern=FULL_WEEK)
    print("OK πρόσληψη/αποχώρηση: απαγορεύονται σε κλεισμένη περίοδο")
