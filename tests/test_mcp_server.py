"""MCP server: τα tools απαντούν με έγκυρο JSON πάνω σε καθαρό σύστημα."""

import json
import sys

import pytest

pytest.importorskip("mcp")


def test_tools_return_json(fresh_system):
    sys.modules.pop("mcp_server", None)  # ξανά import πάνω στο νέο webapp
    import mcp_server

    companies = json.loads(mcp_server.companies())
    assert companies and companies[0]["company_id"]
    cid = companies[0]["company_id"]
    assert json.loads(mcp_server.persons(cid)) == []
    assert json.loads(mcp_server.employments(cid)) == []
    assert isinstance(json.loads(mcp_server.obligations(cid, view="all")), list)
    assert json.loads(mcp_server.payroll(cid, 2026, 1)) == []
    assert json.loads(mcp_server.staff(cid)) == []
    assert "error" in json.loads(mcp_server.certificates(cid, "x"))["tax"]
    sys.modules.pop("mcp_server", None)


def test_dumps_date_keys():
    # EmploymentState.attendance = {date: ώρες} — έσκαγε το json.dumps
    import dataclasses
    from datetime import date

    import mcp_server

    @dataclasses.dataclass
    class S:
        d: date
        attendance: dict
        tags: set

    out = json.loads(mcp_server._dumps([S(date(2026, 1, 2), {date(2026, 1, 2): 8.0}, {"a"})]))
    assert out == [{"d": "2026-01-02", "attendance": {"2026-01-02": 8.0}, "tags": ["a"]}]


def test_pay_scenarios_hypothetical(fresh_system):
    """Μικτά από καθαρά & ετήσιο κόστος υποθετικού: πρώτα ΕΡΩΤΗΣΕΙΣ, μετά ποσά."""
    sys.modules.pop("mcp_server", None)
    import mcp_server

    cid = json.loads(mcp_server.companies())[0]["company_id"]
    assert "questions" in json.loads(mcp_server.gross_for_net(cid, 1200))
    assert "questions" in json.loads(mcp_server.annual_cost(cid))
    assert "questions" in json.loads(mcp_server.annual_cost(cid, monthly_gross=1500))
    assert "error" in json.loads(mcp_server.gross_for_net(cid, -5, children=0))

    for ch, birth in ((0, ""), (2, ""), (0, "2003-05-01")):
        r = json.loads(mcp_server.gross_for_net(cid, 1200, children=ch,
                                                birth_date=birth, on="2026-03-15"))
        assert 1200 <= r["net"] <= 1200.01, r
        assert r["cost"] == round(r["gross"] + r["efka_employer"], 2)
        # ο ΜΙΚΡΟΤΕΡΟΣ μισθός: ένα λεπτό λιγότερο δεν φτάνει τα 1200
        less = mcp_server.pe.calc_monthly(r["gross"] - 0.01, ch, mcp_server.date(2026, 3, 31),
                                          birth_date=mcp_server.date.fromisoformat(birth)
                                          if birth else None)
        assert less.net < 1200

    a = json.loads(mcp_server.annual_cost(cid, monthly_gross=r["gross"], children=0,
                                          year=2026))
    assert set(a["by_kind"]) == {"Μισθοί (12)", "Δώρο Πάσχα", "Δώρο Χριστουγέννων",
                                 "Επίδομα Αδείας"}
    assert a["totals"]["cost"] == round(sum(k["cost"] for k in a["by_kind"].values()), 2)
    assert a["totals"]["cost"] > 14 * r["gross"]  # 14 μισθοί + εισφορές εργοδότη
    assert "error" in json.loads(mcp_server.annual_cost(cid, employment_id="x"))
    sys.modules.pop("mcp_server", None)
