"""
MCP server (stdio) — `uv run --extra mcp -m mcp_server`.

Λεπτά περιτυλίγματα των `webapp/queries.py`: ό,τι βλέπει ήδη το UI, ανά
εταιρεία. Ξεχωριστή διεργασία δίπλα στο web UI, στον ίδιο `MISEVENT_DATA_DIR`.
ΕΓΓΡΑΦΕΣ μόνο από τα ίδια commands με το UI (`run_command`: lock,
transaction, ίδιοι έλεγχοι) — σήμερα ημερήσιο πρόγραμμα και άδειες.
ΣΕΝΑΡΙΑ (read-only, `gross_for_net`, `annual_cost`): ίδιος `payroll_engine`·
όταν λείπει στοιχείο που αλλάζει το ποσό επιστρέφουν `{"questions": [...]}`.
⚠️ ΝΕΟ εργαλείο εγγραφής -> και στο `permissions.ask` του
`.claude/settings.json`, ώστε το Claude Code να ζητά ΠΑΝΤΑ επιβεβαίωση.

➜ ΔΥΟ ΔΙΕΡΓΑΣΙΕΣ, ΜΙΑ ΒΑΣΗ: ό,τι γράφει η μία δεν το ακούν τα projections
της άλλης — το `set_tenant` κάνει replay όταν αλλάξει το πλήθος των events
(`Tenant.sync`), και εδώ και στο web UI.
"""

import json
from calendar import monthrange
from datetime import date, datetime

from flask import Flask
from mcp.server.mcpserver import MCPServer
from pydantic_core import to_jsonable_python

from misevent.domain import payroll_engine as pe
from misevent.domain.events import LeaveType
from misevent.rules import config
from webapp import queries as q
from webapp.helpers import run_command
from webapp.system import proj, registry, set_tenant, svc

mcp = MCPServer("misevent")
_app = Flask(__name__)


def _dumps(o) -> str:
    # pydantic_core (έρχεται με το mcp): dataclasses, date, set ΚΑΙ κλειδιά
    # dict που είναι date — το json.dumps(default=...) δεν πιάνει τα κλειδιά.
    return json.dumps(to_jsonable_python(o, fallback=str), ensure_ascii=False)


def _run(company_id: str, fn):
    with _app.test_request_context():  # χωρίς g.user -> ο guard δεν σκάει
        set_tenant(company_id)
        try:
            return _dumps(fn())
        except ValueError as exc:  # ελληνικό μήνυμα κανόνα -> απάντηση, όχι crash
            return _dumps({"error": str(exc)})


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


# ------------------------------------------- Σενάρια αμοιβής (read-only)
# Όταν λείπει στοιχείο που αλλάζει το ποσό, το tool ΔΕΝ μαντεύει: επιστρέφει
# {"questions": [...]} — το Claude τις κάνει στον χρήστη και ξανακαλεί.
_ASK_WHO = ("Αφορά υπάρχοντα εργαζόμενο; Αν ναι, ποιον (employment_id από το "
            "`employments`) — τότε ποσοστά/τέκνα/ηλικία βγαίνουν από τα στοιχεία του.")
_ASK_PERSONAL = (
    "Αν είναι υποθετικός: πόσα εξαρτώμενα τέκνα έχει;",
    "Ημερομηνία γέννησης; (έως 30 ετών ισχύει μειωμένος φόρος — κενό = άνω των 30)",
)


def _month_end(d: date) -> date:
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def _who(employment_id: str, children: int | None, birth_date: str, on: date):
    """Ποσοστά/τέκνα/ηλικία από την απασχόληση, ή από τις απαντήσεις του
    χρήστη (γενικά ποσοστά ΕΦΚΑ). None = πρέπει να ρωτηθεί."""
    if employment_id:
        emp = proj.employments.get(employment_id)
        if emp is None:
            raise ValueError(f"Άγνωστη απασχόληση {employment_id}")
        notes = ["Ποσοστά από τα ΚΠΚ της απασχόλησης"]
        if emp.pay_type == "daily":
            notes.append("Ημερομίσθιος: τα ποσά είναι μηνιαίο ισοδύναμο μισθωτού")
        return {"emp": emp, "rates": svc.contribution_rates(emp.insurance_profile, on),
                "children": emp.children_on(on) if children is None else children,
                "birth": emp.birth_date, "assumptions": notes}
    if children is None:
        return None
    return {"emp": None, "rates": None, "children": children,
            "birth": date.fromisoformat(birth_date) if birth_date else None,
            "assumptions": ["Γενικά ποσοστά ΕΦΚΑ μισθωτών — χωρίς ΤΕΚΑ/ΒΑΕ/ειδικά ΚΠΚ "
                            "(για ακρίβεια: employment_id)"]
            + ([] if birth_date else ["Φόρος ενηλίκου άνω των 30"])}


def _amounts(results) -> dict:
    """Σύνολα πολλών `PayrollResult` — κόστος = μικτά + εισφορές εργοδότη."""
    out = {k: round(sum(getattr(r, k) for r in results), 2)
           for k in ("gross", "efka_employee", "fmy", "net", "efka_employer")}
    out["cost"] = round(out["gross"] + out["efka_employer"], 2)
    return out


