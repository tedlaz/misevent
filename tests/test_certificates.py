"""
ΒΕΒΑΙΩΣΕΙΣ ΕΡΓΑΖΟΜΕΝΟΥ: ετήσια αποδοχών (φορολογική) & εργασίας.

Τα ποσά της ετήσιας = το καθολικό καταβολών ΟΛΩΝ των προσλήψεων του
προσώπου (`payroll_ledger`) — καθαρό = ακαθάριστα − κρατήσεις − φόρος.

⚠️ Ζητά `fresh_system`: στήνει εταιρεία (seed).
"""


def test_certificates(fresh_system):
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app
    from webapp import queries as q
    from webapp.system import proj

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200
    # μία περίοδος υπολογισμένη (ίδια διαδρομή με το test_cost_report)
    with app.test_request_context("/"):
        y, m = q.next_payroll_period()
        sheet = q.attendance_sheet(y, m)
    data = {"eid": [r["eid"] for r in sheet["rows"]]}
    for r in sheet["rows"]:
        for field in ("worked", "unpaid", "leave"):
            data[f"{field}_{r['eid']}"] = r[field]
        data[f"efka_{r['eid']}"] = r["efka_days"]
    assert c.post(f"/attendance/approve?y={y}&m={m}", data=data).status_code == 200
    assert c.post("/payroll/run", data={"y": y, "m": m}).status_code == 200
    p = proj.payrolls[0]
    emp = proj.employments[p.employee_id]
    person, year = emp.person, p.period_year
    with app.test_request_context("/"):
        from webapp.clock import today

        cert = q.tax_certificate(person.person_id, year, today())
        assert all(b <= today() for _a, b in cert["periods"])
        ledger = q.ledger_totals(q.payroll_ledger(year, set(person.employment_ids)))
        assert cert["totals"] == ledger
        t = cert["totals"]
        assert abs(t["gross"] - t["efka_employee"] - t["fmy"] - t["net"]) < 0.05
        assert cert["periods"] and cert["days"] > 0
        assert sum(k["gross"] for k in cert["kinds"]) == t["gross"]

        ec = q.employment_certificate(person.person_id)
        assert ec["periods"][0]["date_from"] == min(
            proj.employments[e].hire_date for e in person.employment_ids)

    # το κουμπί 📄 της λίστας ανοίγει modal με τα δύο links
    assert f"/persons/{person.person_id}/certificates" in c.get("/persons/").text
    modal = c.get(f"/persons/{person.person_id}/certificates")
    assert modal.status_code == 200 and "certificate/employment" in modal.text

    base = f"/persons/{person.person_id}/certificate"
    for url in (f"{base}/tax?year={year}", f"{base}/employment"):
        r = c.get(url)
        assert r.status_code == 200 and r.data[:4] == b"%PDF", url
    assert c.get(f"{base}/tax?year=1990").status_code == 404  # καμία καταβολή
    assert c.get(f"{base}/other").status_code == 404
