"""
Tests άδειας ασθενείας: τριήμερο εργοδότη, όριο ανά κυλιόμενο έτος,
αφαίρεση επιδόματος ΕΦΚΑ.

Ο κανόνας:
  - Οι πρώτες 3 ημέρες ασθενείας κάθε κυλιόμενου έτους πληρώνονται ΜΙΣΕΣ
    από τον εργοδότη (τριήμερο). Δίνεται ΜΙΑ φορά — δεν ξαναρχίζει σε
    επόμενη ασθένεια μέσα στο ίδιο έτος.
  - Οι υπόλοιπες, μέχρι το όριο, πληρώνονται κανονικά μείον το επίδομα ΕΦΚΑ.
  - Το όριο είναι 13 ημέρες (προϋπηρεσία < 1 έτος) ή 26 (>= 1 έτος).
  - Οι ημέρες πάνω από το όριο ΔΕΝ πληρώνονται.

Εκτέλεση: python test_sick_leave.py
"""

from datetime import date, timedelta

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain import payroll_engine as pe
from misevent.domain.events import LeaveType
from misevent.domain.projections import Projections
from misevent.persistence.database import Database

FULL_WEEK = {  # 5 x 8:00 = 40 ώρες -> πλήρης απασχόληση
    "MON": "09:00-17:00",
    "TUE": "09:00-17:00",
    "WED": "09:00-17:00",
    "THU": "09:00-17:00",
    "FRI": "09:00-17:00",
}


def setup(
    hire=date(2024, 4, 8),
    monthly_gross=1500.0,
    payroll_from=None,
    service_years=0.0,
    frozen_service_years=0.0,
):
    """Στήνει έναν μισθωτό πλήρους απασχόλησης σε in-memory store, με
    υποβεβλημένα Ε3/ΟΧΕ (προαπαιτούμενο των παρουσιών)."""
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()  # μητρώο παραμέτρων από events
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
    svc.define_kpk_rates(
        "efka_main", "0101", date(2024, 1, 1), 0.1337, 0.2179,
        "main_pension=13,37/21,79|healthcare=0/0",
    )

    pid = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ",
        first_name="ΙΩΑΝΝΗΣ",
        father_name="ΝΙΚΟΛΑΟΣ",
        mother_name="ΜΑΡΙΑ",
        birth_date=date(1990, 5, 5),
        afm="111222336",
        amka="05059012343",
        ama="123456789",
    )
    eid = svc.start_employment(
        pid, hire_date=hire, monthly_gross=monthly_gross,
        weekly_hours=40.0, weekly_pattern=FULL_WEEK,
        payroll_from=payroll_from,
        service_years=service_years,
        frozen_service_years=frozen_service_years,
    )
    svc.assign_insurance_profile(
        eid,
        ergani_specialty_code="419",
        ergani_specialty_description="ΛΟΙΠΟΙ ΥΠΑΛΛΗΛΟΙ ΓΡΑΦΕΙΟΥ",
        coverages=({"fund": "efka_main", "kad": "0000",
                    "specialty_code": "000000", "kpk": "0101"},),
    )
    svc.submit_hire_e3(eid)
    svc.submit_hire_wto(eid)
    return svc, proj, eid


def classes(emp):
    """Οι κατηγορίες των ημερών ασθενείας, σε χρονολογική σειρά."""
    return [cls for _, (cls, _) in sorted(emp.sick_day_map().items())]


def test_short_sickness_is_all_half():
    """Ασθένεια <= 3 ημερών: όλες οι ημέρες μισό ημερομίσθιο."""
    svc, proj, eid = setup()
    # Δευ 3/3/2025 - Τετ 5/3/2025: 3 εργάσιμες
    svc.record_leave(eid, LeaveType.SICK, date(2025, 3, 3), date(2025, 3, 5))
    assert classes(proj.employments[eid]) == ["half", "half", "half"]
    print("OK ασθένεια <= 3 ημερών: όλες μισές")


def test_long_sickness_three_half_then_full():
    """Ασθένεια > 3 ημερών: 3 μισές, οι υπόλοιπες κανονικά."""
    svc, proj, eid = setup()
    # Δευ 3/3 - Παρ 14/3/2025: 10 εργάσιμες (2 εβδομάδες)
    svc.record_leave(eid, LeaveType.SICK, date(2025, 3, 3), date(2025, 3, 14))
    assert classes(proj.employments[eid]) == ["half"] * 3 + ["full"] * 7
    print("OK ασθένεια > 3 ημερών: 3 μισές + υπόλοιπες κανονικά")


