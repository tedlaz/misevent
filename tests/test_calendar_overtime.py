"""Ημερολόγιο εργαζομένου: οι ώρες πέραν ωραρίου βγαίνουν από την ΙΔΙΑ
κατάταξη με τη μισθοδοσία — όχι από πρόχειρο «>9ω = υπερωρία».

Εκτέλεση: uv run pytest tests/test_calendar_overtime.py
"""

from test_card_tolerance import BIG, SMALL, _month


def test_calendar_uses_payroll_classification(fresh_system):
    from webapp import create_app
    from webapp.views.employments import _calendar_day, _over_hours

    _svc, proj, eid = _month(10)  # SMALL 09:03-17:07, BIG 09:00-19:00
    e = proj.employments[eid]
    with create_app().test_request_context():
        over = _over_hours(e, 2026, 2)
        small = [i for i in _calendar_day(e, SMALL, over) if i[0] == "overtime"]
        big = [i for i in _calendar_day(e, BIG, over) if i[0] == "overtime"]
    assert small == []  # λεπτά εντός ανοχής: τίποτα
    assert sum(int(h[1:].split(":")[0]) * 60 + int(h.split(":")[1])
               for _k, h, _l in big) == 120  # οι 2 ώρες, όπως στη μισθοδοσία
