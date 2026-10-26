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
