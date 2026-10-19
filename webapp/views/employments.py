"""
Απασχολήσεις (εργασιακές σχέσεις): σύμβαση, ασφαλιστικό προφίλ,
πρόγραμμα εργασίας, άδειες, αποχώρηση.

Η ΠΡΟΣΛΗΨΗ ξεκινά ΠΑΝΤΑ με αναζήτηση στο μητρώο προσώπων:
  1. ο χρήστης ψάχνει με ΑΦΜ/ΑΜΚΑ/ΑΜΑ/όνομα,
  2. αν βρεθεί ανενεργό πρόσωπο -> ΕΠΑΝΑΠΡΟΣΛΗΨΗ (νέο Employment, ίδιο Person),
     αν είναι ενεργό -> απαγορεύεται (πρώτα αποχώρηση, βλ. check_rehire),
  3. αν δεν βρεθεί -> φόρμα νέου προσώπου + απασχόλησης μαζί.
Έτσι δεν διπλογράφονται στοιχεία ταυτότητας που ζουν στο μητρώο (/persons).
"""

import json
from calendar import monthrange
from copy import copy
from datetime import date, timedelta
from types import SimpleNamespace

from flask import Blueprint, Response, abort, jsonify, render_template, request, url_for

from misevent.app.services import DISMISSAL_REASON
from misevent.domain.events import (
    ContractChanged,
    EmploymentHireDetailsCorrected,
    EmploymentStarted,
    LeaveType,
)
from misevent.domain.projections import profile_coverages
from misevent.integrations import ergani as ergani_mod
from misevent.integrations import ergani_parameters as ergani_params
from misevent.rules import config, constraints
from misevent.rules.calendar_gr import is_working_day

from .. import labels
from .. import queries as q
from ..clock import today
from ..filters import money
from ..helpers import (
    flash_to,
    form_date,
    form_digital_card_fields,
    form_employment_relationship,
    form_float,
    form_frozen_service_years,
    form_int,
    form_migration,
    form_oaed_hire_fields,
    form_optional_date,
    form_service_years,
    form_third_country_fields,
    frozen_service_years_fields,
    run_command,
    service_years_fields,
)
from ..labels import LEAVE_LABELS, MONTHS_GEN, TERMINATION_REASONS
from ..system import proj, store, svc

bp = Blueprint("employments", __name__, url_prefix="/employments")

DAYS = [
    ("MON", "Δε"),
    ("TUE", "Τρ"),
    ("WED", "Τε"),
    ("THU", "Πε"),
    ("FRI", "Πα"),
    ("SAT", "Σα"),
    ("SUN", "Κυ"),
]


def weekly_pattern(form) -> dict:
    """Το εβδομαδιαίο πρόγραμμα από τα πεδία day_MON… (κενό = ρεπό)."""
    return {
        code: form.get(f"day_{code}", "").strip()
        for code, _ in DAYS
        if form.get(f"day_{code}", "").strip()
    }


def schedule_opts(form) -> dict:
    """Επιλογές πρώτης δήλωσης προγράμματος (tab «Πρόγραμμα εργασίας»):
    - fixed  → σταθερό εβδομαδιαίο, με προαιρετική ημ. λήξης
    - variable → ημερήσιο από το template για N πλήρεις εβδομάδες (1–10)."""
    mode = form.get("schedule_mode", "fixed")
    return {
        "schedule_mode": mode,
        "schedule_end_date": (
            form_date(form, "schedule_end_date")
            if mode == "fixed" and form.get("schedule_end_date")
            else None
        ),
        "schedule_weeks": form_int(form, "schedule_weeks", 0),
    }


# ------------------------------------------------- Πρόσληψη: modal (αναζήτηση πρώτα)
@bp.get("/hire-modal")
def hire_modal():
    """Ανοίγει το modal πρόσληψης (αναζήτηση μητρώου -> επαναπρόσληψη ή νέο).
    Κλείνει μόνο με Πρόσληψη (submit) ή Άκυρο — ίδιο μοτίβο με /attendance."""
    return render_template("partials/_hire_modal.html")


@bp.get("/hire-modal/close")
def hire_modal_close():
    """Κλείσιμο του modal (Άκυρο) — αδειάζει τον host."""
    return ""


@bp.get("/search")
def search():
    """htmx: αποτελέσματα αναζήτησης στο μητρώο προσώπων."""
    term = request.args.get("q", "")
    return render_template(
        "partials/_hire_search.html", term=term, results=q.search_persons(term)
    )


def specialty_names():
    """Οι δηλωμένες ειδικότητες της εταιρείας (για τα select των φορμών)."""
    return sorted(proj.specialties)


def branch_choices():
    """Παραρτήματα σε σειρά κωδικού για όλες τις φόρμες εργαζομένου."""
    return sorted(
        proj.branches.values(),
        key=lambda b: b.code,
    )


def profile_form_values(e):
    """Οι τιμές ασφάλισης μιας ΥΠΑΡΧΟΥΣΑΣ απασχόλησης, στη γλώσσα της φόρμας.
    Η επιλογή πρόνοιας ανασυντίθεται από το προφίλ: χωρίς πρόνοια -> «no»,
    αλλιώς η κατηγορία ασφαλισμένου (διαφοροποιημένη) ή «yes»."""
    prof = getattr(e, "insurance_profile", None)
    return {
        "specialty_name": getattr(prof, "specialty_name", ""),
        "auxiliary_scheme": getattr(prof, "auxiliary_scheme", ""),
        "pron_choice": (
            "no"
            if getattr(prof, "welfare_scheme", "none") == "none"
            else getattr(prof, "insurance_class", "new")
        ),
        "hire_date": e.hire_date.isoformat() if e.hire_date else "",
    }


def insurance_ctx(fv):
    """Οι επιλογές ασφάλισης της (επανα)πρόσληψης + η ΖΩΝΤΑΝΗ προεπισκόπηση
    των γραμμών ΑΠΔ, για την επιλεγμένη ειδικότητα.

    Read-only: όλα βγαίνουν από `svc.specialty_options`/`specialty_lines` —
    ΤΙΣ ΙΔΙΕΣ συναρτήσεις που επικυρώνουν και την ανάθεση, ώστε η φόρμα να
    μη μπορεί να προτείνει συνδυασμό που θα απορριφθεί."""
    names = specialty_names()
    selected = fv.get("specialty_name") or (names[0] if names else "")
    ctx = {
        "selected": selected,
        "carriers": [],
        "pron_choices": [],
        "pron": dict(config.PRON_DEFAULTS),
        "auxiliary_scheme": "",
        "pron_choice": "",
        "lines": [],
        "employee": 0.0,
        "employer": 0.0,
        "error": "",
    }
    if not selected:
        return ctx
    try:
        opts = svc.specialty_options(selected)
    except ValueError as exc:
        ctx["error"] = str(exc)
        return ctx
    ctx.update(
        carriers=opts["carriers"], pron=opts["pron"], pron_choices=opts["pron_choices"]
    )
    aux = fv.get("auxiliary_scheme") or ""
    if aux not in opts["carriers"]:
        aux = opts["carriers"][0] if opts["carriers"] else ""
    choice = fv.get("pron_choice") or ""
    if opts["pron_choices"] and choice not in opts["pron_choices"]:
        choice = opts["pron_choices"][0]
    ctx.update(auxiliary_scheme=aux, pron_choice=choice)
    # Τα ποσοστά είναι χρονοσειρά: δείχνουμε αυτά που θα ισχύουν την ημ.
    # πρόσληψης (ή, όσο δεν έχει δοθεί, την ημερομηνία αναφοράς του UI).
    # ΜΕΤΑΦΟΡΑ: μετρά η ΑΝΑΛΗΨΗ — ίδια ημερομηνία με την `assign_specialty`,
    # αλλιώς η προεπισκόπηση θα έψαχνε ΚΠΚ ετών πριν και θα έσκαγε.
    on = form_optional_date(fv, "hire_date") or today()
    if fv.get("migrated"):
        on = max(on, form_optional_date(fv, "payroll_from") or on)
    try:
        res = svc.specialty_lines(selected, aux, choice, on=on)
        ctx.update(
            lines=res["lines"],
            employee=res["employee"],
            employer=res["employer"],
            pron_choice=res["pron_choice"],
        )
    except ValueError as exc:
        ctx["error"] = str(exc)
    return ctx


