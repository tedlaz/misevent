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

    svc.approve_attendance(2026, 2)
    retro = [o for o in proj.obligations.values() if o.obligation_type == "RETRO_SCHEDULE"]
    assert [(o.period_month, o.due_date) for o in retro] == [(2, date(2026, 3, 31))]

    svc.run_monthly_payroll(2026, 2)  # κλείνει ο Φεβρουάριος — η δήλωση περνά
    res = svc.submit_retro_schedule(2026, 2)  # ΚΑΤΑΓΡΑΦΗ, όχι υποβολή
    assert res["count"] == 2
    assert emp.day_schedule(date(2026, 2, 3)) == "12:00-20:00"
    assert emp.day_status(date(2026, 2, 3)) == "modified"
    assert proj.obligations[retro[0].obligation_id].fulfilled
    # η υποβολή γίνεται από το «Πρόγραμμα → ΕΡΓΑΝΗ»
    svc.submit_schedule_days({eid: [date(2026, 2, 3), date(2026, 2, 7)]})
    assert emp.day_status(date(2026, 2, 3)) == "submitted"
    assert svc.retro_schedule_proposal(2026, 2) == {}


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
    η αποχώρηση μετατίθεται έως όσο καθυστέρησε η προσέλευση."""
    svc, _proj, _eid = setup()
    ok = svc._within_tolerance
    assert ok("09:00-17:00", "09:00-17:00", 0)
    assert not ok("09:00-17:00", "09:00-17:02", 0)       # χωρίς ευελιξία: ακριβώς
    assert ok("09:00-17:00", "09:20-17:20", 30)
    assert ok("09:00-17:00", "09:10-17:05", 30)          # έφυγε πριν συμπληρώσει
    assert not ok("09:00-17:00", "09:35-17:35", 30)      # πέρα από τα 30΄
    assert not ok("09:00-17:00", "08:50-17:00", 30)      # νωρίτερα: ποτέ
    assert not ok("09:00-17:00", "09:10-17:20", 30)      # 10΄ αργότερα -> 20΄ πέρα
    assert not ok("09:00-17:00", "09:00-16:55", 30)      # πρόωρη αποχώρηση
    assert ok("09:00-13:00+17:00-21:00", "09:15-13:15+17:10-21:15", 30)
