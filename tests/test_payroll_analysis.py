"""
Tests πλήρους αλυσίδας: βάρδιες κάρτας -> ώρες προσαυξήσεων στο συγκεντρωτικό
-> μισθοδοσία με προσαυξήσεις στα μικτά -> ΠΛΗΡΗΣ ανάλυση στο event
(έτοιμη για απόδειξη αποδοχών) -> πίνακες read model.

Εκτέλεση: python test_payroll_analysis.py
"""

from datetime import date, timedelta

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.persistence.database import Database

FULL_WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}


def insure(svc, employment_id):
    if "0101" not in svc.known_kpks("efka_main"):
        svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
        svc.define_kpk_rates(
            "efka_main", "0101", date(2024, 1, 1), 0.1337, 0.2179,
            "main_pension=13,37/21,79|healthcare=0/0",
        )
    svc.assign_insurance_profile(
        employment_id,
        coverages=({"fund": "efka_main", "kad": "0000",
                    "specialty_code": "000000", "kpk": "0101"},),
    )


def setup():
    """Μισθωτός πλήρους απασχόλησης 1500€ (ωρομίσθιο 1500/25x6/40 = 9€),
    με παρουσίες ΟΛΟ τον Ιούλιο 2026 (μήνας χωρίς αργίες) από βάρδιες."""
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()  # μητρώο παραμέτρων από events
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    pid = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ", first_name="ΙΩΑΝΝΗΣ",
        father_name="Ν", mother_name="Μ", birth_date=date(1990, 5, 5),
        afm="111222336", amka="05059012343", ama="123456789",
    )
    eid = svc.start_employment(pid, date(2024, 4, 8), monthly_gross=1500.0,
                               weekly_hours=40.0, weekly_pattern=FULL_WEEK)
    insure(svc, eid)
    svc.submit_hire_e3(eid)
    svc.submit_hire_wto(eid)
    # Παρουσίες Ιουλίου 2026: όλες οι προγραμματισμένες (Δευ-Παρ), 8ω
    # από κάρτα — πλήρης μήνας, ώστε ημέρες ΕΦΚΑ = 25
    d = date(2026, 7, 1)
    while d.month == 7:
        if d.weekday() < 5:
            svc.record_attendance(eid, d, 8, shifts=["09:00-17:00"])
        d += timedelta(days=1)
    return svc, proj, eid


def test_draft_proposes_hours():
    """Το προσχέδιο προτείνει τις ώρες προσαυξήσεων από τις βάρδιες."""
    svc, proj, eid = setup()
    # Κυριακή 12/7 βάρδια 22:00-02:00: 4ω νύχτα, 2ω αργία (οι 2 μετά τα
    # μεσάνυχτα είναι Δευτέρα)
    svc.record_attendance(eid, date(2026, 7, 12), 4, shifts=["22:00-02:00"])
    d = svc.attendance_draft(2026, 7)[eid]
    # 2ω Κυριακή-νύχτα (normal_nh), 2ω Δευτέρα-νύχτα (normal_n)
    assert d["normal_nh"] == 2.0 and d["normal_n"] == 2.0
    assert d["overwork"] == d["overtime"] == d["illegal"] == 0.0
    print("OK το προσχέδιο προτείνει τον πίνακα ωρών από τις βάρδιες")


def test_payroll_includes_premiums():
    """Η μισθοδοσία προσθέτει τις προσαυξήσεις στα μικτά, με πλήρη ανάλυση."""
    svc, proj, eid = setup()
    svc.record_attendance(eid, date(2026, 7, 12), 4, shifts=["22:00-02:00"])
    svc.approve_attendance(2026, 7)
    res = svc.run_monthly_payroll(2026, 7)
    r = res[0][1]
    # ωρομίσθιο 9€, εντός ωραρίου: 2ω νύχτα-αργία x 100% + 2ω νύχτα x 25%
    # = 18 + 4.5 = 22.5€
    assert r.premiums == 22.5
    assert r.base_gross == 1500.0
    assert r.gross == 1522.5

    ev = [x for _, x in svc.store.load()
          if type(x).__name__ == "MonthlyPayrollCalculated"][0]
    assert ev.premiums == 22.5
    a = ev.analysis
    assert a["hourly_rate"] == 9.0
    assert a["premium_lines"]["normal_nh"]["hours"] == 2.0
    assert a["premium_lines"]["normal_nh"]["mult"] == 1.0
    assert a["premium_lines"]["normal_nh"]["amount"] == 18.0
    assert a["premium_lines"]["normal_n"]["amount"] == 4.5
    assert a["gross"] == 1522.5 and a["net"] == ev.net
    print("OK μικτά 1522.50 = 1500 + 22.50 προσαυξήσεις, με ανάλυση στο event")


