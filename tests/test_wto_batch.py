"""
Πρόγραμμα εργασίας (WTO) για ΠΟΛΛΟΥΣ εργαζομένους σε ΜΙΑ κλήση του API.

Το σχήμα WTOWeek/WTODaily έχει `WTOS.WTO[]` (δηλώσεις: παράρτημα + περίοδος)
και μέσα σε κάθε μία `ErgazomenoiWTO[]` (εργαζόμενοι) — η υποβολή ΔΕΝ
«σπάει» ανά εργαζόμενο. Απόρριψη = ΚΑΝΕΝΑΣ δεν καλύπτεται.

Εκτέλεση: uv run pytest tests/test_wto_batch.py
"""

import json
from datetime import date

import _bootstrap  # noqa: F401
import pytest
from test_exit_settlement import hire, make_svc

from misevent.integrations import ergani as ergani_mod

WED = date(2026, 11, 4)


def _counting(svc):
    """Μετρά τις κλήσεις του client (simulate) — (endpoint, payload)."""
    calls, real = [], svc.ergani.submit

    def submit(doc_type, payload, endpoint=""):
        calls.append((endpoint, payload))
        return real(doc_type, payload, endpoint)
    svc.ergani.submit = submit
    return calls


def _two():
    svc, proj, _ = make_svc()
    return svc, proj, hire(svc, proj, 0), hire(svc, proj, 1)


def test_weekly_for_two_employees_is_one_call():
    svc, proj, e1, e2 = _two()
    calls = _counting(svc)
    svc._declare_weekly([(e1, None, None, None), (e2, None, None, None)])
    assert [c[0] for c in calls] == ["WTOWeek"]
    decl = calls[0][1]["WTOS"]["WTO"]
    assert len(decl) == 1, "ίδιο παράρτημα + περίοδος -> ΜΙΑ δήλωση"
    afms = {r["f_afm"] for r in decl[0]["Ergazomenoi"]["ErgazomenoiWTO"]}
    assert afms == {proj.employments[e1].afm, proj.employments[e2].afm}
    # ΕΝΑ event υποβολής για όλους (κενό employee_id) + κάλυψη ανά εργαζόμενο
    assert proj.ergani_submissions[-1].doc_type == "WTO"
    assert proj.ergani_submissions[-1].employee_id == ""
    for eid in (e1, e2):
        assert proj.employments[eid].standing_history


def test_daily_for_two_employees_is_one_call_one_batch():
    svc, proj, e1, e2 = _two()
    svc._declare_weekly([(e1, None, None, None), (e2, None, None, None)])
    for eid in (e1, e2):
        svc.set_daily_schedule(eid, {WED: "10:00-18:00"})
    calls = _counting(svc)
    res = svc.submit_schedule_days({e1: [WED], e2: [WED]})
    assert [c[0] for c in calls] == ["WTODaily"]
    assert res["count"] == 2 and res["employees"] == 2
    rows = calls[0][1]["WTOS"]["WTO"][0]["Ergazomenoi"]["ErgazomenoiWTO"]
    assert len(rows) == 2
    for eid in (e1, e2):
        assert proj.employments[eid].day_status(WED) == "submitted"
    assert len(proj.schedule_batches[res["batch_no"]]["employees"]) == 2


def test_rejection_covers_nobody():
    svc, proj, e1, e2 = _two()
    svc._declare_weekly([(e1, None, None, None), (e2, None, None, None)])
    for eid in (e1, e2):
        svc.set_daily_schedule(eid, {WED: "10:00-18:00"})

    def reject(doc_type, payload, endpoint=""):
        raise ergani_mod.ErganiError("Σφάλμα ΕΡΓΑΝΗ")
    svc.ergani.submit = reject
    with pytest.raises(ValueError, match="ΑΠΕΡΡΙΨΕ"):
        svc.submit_schedule_days({e1: [WED], e2: [WED]})
    for eid in (e1, e2):
        assert proj.employments[eid].day_status(WED) == "modified"


def test_merge_keeps_different_periods_as_separate_declarations():
    a = {"WTOS": {"WTO": [{"f_aa_pararthmatos": "0", "f_from_date": "01/11/2026",
                           "f_to_date": "", "Ergazomenoi": {"ErgazomenoiWTO": [{"f_afm": "1"}]}}]}}
    b = json.loads(json.dumps(a))
    b["WTOS"]["WTO"][0]["Ergazomenoi"]["ErgazomenoiWTO"] = [{"f_afm": "2"}]
    c = json.loads(json.dumps(a))
    c["WTOS"]["WTO"][0]["f_from_date"] = "09/11/2026"
    merged = ergani_mod.merge_wto([a, b, c])["WTOS"]["WTO"]
    assert [[r["f_afm"] for r in w["Ergazomenoi"]["ErgazomenoiWTO"]] for w in merged] == [
        ["1", "2"], ["1"]]
    # το αρχικό payload δεν αλλοιώνεται (λίστες αντιγράφονται)
    assert len(a["WTOS"]["WTO"][0]["Ergazomenoi"]["ErgazomenoiWTO"]) == 1