def form_ctx(fv):
    """Ό,τι χρειάζεται το κοινό tab «Γενικά στοιχεία» και για τις δύο φόρμες."""
    return {
        "specialties": specialty_names(),
        "branches": branch_choices(),
        "ins": insurance_ctx(fv),
        "carrier_labels": config.CARRIERS,
        "pron_labels": labels.PRON_CHOICES,
    }


@bp.post("/insurance-fields")
def insurance_fields():
    """htmx: ξαναχτίζει την ενότητα «Ασφάλιση» όποτε αλλάξει ειδικότητα,
    φορέας επικουρικής ή πρόνοια — μαζί με την προεπισκόπηση των γραμμών."""
    return render_template("partials/_insurance_fields.html", **form_ctx(request.form))


@bp.get("/hire")
def hire_form():
    """htmx: η φόρμα πρόσληψης — για υπάρχον πρόσωπο ή για νέο."""
    pid = request.args.get("person_id")
    person = proj.persons.get(pid) if pid else None
    if pid and person is None:
        abort(404)
    # ΕΠΑΝΑΠΡΟΣΛΗΨΗ: προσυμπληρωμένη προϋπηρεσία (δηλωθείσα της προηγούμενης
    # περιόδου + όσο δούλεψε σε αυτήν) — ΙΔΙΑ πηγή με το modal των Εργαζομένων.
    fv = (
        {
            **service_years_fields(svc.previous_service_years(pid)),
            **frozen_service_years_fields(svc.previous_frozen_service_years(pid)),
        }
        if person
        else {}
    )
    return render_template(
        "partials/_hire_form.html",
        person=person,
        form=fv,
        term=request.args.get("q", ""),
        identity_types=ergani_params.identity_types(),
        nationalities=ergani_params.nationalities(),
        education_levels=ergani_params.education_levels(),
        eu_nationality_codes=ergani_params.eu_nationality_codes(),
        **form_ctx(fv),
    )


@bp.post("/")
def create():
    """Πρόσληψη: υπάρχον πρόσωπο (επαναπρόσληψη) ή νέο πρόσωπο + απασχόληση."""

    def cmd():
        f = request.form
        # Έλεγχοι ΠΡΙΝ δημιουργηθεί πρόσωπο (αλλιώς μένει ορφανό):
        # πρόγραμμα + κατώτατος μισθός με τις δηλωθείσες προσαυξήσεις
        pay_type = f.get("pay_type", "monthly")
        amount = form_float(f, "amount")
        # Η ειδικότητα (θέση εργασίας) πρέπει να είναι δηλωμένη στην εταιρεία
        specialty_name = f.get("specialty_name", "")
        svc.get_specialty(specialty_name)
        constraints.check_schedule(
            weekly_pattern(f), form_float(f, "weekly_hours", 40.0)
        )
        wage_on, wage_service = svc.wage_check_basis(
            form_date(f, "hire_date"),
            form_migration(f)["payroll_from"],
            form_service_years(f),
            frozen_service_years=form_frozen_service_years(f),
        )
        svc.check_minimum(
            pay_type,
            (amount if pay_type == "monthly" else 0.0),
            (amount if pay_type == "daily" else 0.0),
            form_float(f, "weekly_hours", 40.0),
            wage_on,
            service_years=wage_service,
            married=bool(f.get("married")),
            extra_pct=form_float(f, "extra_allowances_pct"),
        )
        # Μεταφορά + κλείδωμα περιόδου — ΠΡΙΝ δημιουργηθεί πρόσωπο (orphan):
        # η ανάληψη μισθοδοσίας σε κλεισμένο μήνα απορρίπτεται ούτως ή άλλως
        # από το command layer, αλλά τότε το πρόσωπο θα είχε ήδη γραφτεί.
        migration = form_migration(f)
        start = constraints.check_payroll_from(
            migration["payroll_from"], form_date(f, "hire_date")
        ) or form_date(f, "hire_date")
        svc.check_open_period(start, "πρόσληψη")
        # Επιλογές προγράμματος — έλεγχος ΠΡΙΝ δημιουργηθεί πρόσωπο (orphan)
        sopts = schedule_opts(f)
        if sopts["schedule_mode"] == "variable" and not (
            1 <= sopts["schedule_weeks"] <= 10
        ):
            raise ValueError("Για ημερήσιο πρόγραμμα δηλώστε πλήθος εβδομάδων 1 έως 10")
        pid = f.get("person_id") or None
        rehire = bool(pid)
        if pid:
            if pid not in proj.persons:
                raise ValueError("Άγνωστο πρόσωπο")
        else:
            pid = svc.register_person(
                last_name=f.get("last_name", ""),
                first_name=f.get("first_name", ""),
                father_name=f.get("father_name", ""),
                mother_name=f.get("mother_name", ""),
                birth_date=form_date(f, "birth_date"),
                afm=f.get("afm", ""),
                amka=f.get("amka", ""),
                ama=f.get("ama", ""),
                children=form_int(f, "children"),
                email=f.get("email", ""),
                sex=f.get("sex", ""),
                nationality_code=f.get("nationality_code", ""),
                identity_type=f.get("identity_type", ""),
                identity_number=f.get("identity_number", ""),
                identity_issuer=f.get("identity_issuer", ""),
                identity_issue_date=form_optional_date(f, "identity_issue_date"),
                identity_expiry_date=form_optional_date(f, "identity_expiry_date"),
                education_level=f.get("education_level", ""),
                **form_third_country_fields(f),
            )

        eid = svc.start_employment(
            pid,
            form_date(f, "hire_date"),
            pay_type=pay_type,
            monthly_gross=(amount if pay_type == "monthly" else 0.0),
            daily_wage=(amount if pay_type == "daily" else 0.0),
            weekly_hours=form_float(f, "weekly_hours", 40.0),
            weekly_system=form_int(f, "weekly_system", 5),
            weekly_pattern=weekly_pattern(f),  # ΥΠΟΧΡΕΩΤΙΚΟ πρόγραμμα (template)
            # Προσαυξήσεις κατώτατου: τριετίες / γάμος / λοιπές (πτυχίο κ.λπ.)
            service_years=form_service_years(f),
            frozen_service_years=form_frozen_service_years(f),
            married=bool(f.get("married")),
            extra_allowances_pct=form_float(f, "extra_allowances_pct"),
            draft=True,
            **form_migration(f),
            **form_employment_relationship(f),
            **form_oaed_hire_fields(f),
            **form_digital_card_fields(f),
            **sopts,
        )
        # Οι ασφαλιστικές καλύψεις αντλούνται από τη δηλωμένη ειδικότητα:
        # εδώ δίνονται μόνο ο φορέας επικουρικής και η επιλογή πρόνοιας.
        svc.assign_specialty(
            eid,
            specialty_name,
            auxiliary_scheme=f.get("auxiliary_scheme"),
            pron_choice=f.get("pron_choice"),
            branch_no=form_int(f, "branch_no", 0),
        )
        name = proj.persons[pid].full_name
        verb = "Επαναπροσλήφθηκε" if rehire else "Προσλήφθηκε"
        emp = proj.employments[eid]
        if emp.migrated:
            # Μεταφορά: το Ε3 μπορεί να έχει ήδη δηλωθεί εκτός συστήματος —
            # το μήνυμα δεν πρέπει να ζητά ό,τι δεν χρειάζεται.
            since = emp.payroll_start.strftime("%m/%Y")
            todo = "ΟΧΕ" if emp.hire_submitted else "Ε3/ΟΧΕ"
            return (
                f"{verb}: {name} — μεταφορά, η μισθοδοσία ξεκινά {since} "
                f"(πρόσληψη {emp.hire_date.strftime('%d/%m/%Y')}) · "
                f"υπόβαλε {todo} στο ΕΡΓΑΝΗ από την καρτέλα"
            )
        return (
            f"{verb}: {name} — υπόβαλε Ε3/ΟΧΕ στο ΕΡΓΑΝΗ από την καρτέλα "
            "της απασχόλησης"
        )

    ok, msg = run_command(cmd)
    if ok:
        # Ο «Νέος εργαζόμενος» ζει στην οθόνη Εργαζομένων -> επιστροφή εκεί
        html = render_template("partials/_persons_panel.html", rows=q.person_rows())
        return flash_to(html, msg, err=False)

    # ΣΦΑΛΜΑ: κράτα τη φόρμα με τα δεδομένα του χρήστη ώστε να διορθώσει —
    # retarget μόνο τη φόρμα, όχι ολόκληρο το panel (αλλιώς «χάνεται» και
    # τον βγάζει στη λίστα). Τα πεδία ξαναγεμίζουν από το request.form.
    pid = request.form.get("person_id") or None
    person = proj.persons.get(pid) if pid else None
    html = render_template(
        "partials/_hire_form.html",
        person=person,
        term=request.form.get("q", ""),
        form=request.form,
        identity_types=ergani_params.identity_types(),
        nationalities=ergani_params.nationalities(),
        education_levels=ergani_params.education_levels(),
        eu_nationality_codes=ergani_params.eu_nationality_codes(),
        **form_ctx(request.form),
    )
    resp = flash_to(html, msg, err=True)
    resp.headers["HX-Retarget"] = "#hire-form"
    resp.headers["HX-Reswap"] = "outerHTML"
    return resp


