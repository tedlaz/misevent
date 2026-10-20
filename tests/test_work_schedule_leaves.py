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

    # ΖΩΝΤΑΝΑ (μήνας: μενού τύπου άδειας): back=live -> κενό σώμα (κλείνει
    # το modal) + εντολή ανανέωσης του grid
    assert 'id="lb-leave-menu"' in c.get("/live/").get_data(as_text=True)
    r = c.post(
        f"/leaves/{eid}/new",
        data={"leave_type": "SICK", "date_from": f"{year}-12-21",
              "date_to": f"{year}-12-22", "back": "live"},
    )
    assert r.status_code == 200 and r.get_data(as_text=True) == ""
    assert "lb-refresh" in r.headers["HX-Trigger"]
    assert any(lv.date_from.isoformat() == f"{year}-12-21"
               for lv in proj.employments[eid].active_leaves())
    # Διήμερη άδεια = ΜΙΑ συνεχόμενη μπάρα, όχι μία ανά ημέρα
    rows = c.get(f"/live/rows?base={year}-12-15&days=15").get_data(as_text=True)
    assert rows.count("lb-leaveband lv-astheneia") == 1

    # ΥΠΟΛΟΙΠΟ ΚΑΝΟΝΙΚΗΣ ΑΔΕΙΑΣ στο τέλος κάθε ημέρας: η άδεια 14–16/12 το
    # μειώνει ακριβώς κατά τις εργάσιμές της· στο έτος μία τιμή ανά μήνα
    from datetime import date, timedelta

    from misevent.rules.calendar_gr import working_days_in_range
    from webapp import queries as q

    with app.test_request_context("/"):
        d0 = date(year, 12, 13)
        row = next(r for r in q.live_board(d0, d0, 4)["rows"] if r["eid"] == eid)
        taken = [a["taken"] for a in row["accr"]]
        assert len(taken) == 4
        assert taken[3] - taken[0] == working_days_in_range(
            date(year, 12, 14), date(year, 12, 16))
        y0 = date(year, 1, 1)
        row = next(r for r in q.live_board(d0, y0, 365)["rows"] if r["eid"] == eid)
        assert all((a["date"] + timedelta(days=1)).day == 1 for a in row["accr"])
        assert row["accr"][-1]["date"] == date(year, 12, 31)

        # ΣΕ ΕΠΙΠΕΔΟ ΗΜΕΡΑΣ: αυξάνει σταδιακά μέσα στον μήνα (όχι ολόκληρη η
        # μερίδα από την 1η) και στο τέλος του μήνα = ο κανόνας του service
        from calendar import monthrange

        from webapp.system import svc
        e = proj.employments[eid]
        acc = [q._accrued_on(e, y0 + timedelta(days=i)) for i in range(365)
               if y0 + timedelta(days=i) <= date(year, 12, 31)]
        assert acc == sorted(acc)
        assert all(isinstance(a, int) for a in acc)  # μόνο ώριμες ολόκληρες
        # 3ο έτος: ΟΛΟ το δικαίωμα από την 1/1
        y3 = date(e.hire_date.year + 2, 1, 1)
        assert q._accrued_on(e, y3) == svc.annual_leave_entitlement(eid, y3.year)
        for m in range(1, 13):
            end = date(year, m, monthrange(year, m)[1])
            if e.hire_date <= end:
                assert q._accrued_on(e, end) == svc.annual_leave_accrued(eid, end)
        if (e.hire_date.year == year and e.hire_date.month < 12
                and e.hire_date.day < 20):
            # 1η ημέρα εργασίας: μόνο κλάσμα της μηνιαίας μερίδας
            assert q._accrued_on(e, e.hire_date) < svc.annual_leave_accrued(
                eid, e.hire_date)
