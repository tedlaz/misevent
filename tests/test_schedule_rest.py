"""
ΟΡΙΑ ΑΝΑΜΕΣΑ ΣΕ ΔΥΟ ΠΑΡΟΥΣΙΕΣ σε ΚΑΘΕ αλλαγή προγράμματος:
11 ώρες ημερήσια ανάπαυση (και στα όρια με τις γειτονικές ημέρες) και
3 ώρες κενό στο σπαστό ωράριο.

Εκτέλεση: python tests/test_schedule_rest.py
"""

from datetime import date

from test_closed_period import setup  # 1/2026 κλειστός, 09-17 Δε–Πα

WEEK = ("MON", "TUE", "WED", "THU", "FRI")


def refused(fn, *args) -> bool:
    try:
        fn(*args)
    except ValueError:
        return True
    return False


def test_daily_changes_respect_rest_and_split_gap():
    svc, _proj, eid = setup()
    mon, tue = date(2026, 3, 2), date(2026, 3, 3)
    assert refused(svc.set_daily_schedule, eid, {mon: "14:00-23:00"})  # 10ω ως Τρ 09
    assert refused(svc.set_daily_schedule, eid, {mon: "09:00-13:00+14:00-18:00"})
    assert not refused(svc.set_daily_schedule, eid, {tue: "06:00-14:00"})  # 13ω


def test_weekly_changes_respect_rest_at_both_edges():
    svc, _proj, eid = setup()
    bad = {"MON": "14:00-23:00", "TUE": "06:00-14:00", "WED": "09:00-17:00",
           "THU": "09:00-17:00", "FRI": "09:00-17:00"}
    assert refused(svc.set_schedule, eid, date(2026, 3, 2), bad)  # μέσα στο μοτίβο

    early = {d: "07:00-15:00" for d in WEEK}
    svc.set_daily_schedule(eid, {date(2026, 3, 16): "12:00-20:00"})
    svc.set_daily_schedule(eid, {date(2026, 3, 15): "16:00-23:00"})  # Κυριακή
    # η 16/3 είχε ημερήσιο 12-20, αλλά το νέο εβδομαδιαίο το ΣΒΗΝΕΙ: ελέγχεται
    # το 07:00 του μοτίβου -> 8ω μετά την Κυριακή 23:00
    assert refused(svc.set_schedule, eid, date(2026, 3, 16), early)

    late = {d: "15:00-23:00" for d in WEEK}
    svc.set_daily_schedule(eid, {date(2026, 3, 28): "07:00-15:00"})  # Σάββατο
    # λήξη Παρασκευή 27/3 23:00 -> Σάββατο 07:00 = 8ω (ημερήσιο μετά τη λήξη)
    assert refused(svc.set_schedule, eid, date(2026, 3, 23), late, date(2026, 3, 27))
