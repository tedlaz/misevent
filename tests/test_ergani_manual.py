"""
ΔΥΟ ΤΡΟΠΟΙ ΥΠΟΒΟΛΗΣ ΣΤΟ ΕΡΓΑΝΗ: REST API ή χειροκίνητα στο portal.

Όλα περνούν από το `ServiceBase._ergani_submit`:
  - `ergani_preview()`  -> `ErganiPreview` με το payload, ΚΑΝΕΝΑ event·
  - `ergani_manual(ref)` -> καμία κλήση, `status="manual"` με το πρωτόκολλο
    του χρήστη, και ΙΔΙΕΣ συνέπειες με το API (αναγγελία, κάλυψη ΟΧΕ).

Εκτέλεση: uv run pytest tests/test_ergani_manual.py
"""

from datetime import date, timedelta

import _bootstrap  # noqa: F401
import pytest
from test_exit_settlement import FULL_WEEK, PEOPLE, make_svc

from misevent.app.services import ErganiPreview, ergani_manual, ergani_preview


class _NoNetwork:
    """Client που σκάει αν κληθεί: η χειροκίνητη υποβολή ΔΕΝ μιλά στο API."""

    environment, simulate = "trial", False

    def submit(self, *_args, **_kwargs):
        raise AssertionError("κλήση στο API σε χειροκίνητη υποβολή")


def _pending_hire(svc, schedule_mode="fixed"):
    """Απασχόληση με ασφαλιστικό προφίλ, ΧΩΡΙΣ αναγγελία πρόσληψης."""
    last, first, afm, amka, ama = PEOPLE[0]
    pid = svc.register_person(
        last_name=last, first_name=first, father_name="Ν", mother_name="Μ",
        birth_date=date(1990, 5, 5), afm=afm, amka=amka, ama=ama,
    )
    eid = svc.start_employment(pid, date(2026, 3, 2), monthly_gross=1500.0,
                               weekly_pattern=FULL_WEEK, schedule_mode=schedule_mode,
                               schedule_weeks=1)
    svc.assign_insurance_profile(eid, coverages=(
        {"fund": "efka_main", "kad": "0000", "specialty_code": "000000", "kpk": "0101"},
    ))
    return eid


def test_preview_returns_the_payload_and_writes_nothing():
    svc, proj, _ = make_svc()
    eid = _pending_hire(svc)
    before = svc.store.event_count()
    with ergani_preview(), pytest.raises(ErganiPreview) as hit:
        svc.submit_hire_e3(eid)
    assert hit.value.doc_type == "WebE3N" and hit.value.payload
    assert svc.store.event_count() == before
    assert not proj.employments[eid].hire_submitted


def test_manual_hire_records_the_protocol_with_the_same_effects():
    svc, proj, _ = make_svc()
    svc.ergani = _NoNetwork()
    eid = _pending_hire(svc)
    with pytest.raises(ValueError, match="πρωτοκόλλου"), ergani_manual("  "):
        svc.submit_hire_e3(eid)
    with ergani_manual("ΑΠ-123"):
        svc.submit_hire_e3(eid)
    last = proj.ergani_submissions[-1]
    assert (last.doc_type, last.status, last.reference, last.environment) == (
        "WebE3N", "manual", "ΑΠ-123", "portal")
    assert proj.employments[eid].hire_submitted
    # και η ΟΧΕ χειροκίνητα: καλύπτει τις ημέρες όπως η υποβολή μέσω API
    with ergani_preview(), pytest.raises(ErganiPreview) as hit:
        svc.submit_weekly_schedules({eid: [date(2026, 3, 2)]})
    assert hit.value.endpoint == "WTOWeek"
    with ergani_manual("ΑΠ-124"):
        svc.submit_weekly_schedules({eid: [date(2026, 3, 2)]})
    emp = proj.employments[eid]
    assert not emp.pending_weekly
    assert emp.day_status(date(2026, 3, 3)) == "submitted"


def test_manual_daily_schedule_declares_the_days():
    svc, proj, _ = make_svc()
    svc.ergani = _NoNetwork()
    eid = _pending_hire(svc, "variable")
    with ergani_manual("ΑΠ-1"):
        svc.submit_hire_e3(eid)
    days = [date(2026, 3, 2) + timedelta(days=i) for i in range(7)]
    with ergani_preview(), pytest.raises(ErganiPreview) as hit:
        svc.submit_schedule_days({eid: days})
    assert hit.value.endpoint == "WTODaily"
    with ergani_manual("ΑΠ-2"):
        res = svc.submit_schedule_days({eid: days})
    assert res["batch_no"] == 1
    assert all(proj.employments[eid].day_status(d) == "submitted" for d in days)
    assert proj.employments[eid].days_declared_here


def test_screen_preview_modal_and_manual_submission(fresh_system):
    """Οθόνη ΕΡΓΑΝΗ: «JSON»/«✍️ Χειροκίνητα» ανοίγουν MODAL με τα πεδία (τίτλοι
    από τα σχήματα του ΕΡΓΑΝΗ) — η προεπισκόπηση δεν γράφει τίποτα· η
    καταχώριση πρωτοκόλλου ολοκληρώνει την υποβολή ως χειροκίνητη."""
    from webapp import create_app
    from webapp.actions import FULL_TIME
    from webapp.clock import today
    from webapp.system import proj, store, svc

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200
    pid = svc.register_person(
        last_name="ΝΕΟΣ", first_name="ΕΡΓΑΖΟΜΕΝΟΣ", father_name="Π",
        mother_name="Μ", birth_date=date(1990, 5, 5),
        afm="090034337", amka="05059012350", ama="123456788")
    with app.test_request_context("/"):
        start = today().replace(day=1)
    eid = svc.start_employment(pid, start, monthly_gross=1300.0, weekly_pattern=FULL_TIME)
    svc.assign_specialty(eid, "ΠΩΛΗΤΗΣ", auxiliary_scheme="efka")

    page = c.get("/ergani/").get_data(as_text=True)
    assert f"/ergani/preview/e3/{eid}?tab=json" in page and 'target="_blank"' not in page

    before = store.event_count()
    modal = c.get(f"/ergani/preview/e3/{eid}").get_data(as_text=True)
    assert "ΑΡΙΘΜΟΣ ΦΟΡΟΛΟΓΙΚΟΥ ΜΗΤΡΩΟΥ" in modal       # τίτλος από το σχήμα
    assert "090034337" in modal and 'name="reference"' in modal
    assert store.event_count() == before                 # καμία εγγραφή

    c.post(f"/ergani/e3/{eid}", data={"reference": "ΑΠ-555"})
    last = proj.ergani_submissions[-1]
    assert (last.status, last.reference) == ("manual", "ΑΠ-555")
    assert proj.employments[eid].hire_submitted
    assert "Χειροκίνητα (portal)" in c.get("/ergani/").get_data(as_text=True)

    # ΟΧΕ: η επιλογή της φόρμας προγράμματος -> modal με δικό της πρωτόκολλο
    start_iso = proj.employments[eid].pending_weekly[0][0].isoformat()
    sel = {"weekly": f"{eid}|{start_iso}"}
    modal = c.post("/ergani/preview/schedules", data=sel).get_data(as_text=True)
    assert 'name="weekly_reference"' in modal and f'value="{eid}|{start_iso}"' in modal
    c.post("/ergani/schedules", data={**sel, "weekly_reference": "ΑΠ-556"})
    assert not proj.employments[eid].pending_weekly
    assert proj.ergani_submissions[-1].status == "manual"