def _minimum_compensation(form):
    """Κατώτατες αποδοχές της ανοικτής φόρμας, από τους ίδιους κανόνες
    που χρησιμοποιεί η αποθήκευση της πρόσληψης."""
    pay_type = str(form.get("pay_type") or "monthly")
    if pay_type not in {"monthly", "daily"}:
        raise ValueError("Μη έγκυρος τύπος αμοιβής")
    weekly_hours = form_float(form, "weekly_hours", 40.0)
    if not 0 < weekly_hours <= 48:
        raise ValueError("Οι εβδομαδιαίες ώρες πρέπει να είναι 1-48")
    hire_date = form_optional_date(form, "hire_date") or today()
    service_years = form_service_years(form)
    frozen_service_years = form_frozen_service_years(form)
    extra_pct = form_float(form, "extra_allowances_pct")
    if extra_pct < 0:
        raise ValueError("Οι λοιπές προσαυξήσεις δεν μπορεί να είναι αρνητικές")
    # ΜΕΤΑΦΟΡΑ: το όριο κρίνεται στην ΑΝΑΛΗΨΗ, με τις τριετίες ωριμασμένες
    # από την πραγματική πρόσληψη — ΙΔΙΟΣ κανόνας με την αποθήκευση, ώστε η
    # ζωντανή ένδειξη να μη διαφέρει από αυτό που θα επιβληθεί.
    on_date, service_years = svc.wage_check_basis(
        hire_date,
        form_migration(form)["payroll_from"],
        service_years,
        frozen_service_years=frozen_service_years,
    )
    # ΙΔΙΟ μήνυμα με την αποθήκευση όταν η ημερομηνία είναι εκτός μητρώου
    # ΓΣΣΕ (καθοδηγεί στη «Μεταφορά») — μία πηγή αλήθειας.
    svc.check_wage_registry(on_date)
    floor, description = svc.minimum_floor(
        pay_type,
        weekly_hours,
        on_date,
        service_years=service_years,
        married=bool(form.get("married")),
        extra_pct=extra_pct,
    )
    minimum_wage = config.minimum_wage_for(on_date)
    return {
        "floor": round(floor, 2),
        "pay_type": pay_type,
        "unit": "€/ημέρα" if pay_type == "daily" else "€/μήνα",
        "description": description,
        "valid_from": minimum_wage.valid_from.isoformat(),
    }


@bp.post("/hire/minimum-compensation")
def minimum_compensation():
    """Live πρόταση ελάχιστων αποδοχών, χωρίς αποθήκευση."""
    try:
        return jsonify(_minimum_compensation(request.form))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ------------------------------------------------------------- Καρτέλα
# Όλες οι φόρμες της καρτέλας ανοίγουν σε MODAL (ίδιο μοτίβο με /attendance):
# το #modal-host ζει ΜΕΣΑ στο #employment-card, οπότε η επιτυχής εντολή (που
# αντικαθιστά όλη την κάρτα) κλείνει αυτόματα το modal. Σε σφάλμα το modal
# ξαναγεμίζει με τα δεδομένα του χρήστη (form) ώστε να διορθώσει.
EMP_MODALS = {
    "contract",
    "contract_history",
    "terminate",
    "insurance",
    "schedule",
    "daily",
    "attendance",
    "leave",
}


def _contract_history(employment_id):
    """Ανασυνθέτει τις εκδόσεις της σύμβασης από το event log.

    Οι διορθώσεις της φόρμας πρόσληψης ενημερώνουν την αρχική εγγραφή και
    δεν εμφανίζονται ως μεταβολές. Τα ContractChanged είναι πλήρη snapshots
    των βασικών όρων τους, με το weekly_system=None να σημαίνει «αμετάβλητο».
    """
    emp = proj.employments.get(employment_id)
    if emp is None:
        abort(404)

    initial = None
    changes = []
    event_types = (
        "EmploymentStarted",
        "EmploymentHireDetailsCorrected",
        "ContractChanged",
    )
    for order, (_aggregate_id, event) in enumerate(
        store.load(employment_id, event_types)
    ):
        if isinstance(event, EmploymentStarted):
            initial = {
                "effective_from": event.hire_date,
                "pay_type": event.pay_type,
                "monthly_gross": event.monthly_gross,
                "daily_wage": event.daily_wage,
                "weekly_hours": event.weekly_hours,
                "weekly_system": event.weekly_system,
                "service_years": event.service_years,
                "married": event.married,
                "extra_allowances_pct": event.extra_allowances_pct,
                "employment_relationship": event.employment_relationship,
                "fixed_term_from": event.fixed_term_from,
                "fixed_term_to": event.fixed_term_to,
                "_order": order,
            }
        elif isinstance(event, EmploymentHireDetailsCorrected) and initial:
            initial.update(
                {
                    "effective_from": event.hire_date,
                    "pay_type": event.pay_type,
                    "monthly_gross": event.monthly_gross,
                    "daily_wage": event.daily_wage,
                    "weekly_hours": event.weekly_hours,
                    "weekly_system": event.weekly_system,
                    "service_years": event.service_years,
                    "married": event.married,
                    "extra_allowances_pct": event.extra_allowances_pct,
                    "employment_relationship": event.employment_relationship,
                    "fixed_term_from": event.fixed_term_from,
                    "fixed_term_to": event.fixed_term_to,
                }
            )
        elif isinstance(event, ContractChanged):
            changes.append(
                {
                    "effective_from": event.effective_from,
                    "pay_type": event.pay_type,
                    "monthly_gross": event.monthly_gross,
                    "daily_wage": event.daily_wage,
                    "weekly_hours": event.weekly_hours,
                    "weekly_system": event.weekly_system,
                    "reason": event.reason,
                    "reason_date": event.reason_date,
                    "_order": order,
                }
            )

    if initial is None:
        return []

    current_system = initial["weekly_system"]
    for change in sorted(changes, key=lambda row: row["_order"]):
        if change["weekly_system"] is not None:
            current_system = change["weekly_system"]
        change["resolved_weekly_system"] = current_system

    changes.sort(key=lambda row: (row["effective_from"] or date.min, row["_order"]))
    current_order = initial["_order"]
    if changes:
        # Η τρέχουσα προβολή είναι το τελευταίο event που καταγράφηκε, ακόμη
        # κι αν πρόκειται για αναδρομική μεταβολή με παλιότερη ημερομηνία.
        current_order = max(row["_order"] for row in changes)
    rows = [
        {
            **initial,
            "kind": "Αρχική σύμβαση",
            "is_current": not changes,
        }
    ]
    for number, change in enumerate(changes, 1):
        rows.append(
            {
                **change,
                "weekly_system": change["resolved_weekly_system"],
                "service_years": initial["service_years"],
                "married": initial["married"],
                "extra_allowances_pct": initial["extra_allowances_pct"],
                "employment_relationship": initial["employment_relationship"],
                "fixed_term_from": initial["fixed_term_from"],
                "fixed_term_to": initial["fixed_term_to"],
                "kind": f"Μεταβολή {number}",
                "is_current": change["_order"] == current_order,
            }
        )
    rows.sort(key=lambda row: (row["effective_from"] or date.min, row["_order"]))
    # Αιτιολογία κάθε έκδοσης: ο κωδικός του event αν υπάρχει, αλλιώς η ΔΙΑΦΟΡΑ
    # από την προηγούμενη έκδοση (χειροκίνητη μεταβολή).
    previous = None
    for row in rows:
        row["note"] = _contract_note(row, previous, emp)
        previous = row
    return rows


