"""
Tests προσαυξήσεων κατώτατου ορίου: τριετίες (μισθωτοί 3x10%, ημερομίσθιοι
6x5% ΕΓΣΣΕ), επίδομα γάμου 10% (προαιρετικό), λοιπές (πτυχίο κ.λπ.) και
αυτόματη ΩΡΙΜΑΝΣΗ τριετιών με τον χρόνο στον εργοδότη.

Εκτέλεση: python test_allowances.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.persistence.database import Database
from misevent.rules import config

FULL_WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}


def make_svc():
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
    svc.define_kpk_rates(
        "efka_main",
        "0101",
        date(2024, 1, 1),
        0.1337,
        0.2179,
        "main_pension=13,37/21,79|healthcare=0/0",
    )
    pid = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ",
        first_name="ΙΩΑΝΝΗΣ",
        father_name="Ν",
        mother_name="Μ",
        birth_date=date(1990, 5, 5),
        afm="111222336",
        amka="05059012343",
        ama="123456789",
    )
    return svc, proj, pid


def insure(svc, employment_id):
    svc.assign_insurance_profile(
        employment_id,
        ergani_specialty_code="419",
        ergani_specialty_description="ΛΟΙΠΟΙ ΥΠΑΛΛΗΛΟΙ ΓΡΑΦΕΙΟΥ",
        coverages=(
            {
                "fund": "efka_main",
                "kad": "0000",
                "specialty_code": "000000",
                "kpk": "0101",
            },
        ),
    )


def test_seniority_factor():
    """Ο συντελεστής: μισθωτοί 3x10% (cap), ημερομίσθιοι 6x5% (cap),
    γάμος +10%, λοιπές ελεύθερο ποσοστό."""
    on = date(2026, 7, 1)
    f = config.seniority_factor
    assert f("monthly", 0, False, 0, on) == 1.0
    assert f("monthly", 3.5, False, 0, on) == 1.10  # 1 τριετία
    assert f("monthly", 9, False, 0, on) == 1.30  # 3 τριετίες
    assert f("monthly", 30, False, 0, on) == 1.30  # cap στις 3
    assert f("daily", 9, False, 0, on) == 1.15  # 3 τριετίες x 5%
    assert f("daily", 30, False, 0, on) == 1.30  # cap στις 6
    assert f("monthly", 0, True, 0, on) == 1.10  # γάμος
    assert round(f("monthly", 6, True, 15, on), 4) == 1.45  # 2x10% + 10% + 15%
    print("OK συντελεστής προσαυξήσεων (μισθωτοί/ημερομίσθιοι/γάμος/λοιπές)")


def test_hire_below_adjusted_floor_rejected():
    """Μισθός πάνω από τον βασικό κατώτατο αλλά ΚΑΤΩ από τον προσαυξημένο
    (2 τριετίες) -> η πρόσληψη απορρίπτεται."""
    svc, _, pid = make_svc()
    # 1/7/2026: βασικός 920€. Με 6 έτη προϋπηρεσίας: 920 x 1.20 = 1104€.
    try:
        svc.start_employment(
            pid,
            date(2026, 7, 1),
            monthly_gross=1000.0,
            weekly_pattern=FULL_WEEK,
            service_years=6,
        )
    except ValueError as e:
        assert "1104.00" in str(e) and "τριετίες" in str(e)
        print("OK πρόσληψη 1000€ < 1104€ (2 τριετίες) απορρίφθηκε")
    else:
        raise AssertionError("έπρεπε να απορριφθεί")


def test_hire_with_allowances_ok():
    """Μισθός πάνω από τον προσαυξημένο κατώτατο -> δεκτός, και τα πεδία
    αποθηκεύονται στην απασχόληση."""
    svc, proj, pid = make_svc()
    eid = svc.start_employment(
        pid,
        date(2026, 7, 1),
        monthly_gross=1250.0,
        weekly_pattern=FULL_WEEK,
        service_years=6,
        married=True,
        extra_allowances_pct=2.5,
    )  # όριο: 920 x (1.20 + 0.10 + 0.025) = 1219€
    svc.record_external_hire_submission(eid)
    emp = proj.employments[eid]
    assert emp.service_years == 6.0
    assert emp.married is True
    assert emp.extra_allowances_pct == 2.5
    print("OK πρόσληψη 1250€ >= 1219€ (τριετίες+γάμος+λοιπές) — δεκτή")


def test_daily_worker_egsse():
    """Ημερομίσθιος με 10 έτη προϋπηρεσίας: 3 τριετίες x 5% = +15%."""
    svc, _, pid = make_svc()
    # 1/7/2026: κατώτατο ημερομίσθιο 41.09 x 1.15 = 47.25€
    try:
        svc.start_employment(
            pid,
            date(2026, 7, 1),
            pay_type="daily",
            daily_wage=45.0,
            weekly_pattern=FULL_WEEK,
            service_years=10,
        )
    except ValueError as e:
        assert "47.25" in str(e)
        print("OK ημερομίσθιος: όριο 41.09 x 1.15 = 47.25€ (ΕΓΣΣΕ 5%/τριετία)")
    else:
        raise AssertionError("έπρεπε να απορριφθεί")


def test_triennium_matures_over_time():
    """Η τριετία ΩΡΙΜΑΖΕΙ με τον χρόνο στον εργοδότη: πρόσληψη με 2.8 έτη
    προϋπηρεσίας -> μετά από λίγους μήνες συμπληρώνεται τριετία και το
    wage_violations πιάνει τον μισθό που ήταν οριακά νόμιμος."""
    svc, proj, pid = make_svc()
    # 4/2026: βασικός 920€. Με 2.8 έτη: καμία τριετία -> όριο 920. ΟΚ τα 950.
    eid = svc.start_employment(
        pid,
        date(2026, 4, 6),
        monthly_gross=950.0,
        weekly_pattern=FULL_WEEK,
        service_years=2.8,
    )
    # Ολοκληρωμένη πρόσληψη (αναγγελία): αλλιώς η απασχόληση είναι εκκρεμής
    # και δεν ελέγχεται από το wage_violations (§5.7α).
    svc.record_external_hire_submission(eid)
    _ = proj.employments[eid]
    # Απρίλιος 2026: καμία παραβίαση
    assert svc.wage_violations(2026, 4) == []
    # Σεπτέμβριος 2026: 2.8 + ~0.4 έτη > 3 -> 1 τριετία -> όριο 1012€ > 950€
    v = svc.wage_violations(2026, 9)
    assert len(v) == 1 and "1012.00" in v[0] and "1 τριετίες" in v[0]
    print("OK η τριετία ωρίμασε στον χρόνο και ο μισθός έπεσε κάτω από το όριο")


def test_triennium_freeze_excludes_2012_to_2023_service():
    """Πρόσληψη πριν από το πάγωμα: μετρά πριν το 14/02/2012 και ξανά από
    01/01/2024, αλλά ΟΧΙ το ενδιάμεσο διάστημα."""
    svc, proj, pid = make_svc()
    # ΜΕΤΑΦΟΡΑ: πρόσληψη ετών πριν (το μητρώο ΓΣΣΕ δεν φτάνει ως εκεί), με
    # ανάληψη μισθοδοσίας από το πρόγραμμα σήμερα.
    eid = svc.start_employment(
        pid,
        date(2010, 1, 1),
        payroll_from=date(2026, 7, 1),
        monthly_gross=1500.0,
        weekly_pattern=FULL_WEEK,
        service_years=0,
    )
    svc.record_external_hire_submission(eid)
    emp = proj.employments[eid]
    total = emp.total_service_years(date(2026, 1, 1))
    assert 4.0 < total < 4.3, total
    assert config.triennia_count(total, 3) == 1
    print("OK το πάγωμα 2012-2023 δεν μετρήθηκε στις τριετίες")


def test_frozen_service_is_extra_and_outside_the_triennia():
    """Ο παγωμένος χρόνος δηλώνεται ΕΠΙΠΛΕΟΝ της προϋπηρεσίας: δεν μετρά στις
    τριετίες (μόνο τα 3 έτη), μετρά όμως στον ΣΥΝΟΛΙΚΟ χρόνο εργασίας — και
    ΔΕΝ περιορίζεται από τα έτη τριετιών (εδώ 6 > 3)."""
    svc, proj, pid = make_svc()
    on = date(2026, 7, 1)
    eid = svc.start_employment(
        pid,
        on,
        monthly_gross=1100.0,
        weekly_pattern=FULL_WEEK,
        service_years=3,
        frozen_service_years=6,
    )
    svc.record_external_hire_submission(eid)
    emp = proj.employments[eid]
    assert emp.frozen_service_years == 6.0
    assert config.triennia_count(emp.total_service_years(on), 3) == 1
    assert emp.full_service_years(on) == 9.0  # σύνολο = 3 + 6
    print("OK ο παγωμένος χρόνος είναι ΕΠΙΠΛΕΟΝ και εκτός τριετιών")


def test_frozen_service_cannot_exceed_the_freeze_span():
    """Ο παγωμένος χρόνος δεν μπορεί να ξεπερνά το ίδιο το διάστημα του
    παγώματος (14/02/2012–31/12/2023 ≈ 11 έτη 10 μήνες)."""
    svc, _proj, pid = make_svc()
    try:
        svc.start_employment(
            pid,
            date(2026, 7, 1),
            monthly_gross=1100.0,
            weekly_pattern=FULL_WEEK,
            service_years=1,
            frozen_service_years=12,
        )
    except ValueError as exc:
        assert "14/02/2012" in str(exc), exc
    else:
        raise AssertionError("δεκτός παγωμένος χρόνος μεγαλύτερος του παγώματος")
    print("OK ο παγωμένος χρόνος περιορίζεται στο διάστημα του παγώματος")


def test_triennium_matures_at_rounding_boundary():
    """Ωρίμανση στο ΟΡΙΟ με στρογγυλοποίηση 2 δεκαδικών: προϋπηρεσία 2.913
    έτη -> στην πρόσληψη 0 τριετίες (όριο 920€, δεκτός μισθός 950€), αλλά
    μετά ~1 μήνα 2.913 + 1/12 ≈ 2.9963 -> 3.00 -> 1 τριετία -> όριο 1012€."""
    on = date(2026, 7, 1)
    # Καθαρός υπολογισμός τριετιών με στρογγυλοποίηση 2 δεκαδικών
    assert config.triennia_count(2.913, 3) == 0  # στην πρόσληψη
    assert config.triennia_count(2.9963, 3) == 1  # 2.9963 -> 3.00
    assert config.triennia_count(2.994, 3) == 0  # 2.99 -> όχι ακόμη
    assert config.seniority_factor("monthly", 2.9963, False, 0, on) == 1.10

    # End-to-end: μισθός οριακά νόμιμος στην πρόσληψη (920€), ωριμάζει εντός μήνα
    svc, proj, pid = make_svc()
    floor_hire, _ = svc.minimum_floor("monthly", 40, on, service_years=2.913)
    assert floor_hire == 920.0  # 0 τριετίες στην ημ. πρόσληψης
    eid = svc.start_employment(
        pid, on, monthly_gross=950.0, weekly_pattern=FULL_WEEK, service_years=2.913
    )
    svc.record_external_hire_submission(eid)  # ολοκληρωμένη πρόσληψη (§5.7α)
    # Τέλος Ιουλίου: 2.913 + ~1 μήνας -> 3.00 -> 1 τριετία -> όριο 1012€ > 950€
    v = svc.wage_violations(2026, 7)
    assert len(v) == 1 and "1012.00" in v[0] and "1 τριετίες" in v[0]
    print("OK τριετία ωρίμασε στο όριο στρογγυλοποίησης (2.913 -> 3.00 σε ~1 μήνα)")


def test_wage_adjustment_triennium_keep_and_offset():
    """Ωρίμανση τριετίας εντός περιόδου -> προσαρμογή μισθού. Παράδειγμα
    χρήστη: βασικός 920, μισθός 1000 (έξτρα +80), ωριμάζει τριετία -> νέο
    όριο 1012 (αύξηση 92). Συμψηφισμός: 1000 -> 1012 (έξτρα απορροφά).
    Διατήρηση: 1000 -> 1092 (η αύξηση περνά, έξτρα διατηρείται)."""
    for keep, expected in ((False, 1012.0), (True, 1092.0)):
        svc, proj, pid = make_svc()
        proj.misc_settings["keep_wage_excess"] = keep
        # Πρόσληψη 1/7/2026: όριο 920 (0 τριετίες), μισθός 1000 δεκτός
        eid = svc.start_employment(
            pid,
            date(2026, 7, 1),
            monthly_gross=1000.0,
            weekly_pattern=FULL_WEEK,
            service_years=2.913,
        )
        svc.record_external_hire_submission(eid)  # ολοκληρωμένη πρόσληψη
        # Τέλος Ιουλίου: 2.913 + ~1 μήνας -> 3.00 -> 1 τριετία -> όριο 1012
        res = svc.apply_minimum_adjustments(2026, 7)
        assert len(res) == 1, (keep, res)
        assert res[0]["old"] == 1000.0 and res[0]["new"] == expected, (keep, res[0])
        # ΑΙΤΙΑ: τριετία, ΟΧΙ ΓΣΣΕ (να μη μπερδεύεται το event)
        assert res[0]["cause"] == "τριετία", res[0]["cause"]
        # …και η ίδια αιτία ΠΑΝΩ στη μεταβολή (στήλη «Σχόλια» του ιστορικού)
        from misevent.domain.events import ContractChanged

        cc = [ev for _a, ev in svc.store.load(event_types=("ContractChanged",))]
        assert len(cc) == 1 and isinstance(cc[0], ContractChanged)
        assert cc[0].reason == "triennium" and cc[0].reason_date is None
    print("OK ωρίμανση τριετίας: συμψηφισμός 1000->1012, διατήρηση 1000->1092")


def test_scheduler_detects_triennium_maturation():
    """Ο scheduler γεννά WAGE_ADJUSTMENT όταν ωριμάζει τριετία (_floor_rose),
    όχι μόνο σε αλλαγή ΓΣΣΕ."""
    from misevent.domain.scheduler import ObligationScheduler

    svc, proj, pid = make_svc()
    eid = svc.start_employment(
        pid,
        date(2026, 7, 1),
        monthly_gross=1000.0,
        weekly_pattern=FULL_WEEK,
        service_years=2.913,
    )
    svc.record_external_hire_submission(eid)
    sched = ObligationScheduler(svc.store, proj, horizon_months=1)
    assert sched._floor_rose(2026, 7) is True  # τριετία ωρίμασε τον Ιούλιο
    # Εργαζόμενος πολύ πάνω από το όριο, καμία ωρίμανση -> κανένα trigger
    svc2, proj2, pid2 = make_svc()
    eid2 = svc2.start_employment(
        pid2,
        date(2026, 7, 1),
        monthly_gross=2000.0,
        weekly_pattern=FULL_WEEK,
        service_years=0.5,
    )
    svc2.record_external_hire_submission(eid2)
    sched2 = ObligationScheduler(svc2.store, proj2, horizon_months=1)
    assert sched2._floor_rose(2026, 7) is False
    print("OK ο scheduler ανιχνεύει ωρίμανση τριετίας για WAGE_ADJUSTMENT")


def test_voluntary_exit_auto_settles_gift_and_allowance():
    """Οικιοθελής αποχώρηση: αυτόματα Δώρο (Πάσχα/Χριστ. ανάλογα τον μήνα) +
    Επίδομα Αδείας (pro-rata στην αποχώρηση). Καταγγελία: ΟΧΙ αυτόματα."""
    # Ιούλιος -> Δώρο Χριστουγέννων + επίδομα (cap μισός μισθός)
    svc, proj, pid = make_svc()
    eid = svc.start_employment(
        pid, date(2024, 4, 8), monthly_gross=1500.0, weekly_pattern=FULL_WEEK
    )
    svc.record_external_hire_submission(eid)
    insure(svc, eid)
    svc.terminate(eid, date(2026, 7, 15), "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
    gifts = {g.gift_type: g for g in proj.gifts}
    assert "christmas" in gifts and "easter" not in gifts
    assert len(proj.allowances) == 1 and proj.allowances[0].year == 2026

    # Μάρτιος -> Δώρο Πάσχα + επίδομα. Πρόσληψη 4/2024, αποχώρηση 3/2026 =
    # 3ο ημερολογιακό έτος υπηρεσίας -> ολόκληρο το επίδομα (μισός μισθός 750)
    svc2, proj2, pid2 = make_svc()
    eid2 = svc2.start_employment(
        pid2, date(2024, 4, 8), monthly_gross=1500.0, weekly_pattern=FULL_WEEK
    )
    svc2.record_external_hire_submission(eid2)
    insure(svc2, eid2)
    svc2.terminate(eid2, date(2026, 3, 20), "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
    assert {g.gift_type for g in proj2.gifts} == {"easter"}
    assert proj2.allowances[0].gross == 750.0  # 3ο έτος+ -> ολόκληρο

    # 2ο έτος υπηρεσίας: αποχώρηση 3/2025 (πρόσληψη 4/2024) -> pro-rata
    # 3 μήνες × 2 ημερομίσθια × (1500/25=60) = 360
    svc4, proj4, pid4 = make_svc()
    eid4 = svc4.start_employment(
        pid4, date(2024, 4, 8), monthly_gross=1500.0, weekly_pattern=FULL_WEEK
    )
    svc4.record_external_hire_submission(eid4)
    insure(svc4, eid4)
    svc4.terminate(eid4, date(2025, 3, 20), "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
    assert proj4.allowances[0].gross == 360.0  # 2ο έτος -> αναλογικά

    # Καταγγελία σύμβασης -> ΚΑΙ αυτή εκκαθαρίζει pro-rata Δώρο + Επίδομα
    # (το αναλογούν Δώρο/επίδομα οφείλεται σε ΚΑΘΕ αποχώρηση)
    svc3, proj3, pid3 = make_svc()
    eid3 = svc3.start_employment(
        pid3, date(2024, 4, 8), monthly_gross=1500.0, weekly_pattern=FULL_WEEK
    )
    svc3.record_external_hire_submission(eid3)
    insure(svc3, eid3)
    svc3.terminate(eid3, date(2026, 7, 15), "ΚΑΤΑΓΓΕΛΙΑ_ΣΥΜΒΑΣΗΣ")
    assert {g.gift_type for g in proj3.gifts} == {"christmas"}  # Ιούλιος > 4
    assert len(proj3.allowances) == 1 and proj3.allowances[0].year == 2026
    print("OK κάθε αποχώρηση (οικιοθελής/καταγγελία) -> αυτόματα Δώρο+Επίδομα")


def test_severance_on_dismissal():
    """Αποζημίωση απόλυσης: μήνες από πίνακα × μηνιαίες × 7/6, αφορολόγητο
    ως 60.000€, καμία ασφαλιστική κράτηση. Μετρά ΜΟΝΟ ο χρόνος στον εργοδότη
    (πρόσληψη→απόλυση), ΟΧΙ η δηλωθείσα προϋπηρεσία."""
    svc, proj, pid = make_svc()
    eid = svc.start_employment(
        pid,
        date(2024, 4, 8),
        monthly_gross=1500.0,
        weekly_pattern=FULL_WEEK,
        service_years=5,
    )
    svc.record_external_hire_submission(eid)
    insure(svc, eid)
    svc.terminate(eid, date(2026, 7, 15), "ΚΑΤΑΓΓΕΛΙΑ_ΣΥΜΒΑΣΗΣ")
    # Χρόνος στον εργοδότη 8/4/2024 -> 15/7/2026 = ~2,27 έτη -> 2 μήνες
    # (η δηλωθείσα προϋπηρεσία 5 ετών ΔΕΝ μετράει)
    a = svc.run_severance(eid, with_notice=False)
    assert a["months"] == 2.0
    assert a["gross"] == round(2 * 1500 * 7 / 6, 2)  # 3500,0
    assert a["fmy"] == 0.0 and a["net"] == a["gross"]  # < 60k αφορολόγητο
    b = svc.severance_preview(eid, with_notice=True)  # μισό
    assert b["months"] == 1.0
    ev = svc.severance(eid)
    assert ev.gross == 3500.0 and not hasattr(ev, "efka_employee")
    # Η καταγγελία εκκαθαρίζει ΚΑΙ το pro-rata Δώρο + Επίδομα Αδείας
    assert proj.gifts and proj.allowances
    print("OK αποζημίωση απόλυσης 2 μήνες x1500 x7/6 = 3500 (χρόνος στον εργοδότη)")


def test_change_contract_uses_allowances():
    """Η αλλαγή σύμβασης ελέγχεται με τις προσαυξήσεις της απασχόλησης."""
    svc, _, pid = make_svc()
    eid = svc.start_employment(
        pid,
        date(2026, 7, 1),
        monthly_gross=1300.0,
        weekly_pattern=FULL_WEEK,
        service_years=6,
        married=True,
    )
    svc.record_external_hire_submission(eid)
    # όριο: 920 x 1.30 = 1196€ -> μείωση σε 1100€ απορρίπτεται
    try:
        svc.change_contract(eid, date(2026, 8, 1), "monthly", monthly_gross=1100.0)
    except ValueError as e:
        assert "1196.00" in str(e)
        print("OK η αλλαγή σύμβασης σεβάστηκε το προσαυξημένο όριο")
    else:
        raise AssertionError("έπρεπε να απορριφθεί")
