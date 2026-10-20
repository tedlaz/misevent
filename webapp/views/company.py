"""Εταιρεία: στοιχεία εργοδότη (Μητρώο Εργοδοτών e-ΕΦΚΑ) — απαιτούνται για
ΑΠΔ — και ΕΙΔΙΚΟΤΗΤΕΣ (θέσεις εργασίας) με την ασφαλιστική τριάδα ΕΦΚΑ/ΤΕΚΑ.
Η πρόσληψη διαλέγει από τις ειδικότητες που δηλώνονται εδώ."""

from io import BytesIO

from flask import Blueprint, abort, g, render_template, request, send_file

from misevent.integrations import ergani, ergani_parameters, kpk_registry
from misevent.rules import config
from misevent.rules.constraints import norm_name

from .. import osyk
from ..clock import today
from ..ergani_credentials import ENVIRONMENTS
from ..helpers import flash_to, run_command
from ..system import proj, registry, svc

bp = Blueprint("company", __name__, url_prefix="/company")

_COMPANY_FORM_FIELDS = (
    "ame", "afm", "legal_name", "first_name", "father_name", "activity",
    "local_office_code", "local_office_name",
)


def _company_form_values(source=None) -> dict[str, str]:
    """Τιμές της φόρμας είτε από το projection είτε από αποτυχημένο POST."""
    if source is None:
        return {field: "" for field in _COMPANY_FORM_FIELDS}
    if hasattr(source, "get"):
        return {field: source.get(field, "") for field in _COMPANY_FORM_FIELDS}
    return {field: getattr(source, field) for field in _COMPANY_FORM_FIELDS}


def _specs():
    """Οι δηλωμένες ειδικότητες, αλφαβητικά."""
    return sorted(proj.specialties.values(), key=lambda s: s.name)


def _panel_ctx():
    """Context του panel ειδικοτήτων.

    ⚠️ ΤΟ ΜΗΤΡΩΟ ΚΠΚ ΔΕΝ ΕΙΝΑΙ ΕΔΩ: είναι θεσμικό και κοινό για όλες τις
    εταιρείες — ζει στη σελίδα «Παράμετροι» (`webapp/views/settings.py`).
    Εδώ μένουν μόνο τα dropdowns των ειδικοτήτων, που ΔΙΑΒΑΖΟΥΝ το μητρώο.
    """
    return {
        "specialties": _specs(),
        "config": config,
        **_branches_ctx(),
    }


def _branches_ctx():
    branches = sorted(proj.branches.values(), key=lambda b: b.code)
    descriptions = {
        b.code: {
            "sepe": ergani_parameters.description("sepe", b.sepe_code),
            "oaed": ergani_parameters.description("oaed", b.oaed_code),
            "kad": ergani_parameters.description("kad", b.kad_code),
            "koinotites": ergani_parameters.description(
                "koinotites", b.kallikratis_code
            ),
        }
        for b in branches
    }
    return {
        "branches": branches,
        "has_main_branch": 0 in proj.branches,
        "branch_descriptions": descriptions,
        "sepe_rows": ergani_parameters.catalog("sepe"),
        "oaed_rows": ergani_parameters.catalog("oaed"),
        "kad_rows": ergani_parameters.catalog("kad"),
        "koinotites_rows": ergani_parameters.catalog("koinotites"),
    }


def _next_branch_code() -> int:
    code = 1
    while code in proj.branches:
        code += 1
    return code


@bp.get("/")
def index():
    return render_template(
        "company.html", c=proj.company,
        company_values=_company_form_values(proj.company), **_panel_ctx(),
        **_ergani_ctx(),
    )


# ------------------------------------------- Σύνδεση ΕΡΓΑΝΗ (ΜΟΝΟ διαχειριστές)
def _ergani_ctx() -> dict:
    store = registry.store_name(g.company_id)
    return {"ergani": registry.secrets.get(store), "environments": ENVIRONMENTS}


def _ergani_html(msg: str, err: bool):
    return flash_to(render_template("partials/_company_ergani.html", **_ergani_ctx()),
                    msg, err=err)


def _require_admin():
    if not (g.user and g.user.is_admin):
        abort(403)


@bp.post("/ergani")
def ergani_save():
    _require_admin()
    f = request.form
    ok, msg = run_command(lambda: registry.set_ergani(
        g.company_id, f.get("environment", "simulate"), f.get("username", ""),
        f.get("password", "")) or "Αποθηκεύτηκαν τα στοιχεία ΕΡΓΑΝΗ")
    return _ergani_html(msg, not ok)