def _pay_amount(row) -> float:
    return row["daily_wage"] if row["pay_type"] == "daily" else row["monthly_gross"]


def _contract_note(row, previous, emp) -> str:
    """Αιτιολογία μιας έκδοσης της σύμβασης (στήλη «Σχόλια»).

    Προτεραιότητα: (α) ο κωδικός αιτίας του `ContractChanged`
    (`config.CONTRACT_CHANGE_REASONS` — ΓΣΣΕ/τριετία, τον γράφει το
    `apply_minimum_adjustments`), (β) για την αρχική σύμβαση η **μεταφορά** από
    άλλο πρόγραμμα μισθοδοσίας, (γ) αλλιώς η ΔΙΑΦΟΡΑ των όρων."""
    if previous is None:  # αρχική σύμβαση
        if emp.migrated:
            return f"Μεταφορά και ανάληψη από {emp.payroll_start.strftime('%d/%m/%Y')}"
        return "Αρχική σύμβαση πρόσληψης"

    template = config.CONTRACT_CHANGE_REASONS.get(row.get("reason") or "")
    if template:
        when = row.get("reason_date")
        return template.format(date=when.strftime("%d/%m/%Y") if when else "—")

    parts = []
    if row["pay_type"] != previous["pay_type"]:
        parts.append(
            "μετατροπή σε "
            + ("ημερομίσθιο" if row["pay_type"] == "daily" else "μηνιαίο μισθό")
        )
    old_pay, new_pay = _pay_amount(previous), _pay_amount(row)
    if abs(new_pay - old_pay) >= 0.005:
        word = "αύξηση" if new_pay > old_pay else "μείωση"
        parts.append(f"{word} αποδοχών {money(old_pay)}€ → {money(new_pay)}€")
    if abs(row["weekly_hours"] - previous["weekly_hours"]) >= 0.005:
        parts.append(
            "ωράριο {:g} → {:g} ώρες/εβδ.".format(
                previous["weekly_hours"], row["weekly_hours"]
            )
        )
    if row["weekly_system"] != previous["weekly_system"]:
        parts.append(
            "σύστημα → " + ("εξαήμερο" if row["weekly_system"] == 6 else "πενθήμερο")
        )
    if not parts:
        return "Μεταβολή όρων σύμβασης"
    text = " · ".join(parts)
    return text[0].upper() + text[1:]


def _insurance_badges(prof, covs):
    """Badges Επικουρικού και λοιπών παραμετροποιημένων ταμείων."""
    aux_scheme = getattr(prof, "auxiliary_scheme", "none") if prof else "none"
    if aux_scheme == "none" and prof and getattr(prof, "teka_member", False):
        aux_scheme = "teka"
    if aux_scheme == "none" and any(
        config.fund_branch(c.get("fund", "")) == "auxiliary" for c in covs
    ):
        aux_scheme = "efka"
    auxiliary = {
        "efka": {"label": "ΤΕΑΜ", "tone": "team"},
        "teka": {"label": "ΤΕΚΑ", "tone": "teka"},
    }.get(aux_scheme, {"label": "ΟΧΙ", "tone": "no"})

    others = []
    seen = set()
    welfare_scheme = getattr(prof, "welfare_scheme", "none") if prof else "none"
    for c in covs:
        fund = c.get("fund", "")
        branch = config.fund_branch(fund)
        if branch in {"main", "auxiliary"}:
            continue
        default_label = config.FUNDS[fund].label if fund in config.FUNDS else fund
        label = (c.get("label") or default_label).strip()
        if label == default_label and welfare_scheme != "none":
            label = welfare_scheme.upper()
        label = label.upper()
        if not label or label in seen:
            continue
        seen.add(label)
        others.append(
            {
                "label": label,
                "tone": "welfare"
                if branch == "welfare" and fund != "other"
                else "other",
            }
        )
    return auxiliary, others


def _service_time(e) -> dict:
    """Χρόνος εργασίας σε δεκαδικά έτη (τα αποδίδει το φίλτρο `service_gr`).

    ΔΥΟ ΜΕΓΕΘΗ, σκόπιμα διακριτά (βλ. §7 — πάγωμα 14/02/2012–31/12/2023):
      - `total` = ο ΣΥΝΟΛΙΚΟΣ χρόνος εργασίας (δηλωθείσα προϋπηρεσία +
        παγωμένος χρόνος + ΟΛΟΣ ο χρόνος στον ίδιο εργοδότη) — η βάση για
        ημέρες άδειας κ.λπ. (`EmploymentState.full_service_years`),
      - `triennia` = ό,τι ΜΕΤΡΑ ΣΤΙΣ ΤΡΙΕΤΙΕΣ — η ΙΔΙΑ βάση με το κατώτατο
        όριο (`EmploymentState.total_service_years`).
    `prior`/`frozen` = τα δύο δηλωθέντα σκέλη της προϋπηρεσίας, `employer` =
    ο χρόνος στον ίδιο εργοδότη μέχρι την ημ. αναφοράς (ή την αποχώρηση)."""
    ref = today()
    if e.termination_date and e.termination_date < ref:
        ref = e.termination_date
    total = e.full_service_years(ref)
    declared = e.service_years + e.frozen_service_years
    return {
        "prior": e.service_years,
        "frozen": e.frozen_service_years,
        "declared": declared,
        "employer": max(0.0, total - declared),
        "total": total,
        "triennia": e.total_service_years(ref),
        "as_of": ref,
    }