def test_weekend_days_do_not_count():
    """Ημέρες που δεν προβλέπει το πρόγραμμα δεν κοστίζουν ούτε στο όριο."""
    svc, proj, eid = setup()
    # Σαβ 8/3 - Κυρ 9/3/2025: κανένα ρεπό δεν μετράει
    svc.record_leave(eid, LeaveType.SICK, date(2025, 3, 8), date(2025, 3, 9))
    assert classes(proj.employments[eid]) == []
    print("OK το σαββατοκύριακο δεν μετράει")


def test_triimero_not_repeated_within_year():
    """2η ασθένεια μέσα στο ΙΔΙΟ κυλιόμενο έτος: το τριήμερο ΔΕΝ ξαναρχίζει."""
    svc, proj, eid = setup()
    svc.record_leave(eid, LeaveType.SICK, date(2025, 3, 3), date(2025, 3, 5))
    svc.record_leave(eid, LeaveType.SICK, date(2025, 6, 2), date(2025, 6, 4))
    # 1η: 3 μισές (εξαντλεί το τριήμερο). 2η: όλες κανονικά.
    assert classes(proj.employments[eid]) == ["half"] * 3 + ["full"] * 3
    print("OK το τριήμερο δεν επαναλαμβάνεται μέσα στο έτος")


def test_new_window_restores_triimero():
    """Ασθένεια μετά από >365 ημέρες: νέο παράθυρο, νέο τριήμερο."""
    svc, proj, eid = setup()
    svc.record_leave(eid, LeaveType.SICK, date(2025, 3, 3), date(2025, 3, 5))
    # 366 ημέρες μετά την πρώτη ημέρα ασθενείας -> νέο κυλιόμενο έτος
    later = date(2025, 3, 3) + timedelta(days=366)  # Πέμ 4/3/2026
    svc.record_leave(eid, LeaveType.SICK, later, later + timedelta(days=1))
    assert classes(proj.employments[eid]) == ["half"] * 3 + ["half"] * 2
    print("OK νέο κυλιόμενο έτος ξαναδίνει το τριήμερο")


def test_limit_26_days_for_senior():
    """Προϋπηρεσία >= 1 έτος: όριο 26 ημέρες, οι υπόλοιπες άνευ αποδοχών."""
    svc, proj, eid = setup(hire=date(2024, 4, 8))
    # 30 συνεχόμενες εργάσιμες (6 εβδομάδες) από Δευ 5/1/2026
    svc.record_leave(eid, LeaveType.SICK, date(2026, 1, 5), date(2026, 2, 13))
    cls = classes(proj.employments[eid])
    assert len(cls) == 30
    assert cls[:3] == ["half"] * 3
    assert cls.count("half") + cls.count("full") == 26  # το όριο
    assert cls[26:] == ["over"] * 4  # πάνω από το όριο: απλήρωτες
    print("OK όριο 26 ημερών (προϋπηρεσία >= 1 έτος)")


def test_limit_13_days_for_newcomer():
    """Προϋπηρεσία < 1 έτος: όριο 13 ημέρες."""
    svc, proj, eid = setup(hire=date(2025, 9, 1))
    svc.record_leave(eid, LeaveType.SICK, date(2025, 10, 1), date(2025, 11, 14))
    cls = classes(proj.employments[eid])
    assert cls.count("half") + cls.count("full") == 13
    assert cls.count("half") == 3
    assert "over" in cls
    print("OK όριο 13 ημερών (προϋπηρεσία < 1 έτος)")


def test_benefit_split_across_months():
    """Το επίδομα ΕΦΚΑ επιμερίζεται στους μήνες ΑΝΑΛΟΓΙΚΑ προς τις
    κανονικά πληρωμένες (`full`) ημέρες της άδειας."""
    svc, proj, eid = setup(hire=date(2024, 4, 8))
    # Δευ 26/1/2026 - Παρ 6/2/2026: 10 εργάσιμες (5 Ιαν + 5 Φεβ)
    lid = svc.record_leave(eid, LeaveType.SICK, date(2026, 1, 26), date(2026, 2, 6))
    svc.record_sickness_benefit(eid, lid, 350.0)
    emp = proj.employments[eid]

    jan = emp.sick_days_in_month(2026, 1)
    feb = emp.sick_days_in_month(2026, 2)
    assert (jan["half"], jan["full"], jan["over"]) == (3, 2, 0)
    assert (feb["half"], feb["full"], feb["over"]) == (0, 5, 0)
    # 7 ημέρες full συνολικά: 2/7 τον Ιαν, 5/7 τον Φεβ
    assert jan["benefit"] == 100.0
    assert feb["benefit"] == 250.0
    assert jan["benefit"] + feb["benefit"] == 350.0
    print("OK επιμερισμός επιδόματος ΕΦΚΑ ανά μήνα")


