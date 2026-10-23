"""
ΣΥΜΦΩΝΙΑ ΠΡΟΓΡΑΜΜΑΤΟΣ ↔ ΚΑΡΤΑΣ ΑΝΑ ΗΜΕΡΑ (ARCH_REF §5.11).

- εντός ανοχής (`card_tolerance_minutes` + ευέλικτη προσέλευση) -> η ημέρα
  πληρώνεται με το ΠΡΟΓΡΑΜΜΑ και ΔΕΝ μπαίνει στην απολογιστική πρόταση·
- εκτός ανοχής -> με την ΚΑΡΤΑ και μπαίνει στην πρόταση·
- χωρίς χτύπημα -> με το ΠΡΟΓΡΑΜΜΑ (τηλεεργασία, ξεχασμένη κάρτα).

Εκτέλεση: uv run pytest tests/test_card_tolerance.py
"""

from datetime import date

from test_closed_period import setup

from misevent.integrations.card_punches import day_matches

FEB = [date(2026, 2, d) for d in range(1, 29)]
SMALL = date(2026, 2, 3)  # 09:03-17:07: λίγα λεπτά
BIG = date(2026, 2, 4)    # 09:00-19:00: 2 ώρες παραπάνω
NONE = date(2026, 2, 5)   # κανένα χτύπημα


def test_day_matches_tolerance():
    assert not day_matches("09:00-17:00", "09:04-16:57", 0)
    assert day_matches("09:00-17:00", "09:04-16:57", 0, 5)
    assert day_matches("09:00-17:00", "08:55-17:05", 0, 5)    # νωρίτερα, εντός
    assert not day_matches("09:00-17:00", "08:54-17:00", 0, 5)
    # ευέλικτη προσέλευση 30΄ + ανοχή 5΄: 20΄ αργότερα, έξοδος έως +25΄
    assert day_matches("09:00-17:00", "09:20-17:25", 30, 5)
    assert not day_matches("09:00-17:00", "09:20-17:26", 30, 5)
    assert day_matches("09:00-13:00+17:00-21:00", "09:02-12:58+17:03-21:00", 0, 5)
    assert not day_matches("09:00-17:00", "", 0, 60)          # δούλεψε / ρεπό
    # ανοχή 10΄: ΚΑΘΕ άκρο ±10 ανεξάρτητα, χωρίς μετάθεση της εξόδου
    for card in ("08:50-17:10", "09:10-16:50", "09:00-17:09", "08:50-16:50"):
        assert day_matches("09:00-17:00", card, 0, 10), card
    assert not day_matches("09:00-17:00", "09:10-17:20", 0, 10)
    assert not day_matches("09:00-17:00", "08:49-17:00", 0, 10)
    # ευέλικτη 30΄ μετρά ΜΕΤΑ την ανοχή: 09:15 -> πρόγραμμα 09:15-17:15, +3΄ ανοχή
    assert day_matches("09:00-17:00", "09:15-17:18", 30, 10)
    assert not day_matches("09:00-17:00", "09:15-17:00", 30, 10)  # έφυγε 15΄ νωρίς


def _month(tol, system="retro"):
    svc, proj, eid = setup()  # πρόγραμμα 09-17 Δε–Πα
    if system == "retro":
        svc.declare_schedule_system(date(2026, 2, 1), "retro", date(2026, 1, 20))
    svc.define_parameters("work_hours", date(2026, 1, 1), night_start=22,
                          night_end=6, card_tolerance_minutes=tol)
    emp = proj.employments[eid]
    emp.flexible_arrival_minutes = "0"
    for d in FEB:
        if emp.day_schedule(d) and d != NONE:
            shift = {SMALL: "09:03-17:07", BIG: "09:00-19:00"}.get(d, "09:00-17:00")
            svc.record_attendance(eid, d, (shift,))
    return svc, proj, eid


def test_small_by_schedule_big_by_card(fresh_system):
    svc, _proj, eid = _month(10)
    assert set(svc.retro_schedule_proposal(2026, 2)[eid]) == {BIG}
    line = svc.attendance_draft(2026, 2)[eid]
    assert line["card_days"] == [BIG]
    assert line["no_punch_days"] == [NONE]
    assert line["worked"] == 20  # 19 με κάρτα + 1 χωρίς χτύπημα
    # μόνο οι 2 ώρες της BIG είναι πέρα από το ωράριο (όχι τα 4΄ της SMALL)
    over = sum(v for k, v in svc.hours_analysis(2026, 2)[eid].items()
               if k.startswith(("overwork", "overtime", "extra")))
    assert over == 2.0


def test_zero_tolerance_small_is_card(fresh_system):
    svc, _proj, eid = _month(0)
    assert set(svc.retro_schedule_proposal(2026, 2)[eid]) == {SMALL, BIG}


def test_pre_system_policy(fresh_system):
    svc, _proj, eid = _month(10, system="pre")
    hours = svc.hours_analysis(2026, 2)[eid]
    assert hours["overwork"] + hours["overtime"] > 0  # προεπιλογή: με την κάρτα
    svc.set_misc_settings(pre_major_by_card=False)
    hours = svc.hours_analysis(2026, 2)[eid]
    assert hours["overwork"] + hours["overtime"] == 0  # με το πρόγραμμα


MIXED = date(2026, 2, 10)  # 09-13 στο γραφείο + Τ16-20 τηλεεργασία
TELE = date(2026, 2, 11)   # μόνο τηλεεργασία


def _telework_month(morning):
    svc, proj, eid = setup()
    svc.declare_schedule_system(date(2026, 2, 1), "retro", date(2026, 1, 20))
    emp = proj.employments[eid]
    emp.flexible_arrival_minutes = "0"
    # ΣΚΟΠΙΜΑ απευθείας στην προβολή: το test αφορά τη σύγκριση, όχι τον
    # έλεγχο εγκυρότητας/πάγωμα του `set_daily_schedule`
    for d, text in ((MIXED, "09:00-13:00+Τ16:00-20:00"), (TELE, "Τ09:00-17:00")):
        emp.daily_schedule[d] = emp.declared_days[d] = text  # δηλωμένο ΕΡΓΑΝΗ
    for d in FEB:
        if emp.day_schedule(d) and d != TELE:
            svc.record_attendance(eid, d, (morning if d == MIXED else "09:00-17:00",))
    return svc, emp, eid


def test_telework_is_never_compared_with_the_card(fresh_system):
    """Η τηλεεργασία δεν χτυπά κάρτα: ισχύει το πρόγραμμα."""
    svc, emp, eid = _telework_month("09:00-13:00")
    assert emp.pay_source(MIXED) == "schedule"   # μόνο το πρωί συγκρίνεται
    assert emp.pay_source(TELE) == "schedule"    # χωρίς χτύπημα
    assert svc.retro_schedule_proposal(2026, 2) == {}
    line = svc.attendance_draft(2026, 2)[eid]
    assert TELE not in line["no_punch_days"]     # αναμενόμενη, όχι «χωρίς χτύπημα»


def test_retro_keeps_the_telework_part(fresh_system):
    """Απόκλιση στο πρωινό: δηλώνεται η κάρτα ΚΑΙ η τηλεεργασία αυτούσια."""
    svc, emp, eid = _telework_month("08:30-12:30")
    assert emp.pay_source(MIXED) == "card"
    assert svc.retro_schedule_proposal(2026, 2)[eid] == {
        MIXED: ("09:00-13:00+Τ16:00-20:00", "08:30-12:30+Τ16:00-20:00"),
    }