def _card(employment_id):
    e = proj.employments.get(employment_id)
    if e is None:
        abort(404)
    year = today().year  # έτος δικαιώματος αδείας, ως προς την ημ. αναφοράς

    # Παρουσίες ανά μήνα (φθίνουσα) — κρίσιμες για τους ημερομίσθιους
    per_month: dict[tuple[int, int], int] = {}
    for d in e.attendance:
        k = (d.year, d.month)
        per_month[k] = per_month.get(k, 0) + 1

    # Ασφαλιστικές καλύψεις του προφίλ
    covs = profile_coverages(e.insurance_profile)
    prof = e.insurance_profile
    selected_branch = proj.branches.get(getattr(prof, "branch_no", 0))

    # TEAM μπορεί να είναι ενσωματωμένο στο 0101, άρα το badge προκύπτει από
    # τα profile facts και όχι μόνο από χωριστές auxiliary coverages.
    auxiliary_badge, other_fund_badges = _insurance_badges(prof, covs)
    employment_status = {
        "0": {"label": "Πλήρης", "tone": "full"},
        "1": {"label": "Μερική", "tone": "part"},
        "2": {"label": "Εκ περιτροπής", "tone": "rotation"},
    }[ergani_mod.employment_status(e.weekly_hours, e.schedule)]

    return {
        "e": e,
        "employment_status": employment_status,
        "days": DAYS,
        "leave_labels": LEAVE_LABELS,
        "reasons": TERMINATION_REASONS,
        "entitlement": svc.annual_leave_entitlement(employment_id, year),
        "taken": svc.annual_leave_taken(employment_id, year),
        "year": year,
        # Εκκαθάριση αποχώρησης — ΟΛΑ υπολογισμένα αυτόματα στην αποχώρηση
        # (`svc.terminate`): η καρτέλα απλώς τα εμφανίζει.
        "leave_comp": svc.leave_compensation(employment_id),
        # Δώρο + Επίδομα Αδείας που αναλογούν + σύνολο προς καταβολή
        "settlement": q.exit_settlement(employment_id),
        # Αποζημίωση απόλυσης (μόνο σε καταγγελία σύμβασης)
        "is_dismissal": e.termination_reason == DISMISSAL_REASON,
        "severance": svc.severance(employment_id),
        "attendance": sorted(per_month.items(), reverse=True),
        # Ημερήσιο πρόγραμμα (υπερισχύει του εβδομαδιαίου), πρόσφατο πρώτα
        "daily_schedule": sorted(e.daily_schedule.items(), reverse=True)[:31],
        # Κατάσταση υποβολών πρόσληψης ΕΡΓΑΝΗ (ενεργοποιεί ο χρήστης)
        "e3_done": (
            svc.ergani_hire_submitted(employment_id, "WebE3N")
            or svc.ergani_hire_submitted(employment_id, "E3")
        ),
        "wto_done": svc.ergani_hire_submitted(employment_id, "WTO"),
        # Τρέχον κατώτατο όριο (με ωριμασμένες τριετίες/γάμο/λοιπές)
        "min_floor": svc.minimum_floor(
            e.pay_type,
            e.weekly_hours,
            today(),
            service_years=e.total_service_years(today()),
            married=e.married,
            extra_pct=e.extra_allowances_pct,
        ),
        # Χρόνος εργασίας: δηλωθείσα προϋπηρεσία + χρόνος στον ίδιο εργοδότη
        "service": _service_time(e),
        "today": today(),  # προεπιλογή στα πεδία ημερομηνίας των modals
        # Δηλωμένες ειδικότητες + επιλογές/προεπισκόπηση ασφάλισης (ΙΔΙΟ
        # fragment με την πρόσληψη — μία πηγή markup)
        **form_ctx(profile_form_values(e)),
        "selected_branch": selected_branch,
        # Οι ασφαλιστικές καλύψεις του προφίλ
        "coverages": covs,
        "auxiliary_badge": auxiliary_badge,
        "other_fund_badges": other_fund_badges,
        # Επικουρική ασφάλιση: ΦΟΡΕΙΣ των καλύψεων του κλάδου (ΕΦΚΑ ή/και
        # ΤΕΚΑ) — tags στην κάρτα, προεπιλογές στο modal ασφαλιστικού
        "aux_funds": [
            c["fund"]
            for c in covs
            if config.fund_branch(c.get("fund", "")) == "auxiliary"
        ],
    }