def test_deduction_math():
    """Η περικοπή = μισό ημερομίσθιο × τριήμερο + επίδομα ΕΦΚΑ."""
    # μισθός 1500 -> ημερομίσθιο 60. 3 μισές = 90. Επίδομα 200 -> 290.
    assert pe.sick_deduction(60.0, 3, 200.0) == 290.0
    assert pe.sick_deduction(60.0, 0, 0.0) == 0.0
    print("OK αριθμητική περικοπής")


def test_payroll_applies_deduction():
    """Η μισθοδοσία κόβει τα μικτά κατά την περικοπή ασθενείας."""
    svc, proj, eid = setup(hire=date(2024, 4, 8))
    # Ασθένεια 5 εργάσιμων: 3 μισές + 2 κανονικά, επίδομα 120€
    lid = svc.record_leave(eid, LeaveType.SICK, date(2026, 1, 5), date(2026, 1, 9))
    svc.record_sickness_benefit(eid, lid, 120.0)

    draft = svc.attendance_draft(2026, 1)[eid]
    assert (draft["sick_half"], draft["sick_full"]) == (3, 2)
    assert draft["sick_benefit"] == 120.0
    # Καμία ημέρα πάνω από το όριο -> οι ημέρες ΕΦΚΑ μένουν 25
    assert draft["efka_days"] == 25

    emp = proj.employments[eid]
    line = svc.approve_attendance(2026, 1)[eid]
    deduction = svc.sick_deduction(emp, line, 2026, 1)
    # ημερομίσθιο 1500/25 = 60 -> 3 x 30 = 90, + 120 επίδομα = 210
    assert deduction == 210.0

    # Η ΠΡΑΓΜΑΤΙΚΗ μισθοδοσία, όχι μόνο η καθαρή συνάρτηση
    res = svc.run_monthly_payroll(2026, 1)
    assert len(res) == 1
    assert res[0][1].gross == 1290.0  # 1500 - 210
    print("OK η μισθοδοσία εφαρμόζει την περικοπή ασθενείας")


def test_sickness_recorded_after_approval():
    """Άδεια ασθενείας ΜΕΤΑ την έγκριση του φύλλου: το φύλλο δεν έχει δεδομένα
    ασθενείας, άρα η περικοπή υπολογίζεται εκ νέου — δεν αγνοείται σιωπηλά."""
    svc, proj, eid = setup(hire=date(2024, 4, 8))
    line = svc.approve_attendance(2026, 1)[eid]  # πρώτα η έγκριση...
    assert line["sick_half"] == 0

    lid = svc.record_leave(eid, LeaveType.SICK, date(2026, 1, 5), date(2026, 1, 9))
    svc.record_sickness_benefit(eid, lid, 120.0)  # ...μετά η ασθένεια

    res = svc.run_monthly_payroll(2026, 1)
    assert res[0][1].gross == 1290.0  # 1500 - (3 x 30 + 120)
    print("OK ασθένεια μετά την έγκριση: η περικοπή δεν χάνεται")


def test_manual_sheet_wins():
    """Χειροκίνητη διόρθωση στο φύλλο ΥΠΕΡΙΣΧΥΕΙ του αυτόματου υπολογισμού."""
    svc, proj, eid = setup(hire=date(2024, 4, 8))
    lid = svc.record_leave(eid, LeaveType.SICK, date(2026, 1, 5), date(2026, 1, 9))
    svc.record_sickness_benefit(eid, lid, 120.0)
    # Ο χρήστης δηλώνει 1 μισή ημέρα αντί για 3 και επίδομα 100
    svc.approve_attendance(2026, 1, {eid: {"sick_half": 1, "sick_benefit": 100.0}})
    res = svc.run_monthly_payroll(2026, 1)
    assert res[0][1].gross == 1370.0  # 1500 - (1 x 30 + 100)
    print("OK η χειροκίνητη διόρθωση υπερισχύει")


