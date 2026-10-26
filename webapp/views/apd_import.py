"""
Οδηγός «Εισαγωγή από ΑΠΔ» (μενού Σύστημα): ΑΠΔ → εταιρεία, ειδικότητες,
εργαζόμενοι, πρόγραμμα → έλεγχος → εισαγωγή.

Η λογική ζει στο `misevent/app/apd_import.py`· εδώ μόνο οι οθόνες και το
draft ανάμεσά τους. ΤΙΠΟΤΑ δεν γράφεται πριν το τελικό «Εισαγωγή», και αυτό
μόνο αν ο δοκιμαστικός έλεγχος (`dry_run`) βγει καθαρός.
"""

import uuid
from datetime import date

from flask import Blueprint, abort, redirect, render_template, request, session, url_for

from misevent.app import apd_import as core
from misevent.integrations import ergani_parameters
from misevent.integrations.apd_files import APDFormatError, parse_apd
from misevent.persistence.event_store import transaction

from .. import clock
from ..system import auth, lock, registry
from .auth import current_user
from .companies import may_create

bp = Blueprint("apd_import", __name__, url_prefix="/apd-import")

# ponytail: drafts στη μνήμη της διεργασίας — χάνονται σε restart (ο χρήστης
# ξανανεβάζει την ΑΠΔ). Σε πίνακα της βάσης αν γίνει πρόβλημα.
_DRAFTS: dict[str, dict] = {}

STEPS = (
    ("upload", "① Αρχείο ΑΠΔ"),
    ("company", "② Εταιρεία & ειδικότητες"),
    ("employees", "③ Εργαζόμενοι"),
    ("schedule", "④ Πρόγραμμα εργασίας"),
    ("check", "⑤ Έλεγχος & εισαγωγή"),
)
PERSON_FIELDS = ("children", "sex", "email", "phone", "nationality_code",
                 "identity_type", "identity_number", "identity_issuer",
                 "identity_issue_date", "identity_expiry_date")
# Πεδία προς συμπλήρωση (ό,τι ΔΕΝ δίνει η ΑΠΔ). Τα στοιχεία της ΑΠΔ
# (ταυτότητες, ημερομίσθιος/μηνιαίος, πενθήμερο/εξαήμερο κ.λπ.) είναι κλειδωμένα.
COMPANY_FIELDS = ("activity",)
BRANCH_FIELDS = ("sepe_code", "oaed_code", "kad_code", "kallikratis_code")
HIRE_FIELDS = ("hire_date", "amount", "weekly_hours",
               "employment_relationship", "fixed_term_to", "service_years",
               "frozen_service_years", "married", "extra_allowances_pct")


def _draft():
    d = _DRAFTS.get(session.get("apd_import", ""))
    if d is None:
        abort(redirect(url_for("apd_import.index")))
    return d


def _page(step, **ctx):
    return render_template("apd_import.html", step=step, steps=STEPS, **ctx)


def _company_of(user, afm):
    """Υπάρχουσα (προσβάσιμη) εταιρεία με αυτό το ΑΦΜ, ή None."""
    for c in auth.companies_for(user):
        t = registry.tenant(c.company_id)
        if t.proj.company and t.proj.company.afm == afm:
            return c.company_id
    return None


def _any_svc():
    """Ένα service μόνο για ανάγνωση των ΚΟΙΝΩΝ (Μητρώο ΚΠΚ)."""
    return registry.tenant(next(iter(registry.auth.companies))).svc


@bp.get("/")
def index():
    return _page("upload")


@bp.post("/")
def upload():
    user = current_user()
    efka_f, teka_f = request.files.get("efka"), request.files.get("teka")
    try:
        if efka_f is None or not efka_f.filename:
            raise APDFormatError("Επιλέξτε το αρχείο ΑΠΔ ΕΦΚΑ")
        efka = parse_apd(efka_f.read())
        if efka.fund != "efka":
            raise APDFormatError("Το πρώτο αρχείο είναι ΑΠΔ ΤΕΚΑ — δώστε την ΑΠΔ ΕΦΚΑ")
        teka = parse_apd(teka_f.read()) if teka_f and teka_f.filename else None
        need = core.check_teka(efka, teka)
        if need and teka is None:
            return _page("upload", need_teka=len(need), error=(
                f"{len(need)} ασφαλισμένοι έχουν επικουρική στο ΤΕΚΑ — "
                "δώστε και την ΑΠΔ ΤΕΚΑ της ίδιας περιόδου"))
        company_id = _company_of(user, efka.employer.afm)
        if company_id is None and not may_create(user):
            raise APDFormatError("Η εταιρεία της ΑΠΔ δεν υπάρχει και δεν έχετε "
                                 "δικαίωμα δημιουργίας νέας")
    except APDFormatError as exc:
        return _page("upload", error=str(exc))
    svc = registry.tenant(company_id).svc if company_id else _any_svc()
    draft = core.draft_from_apd(efka, svc)
    if company_id is None:  # νέα εταιρεία: κανένα πρόσωπο δεν «υπάρχει»
        for e in draft["employees"]:
            e["exists"] = False
    draft["company_id"] = company_id
    token = session.get("apd_import") or uuid.uuid4().hex
    session["apd_import"] = token
    _DRAFTS[token] = draft
    return redirect(url_for("apd_import.step", name="company"))