def _hire_form_values(e):
    """Τρέχουσες τιμές στην ίδια μορφή με τη φόρμα αρχικής πρόσληψης."""
    prof = e.insurance_profile
    values = {
        "hire_date": e.hire_date.isoformat() if e.hire_date else "",
        "pay_type": e.pay_type,
        "amount": e.daily_wage if e.pay_type == "daily" else e.monthly_gross,
        "weekly_hours": e.weekly_hours,
        "weekly_system": e.weekly_system,
        **service_years_fields(e.service_years),
        **frozen_service_years_fields(e.frozen_service_years),
        "married": e.married,
        "extra_allowances_pct": e.extra_allowances_pct,
        **profile_form_values(e),
        "branch_no": getattr(prof, "branch_no", 0),
        "employment_relationship": e.employment_relationship,
        "fixed_term_from": (e.fixed_term_from.isoformat() if e.fixed_term_from else ""),
        "fixed_term_to": e.fixed_term_to.isoformat() if e.fixed_term_to else "",
        "f_topothetisioaed": e.oaed_placement,
        "f_programaoaed": e.oaed_program,
        "f_replaceprograma": e.oaed_replacement,
        "f_replaceprograma_afm": e.oaed_replaced_afm,
        "f_replaceprograma_amka": e.oaed_replaced_amka,
        "f_trial_period": e.trial_period,
        "f_trial_date_to": (e.trial_date_to.isoformat() if e.trial_date_to else ""),
        "f_working_card": e.working_card,
        "f_euelikto_wrario_minutes": e.flexible_arrival_minutes,
        "f_dialeimma_entos_wrariou": e.break_within_schedule,
        "f_dialeimma_minutes": e.break_minutes,
        "schedule_mode": e.schedule_mode,
    }
    values.update({f"day_{code}": e.schedule.get(code, "") for code, _ in DAYS})
    if e.schedule_history:
        latest = max(
            enumerate(e.schedule_history),
            key=lambda item: (item[1][0], item[0]),
        )[1]
        values["schedule_end_date"] = latest[2].isoformat() if latest[2] else ""
    if e.schedule_mode == "variable" and e.daily_schedule and e.hire_date:
        first_monday = e.hire_date - timedelta(days=e.hire_date.weekday())
        full_start = (
            e.hire_date
            if e.hire_date.weekday() == 0
            else first_monday + timedelta(days=7)
        )
        last_day = max(e.daily_schedule)
        values["schedule_weeks"] = max(1, ((last_day - full_start).days + 1 + 6) // 7)
    else:
        values["schedule_weeks"] = 1
    return values


def _hire_json_preview(employment_id):
    """JSON tab: το καταγεγραμμένο payload μετά την υποβολή, αλλιώς preview."""
    submitted = next(
        (
            item
            for item in reversed(proj.ergani_submissions)
            if item.employee_id == employment_id
            and item.doc_type in ("WebE3N", "E3")
            and item.status != "failed"
        ),
        None,
    )
    try:
        if submitted:
            payload = json.loads(submitted.payload)
        else:
            payload = svc.build_hire_web_e3n(employment_id)
        return {
            "payload_json": json.dumps(
                payload, ensure_ascii=False, indent=2, default=str
            ),
            "json_error": "",
        }
    except (ValueError, json.JSONDecodeError) as exc:
        return {"payload_json": "", "json_error": str(exc)}


def _hire_preview_profile(emp, form):
    """Προσωρινό ασφαλιστικό προφίλ από τις επιλογές της ανοικτής φόρμας —
    ΙΔΙΑ επίλυση με την ανάθεση (svc.specialty_lines), χωρίς event."""
    result = svc.specialty_lines(
        form.get("specialty_name", ""),
        form.get("auxiliary_scheme"),
        form.get("pron_choice"),
        on=form_optional_date(form, "hire_date") or today(),
    )
    specialty, auxiliary = result["specialty"], result["carrier"]
    resolved = tuple(line["coverage"] for line in result["lines"])
    mains = [c for c in resolved if config.fund_branch(c["fund"]) == "main"]
    if len(mains) != 1:
        raise ValueError(
            f"Η ειδικότητα «{specialty.name}» πρέπει να επιλύει ακριβώς "
            f"ένα κύριο πακέτο (βρέθηκαν {len(mains)})"
        )
    main = mains[0]
    teka = next((c for c in resolved if config.fund_apd(c["fund"]) == "teka"), None)
    current = emp.insurance_profile
    return SimpleNamespace(
        branch_no=form_int(form, "branch_no", getattr(current, "branch_no", 0)),
        kad=main["kad"],
        specialty_code=main["specialty_code"],
        kpk=main["kpk"],
        auxiliary_scheme=auxiliary,
        teka_member=auxiliary == "teka",
        teka_kpk=teka["kpk"] if teka else "",
        specialty_name=specialty.name,
        ergani_specialty_code=getattr(specialty, "ergani_specialty_code", ""),
        ergani_specialty_description=getattr(
            specialty, "ergani_specialty_description", ""
        ),
    )


def _hire_json_preview_from_form(employment_id, form):
    """WebE3N preview των μη αποθηκευμένων τιμών, χωρίς event ή mutation."""
    original = proj.employments.get(employment_id)
    if original is None:
        raise ValueError(f"Άγνωστη απασχόληση {employment_id}")
    hire_date = form_date(form, "hire_date")
    pay_type = form.get("pay_type", "monthly")
    amount = form_float(form, "amount")
    weekly_hours = form_float(form, "weekly_hours", 40.0)
    schedule = weekly_pattern(form)
    constraints.check_contract(
        pay_type,
        amount if pay_type == "monthly" else 0.0,
        amount if pay_type == "daily" else 0.0,
        weekly_hours,
    )
    constraints.check_schedule(schedule, weekly_hours)
    relationship_values = form_employment_relationship(form)
    relationship, fixed_from, fixed_to = constraints.check_employment_relationship(
        relationship_values["employment_relationship"],
        relationship_values["fixed_term_from"],
        relationship_values["fixed_term_to"],
        hire_date=hire_date,
    )

    preview = copy(original)
    preview.hire_date = hire_date
    preview.pay_type = pay_type
    preview.monthly_gross = amount if pay_type == "monthly" else 0.0
    preview.daily_wage = amount if pay_type == "daily" else 0.0
    preview.weekly_hours = weekly_hours
    preview.contract_weekly_system = form_int(form, "weekly_system", 5)
    preview.service_years = form_service_years(form)
    preview.frozen_service_years = form_frozen_service_years(form)
    preview.married = bool(form.get("married"))
    preview.extra_allowances_pct = form_float(form, "extra_allowances_pct")
    preview.employment_relationship = relationship
    preview.fixed_term_from = fixed_from
    preview.fixed_term_to = fixed_to
    oaed_values = form_oaed_hire_fields(form)
    oaed_values = constraints.check_oaed_hire_fields(
        oaed_values["oaed_placement"],
        oaed_values["oaed_program"],
        oaed_values["oaed_replacement"],
        oaed_values["oaed_replaced_afm"],
        oaed_values["oaed_replaced_amka"],
        oaed_values["trial_period"],
        oaed_values["trial_date_to"],
        hire_date,
    )
    for field_name, value in zip(
        (
            "oaed_placement",
            "oaed_program",
            "oaed_replacement",
            "oaed_replaced_afm",
            "oaed_replaced_amka",
            "trial_period",
            "trial_date_to",
        ),
        oaed_values,
    ):
        setattr(preview, field_name, value)
    digital_values = form_digital_card_fields(form)
    digital_values = constraints.check_digital_card_fields(
        digital_values["working_card"],
        digital_values["flexible_arrival_minutes"],
        digital_values["break_within_schedule"],
        digital_values["break_minutes"],
    )
    for field_name, value in zip(
        (
            "working_card",
            "flexible_arrival_minutes",
            "break_within_schedule",
            "break_minutes",
        ),
        digital_values,
    ):
        setattr(preview, field_name, value)
    preview.schedule = schedule
    profile = _hire_preview_profile(preview, form)
    branch = svc.get_branch(profile.branch_no)
    return ergani_mod.build_web_e3n(
        proj.company, preview, profile, schedule, branch=branch
    )


@bp.get("/<employment_id>")
def detail(employment_id):
    return render_template("employment_detail.html", **_card(employment_id))


def _fragment(employment_id):
    return render_template("partials/_employment_card.html", **_card(employment_id))


@bp.get("/modal-close")
def modal_close():
    """Κλείσιμο modal καρτέλας (Άκυρο) — αδειάζει τον host."""
    return ""


@bp.get("/<employment_id>/modal/<name>")
def modal(employment_id, name):
    """htmx: ανοίγει το modal της αντίστοιχης φόρμας της καρτέλας."""
    if name not in EMP_MODALS:
        abort(404)
    context = _card(employment_id)
    if name == "contract_history":
        context["contracts"] = _contract_history(employment_id)
    if name == "terminate":
        context.update(_terminate_preview(employment_id, {}))
    return render_template(f"partials/_emp_{name}_modal.html", **context)


def _term_window():
    """Το επιτρεπτό παράθυρο ημ. αποχώρησης: ο ΤΡΕΧΩΝ μήνας μισθοδοσίας
    (`svc.current_payroll_period`) — ίδιος κανόνας με το command layer, ώστε
    το date input να μη δέχεται καν ημερομηνία που θα απορριφθεί."""
    current = svc.current_payroll_period()
    if current is None:
        return None, None, None
    y, m = current
    first = date(y, m, 1)
    last = date(y, m, monthrange(y, m)[1])
    ref = today()
    default = ref if first <= ref <= last else last
    return first, last, default


def _terminate_preview(employment_id, form):
    """Προεπισκόπηση της αποχώρησης για τις τιμές της φόρμας: ΤΙ θα
    υπολογιστεί, χωρίς κανένα event. Το σφάλμα (π.χ. κλεισμένη περίοδος,
    ημερομηνία εκτός τρέχοντος μήνα, εκκρεμές Ε3) δείχνεται μέσα στο modal —
    ο χρήστης δεν χρειάζεται να πατήσει «Επιβεβαίωση» για να το μάθει."""
    first, last, default = _term_window()
    window = {
        "term_min": first.isoformat() if first else "",
        "term_max": last.isoformat() if last else "",
        "term_default": (default or today()).isoformat(),
        "term_period": f"{MONTHS_GEN[first.month]} {first.year}" if first else "",
    }
    raw_date = form.get("termination_date") or window["term_default"]
    reason = form.get("reason") or TERMINATION_REASONS[0][0]
    with_notice = bool(form.get("with_notice"))
    try:
        term_date = date.fromisoformat(raw_date)
    except ValueError:
        return {"preview": None, "preview_error": "Μη έγκυρη ημερομηνία λήξης",
                **window}
    try:
        preview = svc.termination_preview(
            employment_id, term_date, reason, with_notice
        )
    except ValueError as exc:
        return {"preview": None, "preview_error": str(exc), **window}
    return {"preview": preview, "preview_error": "", **window}


@bp.post("/<employment_id>/terminate/preview")
def terminate_preview(employment_id):
    """htmx: ξαναχτίζει ΜΟΝΟ την προεπισκόπηση του modal αποχώρησης όταν
    αλλάξει η ημερομηνία ή η αιτία."""
    if employment_id not in proj.employments:
        abort(404)
    return render_template(
        "partials/_emp_terminate_preview.html",
        **_terminate_preview(employment_id, request.form),
    )


@bp.get("/<employment_id>/hire-form")
def hire_details_form(employment_id):
    """Η πραγματική φόρμα πρόσληψης: edit πριν WebE3N, read-only μετά."""
    card = _card(employment_id)
    e = card["e"]
    return render_template(
        "partials/_emp_hire_details_modal.html",
        **card,
        **_hire_json_preview(employment_id),
        hire_values=_hire_form_values(e),
        readonly=card["e3_done"],
    )


@bp.post("/<employment_id>/delete")
def delete_employment(employment_id):
    """Διαγραφή απασχόλησης που καταχωρήθηκε εκ παραδρομής (πριν το ΕΡΓΑΝΗ).

    Η καρτέλα παύει να υπάρχει, οπότε η επιτυχία στέλνει τον χρήστη στη λίστα
    Εργαζομένων· το σφάλμα κρατά τη σελίδα και δείχνει τον λόγο."""
    e = proj.employments.get(employment_id)
    name = e.full_name if e else ""

    def cmd():
        svc.delete_employment(employment_id)
        return f"Διαγράφηκε η απασχόληση: {name}"

    ok, msg = run_command(cmd)
    if not ok:
        return flash_to("", msg, err=True)
    resp = flash_to("", msg, err=False)
    resp.headers["HX-Redirect"] = url_for("persons.index")
    return resp


@bp.post("/<employment_id>/hire-form/json-preview")
def hire_json_preview(employment_id):
    """Live WebE3N JSON των τιμών της φόρμας, χωρίς αποθήκευση."""
    card = _card(employment_id)
    readonly = card["e3_done"]
    try:
        if readonly:
            result = _hire_json_preview(employment_id)
        else:
            if proj.company is None:
                raise ValueError("Δεν έχουν καταχωρηθεί στοιχεία εταιρείας")
            payload = _hire_json_preview_from_form(employment_id, request.form)
            result = {
                "payload_json": json.dumps(
                    payload, ensure_ascii=False, indent=2, default=str
                ),
                "json_error": "",
            }
    except ValueError as exc:
        result = {"payload_json": "", "json_error": str(exc)}
    return render_template(
        "partials/_hire_json_preview.html",
        e=card["e"],
        readonly=readonly,
        **result,
    )


@bp.post("/<employment_id>/hire-form")
def correct_hire_details(employment_id):
    def cmd():
        f = request.form
        pay_type = f.get("pay_type", "monthly")
        amount = form_float(f, "amount")
        specialty_name = f.get("specialty_name", "")
        svc.get_specialty(specialty_name)
        svc.correct_hire_details(
            employment_id,
            form_date(f, "hire_date"),
            pay_type=pay_type,
            monthly_gross=(amount if pay_type == "monthly" else 0.0),
            daily_wage=(amount if pay_type == "daily" else 0.0),
            weekly_hours=form_float(f, "weekly_hours", 40.0),
            weekly_system=form_int(f, "weekly_system", 5),
            weekly_pattern=weekly_pattern(f),
            service_years=form_service_years(f),
            frozen_service_years=form_frozen_service_years(f),
            married=bool(f.get("married")),
            extra_allowances_pct=form_float(f, "extra_allowances_pct"),
            **form_employment_relationship(f),
            **form_oaed_hire_fields(f),
            **form_digital_card_fields(f),
            **schedule_opts(f),
        )
        svc.assign_specialty(
            employment_id,
            specialty_name,
            auxiliary_scheme=f.get("auxiliary_scheme"),
            pron_choice=f.get("pron_choice"),
            branch_no=form_int(f, "branch_no", 0),
        )
        return "Αποθηκεύτηκαν οι διορθώσεις της φόρμας πρόσληψης"

    ok, msg = run_command(cmd)
    if ok:
        return flash_to(_fragment(employment_id), msg, err=False)
    return _modal_error(
        employment_id,
        "hire_details",
        msg,
        readonly=False,
        **_hire_json_preview(employment_id),
    )


def _leave_or_404(employment_id, leave_id):
    e = proj.employments.get(employment_id) or abort(404)
    for lv in e.active_leaves():
        if lv.leave_id == leave_id:
            return lv
    abort(404)


@bp.get("/<employment_id>/leave/<leave_id>/benefit-modal")
def benefit_modal(employment_id, leave_id):
    """htmx: modal καταχώρησης επιδόματος ασθενείας ΕΦΚΑ για μια άδεια."""
    lv = _leave_or_404(employment_id, leave_id)
    return render_template(
        "partials/_emp_benefit_modal.html", lv=lv, **_card(employment_id)
    )


def _modal_error(employment_id, name, msg, **extra):
    """Σε σφάλμα εντολής: ξαναγέμισε το modal με τα δεδομένα του χρήστη
    (retarget στο #modal-host) και δείξε toast σφάλματος."""
    html = render_template(
        f"partials/_emp_{name}_modal.html",
        form=request.form,
        **extra,
        **_card(employment_id),
    )
    resp = flash_to(html, msg, err=True)
    resp.headers["HX-Retarget"] = "#modal-host"
    resp.headers["HX-Reswap"] = "innerHTML"
    return resp


@bp.post("/<employment_id>/contract")
def contract(employment_id):
    def cmd():
        f = request.form
        pay_type = f.get("pay_type", "monthly")
        amount = form_float(f, "amount")
        svc.change_contract(
            employment_id,
            form_date(f, "effective_from"),
            pay_type,
            monthly_gross=(amount if pay_type == "monthly" else 0.0),
            daily_wage=(amount if pay_type == "daily" else 0.0),
            weekly_hours=form_float(f, "weekly_hours", 40.0),
            weekly_system=form_int(f, "weekly_system", 5),
        )
        return "Άλλαξε η σύμβαση — υποβλήθηκε Δήλωση Μεταβολής στο ΕΡΓΑΝΗ"

    ok, msg = run_command(cmd)
    if ok:
        return flash_to(_fragment(employment_id), msg, err=False)
    return _modal_error(employment_id, "contract", msg)


@bp.post("/<employment_id>/ergani/e3")
def ergani_e3(employment_id):
    def cmd():
        res = svc.submit_hire_e3(employment_id)
        return f"Υποβλήθηκε Αναγγελία Πρόσληψης WebE3N ({res['status']})"

    ok, msg = run_command(cmd)
    return flash_to(_fragment(employment_id), msg, err=not ok)


@bp.get("/<employment_id>/ergani/e3/json")
def ergani_e3_json(employment_id):
    """Προεπισκόπηση του ακριβούς WebE3N JSON, χωρίς υποβολή."""
    try:
        payload = svc.build_hire_web_e3n(employment_id)
    except ValueError as exc:
        return Response(str(exc), status=400, content_type="text/plain; charset=utf-8")
    body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return Response(
        body,
        content_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (f'inline; filename="WebE3N-{employment_id}.json"')
        },
    )