@mcp.tool()
def gross_for_net(company_id: str, net: float, employment_id: str = "",
                  children: int | None = None, birth_date: str = "",
                  on: str = "") -> str:
    """ΜΙΚΤΟΣ μηνιαίος μισθός για ΣΤΟΧΟ καθαρών («να παίρνει 1200 καθαρά»).
    Υπάρχων εργαζόμενος: `employment_id`. Υποθετικός: `children` (+ προαιρετικά
    `birth_date` ISO). `on` ISO = μήνας κανόνων (default σήμερα). Πλήρης μήνας,
    χωρίς άδειες/προσαυξήσεις/επιδοτήσεις. Αν η απάντηση έχει `questions`,
    ΡΩΤΑ τον χρήστη και ξανακάλεσε με τις απαντήσεις."""
    def build():
        if net <= 0:
            raise ValueError("Τα καθαρά πρέπει να είναι θετικό ποσό")
        end = _month_end(date.fromisoformat(on) if on else date.today())
        who = _who(employment_id, children, birth_date, end)
        if who is None:
            return {"questions": [_ASK_WHO, *_ASK_PERSONAL]}

        def calc(cents: int):
            return pe.calc_monthly(cents / 100, who["children"], end,
                                   rates=who["rates"], birth_date=who["birth"])

        # Τα καθαρά αυξάνουν μονότονα με τα μικτά -> δυαδική αναζήτηση σε λεπτά:
        # ο ΜΙΚΡΟΤΕΡΟΣ μισθός που δίνει τουλάχιστον τα ζητούμενα καθαρά.
        lo = hi = round(net * 100)
        while calc(hi).net < net:
            hi *= 2
        while lo < hi:
            mid = (lo + hi) // 2
            lo, hi = (lo, mid) if calc(mid).net >= net else (mid + 1, hi)
        out = _amounts([calc(lo)])
        floor = config.minimum_wage_for(end).monthly
        if out["gross"] < floor:
            who["assumptions"].append(
                f"⚠️ Κάτω από τον κατώτατο μισθό ({floor:.2f} €) για πλήρη απασχόληση")
        return {**out, "period": f"{end.month}/{end.year}",
                "assumptions": who["assumptions"],
                "next": "Ετήσιο κόστος: annual_cost με αυτό το monthly_gross"}
    return _run(company_id, build)


@mcp.tool()
def annual_cost(company_id: str, employment_id: str = "",
                monthly_gross: float | None = None, children: int | None = None,
                birth_date: str = "", year: int = 0) -> str:
    """ΕΤΗΣΙΟ ΚΟΣΤΟΣ εργοδότη (μικτά + εισφορές εργοδότη) ανά είδος: μισθοί,
    Δώρα, Επίδομα Αδείας. Υπάρχων εργαζόμενος (`employment_id`): ό,τι
    υπολογίστηκε + πρόβλεψη έως 31/12, ίδιο με την καρτέλα αποδοχών.
    Υποθετικός: `monthly_gross` + `children` -> πλήρες έτος (12 μισθοί + 2 Δώρα
    + Επίδομα). Από καθαρά; πρώτα `gross_for_net`. Αν η απάντηση έχει
    `questions`, ΡΩΤΑ τον χρήστη και ξανακάλεσε με τις απαντήσεις."""
    def build():
        y = year or date.today().year
        if employment_id:
            emp = proj.employments.get(employment_id)
            if emp is None:
                raise ValueError(f"Άγνωστη απασχόληση {employment_id}")
            rows = q.payroll_ledger(y, {employment_id})
            if y >= date.today().year and emp.termination_date is None:
                rows += q._forecast_rows(emp, y)
            labels = dict(q.LEDGER_KINDS)
            return {
                "year": y,
                "by_kind": {labels[k]: q.ledger_totals([r for r in rows if r["kind"] == k])
                            for k in labels if any(r["kind"] == k for r in rows)},
                "totals": q.ledger_totals(rows),
                "forecast_cost": round(sum(r["cost"] for r in rows if r.get("forecast")), 2),
                "assumptions": ["Πρόβλεψη: σύμβαση σε ισχύ, πλήρεις μήνες χωρίς "
                                "άδειες/προσαυξήσεις — καμία αποχώρηση"],
            }
        if monthly_gross is None:
            return {"questions": [
                _ASK_WHO,
                "Αν είναι υποθετικός: ποιος ο μικτός μηνιαίος μισθός; (αν ξέρεις "
                "μόνο τα καθαρά, θα τον βρω πρώτα)",
            ]}
        who = _who("", children, birth_date, date(y, 1, 31))
        if who is None:
            return {"questions": list(_ASK_PERSONAL)}
        ch, b, hire = who["children"], who["birth"], date(y - 2, 1, 1)
        parts = {
            "Μισθοί (12)": [pe.calc_monthly(monthly_gross, ch, _month_end(date(y, m, 1)),
                                           birth_date=b) for m in range(1, 13)],
            "Δώρο Πάσχα": [pe.calc_easter_gift(monthly_gross, ch, y, hire, birth_date=b)],
            "Δώρο Χριστουγέννων": [pe.calc_christmas_gift(monthly_gross, ch, y, hire,
                                                          birth_date=b)],
            "Επίδομα Αδείας": [pe.calc_leave_allowance(monthly_gross, ch, y, hire,
                                                       birth_date=b, full_year=True)],
        }
        return {"year": y, "by_kind": {k: _amounts(v) for k, v in parts.items()},
                "totals": _amounts([r for v in parts.values() for r in v]),
                "assumptions": who["assumptions"]
                + ["Πλήρες έτος, πλήρης απασχόληση, χωρίς άδειες άνευ αποδοχών"]}
    return _run(company_id, build)


if __name__ == "__main__":
    mcp.run()
