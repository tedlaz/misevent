"""
ΑΠΟΛΟΓΙΣΤΙΚΟ ΣΥΣΤΗΜΑ ΔΗΛΩΣΗΣ ΩΡΑΡΙΟΥ (ΥΑ 32689/2025, άρθρο 2Α).

- Το σύστημα δηλώνεται ανά εταιρεία, έως το τέλος του προηγούμενου μήνα.
- Με την έγκριση παρουσιών ενός μήνα σε απολογιστικό, γεννιέται η υποχρέωση
  RETRO_SCHEDULE (προθεσμία: τέλος επόμενου μήνα).
- Η πρόταση = ημέρες όπου η κάρτα απέκλινε πέρα από την ευέλικτη προσέλευση·
  η δήλωση περνά ΚΑΙ μετά το κλείσιμο της μισθοδοσίας του μήνα.
- Οι άδειες: προγραμματισμένη / σε εξέλιξη / ληφθείσα κατά την ημερομηνία.

Εκτέλεση: python tests/test_retro_schedule.py
"""

from datetime import date, timedelta

from test_closed_period import setup

FEB = [date(2026, 2, d) for d in range(1, 29)]


def test_declare_schedule_system_rules():
    svc, proj, _eid = setup()
    for args in ((date(2026, 3, 1), "retro", date(2026, 3, 1)),  # όχι ίδιο μήνα
                 (date(2026, 4, 1), "xyz", date(2026, 3, 1))):
        try:
            svc.declare_schedule_system(*args)
        except ValueError:
            continue
        raise AssertionError(f"έπρεπε να απορριφθεί: {args}")
    svc.declare_schedule_system(date(2026, 4, 15), "retro", date(2026, 3, 31))
    assert proj.schedule_system_on(date(2026, 3, 31)) == "pre"
    assert proj.schedule_system_on(date(2026, 4, 1)) == "retro"
    svc.declare_schedule_system(date(2026, 6, 1), "pre", date(2026, 3, 31))
    assert proj.schedule_system_on(date(2026, 7, 9)) == "pre"


def test_retro_month_flow():
    svc, proj, eid = setup()  # 1/2026 κλεισμένη, πρόγραμμα 09-17 Δε–Πα
    svc.declare_schedule_system(date(2026, 2, 1), "retro", date(2026, 1, 20))
    emp = proj.employments[eid]
    for d in FEB:  # η κάρτα: κανονικά, εκτός από δύο ημέρες
        if emp.day_schedule(d):
            shift = "12:00-20:00" if d == date(2026, 2, 3) else "09:10-17:05"
            svc.record_attendance(eid, d, (shift,))
    svc.record_attendance(eid, date(2026, 2, 7), ("10:00-14:00",))  # Σάββατο

    proposal = svc.retro_schedule_proposal(2026, 2)[eid]
    assert proposal == {
        date(2026, 2, 3): ("09:00-17:00", "12:00-20:00"),  # εκτός ανοχής 120΄
        date(2026, 2, 7): ("", "10:00-14:00"),             # δούλεψε σε ρεπό
    }

    # η ΕΓΚΡΙΣΗ ετοιμάζει ΑΜΕΣΩΣ τη δήλωση (ΚΑΤΑΓΡΑΦΗ) και ΑΝΟΙΓΕΙ την
    # υποχρέωση ΥΠΟΒΟΛΗΣ στο ΕΡΓΑΝΗ
    svc.approve_attendance(2026, 2)
    retro = [o for o in proj.obligations.values() if o.obligation_type == "RETRO_SCHEDULE"]
    assert [(o.period_month, o.due_date) for o in retro] == [(2, date(2026, 3, 31))]
    assert emp.day_schedule(date(2026, 2, 3)) == "12:00-20:00"
    assert emp.day_status(date(2026, 2, 3)) == "modified"
    assert not proj.obligations[retro[0].obligation_id].fulfilled

    svc.run_monthly_payroll(2026, 2)  # κλείνει ο Φεβρουάριος — η υποβολή περνά
    res = svc.submit_retro_schedule(2026, 2)  # ΥΠΟΒΟΛΗ στο ΕΡΓΑΝΗ
    assert res["count"] == 2 and res["batch_no"]
    assert emp.day_status(date(2026, 2, 3)) == "submitted"
    assert emp.day_status(date(2026, 2, 7)) == "submitted"
    assert proj.obligations[retro[0].obligation_id].fulfilled
    assert svc.retro_schedule_proposal(2026, 2) == {}


