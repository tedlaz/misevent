"""
ΑΔΕΙΕΣ ΜΕΣΑ ΣΤΟ «ΠΡΟΓΡΑΜΜΑ ΕΡΓΑΣΙΑΣ».

Η σελίδα δείχνει το υπόλοιπο κανονικής άδειας ανά μήνα (ίδιο `_accrual` με
το πλάνο αδειών) και δημιουργεί άδεια από επιλεγμένες ημέρες: το modal
νέας άδειας με `back=work` αποθηκεύει και επιστρέφει το panel του
ΠΡΟΓΡΑΜΜΑΤΟΣ, όχι του πλάνου αδειών.

⚠️ Ζητά `fresh_system`: στήνει εταιρεία και γράφει άδειες.
"""


def test_work_schedule_leaves(fresh_system):
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app
    from webapp.clock import today
    from webapp.system import proj

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert c.post("/seed").status_code == 200

    with app.test_request_context("/"):
        year = today().year
    eid = next(e.employment_id for e in proj.employments.values() if e.active)

    page = c.get(f"/work-schedules/?year={year}").get_data(as_text=True)
    assert "leave-accrual-strip" in page and "Υπόλοιπο" in page

    modal = c.get(
        f"/leaves/{eid}/new?date={year}-12-14&date_to={year}-12-16"
        f"&year={year}&back=work"
    ).get_data(as_text=True)
    assert "#work-schedule-panel" in modal
    assert f'value="{year}-12-16"' in modal

    r = c.post(
        f"/leaves/{eid}/new?year={year}&back=work",
        data={"leave_type": "ANNUAL", "date_from": f"{year}-12-14",
              "date_to": f"{year}-12-16", "notes": ""},
    )
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'id="work-schedule-panel"' in html, "επέστρεψε λάθος panel"
    assert "has-leave" in html and "data-leave-modal-url" in html

    # Το πλάνο αδειών μένει όπως ήταν (χωρίς back)
    assert "Ετήσιο πλάνο" in c.get(f"/leaves/?year={year}").get_data(as_text=True)
