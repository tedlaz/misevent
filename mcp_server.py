"""
MCP server (stdio) — `uv run --extra mcp -m mcp_server`.

Λεπτά περιτυλίγματα των `webapp/queries.py`: ό,τι βλέπει ήδη το UI, ανά
εταιρεία. Ξεχωριστή διεργασία δίπλα στο web UI, στον ίδιο `MISEVENT_DATA_DIR`.
ΕΓΓΡΑΦΕΣ μόνο από τα ίδια commands με το UI (`run_command`: lock,
transaction, ίδιοι έλεγχοι) — σήμερα ημερήσιο πρόγραμμα και άδειες.
⚠️ ΝΕΟ εργαλείο εγγραφής -> και στο `permissions.ask` του
`.claude/settings.json`, ώστε το Claude Code να ζητά ΠΑΝΤΑ επιβεβαίωση.

➜ ΔΥΟ ΔΙΕΡΓΑΣΙΕΣ, ΜΙΑ ΒΑΣΗ: ό,τι γράφει η μία δεν το ακούν τα projections
της άλλης — το `set_tenant` κάνει replay όταν αλλάξει το πλήθος των events
(`Tenant.sync`), και εδώ και στο web UI.
"""

import json
from datetime import date, datetime

from flask import Flask
from mcp.server.mcpserver import MCPServer
from pydantic_core import to_jsonable_python

from misevent.domain.events import LeaveType
from webapp import queries as q
from webapp.helpers import run_command
from webapp.system import registry, set_tenant, svc

mcp = MCPServer("misevent")
_app = Flask(__name__)


def _dumps(o) -> str:
    # pydantic_core (έρχεται με το mcp): dataclasses, date, set ΚΑΙ κλειδιά
    # dict που είναι date — το json.dumps(default=...) δεν πιάνει τα κλειδιά.
    return json.dumps(to_jsonable_python(o, fallback=str), ensure_ascii=False)


def _run(company_id: str, fn):
    with _app.test_request_context():  # χωρίς g.user -> ο guard δεν σκάει
        set_tenant(company_id)
        return _dumps(fn())


def _write(company_id: str, fn) -> str:
    """Εντολή όπως από το UI: {"ok", "message"} — το ValueError γίνεται μήνυμα."""
    def go():
        ok, msg = run_command(fn)
        return {"ok": ok, "message": msg}
    return _run(company_id, go)


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


@mcp.tool()
def staff(company_id: str) -> str:
    """Προσωπικό ΤΩΡΑ: κατάσταση/επόμενη βάρδια, υπόλοιπο κανονικής άδειας και
    «χωράει;» ως 31/12 (`fit_slack` < 0 = δεν χωράει, χωρίς περιόδους αιχμής)."""
    now = datetime.now()
    return _run(company_id, lambda: q.staff_now(now.date(), now.hour * 60 + now.minute))


@mcp.tool()
def certificates(company_id: str, person_id: str, year: int = 0) -> str:
    """Βεβαιώσεις προσώπου: ετήσια αποδοχών του `year` (default πέρσι) και
    εργασίας/προϋπηρεσίας. Ό,τι δεν βγαίνει -> {"error": ...}."""
    def build():
        out = {}
        for key, fn in (
            ("tax", lambda: q.tax_certificate(person_id, year or date.today().year - 1,
                                              date.today())),
            ("employment", lambda: q.employment_certificate(person_id)),
        ):
            try:
                out[key] = fn()
            except ValueError as exc:
                out[key] = {"error": str(exc)}
        return out
    return _run(company_id, build)


@mcp.tool()
def set_day_schedule(company_id: str, employment_id: str, day: str, hours: str) -> str:
    """ΑΛΛΑΓΗ ημερήσιου προγράμματος ΜΙΑΣ ημέρας (`day` ISO). `hours`:
    «15:00-21:00», σπαστό «09:00-13:00+17:00-21:00», τηλεεργασία «Τ09:00-17:00»,
    κενό = ρεπό. Ελέγχει ανάπαυση 11ω και κλεισμένη περίοδο. ΔΕΝ υποβάλλεται
    στο ΕΡΓΑΝΗ — μένει εκκρεμής δήλωση στο UI («Προγράμματα»)."""
    def cmd():
        svc.set_daily_schedule(employment_id, {date.fromisoformat(day): hours.strip()})
        return f"Πρόγραμμα {day}: {hours.strip() or 'ρεπό'} — εκκρεμεί δήλωση ΕΡΓΑΝΗ"
    return _write(company_id, cmd)


@mcp.tool()
def record_leave(company_id: str, employment_id: str, leave_type: str,
                 date_from: str, date_to: str, notes: str = "") -> str:
    """ΚΑΤΑΧΩΡΗΣΗ άδειας/απουσίας (ISO, `date_to` συμπεριλαμβάνεται).
    `leave_type`: ANNUAL (κανονική) | SICK (ασθένεια) | UNPAID (άνευ αποδοχών)
    | MATERNITY | PARENTAL | CHILDCARE. «Λείπει 2 μέρες από αύριο» =
    date_from αύριο, date_to μεθαύριο."""
    def cmd():
        try:
            kind = LeaveType[leave_type.strip().upper()]
        except KeyError:
            raise ValueError(f"Άγνωστος τύπος άδειας {leave_type}") from None
        start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
        if kind == LeaveType.ANNUAL:  # ίδια διαδρομή με το UI (views/leaves.py)
            svc.plan_annual_leave(employment_id, start, end, notes)
        else:
            svc.record_leave(employment_id, kind, start, end, notes)
        return f"Καταχωρήθηκε {kind.name} {date_from} έως {date_to}"
    return _write(company_id, cmd)


if __name__ == "__main__":
    mcp.run()