@bp.post("/<employment_id>/ergani/wto")
def ergani_wto(employment_id):
    def cmd():
        emp = proj.employments.get(employment_id)
        if emp is None:
            raise ValueError(f"Άγνωστη απασχόληση {employment_id}")
        # ΜΕΤΑΒΛΗΤΟ: δηλώνει τις αδήλωτες ημέρες του σχεδιασμένου ημερήσιου
        # (όχι standing εβδομαδιαίο). ΣΤΑΘΕΡΟ: standing εβδομαδιαίο ΟΧΕ.
        if emp.schedule_mode == "variable":
            res = svc.submit_hire_wto_variable(employment_id)
            return (
                f"Δηλώθηκαν {res['count']} ημερήσια προγράμματα στο ΕΡΓΑΝΗ "
                f"(υποβολή #{res['batch_no']})"
            )
        svc.submit_hire_wto(employment_id)
        return "Υποβλήθηκε ΟΧΕ (σταθερό εβδομαδιαίο) στο ΕΡΓΑΝΗ"

    ok, msg = run_command(cmd)
    return flash_to(_fragment(employment_id), msg, err=not ok)


@bp.post("/<employment_id>/insurance")
def insurance(employment_id):
    def cmd():
        f = request.form
        svc.assign_specialty(
            employment_id,
            f.get("specialty_name", ""),
            auxiliary_scheme=f.get("auxiliary_scheme"),
            pron_choice=f.get("pron_choice"),
            branch_no=form_int(f, "branch_no"),
        )
        return "Ενημερώθηκε το ασφαλιστικό προφίλ της απασχόλησης"

    ok, msg = run_command(cmd)
    if ok:
        return flash_to(_fragment(employment_id), msg, err=False)
    return _modal_error(employment_id, "insurance", msg)


