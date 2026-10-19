"""
Tests ΕΚΚΑΘΑΡΙΣΗΣ ΑΠΟΧΩΡΗΣΗΣ:

1. Η «Επιβεβαίωση αποχώρησης» εκτελεί **ΟΛΟΥΣ** τους υπολογισμούς αυτόματα —
   Δώρο, Επίδομα Αδείας, αποζημίωση μη ληφθείσας αδείας και (σε καταγγελία)
   αποζημίωση απόλυσης. Κανένα χειροκίνητο κουμπί.
2. Το Δώρο/Επίδομα της αποχώρησης αφορά **ΑΠΟΚΛΕΙΣΤΙΚΑ** τον εργαζόμενο που
   αποχωρεί: ΔΕΝ κλείνει την εταιρική υποχρέωση, ώστε οι υπόλοιποι να μη
   χάσουν το δικό τους Δώρο.
3. Η προεπισκόπηση (`termination_preview`) δίνει ΤΑ ΙΔΙΑ ποσά με την
   καταχώρηση και ΔΕΝ γράφει κανένα event.

Εκτέλεση: uv run tests/test_exit_settlement.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.domain.scheduler import ObligationScheduler
from misevent.persistence.database import Database

FULL_WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}

PEOPLE = [
    ("ΠΑΠΑΔΟΠΟΥΛΟΣ", "ΙΩΑΝΝΗΣ", "111222336", "05059012343", "123456789"),
    ("ΓΕΩΡΓΙΟΥ", "ΜΑΡΙΑ", "090034337", "05059012350", "123456788"),
]


def make_svc(with_scheduler=False, horizon=40):
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    sched = None
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
    if with_scheduler:
        # Ορίζοντας ως το 2026 ώστε να έχει γεννηθεί η ετήσια υποχρέωση Δώρων
        sched = ObligationScheduler(store, proj, horizon_months=horizon)
        store.subscribe(sched.handle)
    return svc, proj, sched


def hire(svc, proj, idx, hire_date=date(2024, 4, 8), gross=1500.0):
    last, first, afm, amka, ama = PEOPLE[idx]
    pid = svc.register_person(
        last_name=last,
        first_name=first,
        father_name="Ν",
        mother_name="Μ",
        birth_date=date(1990, 5, 5),
        afm=afm,
        amka=amka,
        ama=ama,
    )
    eid = svc.start_employment(
        pid, hire_date, monthly_gross=gross, weekly_pattern=FULL_WEEK
    )
    svc.record_external_hire_submission(eid)
    svc.assign_insurance_profile(
        eid,
        coverages=(
            {
                "fund": "efka_main",
                "kad": "0000",
                "specialty_code": "000000",
                "kpk": "0101",
            },
        ),
    )
    # ΟΧΕ (standing εβδομαδιαίο): προαπαιτούμενο των παρουσιών (§5.7α)
    svc.submit_hire_wto(eid)
    return eid


def test_termination_settles_everything():
    """Καταγγελία: Δώρο + Επίδομα + αποζημίωση αδείας + αποζημίωση απόλυσης,
    ΟΛΑ με την ίδια εντολή."""
    svc, proj, _ = make_svc()
    eid = hire(svc, proj, 0)
    svc.terminate(eid, date(2026, 9, 30), "ΚΑΤΑΓΓΕΛΙΑ_ΣΥΜΒΑΣΗΣ")

    assert {g.gift_type for g in proj.gifts} == {"christmas"}  # Σεπτέμβριος > 4
    assert len(proj.allowances) == 1
    assert svc.leave_compensation(eid) is not None, "αποζημίωση αδείας"
    sev = svc.severance(eid)
    assert sev is not None and sev.gross > 0, "αποζημίωση απόλυσης"
    assert sev.with_notice is False
    print("OK  καταγγελία -> Δώρο + Επίδομα + αποζ. αδείας + αποζ. απόλυσης")

    # Οικιοθελής: ΚΑΜΙΑ αποζημίωση απόλυσης — τα υπόλοιπα κανονικά
    svc2, proj2, _ = make_svc()
    eid2 = hire(svc2, proj2, 0)
    svc2.terminate(eid2, date(2026, 9, 30), "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
    assert svc2.severance(eid2) is None
    assert proj2.gifts and proj2.allowances
    assert svc2.leave_compensation(eid2) is not None
    print("OK  οικιοθελής -> χωρίς αποζημίωση απόλυσης")

    # «Με προειδοποίηση» -> οι μισοί μήνες
    svc3, proj3, _ = make_svc()
    eid3 = hire(svc3, proj3, 0)
    svc3.terminate(eid3, date(2026, 9, 30), "ΚΑΤΑΓΓΕΛΙΑ_ΣΥΜΒΑΣΗΣ", with_notice=True)
    half = svc3.severance(eid3)
    assert half.with_notice is True and half.months == sev.months / 2
    print("OK  «με προειδοποίηση» -> μισοί μήνες αποζημίωσης")


def test_exit_gift_is_only_for_the_leaver():
    """Η εκκαθάριση αποχώρησης ΔΕΝ κλείνει την εταιρική υποχρέωση Δώρου:
    οι υπόλοιποι εργαζόμενοι υπολογίζονται κανονικά αργότερα."""
    # Ορίζοντας 1 μήνα ώστε ο τρέχων μήνας μισθοδοσίας να είναι ο μήνας
    # πρόσληψης — εκεί επιτρέπεται η αποχώρηση
    svc, proj, sched = make_svc(with_scheduler=True, horizon=1)
    leaver = hire(svc, proj, 0, hire_date=date(2026, 9, 1))
    stays = hire(svc, proj, 1, hire_date=date(2026, 9, 1))
    # Οι ετήσιες υποχρεώσεις Δώρων γεννιούνται όταν ανοίγει περίοδος ΜΕΤΑ την
    # αναγγελία πρόσληψης (η πρώτη πρόσληψη είναι ακόμη draft στο _on_hired)
    sched._ensure_gifts(2026)

    assert svc.current_payroll_period() == (2026, 9)
    svc.terminate(leaver, date(2026, 9, 30), "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")

    holders = {g.employee_id for g in proj.gifts}
    assert holders == {leaver}, "το Δώρο αφορά ΜΟΝΟ αυτόν που αποχωρεί"
    assert {a.employee_id for a in proj.allowances} == {leaver}
    assert proj.gifts[0].exit_settlement is True
    print("OK  η εκκαθάριση υπολογίζει ΜΟΝΟ τον αποχωρούντα")

    ob = next(
        (
            o
            for o in proj.obligations.values()
            if o.obligation_type == "CHRISTMAS_GIFT" and o.period_year == 2026
        ),
        None,
    )
    assert ob is not None and not ob.fulfilled, (
        "η υποχρέωση Δώρου της εταιρείας ΔΕΝ κλείνει από την αποχώρηση ενός "
        "εργαζομένου"
    )
    print("OK  η εταιρική υποχρέωση Δώρου παραμένει ανοιχτή")

    # Ο εταιρικός κύκλος τρέχει κανονικά και καλύπτει ΟΛΟΥΣ
    svc.run_christmas_gifts(2026)
    assert {g.employee_id for g in proj.gifts} == {leaver, stays}
    ob = proj.obligations[ob.obligation_id]
    assert ob.fulfilled, "ο εταιρικός υπολογισμός κλείνει την υποχρέωση"
    print("OK  ο εταιρικός κύκλος καλύπτει ΟΛΟΥΣ και κλείνει την υποχρέωση")


def test_preview_matches_and_writes_nothing():
    """Η προεπισκόπηση δίνει τα ΙΔΙΑ ποσά με την καταχώρηση, χωρίς event."""
    svc, proj, _ = make_svc()
    eid = hire(svc, proj, 0)
    term = date(2026, 9, 30)

    before = len(list(svc.store.load()))
    pv = svc.termination_preview(eid, term, "ΚΑΤΑΓΓΕΛΙΑ_ΣΥΜΒΑΣΗΣ")
    assert len(list(svc.store.load())) == before, "η προεπισκόπηση δεν γράφει"
    assert proj.employments[eid].termination_date is None

    svc.terminate(eid, term, "ΚΑΤΑΓΓΕΛΙΑ_ΣΥΜΒΑΣΗΣ")
    gift = proj.gifts[0]
    allowance = proj.allowances[0]
    assert round(pv["gift"].gross, 2) == round(gift.gross, 2)
    assert round(pv["allowance"].gross, 2) == round(allowance.gross, 2)
    assert pv["leave_comp"]["net"] == svc.leave_compensation(eid).net
    assert pv["severance"]["gross"] == svc.severance(eid).gross

    # ΤΟ ΣΥΝΟΛΟ ΕΙΝΑΙ ΤΟ ΑΜΕΣΑ ΚΑΤΑΒΛΗΤΕΟ: ΧΩΡΙΣ το Δώρο, που πληρώνεται στη
    # δική του ημερομηνία (εδώ δεν υπάρχει μισθοδοσία αποχώρησης — λείπουν
    # παράμετροι/περίοδοι, οπότε το `payroll_blocked` το εξηγεί).
    assert pv["payroll"] is None and pv["payroll_blocked"]
    total = round(
        allowance.net + svc.leave_compensation(eid).net + svc.severance(eid).net, 2
    )
    assert pv["total"]["net"] == total, "άμεσα καταβλητέο = όλα ΕΚΤΟΣ του Δώρου"
    assert pv["gift_pay_date"] > term, "το Δώρο καταβάλλεται αργότερα"
    print("OK  προεπισκόπηση = καταχώρηση · σύνολο χωρίς το Δώρο")


def test_exit_payroll_is_only_for_the_leaver():
    """Η αποχώρηση υπολογίζει τη ΜΙΣΘΟΔΟΣΙΑ ΤΟΥ ΜΗΝΑ μόνο για τον αποχωρούντα,
    χωρίς να κλείσει την περίοδο ούτε να ανοίξει τον επόμενο μήνα."""
    # Ορίζοντας 1 μήνα: η αλυσίδα ανοίγει τους επόμενους μόνο με μισθοδοσία,
    # οπότε φαίνεται καθαρά ότι η μισθοδοσία αποχώρησης ΔΕΝ την προχωρά.
    svc, proj, sched = make_svc(with_scheduler=True, horizon=1)
    svc.confirm_default_parameters()
    leaver = hire(svc, proj, 0, hire_date=date(2026, 5, 4))
    stays = hire(svc, proj, 1, hire_date=date(2026, 5, 4))

    # Ο Μάιος κλείνει κανονικά (εταιρικός κύκλος) -> ανοίγει ο Ιούνιος
    svc.approve_attendance(2026, 5)
    svc.run_monthly_payroll(2026, 5)
    assert {(p.period_year, p.period_month) for p in proj.payrolls} == {(2026, 5)}

    svc.terminate(leaver, date(2026, 6, 15), "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
    june = [p for p in proj.payrolls if (p.period_year, p.period_month) == (2026, 6)]
    assert len(june) == 1 and june[0].employee_id == leaver
    assert june[0].exit_settlement is True
    # Ημέρες ΕΦΚΑ pro-rata (αποχώρηση στη μέση του μήνα) — όχι ολόκληρες οι 25
    assert 0 < june[0].insurance_days < 25
    print("OK  μισθοδοσία μήνα αποχώρησης — ΜΟΝΟ για τον αποχωρούντα, pro-rata")

    ob = next(
        o
        for o in proj.obligations.values()
        if o.obligation_type == "MONTHLY_PAYROLL"
        and (o.period_year, o.period_month) == (2026, 6)
    )
    assert not ob.fulfilled, "η περίοδος ΔΕΝ κλείνει από τη μισθοδοσία ενός"
    opened = {
        (o.period_year, o.period_month)
        for o in proj.obligations.values()
        if o.obligation_type == "MONTHLY_PAYROLL"
    }
    assert (2026, 7) not in opened, "ούτε ανοίγει ο επόμενος μήνας"
    print("OK  η περίοδος παραμένει ανοιχτή και δεν προχωρά η αλυσίδα")

    # ΟΙ ΗΜΕΡΕΣ ΦΤΑΝΟΥΝ ΜΕΧΡΙ ΚΑΙ ΤΗΝ ΑΠΟΧΩΡΗΣΗ, ΟΧΙ ΟΛΟΣ Ο ΜΗΝΑΣ: αποχώρηση
    # 15/6 -> 11 από τις 22 ημέρες προγράμματος του Ιουνίου 2026 -> 25 × 11/22
    full = [p for p in proj.payrolls
            if (p.period_year, p.period_month) == (2026, 5)][0]
    assert june[0].insurance_days == 12, june[0].insurance_days
    assert june[0].gross == round(1500 * 12 / 25, 2) == 720.0
    assert june[0].gross < full.gross
    print(f"OK  μισθοδοσία έως την ημ. αποχώρησης: {june[0].insurance_days} "
          f"ημέρες / μικτά {june[0].gross:.2f}€ "
          f"(προηγούμενος μήνας {full.insurance_days} / {full.gross:.2f}€)")

    # Ο εταιρικός κύκλος του Ιουνίου καλύπτει τον υπόλοιπο και κλείνει
    svc.approve_attendance(2026, 6)
    svc.run_monthly_payroll(2026, 6)
    june = {
        p.employee_id for p in proj.payrolls
        if (p.period_year, p.period_month) == (2026, 6)
    }
    assert june == {leaver, stays}
    assert proj.obligations[ob.obligation_id].fulfilled
    print("OK  ο εταιρικός κύκλος του μήνα καλύπτει και τους δύο")


def test_termination_only_inside_current_month():
    """Η ημ. αποχώρησης πρέπει να είναι μέσα στον ΤΡΕΧΟΝΤΑ μήνα μισθοδοσίας:
    ό,τι είναι πριν κόβεται ως κλεισμένη περίοδος, ό,τι μετά ως μελλοντικό."""
    svc, proj, sched = make_svc(with_scheduler=True, horizon=1)
    svc.confirm_default_parameters()
    eid = hire(svc, proj, 0, hire_date=date(2026, 5, 4))
    svc.approve_attendance(2026, 5)
    svc.run_monthly_payroll(2026, 5)  # ο Μάιος κλείνει -> τρέχων ο Ιούνιος

    assert svc.current_payroll_period() == (2026, 6)
    for bad, why in (
        (date(2026, 5, 20), "κλεισμένη περίοδος"),
        (date(2026, 7, 10), "επόμενος μήνας"),
    ):
        try:
            svc.terminate(eid, bad, "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
        except ValueError:
            pass
        else:
            raise AssertionError(f"δεκτή αποχώρηση εκτός τρέχοντος μήνα ({why})")
    assert proj.employments[eid].termination_date is None
    # ...ενώ μέσα στον τρέχοντα μήνα περνά
    svc.terminate(eid, date(2026, 6, 10), "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
    assert proj.employments[eid].termination_date == date(2026, 6, 10)
    print("OK  αποχώρηση μόνο μέσα στον τρέχοντα μήνα μισθοδοσίας")