def test_retro_orphan_punches_completed_from_schedule(fresh_system):
    """ΟΡΦΑΝΟ χτύπημα: μετρά το ΠΡΟΓΡΑΜΜΑ ως κανονική παρουσία — το ταίρι
    συμπληρώνεται από το πρόγραμμα και στο απολογιστικό ΔΗΛΩΝΕΤΑΙ πάντα
    (`retro_declare_orphans`=1)· με 0 μόνο όταν αποκλίνει."""
    svc, proj, eid = setup()
    svc.declare_schedule_system(date(2026, 2, 1), "retro", date(2026, 1, 20))
    emp = proj.employments[eid]
    svc.record_attendance(eid, date(2026, 2, 5), ("-20:00",))  # χωρίς είσοδο
    svc.record_attendance(eid, date(2026, 2, 6), ("09:00-",))  # χωρίς έξοδο
    svc.record_attendance(eid, date(2026, 2, 9), ("09:00-17:00",))  # πλήρης
    assert emp.actual_shifts(date(2026, 2, 5)) == ["09:00-20:00"]
    assert emp.actual_shifts(date(2026, 2, 6)) == ["09:00-17:00"]
    assert svc.retro_schedule_proposal(2026, 2)[eid] == {
        date(2026, 2, 5): ("09:00-17:00", "09:00-20:00"),
        date(2026, 2, 6): ("09:00-17:00", "09:00-17:00"),  # ορφανό = δηλώνεται
    }
    assert not any(i["blocking"] for i in svc.card_issues(emp, 2026, 2))

    svc.define_parameters("work_hours", date(2026, 1, 1), night_start=22,
                          night_end=6, retro_declare_orphans=0)
    assert svc.retro_schedule_proposal(2026, 2)[eid] == {
        date(2026, 2, 5): ("09:00-17:00", "09:00-20:00"),  # μόνο η απόκλιση
    }
    svc.define_parameters("work_hours", date(2026, 1, 1), night_start=22,
                          night_end=6, retro_declare_orphans=1)

    # μετά την ΥΠΟΒΟΛΗ δεν ξαναπροτείνονται
    for d in FEB:
        if emp.day_schedule(d) and not emp.punches_on(d):
            svc.record_attendance(eid, d, ("09:00-17:00",))
    svc.approve_attendance(2026, 2)
    svc.run_monthly_payroll(2026, 2)
    assert svc.submit_retro_schedule(2026, 2)["count"] == 2
    assert svc.retro_schedule_proposal(2026, 2) == {}


