"""
Οθόνη «Ημερολόγιο» (`/employments/calendar`): το ημερολόγιο εργαζομένου
εκτός καρτέλας. Η αλλαγή προγράμματος ημέρας από το modal εκεί ΔΕΝ μπορεί να
στοχεύσει την #employment-card (δεν υπάρχει) — ανανεώνει modal + ημερολόγιο.

⚠️ Ζητά `fresh_system`: στήνει εταιρεία και χρήστες.
"""

from datetime import timedelta


def test_calendar_page(fresh_system):
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app
    from webapp.clock import today
    from webapp.system import proj

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    e = next(e for e in proj.employments.values() if e.hire_submitted and e.active)
    eid = e.employment_id
    page = c.get(f"/employments/calendar?eid={eid}").get_data(as_text=True)
    assert f"/employments/{eid}/calendar" in page and 'id="modal-host"' in page
    card = c.get(f"/employments/{eid}").get_data(as_text=True)
    assert f"/employments/calendar?eid={eid}" in card

    with app.test_request_context("/"):
        d = today() + timedelta(days=7)
    from_page = {"HX-Request": "true",
                 "HX-Current-URL": "http://localhost/employments/calendar"}
    modal = c.get(f"/employments/{eid}/calendar/day?d={d}", headers=from_page)
    assert 'name="cal"' in modal.get_data(as_text=True)
    on_card = {"HX-Request": "true",
               "HX-Current-URL": f"http://localhost/employments/{eid}"}
    other = c.get(f"/employments/{eid}/calendar/day?d={d}",
                  headers=on_card).get_data(as_text=True)
    assert 'name="cal"' not in other and "#employment-card" in other
    # modal ημερολογίου από τις Παρουσίες: ούτε εκεί υπάρχει #employment-card
    att = c.get(f"/employments/{eid}/calendar/day?d={d}",
                headers={"HX-Request": "true",
                         "HX-Current-URL": "http://localhost/attendance"}).get_data(as_text=True)
    assert 'name="cal"' in att

    r = c.post(f"/employments/{eid}/schedule/daily", headers=from_page,
               data={"week": d.isoformat(), "cal": "1",
                     f"hours_{d.isoformat()}": "10:00-14:00"})
    assert r.status_code == 200
    assert "ecal-refresh" in r.headers.get("HX-Trigger", "")
    assert 'id="day-modal"' in r.get_data(as_text=True)
    assert e.day_schedule(d).replace(" ", "") == "10:00-14:00"

    # Τηλεεργασία: ξεχωριστή κατηγορία/χρώμα στο ημερολόγιο
    c.post(f"/employments/{eid}/schedule/daily", headers=from_page,
           data={"week": d.isoformat(), "cal": "1", f"hours_{d.isoformat()}": "Τ10:00-14:00"})
    cal = c.get(f"/employments/{eid}/calendar?m={d:%Y-%m}").get_data(as_text=True)
    assert 'ecal-ev plan tele' in cal and "ΤΗΛΕΕΡΓΑΣΙΑ" in cal

    # Σφάλμα: ΜΕΝΕΙ στο modal της ημέρας (εδώ δεν υπάρχει #daily-modal)
    r = c.post(f"/employments/{eid}/schedule/daily", headers=from_page,
               data={"week": d.isoformat(), "cal": "1", f"hours_{d.isoformat()}": "άκυρο"})
    assert "HX-Retarget" not in r.headers and '"err": true' in r.headers["HX-Trigger"]
    assert 'id="day-modal"' in r.get_data(as_text=True)
