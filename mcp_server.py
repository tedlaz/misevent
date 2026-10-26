"""
MCP server (ΜΟΝΟ ΑΝΑΓΝΩΣΗ, stdio) — `uv run --extra mcp -m mcp_server`.

Λεπτά περιτυλίγματα των `webapp/queries.py`: ό,τι βλέπει ήδη το UI, ανά
εταιρεία. Ξεχωριστή διεργασία δίπλα στο web UI, στον ίδιο `MISEVENT_DATA_DIR`.

➜ ΓΙΑΤΙ `rebuild_all()` ΣΕ ΚΑΘΕ ΚΛΗΣΗ: τα projections αυτής της διεργασίας
δεν ακούν τα events που γράφει το web UI — το replay τα φέρνει στο σήμερα.
"""

import json
from datetime import date

from flask import Flask
from mcp.server.mcpserver import MCPServer
from pydantic_core import to_jsonable_python

from webapp import queries as q
from webapp.system import registry, set_tenant, svc

mcp = MCPServer("misevent")
_app = Flask(__name__)


def _dumps(o) -> str:
    # pydantic_core (έρχεται με το mcp): dataclasses, date, set ΚΑΙ κλειδιά
    # dict που είναι date — το json.dumps(default=...) δεν πιάνει τα κλειδιά.
    return json.dumps(to_jsonable_python(o, fallback=str), ensure_ascii=False)


def _run(company_id: str, fn):
    # ponytail: replay ανά κλήση — αν αργεί σε μεγάλη βάση, rebuild μόνο όταν
    # αλλάζει το event_count.
    with _app.test_request_context():  # χωρίς g.user -> ο guard δεν σκάει
        set_tenant(company_id).rebuild_all()
        return _dumps(fn())


@mcp.tool()
def companies() -> str:
    """Οι ενεργές εταιρείες (company_id + επωνυμία). Ξεκίνα από εδώ."""
    rows = [{"company_id": c.company_id, "name": registry.display_name(c.company_id)}
            for c in registry.auth.active_companies()]
    return json.dumps(rows, ensure_ascii=False)


@mcp.tool()
def persons(company_id: str, search: str = "") -> str:
    """Πρόσωπα με τις απασχολήσεις τους. `search`: ΑΦΜ/ΑΜΚΑ/ΑΜΑ ή όνομα."""
    return _run(company_id, lambda: q.search_persons(search) if search else q.person_rows())


@mcp.tool()
def employments(company_id: str, person_id: str = "") -> str:
    """Απασχολήσεις (με υποβληθείσα πρόσληψη), νεότερες πρώτα."""
    return _run(company_id, lambda: q.employment_rows(person_id or None))


@mcp.tool()
def obligations(company_id: str, ref_date: str = "", view: str = "active") -> str:
    """Υποχρεώσεις. `ref_date` ISO (default σήμερα), `view`: active | done | all."""
    ref = date.fromisoformat(ref_date) if ref_date else date.today()
    return _run(company_id, lambda: q.obligation_list(ref, view=view))


@mcp.tool()
def payroll(company_id: str, year: int, month: int, preview: bool = False) -> str:
    """Γραμμές μισθοδοσίας του μήνα: κλεισμένες, ή προβλεπόμενες με `preview`."""
    return _run(company_id, lambda: q.payroll_rows(year, month, preview))


@mcp.tool()
def apd(company_id: str, year: int, month: int) -> str:
    """ΑΠΔ του μήνα ανά ταμείο: {ταμείο: {rows, totals, error}}."""
    def build():
        try:
            funds = svc.build_apd_rows(year, month)
        except ValueError as exc:
            return {"error": str(exc)}
        out = {}
        for fund in funds:
            rows, totals, error = q.apd_rows(year, month, fund)
            out[fund] = {"rows": rows, "totals": totals, "error": error}
        return out
    return _run(company_id, build)


if __name__ == "__main__":
    mcp.run()