@bp.post("/ergani/test")
def ergani_test():
    """Μόνο authentication — καμία υποβολή εντύπου."""
    _require_admin()
    client = registry.secrets.client(registry.store_name(g.company_id))
    if client.simulate:
        return _ergani_html("Προσομοίωση: δεν γίνεται σύνδεση στο ΕΡΓΑΝΗ", False)
    try:
        client.authenticate()
    except ergani.ErganiError as exc:
        return _ergani_html(f"Αποτυχία σύνδεσης: {exc}", True)
    return _ergani_html(f"Επιτυχής σύνδεση ({ENVIRONMENTS[client.environment]})", False)


@bp.post("/")
def save():
    submitted = _company_form_values(request.form)

    def cmd():
        f = request.form
        svc.register_company(
            ame=f.get("ame", "").strip(),
            afm=f.get("afm", "").strip(),
            legal_name=f.get("legal_name", "").strip().upper(),
            # Ατομική επιχείρηση: όνομα/πατρώνυμο του φυσικού προσώπου.
            # Κενά για εταιρίες (τότε το JL10 γράφει 2.4 = 0).
            first_name=f.get("first_name", "").strip().upper(),
            father_name=f.get("father_name", "").strip().upper(),
            local_office_code=f.get("local_office_code") or "0000000000",
            local_office_name=f.get("local_office_name", ""),
            activity=f.get("activity", ""),
        )
        return "Αποθηκεύτηκαν τα στοιχεία της εταιρείας"

    ok, msg = run_command(cmd)
    values = _company_form_values(proj.company) if ok else submitted
    html = render_template(
        "partials/_company_form.html", c=proj.company, company_values=values
    )
    return flash_to(html, msg, err=not ok)


# ------------------- Εξαγωγή/εισαγωγή ΟΛΩΝ των στοιχείων εταιρείας (JSON)
# ΕΝΑ αρχείο: εργοδότης, παραρτήματα, ειδικότητες — ΚΑΙ το κοινό μητρώο ΚΠΚ,
# ώστε ένα φρέσκο σύστημα να στήνεται με μία κίνηση (βλ. settings.py).
@bp.get("/export")
def export_json():
    """Κατέβασμα — ο χρήστης διαλέγει πού θα αποθηκευτεί από τον browser."""
    c = proj.company
    stamp = today().isoformat()
    name = f"company_{c.afm}_{stamp}.json" if c else f"company_{stamp}.json"
    return send_file(
        BytesIO(svc.export_company_bytes()), mimetype="application/json",
        as_attachment=True, download_name=name,
    )


@bp.post("/import")
def import_json():
    """Φόρτωση αρχείου εξαγωγής: κάθε ενότητα περνά από τα κανονικά commands
    (γράφονται events), ώστε να ισχύουν όλοι οι έλεγχοι."""

    def cmd():
        f = request.files.get("file")
        if f is None or not f.filename:
            raise ValueError("Επιλέξτε αρχείο JSON")
        res = svc.import_company(f.read())
        c = res["counts"]
        parts = []
        if c["company"]:
            parts.append("στοιχεία εργοδότη")
        for key, label in (("branches", "παραρτήματα"), ("kpks", "ΚΠΚ"),
                           ("specialties", "ειδικότητες")):
            if c[key]:
                parts.append(f"{c[key]} {label}")
        if not parts:
            raise ValueError(
                "Δεν εισήχθη τίποτα — " + " | ".join(res["errors"][:3])
            )
        msg = "Εισήχθησαν: " + ", ".join(parts)
        if res["errors"]:
            msg += (f" · {len(res['errors'])} σφάλματα: "
                    + " | ".join(res["errors"][:3]))
        return msg

    ok, msg = run_command(cmd)
    # Το αρχείο αγγίζει ΟΛΑ τα tabs: μαζί με το panel ειδικοτήτων
    # ανανεώνονται OOB και η φόρμα εργοδότη και τα παραρτήματα.
    html = render_template("partials/_company_specialties.html", **_panel_ctx())
    html += render_template(
        "partials/_company_form.html", c=proj.company, oob=True,
        company_values=_company_form_values(proj.company),
    )
    html += render_template("partials/_company_branches.html", oob=True,
                            **_branches_ctx())
    return flash_to(html, msg, err=not ok)


@bp.post("/branches")
def branch_save():
    def cmd():
        f = request.form
        svc.define_branch(
            code=f.get("code", "0"), street=f.get("street", ""),
            street_no=f.get("street_no", ""),
            postal_code=f.get("postal_code") or "00000",
            city=f.get("city", ""), sepe_code=f.get("sepe_code", ""),
            oaed_code=f.get("oaed_code", ""), kad_code=f.get("kad_code", ""),
            kallikratis_code=f.get("kallikratis_code", ""),
        )
        return f"Αποθηκεύτηκε το παράρτημα {int(f.get('code', '0'))}"

    ok, msg = run_command(cmd)
    html = render_template("partials/_company_branches.html", **_branches_ctx())
    return flash_to(html, msg, err=not ok)