def test_manual_hours_override():
    """Χειροκίνητη διόρθωση των ωρών στο φύλλο υπερισχύει της κατάταξης."""
    svc, proj, eid = setup()
    svc.approve_attendance(2026, 7, {eid: {"overtime": 2.0}})
    res = svc.run_monthly_payroll(2026, 7)
    r = res[0][1]
    # Η υπερωρία πληρώνεται ΟΛΟΚΛΗΡΗ: 2ω x 9€ x 1.40 = 25.20€
    assert r.premiums == 25.2
    assert r.gross == 1525.2
    print("OK χειροκίνητες ώρες υπερωρίας στο φύλλο -> +25.20€ (x1.40)")


def test_sheet_hours_and_payroll_analysis():
    """Το εγκεκριμένο φύλλο κρατά την κατάταξη ωρών και η μισθοδοσία την
    πλήρη ανάλυση — έτοιμα για reports/αποδείξεις."""
    svc, proj, eid = setup()
    svc.record_attendance(eid, date(2026, 7, 12), 4, shifts=["22:00-02:00"])
    svc.approve_attendance(2026, 7)
    svc.run_monthly_payroll(2026, 7)

    # Το replay δίνει τα ΙΔΙΑ νούμερα — η προβολή είναι παράγωγη του log.
    rebuilt = Projections()
    rebuilt.rebuild(svc.store)
    line = rebuilt.attendance_sheets[(2026, 7)].lines[eid]
    assert (
        line["normal_n"], line["normal_nh"],
        line["overwork"], line["overtime"], line["illegal"],
    ) == (2.0, 2.0, 0.0, 0.0, 0.0)

    payroll, = [p for p in rebuilt.payrolls if p.employee_id == eid]
    assert payroll.gross == 1522.5 and payroll.premiums == 22.5
    analysis = payroll.analysis
    assert analysis["premium_lines"]["normal_nh"]["label"] == (
        "Εντός ωραρίου (νύχτα + αργία)"
    )
    assert analysis["base_gross"] == 1500.0
    print("OK ώρες στο φύλλο + ανάλυση στη μισθοδοσία, μετά από replay")

def test_part_time_efka_days():
    """Μερική απασχόληση μισθωτού: οι ημέρες ΕΦΚΑ βγαίνουν από τον τύπο
    ⌈συμβατικές ώρες/6,66⌉ x 4,33 (ΟΧΙ από τις πραγματικές παρουσίες),
    και πλήρης μήνας = πλήρης συμφωνημένος μισθός."""
    from misevent.domain import payroll_engine as pe

    assert pe.monthly_insurance_days(40.0) == 25
    assert pe.monthly_insurance_days(20.0) == 13  # 3 ημέρες/εβδ x 4,33
    assert pe.monthly_insurance_days(24.0) == 17  # 4 ημέρες/εβδ x 4,33
    assert pe.monthly_insurance_days(30.0) == 22  # 5 ημέρες/εβδ x 4,33

    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()  # μητρώο παραμέτρων από events
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    pid = svc.register_person(
        last_name="ΜΕΡΙΚΟΥ", first_name="ΕΛΕΝΗ",
        father_name="Ν", mother_name="Μ", birth_date=date(1992, 3, 3),
        afm="444555667", amka="03039211226", ama="456789012",
    )
    part = {d: "09:00-13:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}
    eid = svc.start_employment(pid, date(2024, 4, 8), monthly_gross=500.0,
                               weekly_hours=20.0, weekly_pattern=part)
    insure(svc, eid)
    svc.submit_hire_e3(eid)
    svc.submit_hire_wto(eid)
    d = date(2026, 7, 1)
    while d.month == 7:
        if d.weekday() < 5:
            svc.record_attendance(eid, d, 4, shifts=["09:00-13:00"])
        d += timedelta(days=1)
    # Το προσχέδιο προτείνει τη συμβατική βάση (13), όχι τις ~23 παρουσίες
    draft = svc.attendance_draft(2026, 7)[eid]
    assert draft["efka_days"] == 13, draft["efka_days"]
    svc.approve_attendance(2026, 7)
    res = svc.run_monthly_payroll(2026, 7)
    r = res[0][1]
    # Πλήρης μήνας: 13/13 ημέρες -> πλήρης μισθός, καμία περικοπή
    assert r.base_gross == 500.0 and r.gross == 500.0
    # Ημερομίσθιο μερικής = ωρομίσθιο x ώρες ημέρας (ΟΧΙ μισθός/25):
    # ωρομίσθιο 500/25x6/20 = 6€ x 4ω = 24€ -> τριήμερο μισό = 12€/ημέρα
    emp = proj.employments[eid]
    assert svc.sick_deduction(emp, {"sick_half": 1}, 2026, 7) == 12.0
    print("OK μερική 20ω: 13 ημέρες ΕΦΚΑ (στρογγ.άνω(20/6,66)x4,33) και πλήρης μισθός")


def test_midmonth_termination_is_paid():
    """Αποχώρηση ΜΕΣΑ στον μήνα: πληρώνεται για τις ημέρες που δούλεψε.
    Πριν τη διόρθωση το `active` τον απέκλειε εντελώς από τη μισθοδοσία,
    παρότι είχαν υπολογιστεί οι ημέρες του (attendance_draft)."""
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    pid = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ", first_name="ΙΩΑΝΝΗΣ",
        father_name="Ν", mother_name="Μ", birth_date=date(1990, 5, 5),
        afm="111222336", amka="05059012343", ama="123456789",
    )
    eid = svc.start_employment(pid, date(2024, 4, 8), monthly_gross=1500.0,
                               weekly_hours=40.0, weekly_pattern=FULL_WEEK)
    insure(svc, eid)
    svc.submit_hire_e3(eid)
    svc.submit_hire_wto(eid)
    # Αποχώρηση 15/7/2026 — τεκμαρτές παρουσίες από το πρόγραμμα ως τότε
    svc.terminate(eid, date(2026, 7, 15))
    svc.approve_attendance(2026, 7)
    res = svc.run_monthly_payroll(2026, 7)
    assert len(res) == 1, f"ο αποχωρών δεν πληρώθηκε: {res}"
    ev = [x for _, x in store.load()
          if type(x).__name__ == "MonthlyPayrollCalculated"][0]
    assert 0 < ev.insurance_days < 25, ev.insurance_days
    print(f"OK αποχώρηση 15/7 -> μισθοδοσία με {ev.insurance_days} ημέρες ΕΦΚΑ (<25)")