def test_leave_dates_cannot_overlap():
    """Για κάθε εργαζόμενο επιτρέπεται μόνο μία άδεια ανά ημερομηνία."""
    svc, proj, eid = setup(hire=date(2024, 4, 8))
    svc.record_leave(eid, LeaveType.SICK, date(2026, 3, 10), date(2026, 3, 12))
    try:
        svc.record_leave(
            eid, LeaveType.UNPAID, date(2026, 3, 12), date(2026, 3, 14)
        )
    except ValueError as exc:
        assert "Υπάρχει ήδη άδεια" in str(exc)
    else:
        raise AssertionError("Επιτράπηκαν επικαλυπτόμενες άδειες")
    # Η αμέσως επόμενη ημέρα δεν επικαλύπτεται.
    svc.record_leave(eid, LeaveType.UNPAID, date(2026, 3, 13), date(2026, 3, 14))


def test_reschedule_rejects_overlap_without_changing_leave():
    svc, proj, eid = setup(hire=date(2024, 4, 8))
    svc.record_leave(eid, LeaveType.SICK, date(2026, 4, 1), date(2026, 4, 3))
    second = svc.record_leave(
        eid, LeaveType.UNPAID, date(2026, 4, 8), date(2026, 4, 9)
    )
    try:
        svc.reschedule_leave(
            eid, second, LeaveType.UNPAID,
            date(2026, 4, 3), date(2026, 4, 4)
        )
    except ValueError as exc:
        assert "Υπάρχει ήδη άδεια" in str(exc)
    else:
        raise AssertionError("Η μετακίνηση πάνω σε άλλη άδεια επιτράπηκε")
    leave = next(item for item in proj.employments[eid].active_leaves()
                 if item.leave_id == second)
    assert (leave.date_from, leave.date_to) == (date(2026, 4, 8), date(2026, 4, 9))


def test_leave_must_be_inside_employment_period():
    svc, proj, eid = setup(hire=date(2026, 5, 10))
    try:
        svc.record_leave(
            eid, LeaveType.SICK, date(2026, 5, 8), date(2026, 5, 11)
        )
    except ValueError as exc:
        assert "προηγείται της πρόσληψης" in str(exc)
    else:
        raise AssertionError("Επιτράπηκε άδεια πριν από την πρόσληψη")


def test_annual_leave_accrues_during_first_year():
    svc, _, eid = setup(hire=date(2026, 1, 1))
    assert svc.annual_leave_accrued(eid, date(2026, 1, 1)) == 2
    assert svc.annual_leave_accrued(eid, date(2026, 6, 30)) == 10
    assert svc.annual_leave_accrued(eid, date(2026, 12, 31)) == 20


def test_partial_first_year_is_rounded_up_then_distributed_as_integers():
    svc, _, eid = setup(hire=date(2026, 8, 17))
    assert svc.annual_leave_entitlement(eid, 2026) == 9
    assert [
        svc.annual_leave_accrued(eid, date(2026, month, 28))
        for month in range(8, 13)
    ] == [2, 4, 5, 7, 9]


def test_second_year_distributes_21_days_over_twelve_months():
    svc, _, eid = setup(hire=date(2025, 8, 17))
    assert svc.annual_leave_entitlement(eid, 2026) == 21
    assert svc.annual_leave_accrued(eid, date(2026, 1, 1)) == 2
    assert svc.annual_leave_accrued(eid, date(2026, 6, 30)) == 11
    assert svc.annual_leave_accrued(eid, date(2026, 12, 31)) == 21


def test_annual_leave_balance_counts_only_days_up_to_reference_date():
    svc, _, eid = setup(hire=date(2026, 1, 1))
    svc.plan_annual_leave(eid, date(2026, 2, 2), date(2026, 2, 6))
    svc.plan_annual_leave(eid, date(2026, 8, 3), date(2026, 8, 5))
    assert svc.annual_leave_taken_until(eid, date(2026, 6, 30)) == 5
    assert svc.annual_leave_taken_until(eid, date(2026, 12, 31)) == 8


def test_full_entitlement_is_available_from_third_calendar_year():
    svc, _, eid = setup(hire=date(2024, 8, 1))
    assert svc.annual_leave_accrued(eid, date(2026, 1, 1)) == 22