@bp.post("/<employment_id>/schedule")
def schedule(employment_id):
    def cmd():
        f = request.form
        pattern = weekly_pattern(f)  # ο έλεγχος ωρών γίνεται στο svc.set_schedule
        end = form_date(f, "end_date") if f.get("end_date") else None
        svc.set_schedule(employment_id, form_date(f, "effective_from"), pattern, end)
        suffix = f" — λήγει {end.strftime('%d/%m/%Y')}" if end else ""
        return f"Ενημερώθηκε το πρόγραμμα ({len(pattern)} ημέρες/εβδομάδα){suffix}"

    ok, msg = run_command(cmd)
    if ok:
        return flash_to(_fragment(employment_id), msg, err=False)
    return _modal_error(employment_id, "schedule", msg)


@bp.post("/<employment_id>/schedule/daily")
def schedule_daily(employment_id):
    """ΗΜΕΡΗΣΙΟ πρόγραμμα σε εύρος ημερομηνιών — υπερισχύει του εβδομαδιαίου.
    Νέα υποβολή αντικαθιστά τις ίδιες ημέρες (και υποβάλλεται στο ΕΡΓΑΝΗ)."""

    def cmd():
        f = request.form
        d_from = form_date(f, "date_from")
        d_to = form_date(f, "date_to")
        if d_to < d_from:
            raise ValueError("Η ημερομηνία «έως» προηγείται της «από»")
        hours = (f.get("hours") or "").strip()  # κενό = ρεπό
        days, d = {}, d_from
        while d <= d_to:
            days[d] = hours
            d += timedelta(days=1)
        svc.set_daily_schedule(employment_id, days)
        what = f"ωράριο {hours}" if hours else "ΡΕΠΟ"
        return (
            f"Ημερήσιο πρόγραμμα: {len(days)} ημέρες ({what}) — "
            "υποβλήθηκε ΟΧΕ στο ΕΡΓΑΝΗ"
        )

    ok, msg = run_command(cmd)
    if ok:
        return flash_to(_fragment(employment_id), msg, err=False)
    return _modal_error(employment_id, "daily", msg)


@bp.post("/<employment_id>/attendance")
def attendance(employment_id):
    """Καταγραφή παρουσιών σε εύρος ημερομηνιών (μόνο εργάσιμες).

    ΑΠΑΡΑΙΤΗΤΟ για ημερομίσθιους: η μισθοδοσία τους είναι ημερομίσθιο x
    ημέρες παρουσίας — χωρίς παρουσίες δεν υπολογίζεται τίποτα."""

    def cmd():
        f = request.form
        d_from = form_date(f, "date_from")
        d_to = form_date(f, "date_to")
        if d_to < d_from:
            raise ValueError("Η ημερομηνία «έως» προηγείται της «από»")
        hours = form_float(f, "hours", 8.0)
        holidays = bool(f.get("holidays"))  # και αργίες/Σαββατοκύριακα
        n, d = 0, d_from
        while d <= d_to:
            if holidays or is_working_day(d):
                svc.record_attendance(employment_id, d, hours)
                n += 1
            d += timedelta(days=1)
        if n == 0:
            raise ValueError("Καμία εργάσιμη ημέρα στο διάστημα που επιλέξατε")
        return f"Καταγράφηκαν {n} ημέρες παρουσίας ({hours:g} ώρες/ημέρα)"

    ok, msg = run_command(cmd)
    if ok:
        return flash_to(_fragment(employment_id), msg, err=False)
    return _modal_error(employment_id, "attendance", msg)


@bp.post("/<employment_id>/leave")
def leave(employment_id):
    def cmd():
        f = request.form
        ltype = f.get("leave_type", "ANNUAL")
        d_from = form_date(f, "date_from")
        d_to = form_date(f, "date_to")
        notes = f.get("notes", "")
        if ltype == "ANNUAL":
            res = svc.plan_annual_leave(employment_id, d_from, d_to, notes)
            return (
                f"Κανονική άδεια {res['days']} εργάσιμων ημερών — "
                f"υπόλοιπο {res['remaining']}/{res['entitlement']}"
            )
        svc.record_leave(employment_id, LeaveType[ltype], d_from, d_to, notes)
        return f"Καταχωρήθηκε: {LEAVE_LABELS.get(LeaveType[ltype].value, ltype)}"

    ok, msg = run_command(cmd)
    if ok:
        return flash_to(_fragment(employment_id), msg, err=False)
    return _modal_error(employment_id, "leave", msg)


@bp.post("/<employment_id>/leave/<leave_id>/benefit")
def sickness_benefit(employment_id, leave_id):
    """Το επίδομα ασθενείας που ενέκρινε ο ΕΦΚΑ — αφαιρείται από όσα οφείλει
    ο εργοδότης για τις ημέρες που πληρώνονται κανονικά."""

    def cmd():
        f = request.form
        amount = form_float(f, "amount")
        svc.record_sickness_benefit(
            employment_id, leave_id, amount, f.get("reference", "")
        )
        return f"Καταχωρήθηκε επίδομα ΕΦΚΑ {amount:.2f}€"

    ok, msg = run_command(cmd)
    if ok:
        return flash_to(_fragment(employment_id), msg, err=False)
    lv = _leave_or_404(employment_id, leave_id)
    return _modal_error(employment_id, "benefit", msg, lv=lv)


@bp.post("/<employment_id>/leave/<leave_id>/cancel")
def leave_cancel(employment_id, leave_id):
    def cmd():
        svc.cancel_leave(employment_id, leave_id)
        return "Η άδεια ακυρώθηκε"

    ok, msg = run_command(cmd)
    return flash_to(_fragment(employment_id), msg, err=not ok)


@bp.post("/<employment_id>/terminate")
def terminate(employment_id):
    def cmd():
        f = request.form
        svc.terminate(
            employment_id,
            form_date(f, "termination_date"),
            f.get("reason", ""),
            with_notice=bool(f.get("with_notice")),
        )
        return (
            "Καταχωρήθηκε η αποχώρηση — Ψηφιακή Αναγγελία Λήξης στο ΕΡΓΑΝΗ "
            "και εκκαθάριση αποδοχών"
        )

    ok, msg = run_command(cmd)
    if ok:
        return flash_to(_fragment(employment_id), msg, err=False)
    return _modal_error(
        employment_id,
        "terminate",
        msg,
        **_terminate_preview(employment_id, request.form),
    )


# ⚠️ ΔΕΝ υπάρχουν routes «Υπολογισμός αποζημίωσης αδείας/απόλυσης»: ΟΛΗ η
# εκκαθάριση εκτελείται αυτόματα με την «Επιβεβαίωση αποχώρησης»
# (`svc.terminate` -> `_settle_exit_accruals`). Η καρτέλα μόνο εμφανίζει τα
# αποτελέσματα και τις εκτυπώσεις τους.