@bp.get("/branches/modal")
def branch_modal():
    raw_code = request.args.get("code")
    if raw_code is None:
        branch = None
        suggested_code = _next_branch_code()
    else:
        try:
            code = int(raw_code)
        except ValueError:
            abort(404)
        branch = proj.branches.get(code)
        if branch is None:
            abort(404)
        suggested_code = code
    return render_template(
        "partials/_company_branch_modal.html", branch=branch,
        suggested_code=suggested_code, **_branches_ctx(),
    )


@bp.get("/branches/modal/close")
def branch_modal_close():
    return ""


# ------------------------- Τοπική Δ/νση e-ΕΦΚΑ (ΑΠΔ No 3/No 4) — lookup
# Ίδιο μητρώο και ίδια συμπεριφορά με το lookup του eΑΠΔ viewer: αναζήτηση
# χωρίς τόνους σε κωδικό/ονομασία, και η ονομασία (No 4) συμπληρώνεται
# ΑΥΤΟΜΑΤΑ από τον επιλεγμένο κωδικό, κανονικοποιημένη σε κεφαλαία χωρίς
# τόνους — όπως θα γραφτεί και στο αρχείο ΑΠΔ.
@bp.get("/topik/modal")
def topik_modal():
    # Ανοίγει ΚΕΝΟ με ολόκληρο το μητρώο: προσυμπληρωμένο κριτήριο θα
    # ανάγκαζε τον χρήστη να το σβήσει πρώτα για να ψάξει κάτι άλλο.
    return render_template(
        "partials/_topik_modal.html", rows=osyk.search_topiks("")
    )


@bp.get("/topik/search")
def topik_search():
    q = request.args.get("q", "")
    return render_template("partials/_topik_results.html",
                           rows=osyk.search_topiks(q))


@bp.get("/topik/modal/close")
def topik_modal_close():
    """Κλείσιμο modal (Άκυρο) — αδειάζει τον host."""
    return ""


@bp.get("/topik/pick")
def topik_pick():
    """Επιλογή από το μητρώο: γυρίζει ΜΟΝΟ τα δύο πεδία ως OOB — το κενό
    υπόλοιπο πέφτει στον #employer-modal-host και κλείνει το modal."""
    r = osyk.topik(request.args.get("code", "")) or {}
    return render_template(
        "partials/_company_office_fields.html",
        oob=True,
        code=r.get("code", ""),
        office_name=norm_name(r.get("per", "")),
    )


# --------------------------------------- Ειδικότητες (θέσεις εργασίας)
@bp.get("/specialties/modal")
def specialty_modal():
    """Modal νέας ειδικότητας — ή επεξεργασίας, αν δοθεί ?name=."""
    return render_template(
        "partials/_specialty_modal.html", **_specialty_ctx(request.args.get("name"))
    )


@bp.get("/specialties/modal/close")
def specialty_modal_close():
    """Κλείσιμο modal (Άκυρο) — αδειάζει τον host."""
    return ""


@bp.get("/specialties/ergani/search")
def ergani_specialty_search():
    """Autocomplete του μεγάλου πίνακα ``eidikotites`` χωρίς πλήρες render."""
    # Το request προέρχεται από όποιο από τα δύο πεδία πληκτρολογεί ο
    # χρήστης: αναζήτηση είτε με code είτε με λέξεις της description.
    q = (
        request.args.get("ergani_specialty_code", "").strip()
        or request.args.get("ergani_specialty_description", "").strip()
    )
    return render_template(
        "partials/_ergani_specialty_results.html",
        rows=ergani_parameters.search_specialties(q),
        searched=bool(q),
    )


@bp.get("/specialties/ergani/pick")
def ergani_specialty_pick():
    """Συμπληρώνει μαζί code/description ώστε να μην προκύπτει λάθος ζεύγος."""
    row = ergani_parameters.specialty(request.args.get("code", "")) or {}
    return render_template(
        "partials/_ergani_specialty_fields.html",
        code=row.get("code", ""),
        description=row.get("description", ""),
        oob=True,
    )