@bp.get("/<name>")
def step(name):
    if name not in {s for s, _ in STEPS[1:]}:
        abort(404)
    draft = _draft()
    ctx = {}
    if name == "check":
        ctx["errors"] = _dry_run(draft)
    if name == "employees":
        ctx["nationalities"] = ergani_parameters.nationalities()
        ctx["identity_types"] = ergani_parameters.identity_types()
    if name == "company" and not draft["company_id"]:
        ctx["catalogs"] = {
            f"{kind}_code" if kind != "koinotites" else "kallikratis_code":
                ergani_parameters.catalog(kind)
            for kind in ("sepe", "oaed", "kad", "koinotites")
        }
    if name == "employees":
        y, m = draft["period"]
        ctx["apd_next"] = date(y + m // 12, m % 12 + 1, 1)
        ctx["payroll_start"] = draft.setdefault(
            "payroll_start", {"mode": "next", "month": ""})
    return _page(name, d=draft, days=core.DAYS, error=request.args.get("error"), **ctx)


@bp.post("/<name>")
def save(name):
    draft, f = _draft(), request.form
    if name == "company":
        # ΜΟΝΟ όσα δεν δίνει η ΑΠΔ — τα υπόλοιπα είναι κλειδωμένα και δεν
        # διαβάζονται καν από τη φόρμα (αλλιώς θα άλλαζαν με πλαστό POST).
        for k in COMPANY_FIELDS:
            draft["company"][k] = f.get(f"c_{k}", draft["company"][k]).strip()
        for k in BRANCH_FIELDS:
            draft["branch"][k] = f.get(f"b_{k}", draft["branch"][k]).strip()
        for i, sp in enumerate(draft["specialties"]):
            sp["name"] = f.get(f"sp{i}_name", "").strip()
            # Το ζεύγος κωδικός/περιγραφή ΜΟΝΟ από το μητρώο (όχι ό,τι πληκτρολογήθηκε)
            code = f.get(f"sp{i}_ergani_specialty_code", "").strip()
            row = ergani_parameters.specialty(code) if code else None
            sp["ergani_specialty_code"] = code if row else ""
            sp["ergani_specialty_description"] = row["description"] if row else ""
        nxt = "employees"
    elif name == "employees":
        for i, e in enumerate(draft["employees"]):
            for k in PERSON_FIELDS:
                e["person"][k] = f.get(f"e{i}_{k}", "").strip()
            for k in HIRE_FIELDS:
                e["hire"][k] = f.get(f"e{i}_{k}", "").strip()
            e["pron_choice"] = f.get(f"e{i}_pron_choice", e["pron_choice"])
        # ΕΝΑΡΞΗ ΜΙΣΘΟΔΟΣΙΑΣ (ίδια για όλους): η επόμενη περίοδος μετά την ΑΠΔ
        # ή μήνας που ορίζει ο χρήστης. Τα πεδία του βήματος ήδη κρατήθηκαν.
        choice = {"mode": f.get("payroll_mode", "next"),
                  "month": f.get("payroll_month", "").strip()}
        draft["payroll_start"] = choice
        try:
            start = _payroll_start(draft["period"], choice)
        except ValueError as exc:
            return redirect(url_for("apd_import.step", name="employees", error=str(exc)))
        for e in draft["employees"]:
            e["hire"]["payroll_from"] = start.isoformat()
        nxt = "schedule"
    elif name == "schedule":
        for i, e in enumerate(draft["employees"]):
            e["schedule"] = {
                "mode": f.get(f"e{i}_mode", "fixed"),
                "weeks": f.get(f"e{i}_weeks", "4"),
                "days": {d: f.get(f"e{i}_day_{d}", "").strip() for d in core.DAYS},
            }
        nxt = "check"
    else:
        abort(404)
    return redirect(url_for("apd_import.step", name=nxt))


def _payroll_start(period, choice) -> date:
    """1η του μήνα από τον οποίο το σύστημα υπολογίζει μισθοδοσία."""
    y, m = period
    if choice["mode"] != "custom":
        return date(y + m // 12, m % 12 + 1, 1)
    try:
        cy, cm = (int(x) for x in choice["month"].split("-")[:2])
        return date(cy, cm, 1)
    except ValueError:
        raise ValueError("Επιλέξτε τον μήνα έναρξης της μισθοδοσίας") from None


def _dry_run(draft):
    cid = draft["company_id"]
    with lock:
        store = registry.tenant(cid).store if cid else None
        return core.dry_run(registry.shared, store, draft, clock.today())


@bp.post("/commit")
def commit():
    draft = _draft()
    if _dry_run(draft):  # ξανά: κάτι μπορεί να άλλαξε από την οθόνη ελέγχου
        return redirect(url_for("apd_import.step", name="check"))
    user = current_user()
    with lock, transaction():
        # Νέα εταιρεία: ο ΚΕΝΟΣ χώρος (νέα εγκατάσταση) ή καινούργιος
        cid = draft["company_id"] or registry.claim_company(
            draft["company"]["legal_name"], user)
        errors = core.apply(registry.tenant(cid).svc, draft, clock.today())
    if errors:  # δεν θα έπρεπε: ίδια commands, ίδια κατάσταση με το dry run
        return _page("check", d=draft, errors=errors, committed=True)
    _DRAFTS.pop(session.pop("apd_import", ""), None)
    session["company_id"] = cid
    return redirect(url_for("persons.index"))
