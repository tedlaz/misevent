"""
ΔΗΛΩΣΗ ΑΔΕΙΩΝ ΣΤΟ ΕΡΓΑΝΗ (WTOLeave) — απολογιστικά, έως το τέλος του
επόμενου μήνα.

- Η καταχώρηση άδειας γεννά LEAVE_DECLARATION για τον μήνα της, που περιμένει
  την έγκριση παρουσιών.
- Δηλώνονται ΜΟΝΟ οι ημέρες προγράμματος, με κωδικό ΕΡΓΑΝΗ, έτος αναφοράς και
  δικαιούμενες ημέρες (κανονική άδεια), σε μορφή που περνά το σχήμα.
- Η δηλωμένη άδεια ΚΛΕΙΔΩΝΕΙ: ούτε ακύρωση ούτε τροποποίηση.
- Νέα (ξεχασμένη) άδεια σε ήδη δηλωμένο μήνα -> νέα υποχρέωση.

Εκτέλεση: uv run pytest tests/test_leave_declaration.py
"""

import json
from datetime import date

from test_closed_period import refuses, setup
from test_ergani_schemas import _valid

from misevent.domain import dependencies as deps
from misevent.domain.events import LeaveType


def _decl(proj, month):
    return [o for o in proj.obligations.values()
            if o.obligation_type == "LEAVE_DECLARATION" and o.period_month == month]


def test_leave_declaration_flow():
    svc, proj, eid = setup()  # 1/2026 κλεισμένη, πρόγραμμα 09-17 Δε–Πα
    # Πέμπτη 5/2 – Δευτέρα 9/2: εργάσιμες 5, 6, 9 (το Σ/Κ είναι ρεπό)
    lid = svc.record_leave(eid, LeaveType.ANNUAL, date(2026, 2, 5), date(2026, 2, 9))
    [ob] = _decl(proj, 2)
    assert ob.due_date == date(2026, 3, 31)
    index = deps.build_index(proj.obligations.values())
    assert deps.unmet_prerequisites(ob, index)       # περιμένει την έγκριση

    proposal = svc.leave_declaration_proposal(2026, 2)[eid]
    assert sorted(proposal) == [date(2026, 2, 5), date(2026, 2, 6), date(2026, 2, 9)]
    code, year, req = proposal[date(2026, 2, 5)]
    assert (code, year) == ("ΑΔΚΑΝ", 2026) and req == svc.annual_leave_entitlement(eid, 2026)

    svc.approve_attendance(2026, 2)
    res = svc.submit_leave_declaration(2026, 2)
    assert (res["count"], res["employees"]) == (3, 1)
    assert proj.obligations[ob.obligation_id].fulfilled
    assert svc.leave_declaration_proposal(2026, 2) == {}

    sent = [s for s in proj.ergani_submissions if s.doc_type == "WTOLeave"]
    payload = json.loads(sent[-1].payload)
    assert _valid("WTOLeave", payload) == []
    rows = payload["WTOS"]["WTO"][0]["Ergazomenoi"]["ErgazomenoiWTO"]
    first = rows[0]["ErgazomenosAnalytics"]["ErgazomenosWTOAnalytics"][0]
    assert rows[0]["f_date"] == "05/02/2026"
    assert first["f_type"] == "ΑΔΚΑΝ" and first["f_year"] == "2026"
    assert first["f_req_days"] == f"{req:03d}"
    # Ιστορικό ΕΡΓΑΝΗ: το «Αφορά» βγαίνει από τα ΑΦΜ του payload (μία κλήση
    # για πολλούς -> κενό employee_id), όχι «ΕΤΑΙΡΕΙΑ»
    from webapp.views.ergani import _wto_afms

    assert _wto_afms(sent[-1].payload) == [proj.employments[eid].afm]

    # Η δηλωμένη άδεια κλειδώνει
    assert svc.leave_declared_to_ergani(eid, lid)
    assert "ΕΡΓΑΝΗ" in refuses(svc.cancel_leave, eid, lid)
    assert "ΕΡΓΑΝΗ" in refuses(svc.reschedule_leave, eid, lid, LeaveType.ANNUAL,
                               date(2026, 2, 5), date(2026, 2, 6))

    # Ξεχασμένη άδεια ασθενείας στον ίδιο μήνα -> νέα υποχρέωση, μόνο οι νέες ημέρες
    svc.record_leave(eid, LeaveType.SICK, date(2026, 2, 16), date(2026, 2, 16))
    assert [o.fulfilled for o in _decl(proj, 2)].count(False) == 1
    assert svc.leave_declaration_proposal(2026, 2) == {
        eid: {date(2026, 2, 16): ("ΑΔΑΣ", None, None)}}


def test_cancelled_before_declaration_closes_empty():
    svc, proj, eid = setup()
    lid = svc.record_leave(eid, LeaveType.UNPAID, date(2026, 2, 10), date(2026, 2, 10))
    svc.cancel_leave(eid, lid)                       # πριν τη δήλωση: επιτρέπεται
    svc.approve_attendance(2026, 2)
    assert svc.submit_leave_declaration(2026, 2)["count"] == 0
    assert all(o.fulfilled for o in _decl(proj, 2))