def _form_packages(f):
    """Τα πακέτα της φόρμας ειδικότητας, ΑΝΑ ΡΟΛΟ. Η φόρμα έχει ΕΝΑ ΚΑΔ +
    ΜΙΑ ειδικότητα κύριας (`main_*`, κοινά στους δύο κύριους ρόλους) και ΕΝΑ
    ΚΑΔ πρόνοιας (`pron_kad`, κοινό στους ρόλους πρόνοιας)· το ΤΕΚΑ είναι
    σταθερό (το βάζει η υπηρεσία).

    ➜ Οι ρόλοι πρόνοιας που η φόρμα ΚΡΥΒΕΙ (βλ. `.pron-sec` στο app.css)
    αγνοούνται: ένα κρυμμένο `<select>` εξακολουθεί να υποβάλλεται, οπότε μια
    παλιά επιλογή θα έφτανε στο `define_specialty` ως «πακέτο που δεν
    αντιστοιχεί στον συνδυασμό». Ό,τι δεν βλέπει ο χρήστης δεν μετράει."""
    hidden = {"PRON_UNIFORM"} if f.get("pron_differentiated") else {
        "PRON_NEW", "PRON_OLD"
    }
    out = {}
    for role, spec_role in config.SPECIALTY_ROLES.items():
        if role in hidden or role == "TEKA":
            continue
        if spec_role.tier == "welfare":
            kad, code = f.get("pron_kad", ""), f.get(f"{role}_code", "")
        else:
            kad, code = f.get("main_kad", ""), f.get("main_code", "")
        out[role] = {
            "kpk": f.get(f"{role}_kpk", "").strip(),
            "kad": kad.strip(),
            "specialty_code": code.strip(),
        }
    return out


def _form_pron(f):
    """Ρυθμίσεις προνοιακής ασφάλισης της φόρμας ειδικότητας."""
    return {
        "enabled": bool(f.get("pron_enabled")),
        "name": f.get("pron_name", "").strip(),
        "mandatory": bool(f.get("pron_mandatory")),
        "differentiated": bool(f.get("pron_differentiated")),
    }


def _specialty_ctx(name=None, form=None):
    """Context του modal ειδικότητας — ΜΙΑ πηγή για άνοιγμα και για σφάλμα."""
    return {
        "sp": proj.specialties.get(name) if name else None,
        "form": form,
        "config": config,
        # Τα dropdowns κάθε ρόλου δείχνουν ΜΟΝΟ ΚΠΚ της βαθμίδας του.
        "kpks_by_tier": {t: svc.kpks_by_tier(t) for t in config.TIERS},
        "kpkreg": kpk_registry,
    }


@bp.get("/specialties/main")
def specialty_main():
    """Το μπλοκ κύριας/επικουρικής, μετά από αλλαγή ΚΑΔ, ειδικότητας ή
    πρόνοιας: τα ΚΠΚ πετιούνται ώστε να ξαναπροταθούν (Ο.Σ.Υ.Κ.)."""
    form = request.args.copy()
    for role in ("MAIN_EPIK", "MAIN"):
        form.pop(f"{role}_kpk", None)
    return render_template("partials/_specialty_main.html", **_specialty_ctx(form=form))


@bp.get("/specialties/pron")
def specialty_pron():
    """Οι κωδικοί πρόνοιας, μετά από αλλαγή ΚΑΔ ή ειδικότητας πρόνοιας."""
    form = request.args.copy()
    trigger = request.headers.get("HX-Trigger-Name", "")
    for role in ("PRON_NEW", "PRON_OLD", "PRON_UNIFORM"):
        if trigger in ("pron_kad", f"{role}_code"):
            form.pop(f"{role}_kpk", None)
    return render_template("partials/_specialty_pron.html", **_specialty_ctx(form=form))


@bp.post("/specialties")
def specialty_save():
    def cmd():
        f = request.form
        name = svc.define_specialty(
            f.get("name", ""),
            _form_packages(f),
            f.get("ergani_specialty_code", ""),
            f.get("ergani_specialty_description", ""),
            epik_carriers=f.getlist("epik_carriers"),
            pron=_form_pron(f),
        )
        return f"Αποθηκεύτηκε η ειδικότητα «{name}»"

    ok, msg = run_command(cmd)
    if ok:
        # Φρέσκο panel (με κενό modal-host) -> κλείνει το modal
        html = render_template(
            "partials/_company_specialties.html", **_panel_ctx()
        )
        return flash_to(html, msg, err=False)

    # ΣΦΑΛΜΑ: κράτα το modal ανοιχτό με τα δεδομένα του χρήστη
    html = render_template(
        "partials/_specialty_modal.html",
        **_specialty_ctx(request.form.get("name"), request.form),
    )
    resp = flash_to(html, msg, err=True)
    resp.headers["HX-Retarget"] = "#modal-host"
    resp.headers["HX-Reswap"] = "innerHTML"
    return resp