def test_retro_absence_needs_reason():
    """Κάρτα με ΛΙΓΟΤΕΡΕΣ ώρες από το πρόγραμμα = ΑΠΟΥΣΙΑ: το κενό δηλώνεται
    στο απολογιστικό με ΛΟΓΟ (ΜΕ / ωροάδεια)· χωρίς λόγο η υποβολή απορρίπτεται."""
    import pytest

    from misevent.integrations import card_punches as cp
    from misevent.integrations import ergani

    assert cp.absence_gaps("09:00-17:00", ["09:00-15:00"]) == [(900, 1020)]
    assert cp.absence_gaps("09:00-17:00", ["10:00-14:00"]) == [(540, 600), (840, 1020)]
    assert cp.absence_gaps("09:00-17:00", ["10:00-18:00"]) == []  # μετάθεση

    svc, proj, eid = setup()
    svc.declare_schedule_system(date(2026, 2, 1), "retro", date(2026, 1, 20))
    emp = proj.employments[eid]
    short = date(2026, 2, 4)
    for d in FEB:
        if emp.day_schedule(d):
            svc.record_attendance(eid, d, ("09:00-15:00" if d == short else "09:00-17:00",))
    assert svc.retro_absences(2026, 2) == {
        eid: {short: {"gaps": [(900, 1020)], "code": ""}}}
    svc.approve_attendance(2026, 2)
    svc.run_monthly_payroll(2026, 2)
    with pytest.raises(ValueError, match="λόγος απουσίας"):
        svc.submit_retro_schedule(2026, 2)
    with pytest.raises(ValueError, match="Άγνωστος λόγος"):
        svc.set_absence_reason(eid, short, "ΧΧΧ")
    svc.set_absence_reason(eid, short, "ΩΑΓΟΝ")
    assert svc.submit_retro_schedule(2026, 2)["count"] == 1

    rows = ergani.build_wto_variable(
        None, emp, {short.isoformat(): "09:00-15:00"}, short,
        {short.isoformat(): [("ΩΑΓΟΝ", 900, 1020)]},
    )["WTOS"]["WTO"][0]["Ergazomenoi"]["ErgazomenoiWTO"][0]
    assert rows["ErgazomenosAnalytics"]["ErgazomenosWTOAnalytics"] == [
        {"f_type": "ΕΡΓ", "f_from": "09:00", "f_to": "15:00"},
        {"f_type": "ΩΑΓΟΝ", "f_from": "15:00", "f_to": "17:00"},
    ]


def test_retro_without_deviation_closes_on_approval(fresh_system):
    svc, proj, eid = setup()
    svc.declare_schedule_system(date(2026, 2, 1), "retro", date(2026, 1, 20))
    emp = proj.employments[eid]
    for d in FEB:
        if emp.day_schedule(d):
            svc.record_attendance(eid, d, ("09:00-17:00",))
    svc.approve_attendance(2026, 2)
    retro = [o for o in proj.obligations.values() if o.obligation_type == "RETRO_SCHEDULE"]
    assert len(retro) == 1 and retro[0].fulfilled  # τίποτα προς υποβολή


def test_leave_state_by_date():
    from types import SimpleNamespace

    from misevent.domain.projections import leave_state

    lv = SimpleNamespace(date_from=date(2026, 3, 10), date_to=date(2026, 3, 12))
    assert leave_state(lv, lv.date_from - timedelta(days=1)) == "planned"
    assert leave_state(lv, lv.date_from) == "ongoing"
    assert leave_state(lv, lv.date_to) == "ongoing"
    assert leave_state(lv, lv.date_to + timedelta(days=1)) == "taken"


def test_flexible_arrival_per_circular():
    """Εγκ. 37271/2024 §Δ: προσέλευση ΜΟΝΟ στο [έναρξη, έναρξη+X], ποτέ νωρίτερα·
    όλο το πρόγραμμα μετατίθεται ΑΚΡΙΒΩΣ όσο καθυστέρησε η προσέλευση
    (απόκλιση από αυτό = μόνο εντός ανοχής, εδώ 0)."""
    from misevent.integrations.card_punches import day_matches as ok

    assert ok("09:00-17:00", "09:00-17:00", 0)
    assert not ok("09:00-17:00", "09:00-17:02", 0)       # χωρίς ευελιξία: ακριβώς
    assert ok("09:00-17:00", "09:20-17:20", 30)
    assert not ok("09:00-17:00", "09:10-17:05", 30)      # έφυγε πριν συμπληρώσει
    assert not ok("09:00-17:00", "09:35-17:35", 30)      # πέρα από τα 30΄
    assert not ok("09:00-17:00", "08:50-17:00", 30)      # νωρίτερα: ποτέ
    assert not ok("09:00-17:00", "09:10-17:20", 30)      # 10΄ αργότερα -> 20΄ πέρα
    assert not ok("09:00-17:00", "09:00-16:55", 30)      # πρόωρη αποχώρηση
    assert ok("09:00-13:00+17:00-21:00", "09:15-13:15+17:15-21:15", 30)
    assert not ok("09:00-13:00+17:00-21:00", "09:15-13:15+17:10-21:15", 30)
