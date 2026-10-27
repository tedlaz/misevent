"""MCP εγγραφές (πρόγραμμα ημέρας, άδεια) + συγχρονισμός ΔΥΟ διεργασιών στην
ίδια βάση (`Tenant.sync`): ό,τι γράφει η μία, το βλέπει η άλλη.

⚠️ Ζητά `fresh_system`: στήνει εταιρεία (seed).
"""

import json
import sys
from datetime import timedelta

import pytest

pytest.importorskip("mcp")


def test_mcp_writes_and_sync(fresh_system):
    import _bootstrap

    from webapp import create_app
    from webapp.system import proj, registry
    from webapp.tenancy import Registry

    app = create_app()
    assert _bootstrap.seed(_bootstrap.ui_client(app)).status_code == 200
    sys.modules.pop("mcp_server", None)
    import mcp_server

    cid = json.loads(mcp_server.companies())[0]["company_id"]
    with app.test_request_context("/"):
        e = next(e for e in proj.employments.values()
                 if e.lifecycle == "active" and e.termination_date is None)
    eid = e.employment_id
    day = max(e.hire_date, mcp_server.date.today()) + timedelta(days=40)

    r = json.loads(mcp_server.set_day_schedule(cid, eid, day.isoformat(), "15:00-21:00"))
    assert r["ok"], r
    assert e.day_schedule(day) == "15:00-21:00"
    bad = json.loads(mcp_server.set_day_schedule(cid, eid, day.isoformat(), "25:00-26:00"))
    assert not bad["ok"]

    # «άλλη διεργασία»: δεύτερο μητρώο στην ίδια βάση γράφει άδεια ασθενείας
    other = Registry(registry.root).tenant(cid)
    sick = day + timedelta(days=1)
    other.svc.record_leave(eid, mcp_server.LeaveType.SICK, sick, sick + timedelta(days=1))
    with app.test_request_context("/"):
        mcp_server.set_tenant(cid)  # sync -> replay
        assert any(lv.date_from == sick for lv in proj.employments[eid].active_leaves())

    # και ο έλεγχος επικάλυψης δουλεύει πάνω στα φρέσκα δεδομένα
    dup = json.loads(mcp_server.record_leave(cid, eid, "sick", sick.isoformat(),
                                             sick.isoformat()))
    assert not dup["ok"]
    assert not json.loads(mcp_server.record_leave(cid, eid, "xx", "2026-01-01",
                                                  "2026-01-01"))["ok"]
    sys.modules.pop("mcp_server", None)
