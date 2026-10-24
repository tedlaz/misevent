"""
ΑΔΕΙΕΣ & ΠΡΟΓΡΑΜΜΑ ΣΤΑ «ΖΩΝΤΑΝΑ» + οθόνη ΕΡΓΑΝΗ.

Οι άδειες δημιουργούνται/επεξεργάζονται από τα Ζωντανά (modal → κενό σώμα +
`lb-refresh`). Οι αλλαγές προγράμματος (μαζική αλλαγή στα Ζωντανά)
συγκεντρώνονται στην οθόνη `/ergani/`, από όπου αναιρούνται ή
στέλνονται στο ΕΡΓΑΝΗ — και μένουν στο ιστορικό αποστολών.

⚠️ Ζητά `fresh_system`: στήνει εταιρεία και γράφει άδειες/προγράμματα.
"""

import re
from datetime import timedelta


def test_schedule_changes_go_through_ergani_screen(fresh_system):
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app
    from webapp.clock import today
    from webapp.system import proj

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    with app.test_request_context("/"):
        ref = today()
    e = next(e for e in proj.employments.values() if e.active and e.hire_submitted)
    eid = e.employment_id
    # Τετάρτη σε 2 εβδομάδες: μελλοντική, εντός ορίζοντα της οθόνης
    day = ref - timedelta(days=ref.weekday()) + timedelta(days=16)
    monday = day - timedelta(days=day.weekday())
    old = e.day_schedule(day)
    new = "10:00-18:00" if old != "10:00-18:00" else "09:00-17:00"

    assert c.get("/").get_data(as_text=True).count("/ergani/") >= 1
    assert "Μαζική αλλαγή" in c.get("/work-schedules/range").get_data(as_text=True)
    r = c.post("/work-schedules/range", data={
        "eid": eid, "date_from": day.isoformat(), "date_to": day.isoformat(),
        "hours": new})
    assert r.status_code == 200 and r.get_data(as_text=True) == "", r.headers
    assert "lb-refresh" in r.headers["HX-Trigger"]
    assert e.day_schedule(day) == new

    assert c.get("/work-schedules/").status_code == 302  # → οθόνη ΕΡΓΑΝΗ
    page = c.get("/ergani/").get_data(as_text=True)
    assert f'value="{eid}|{monday.isoformat()}"' in page
    assert new in page

    # Το seed έχει δηλωμένο πρόγραμμα -> η αλλαγή είναι «τροποποιημένη»
    assert e.day_status(day) == "modified"
    # ΑΝΑΙΡΕΣΗ: επαναφορά στη δηλωμένη τιμή
    c.post("/ergani/schedules/revert",
           data={"week": f"{eid}|{monday.isoformat()}"})
    assert e.day_schedule(day) == old
    c.post("/work-schedules/range", data={
        "eid": eid, "date_from": day.isoformat(), "date_to": day.isoformat(),
        "hours": new})

    # ΥΠΟΒΟΛΗ όλων των εκκρεμών (χρονολογικά — καμία τρύπα)
    weeks = re.findall(r'name="week" checked\s+value="([^"]+)"', page)
    assert weeks
    html = c.post("/ergani/schedules", data={"week": weeks}).get_data(as_text=True)
    assert e.day_status(day) == "submitted"
    # ΕΝΑ ιστορικό: η υποβολή WTO κουβαλά και το περιεχόμενο (batch + ημέρες)
    assert "Ιστορικό υποβολών" in html and "Ημερήσιο #" in html
    assert f"{day.strftime('%d/%m/%Y')}: {new}" in html
    assert f'value="{eid}|{monday.isoformat()}"' not in html


def test_work_schedule_leaves(fresh_system):
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app
    from webapp.clock import today
    from webapp.system import proj

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    with app.test_request_context("/"):
        year = today().year
    eid = next(e.employment_id for e in proj.employments.values() if e.active)

    # ΖΩΝΤΑΝΑ (μήνας: μενού τύπου άδειας): κενό σώμα (κλείνει το modal) +
    # εντολή ανανέωσης του grid
    assert 'id="lb-leave-menu"' in c.get("/").get_data(as_text=True)
    for kind, d0, d1 in (("ANNUAL", 14, 16), ("SICK", 21, 22)):
        r = c.post(
            f"/leaves/{eid}/new",
            data={"leave_type": kind, "date_from": f"{year}-12-{d0}",
                  "date_to": f"{year}-12-{d1}"},
        )
        assert r.status_code == 200 and r.get_data(as_text=True) == ""
        assert "lb-refresh" in r.headers["HX-Trigger"]
    assert any(lv.date_from.isoformat() == f"{year}-12-21"
               for lv in proj.employments[eid].active_leaves())
    # Διήμερη άδεια = ΜΙΑ συνεχόμενη μπάρα, όχι μία ανά ημέρα
    rows = c.get(f"/live/rows?base={year}-12-15&days=15").get_data(as_text=True)
    assert rows.count("lb-leaveband lv-astheneia") == 1

    # ΕΠΕΞΕΡΓΑΣΙΑ: κλικ στη ζώνη (data-lid) -> modal -> αποθήκευση στα Ζωντανά
    lid = re.search(r'lb-leaveband lv-astheneia" data-lid="([^"]+)"', rows)[1]
    modal = c.get(f"/leaves/{eid}/{lid}/modal").get_data(as_text=True)
    assert 'hx-target="#modal-host"' in modal and f'value="{year}-12-22"' in modal
    r = c.post(f"/leaves/{eid}/{lid}", data={
        "leave_type": "SICK", "date_from": f"{year}-12-21",
        "date_to": f"{year}-12-23", "notes": ""})
    assert r.get_data(as_text=True) == "" and "lb-refresh" in r.headers["HX-Trigger"]
    assert any(lv.leave_id == lid and lv.date_to.day == 23
               for lv in proj.employments[eid].active_leaves())

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