def test_ten_years_with_the_same_employer_give_25_days():
    """10 έτη ΣΤΟΝ ΙΔΙΟ εργοδότη -> 25 ημέρες αντί 22 (μεταφορά από άλλο
    πρόγραμμα: πρόσληψη 2016, ανάληψη μισθοδοσίας 2026)."""
    svc, _, eid = setup(hire=date(2016, 3, 1), payroll_from=date(2026, 1, 1))
    assert svc.annual_leave_entitlement(eid, 2025) == 22  # 9 έτη
    assert svc.annual_leave_entitlement(eid, 2026) == 25  # συμπληρώνει τα 10
    assert svc.annual_leave_accrued(eid, date(2026, 1, 1)) == 25


def test_twelve_years_of_total_service_give_25_days():
    """12 έτη ΣΥΝΟΛΙΚΗΣ εργασίας (και σε άλλους εργοδότες) -> 25 ημέρες.
    Ο παγωμένος χρόνος 2012-2023 ΜΕΤΡΑ εδώ (δεν μετρά μόνο στις τριετίες)."""
    svc, _, eid = setup(
        hire=date(2024, 4, 8), service_years=1.0, frozen_service_years=8.0
    )
    assert svc.annual_leave_entitlement(eid, 2026) == 22  # 9 + 2,7 = 11,7 έτη
    assert svc.annual_leave_entitlement(eid, 2027) == 25  # 9 + 3,7 = 12,7 έτη


def test_weekly_schedule_history_respects_effective_date():
    svc, proj, eid = setup(hire=date(2026, 1, 1))
    tue_to_sat = {
        "TUE": "09:00-17:00",
        "WED": "09:00-17:00",
        "THU": "09:00-17:00",
        "FRI": "09:00-17:00",
        "SAT": "09:00-17:00",
    }
    svc.set_schedule(eid, date(2026, 7, 1), tue_to_sat)
    emp = proj.employments[eid]
    assert emp.day_schedule(date(2026, 6, 29)) == "09:00-17:00"  # Δευτέρα
    assert emp.day_schedule(date(2026, 6, 27)) == ""  # Σάββατο πριν την αλλαγή
    assert emp.day_schedule(date(2026, 7, 4)) == "09:00-17:00"  # Σάββατο μετά
    assert emp.day_schedule(date(2026, 7, 6)) == ""  # Δευτέρα μετά


def test_long_service_counts_from_the_first_year_pro_rata():
    """ΜΑΚΡΑ ΥΠΗΡΕΣΙΑ ΚΑΙ ΣΕ ΝΕΑ ΠΡΟΣΛΗΨΗ: 12 έτη αλλού -> κλίμακα 25 από την
    πρώτη μέρα, με το ΠΡΩΤΟ έτος αναλογικά προς τον μήνα πρόσληψης.

    Πρόσληψη Σεπτεμβρίου = 4 μήνες -> 25 × 4/12 = 8,33 -> 9 ημέρες (με την
    παλιά κλίμακα των 20 έβγαιναν 7 — έχανε 2 ημέρες κάθε χρόνο μέχρι το 3ο).
    """
    svc, _, eid = setup(hire=date(2026, 9, 1), service_years=12.0)
    assert svc.annual_leave_entitlement(eid, 2026) == 9
    assert [
        svc.annual_leave_accrued(eid, date(2026, month, 28))
        for month in range(9, 13)
    ] == [2, 5, 7, 9]

    # Χωρίς προϋπηρεσία, ο ΙΔΙΟΣ μήνας πρόσληψης δίνει την κλίμακα των 20
    svc2, _, eid2 = setup(hire=date(2026, 9, 1))
    assert svc2.annual_leave_entitlement(eid2, 2026) == 7


def test_long_service_gives_25_in_the_second_year_too():
    """2ο ημερολογιακό έτος: 25 (μακρά υπηρεσία) αντί για 21."""
    svc, _, eid = setup(hire=date(2025, 9, 1), service_years=12.0)
    assert svc.annual_leave_entitlement(eid, 2026) == 25
    assert svc.annual_leave_accrued(eid, date(2026, 12, 31)) == 25
    svc2, _, eid2 = setup(hire=date(2025, 9, 1))
    assert svc2.annual_leave_entitlement(eid2, 2026) == 21
