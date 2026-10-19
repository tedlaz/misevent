"""Διαγραφή απασχόλησης που καταχωρήθηκε ΕΚ ΠΑΡΑΔΡΟΜΗΣ (§2).

ΔΕΝ είναι αποχώρηση: η σχέση δεν υπήρξε ποτέ. Το event log μένει άθικτο —
η αφαίρεση γίνεται με αντισταθμιστικό `EmploymentDeleted`, οπότε το replay
δίνει ακριβώς το ίδιο αποτέλεσμα.
"""

from datetime import date

import _bootstrap  # noqa: F401
from _bootstrap import ui_client

from misevent.app.services import PayrollService
from misevent.domain.events import ErganiSubmissionRecorded
from misevent.domain.projections import Projections
from misevent.persistence.database import Database

WEEK = {day: "09:00-17:00" for day in ("MON", "TUE", "WED", "THU", "FRI")}


def _system(db=":memory:"):
    store = Database(db).store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    pid = svc.register_person(
        last_name="ΔΟΚΙΜΗ", first_name="ΜΑΡΙΑ", father_name="ΙΩΑΝΝΗΣ",
        mother_name="ΕΛΕΝΗ", birth_date=date(1990, 5, 5),
        afm="111222336", amka="05059012343",
    )
    return store, proj, svc, pid




def test_delete_removes_the_employment_from_projections_and_tables(tmp_path):
    db = str(tmp_path / "del.db")
    store, proj, svc, pid = _system(db)
    eid = svc.start_employment(
        pid, date(2026, 3, 1), monthly_gross=1500, weekly_pattern=WEEK,
        draft=True,   # όπως το UI: η καταχώρηση δεν έχει φύγει στο ΕΡΓΑΝΗ
    )

    svc.delete_employment(eid, reason="λάθος καταχώρηση")

    assert eid not in proj.employments
    assert proj.persons[pid].employment_ids == []   # το ΠΡΟΣΩΠΟ μένει

    # Το event log ΔΕΝ πειράχτηκε — απλώς απέκτησε το αντισταθμιστικό event
    types = [type(ev).__name__ for _agg, ev in store.load(eid)]
    assert "EmploymentStarted" in types and types[-1] == "EmploymentDeleted"


def test_rebuild_after_delete_gives_the_same_result(tmp_path):
    """Κανόνας #3: η προβολή πρέπει να ξαναχτίζεται από το log."""
    db = str(tmp_path / "rebuild.db")
    store, proj, svc, pid = _system(db)
    eid = svc.start_employment(
        pid, date(2026, 3, 1), monthly_gross=1500, weekly_pattern=WEEK,
        draft=True,   # όπως το UI: η καταχώρηση δεν έχει φύγει στο ΕΡΓΑΝΗ
    )
    svc.delete_employment(eid)

    replay = Projections()
    for _agg, ev in store.load():
        replay.handle(_agg, ev)

    assert eid not in replay.employments
    assert replay.persons[pid].employment_ids == []


def test_delete_is_refused_after_ergani_or_payroll():
    store, proj, svc, pid = _system()
    eid = svc.start_employment(
        pid, date(2026, 3, 1), monthly_gross=1500, weekly_pattern=WEEK,
        draft=True,   # όπως το UI: η καταχώρηση δεν έχει φύγει στο ΕΡΓΑΝΗ
    )
    store.append(eid, ErganiSubmissionRecorded(
        doc_type="WebE3N", employee_id=eid, payload="{}", status="simulated",
        reference="X1",
    ))
    try:
        svc.delete_employment(eid)
    except ValueError as exc:
        assert "ΕΡΓΑΝΗ" in str(exc), exc
    else:
        raise AssertionError("δεν έπρεπε να διαγραφεί υποβληθείσα απασχόληση")


def test_pending_hire_is_not_active_and_cannot_terminate():
    """ΕΚΚΡΕΜΗΣ πρόσληψη (χωρίς αναγγελία Ε3): ΔΕΝ είναι «Ενεργή» και ΔΕΝ
    αποχωρεί — η σωστή διόρθωση είναι η ΔΙΑΓΡΑΦΗ (§5.7α)."""
    store, proj, svc, pid = _system()
    eid = svc.start_employment(
        pid, date(2026, 3, 2), monthly_gross=1500, weekly_pattern=WEEK,
    )
    emp = proj.employments[eid]
    assert emp.hire_submitted is False
    assert emp.lifecycle == "pending"  # ΟΧΙ "active", παρότι δεν έχει λήξει
    try:
        svc.terminate(eid, date(2026, 4, 30))
    except ValueError as exc:
        assert "δεν έχει ολοκληρωθεί" in str(exc), exc
    else:
        raise AssertionError("δεν έπρεπε να αποχωρήσει εκκρεμής πρόσληψη")

    # Με την αναγγελία η σχέση υπάρχει: η απασχόληση γίνεται ΕΝΕΡΓΗ (και
    # επιδέχεται πλέον αποχώρηση — η εκκαθάρισή της ελέγχεται αλλού)
    store.append(eid, ErganiSubmissionRecorded(
        doc_type="WebE3N", employee_id=eid, payload="{}", status="simulated",
        reference="X1",
    ))
    assert emp.hire_submitted is True and emp.lifecycle == "active"


def test_delete_button_and_route(monkeypatch):
    """Το κόκκινο «Διαγραφή» δίπλα στο «Άκυρο» — και μόνο πριν το ΕΡΓΑΝΗ."""
    from webapp import create_app
    from webapp.views import employments as employment_views

    store, proj, svc, pid = _system()
    eid = svc.start_employment(
        pid, date(2026, 3, 1), monthly_gross=1500, weekly_pattern=WEEK,
        draft=True,
    )
    monkeypatch.setattr(employment_views, "proj", proj)
    monkeypatch.setattr(employment_views, "svc", svc)
    client = ui_client(create_app())

    html = client.get(f"/employments/{eid}/hire-form").get_data(as_text=True)
    assert "Διαγραφή" in html and f"/employments/{eid}/delete" in html

    resp = client.post(f"/employments/{eid}/delete")
    assert resp.status_code == 200
    assert resp.headers["HX-Redirect"] == "/persons/"
    assert eid not in proj.employments

    # Δεύτερη προσπάθεια: δεν υπάρχει πια -> σφάλμα ως toast, όχι 500
    again = client.post(f"/employments/{eid}/delete")
    assert again.status_code == 200 and "HX-Redirect" not in again.headers
    import json
    flash = json.loads(again.headers["HX-Trigger"])["flash"]
    assert flash["err"] is True and "Άγνωστη απασχόληση" in flash["msg"]


def test_delete_rejects_unknown_employment():
    _store, _proj, svc, _pid = _system()
    try:
        svc.delete_employment("ΑΝΥΠΑΡΚΤΗ")
    except ValueError as exc:
        assert "Άγνωστη απασχόληση" in str(exc)
    else:
        raise AssertionError("άγνωστη απασχόληση πρέπει να απορρίπτεται")