def test_leave_compensation_untaken_leave():
    """Αποζημίωση μη ληφθείσας αδείας κατά την αποχώρηση: υπόλοιπο × 1,2
    (πενθήμερο→εξαήμερο) × (μισθός/25), ΜΟΝΟ ΦΜΥ (καμία ασφαλιστική)."""
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    pid = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ", first_name="ΙΩΑΝΝΗΣ",
        father_name="Ν", mother_name="Μ", birth_date=date(1990, 5, 5),
        afm="111222336", amka="05059012343", ama="123456789",
    )
    eid = svc.start_employment(pid, date(2024, 4, 8), monthly_gross=1500.0,
                               weekly_hours=40.0, weekly_pattern=FULL_WEEK)
    insure(svc, eid)
    # Ολοκληρωμένη πρόσληψη (αναγγελία Ε3) — χωρίς αυτήν δεν υπάρχει
    # σχέση προς λήξη (§5.7α)
    svc.record_external_hire_submission(eid)
    # Καμία άδεια ληφθείσα -> πλήρες υπόλοιπο 22 ημερών (3ο+ έτος)
    svc.terminate(eid, date(2026, 7, 15))
    a = svc.run_leave_compensation(eid)
    assert a["leave_days"] == 22
    assert a["comp_days"] == 26.4          # 22 × 1,2
    assert a["gross"] == 1584.0            # 26,4 × (1500/25 = 60)
    assert a["fmy"] > 0 and a["net"] == round(1584.0 - a["fmy"], 2)
    ev = svc.leave_compensation(eid)       # καταγράφηκε, ΧΩΡΙΣ ασφαλιστικά
    assert ev.gross == 1584.0 and not hasattr(ev, "efka_employee")
    print("OK αποζημίωση αδείας: 22 ημ x1,2 x60 = 1584 μικτά, μόνο ΦΜΥ")


def test_leave_compensation_prorated_second_year():
    """1ο/2ο έτος υπηρεσίας: η αποζημίωση μη ληφθείσας αδείας υπολογίζεται
    αναλογικά, 2 ημέρες ανά μήνα από την αρχή του έτους έως την αποχώρηση."""
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    pid = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ", first_name="ΙΩΑΝΝΗΣ",
        father_name="Ν", mother_name="Μ", birth_date=date(1990, 5, 5),
        afm="111222336", amka="05059012343", ama="123456789",
    )
    eid = svc.start_employment(pid, date(2024, 4, 8), monthly_gross=1500.0,
                               weekly_hours=40.0, weekly_pattern=FULL_WEEK)
    insure(svc, eid)
    # Ολοκληρωμένη πρόσληψη (αναγγελία Ε3) — χωρίς αυτήν δεν υπάρχει
    # σχέση προς λήξη (§5.7α)
    svc.record_external_hire_submission(eid)
    # Αποχώρηση 20/3/2025 = 2ο ημερολογιακό έτος -> 3 μήνες × 2 = 6 ημέρες
    svc.terminate(eid, date(2025, 3, 20))
    a = svc.run_leave_compensation(eid)
    assert a["leave_days"] == 6            # min(2×3 μήνες, ετήσιο 21)
    assert a["comp_days"] == 7.2           # 6 × 1,2
    assert a["gross"] == 432.0             # 7,2 × (1500/25 = 60)
    print("OK αποζημίωση αδείας 2ου έτους: 6 ημ αναλογικά x1,2 x60 = 432")


def _svc_with_scheduler():
    from misevent.domain.scheduler import ObligationScheduler
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    sched = ObligationScheduler(store, proj)
    store.subscribe(sched.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    return store, proj, svc


def _person(svc, afm, amka, last="ΠΑΠΑΔΟΠΟΥΛΟΣ"):
    return svc.register_person(
        last_name=last, first_name="ΙΩΑΝΝΗΣ", father_name="Ν", mother_name="Μ",
        birth_date=date(1985, 5, 5), afm=afm, amka=amka, ama=afm)


def test_empty_month_skipped_in_chain():
    """Αποχώρηση 5ου + επαναπρόσληψη 7ου: ο κενός 6ος ΔΕΝ μπλοκάρει τη
    μισθοδοσία του 7ου (προσπερνιέται στην αλυσίδα)."""
    from misevent.domain.events import ObligationFulfilled

    store, proj, svc = _svc_with_scheduler()
    pid = _person(svc, "111222336", "05058512343")
    eid = svc.start_employment(pid, date(2025, 1, 8), monthly_gross=1500.0,
                               weekly_pattern=FULL_WEEK)
    insure(svc, eid)
    # Ολοκληρωμένη πρόσληψη (αναγγελία Ε3) — χωρίς αυτήν δεν υπάρχει
    # σχέση προς λήξη (§5.7α)
    svc.record_external_hire_submission(eid)
    # Οι μήνες 1-4 κλείνουν, ώστε ΤΡΕΧΩΝ μήνας μισθοδοσίας να είναι ο 5ος:
    # η αποχώρηση επιτρέπεται ΜΟΝΟ μέσα σε αυτόν (§2)
    for month in (1, 2, 3, 4):
        mp = next(o for o in proj.obligations.values()
                  if o.obligation_type == "MONTHLY_PAYROLL"
                  and (o.period_year, o.period_month) == (2025, month))
        store.append("COMPANY", ObligationFulfilled(mp.obligation_id, ""))
    assert svc.current_payroll_period() == (2025, 5)
    svc.terminate(eid, date(2025, 5, 15), "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
    svc.start_employment(pid, date(2025, 7, 1), monthly_gross=1600.0,
                         weekly_pattern=FULL_WEEK)
    assert svc.period_has_employment(2025, 6) is False
    pend = svc.pending_payroll_before(2025, 7)
    # ο Ιούνιος (κενός) δεν πρέπει να είναι εκκρεμής
    assert pend is None or (pend.period_year, pend.period_month) != (2025, 6)
    print("OK ο κενός μήνας (αποχώρηση/επαναπρόσληψη) προσπερνιέται στην αλυσίδα")


def test_closed_period_blocks_retroactive_hire_and_exit():
    """Κλεισμένη μισθοδοσία μήνα: νέα πρόσληψη/αποχώρηση σε αυτόν ή
    προγενέστερο απορρίπτεται· σε μεταγενέστερο επιτρέπεται."""
    from misevent.domain.events import ObligationFulfilled
    store, proj, svc = _svc_with_scheduler()
    pid = _person(svc, "111222336", "05058512343")
    pid2 = _person(svc, "222333440", "03039012343", last="ΓΕΩΡΓΙΟΥ")
    svc.start_employment(pid, date(2025, 1, 8), monthly_gross=1500.0,
                         weekly_pattern=FULL_WEEK)
    mp = next(o for o in proj.obligations.values()
              if o.obligation_type == "MONTHLY_PAYROLL"
              and (o.period_year, o.period_month) == (2025, 3))
    store.append("COMPANY", ObligationFulfilled(mp.obligation_id, ""))
    for when in (date(2025, 3, 20), date(2025, 2, 10)):  # κλεισμένος/προγενέστερος
        try:
            svc.start_employment(pid2, when, monthly_gross=1500.0,
                                 weekly_pattern=FULL_WEEK)
            raise AssertionError("έπρεπε να απορριφθεί")
        except ValueError as e:
            assert "έχει ήδη κλείσει" in str(e)
    # μεταγενέστερος -> επιτρέπεται
    svc.start_employment(pid2, date(2025, 4, 1), monthly_gross=1500.0,
                         weekly_pattern=FULL_WEEK)
    print("OK κλεισμένη μισθοδοσία μπλοκάρει αναδρομική πρόσληψη/αποχώρηση")
