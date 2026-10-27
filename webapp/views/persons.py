"""
Πρόσωπα (φυσικά πρόσωπα — σταθερή ταυτότητα).

Εδώ φαίνεται η θεμελιώδης διάκριση: ένα Person έχει ΠΟΛΛΕΣ περιόδους
απασχόλησης. Όλες οι ενέργειες (νέο πρόσωπο, επεξεργασία στοιχείων,
πληροφορίες, επαναπρόσληψη) γίνονται σε MODAL από το μητρώο — δεν υπάρχει
ξεχωριστή σελίδα-καρτέλα προσώπου.
"""

import base64
from datetime import date
from io import BytesIO

from flask import Blueprint, abort, render_template, request, send_file

from misevent.integrations import ergani_parameters as ergani_params

from .. import amka_pdf, clock
from .. import queries as q
from ..helpers import (
    flash_to,
    form_contact_fields,
    form_date,
    form_digital_card_fields,
    form_employment_relationship,
    form_float,
    form_frozen_service_years,
    form_hire_status_fields,
    form_int,
    form_migration,
    form_oaed_hire_fields,
    form_optional_date,
    form_service_years,
    form_third_country_fields,
    frozen_service_years_fields,
    pdf_response,
    run_command,
    service_years_fields,
)
from ..system import proj, svc
from .employments import form_ctx, schedule_opts, weekly_pattern

bp = Blueprint("persons", __name__, url_prefix="/persons")


@bp.get("/")
def index():
    return render_template("persons_list.html", rows=q.person_rows())


# ------------------------------------------------- Νέο πρόσωπο: modal
@bp.get("/new")
def new_modal():
    """Ανοίγει το modal νέου προσώπου. Κλείνει μόνο με Καταχώρηση ή Άκυρο."""
    return render_template(
        "partials/_person_modal.html",
        identity_types=ergani_params.identity_types(),
        nationalities=ergani_params.nationalities(),
        education_levels=ergani_params.education_levels(),
        eu_nationality_codes=ergani_params.eu_nationality_codes(),
    )


def _identity_dates(form) -> dict:
    """Ημ/νίες ταυτότητας ΜΟΝΟ αν υπάρχουν στη φόρμα: κενό πεδίο = σβήσιμο,
    απόν πεδίο = χωρίς αλλαγή (το None μόνο του δεν τα ξεχωρίζει)."""
    return {k: form_optional_date(form, k)
            for k in ("identity_issue_date", "identity_expiry_date") if k in form}


def _form_documents(form) -> dict:
    """{είδος: PDF bytes} από τα hidden `doc_<είδος>` του modal νέου προσώπου."""
    docs = {}
    for kind in amka_pdf.ATTACHMENT_KINDS:
        raw = form.get(f"doc_{kind}")
        if raw:
            try:
                docs[kind] = base64.b64decode(raw, validate=True)
            except ValueError:  # binascii.Error ⊂ ValueError (και μη-ASCII)
                raise ValueError("Χαλασμένο συνημμένο — ανεβάστε ξανά την αίτηση") from None
    return docs


@bp.post("/new/amka")
def new_from_amka():
    """Προσυμπλήρωση του modal από Βεβαίωση ΑΜΚΑ (PDF). ΔΕΝ καταχωρεί τίποτα:
    ξαναζωγραφίζει τη φόρμα — ό,τι είχε ήδη γράψει ο χρήστης μένει, όσα
    βρέθηκαν στη βεβαίωση το αντικαθιστούν. Η καταχώρηση περνά μετά από τους
    κανονικούς ελέγχους του `create`."""
    form = request.form.to_dict()
    f = request.files.get("amka_pdf")
    try:
        if f is None or not f.filename:
            raise ValueError("Επιλέξτε αρχείο (PDF βεβαίωσης ΑΜΚΑ ή .json αίτησης)")
        found, docs = amka_pdf.read_upload(f.read())
        if not found:
            raise ValueError("Δεν βρέθηκαν στοιχεία στο αρχείο")
        form.update(found)
        # Τα συνημμένα ταξιδεύουν ως hidden πεδία (base64) ως το `create` —
        # καμία προσωρινή αποθήκευση στον server πριν την καταχώρηση.
        form.update({f"doc_{k}": base64.b64encode(v).decode() for k, v in docs.items()})
        bad = amka_pdf.problems(found)
        ok, msg = not bad, (f"Συμπληρώθηκαν {len(found)} πεδία"
                            + (f" και {len(docs)} συνημμένα" if docs else "")
                            + " από το αρχείο — ελέγξτε τα και συμπληρώστε τα υπόλοιπα")
        if bad:
            msg += " ⚠ " + " · ".join(bad)
    except ValueError as exc:
        ok, msg = False, str(exc)
    html = render_template(
        "partials/_person_modal.html", form=form,
        identity_types=ergani_params.identity_types(),
        nationalities=ergani_params.nationalities(),
        education_levels=ergani_params.education_levels(),
        eu_nationality_codes=ergani_params.eu_nationality_codes(),
    )
    return flash_to(html, msg, err=not ok)


@bp.get("/form.html")
def blank_form():
    """Κενή αίτηση στοιχείων για τον υποψήφιο — αυτόνομο .html· το .json που
    βγάζει ανεβαίνει από το ίδιο κουμπί με τη βεβαίωση ΑΜΚΑ."""
    html = render_template(
        "candidate_form.html", fields=amka_pdf.FORM_FIELDS,
        tc_groups=amka_pdf.TC_GROUPS, lists=amka_pdf.choices(),
        dates=amka_pdf.DATE_FIELDS, digits={"afm": 9, "amka": 11, "ama": 9, "postal_code": 5},
        defaults={"nationality_code": "048"}, schema=amka_pdf.CANDIDATE_SCHEMA,
        eu_codes=list(ergani_params.eu_nationality_codes()),
    )
    return send_file(
        BytesIO(html.encode("utf-8")), mimetype="text/html",
        as_attachment=True, download_name="aitisi_stoixeion_ergazomenou.html",
    )


@bp.get("/modal/close")
def modal_close():
    """Κλείσιμο modal (Άκυρο) — αδειάζει τον host."""
    return ""


# ------------------------- Εξαγωγή/εισαγωγή ΠΡΟΣΩΠΩΝ (JSON)
# ΜΟΝΟ η ταυτότητα των φυσικών προσώπων — ΟΧΙ απασχολήσεις (η πρόσληψη είναι
# ξεχωριστή πράξη με δικούς της όρους) ούτε κίνηση (παρουσίες/άδειες/
# μισθοδοσίες: ζουν στο event log, που έχει δικό του backup).
@bp.get("/export")
def export_json():
    """Κατέβασμα — ο χρήστης διαλέγει πού θα αποθηκευτεί από τον browser."""
    c = proj.company
    stamp = clock.today().isoformat()
    name = f"persons_{c.afm}_{stamp}.json" if c else f"persons_{stamp}.json"
    return send_file(
        BytesIO(svc.export_persons_bytes()), mimetype="application/json",
        as_attachment=True, download_name=name,
    )


@bp.post("/import")
def import_json():
    """Φόρτωση αρχείου: κάθε γραμμή περνά από τα κανονικά commands (ίδιο ΑΦΜ
    = ενημέρωση, αλλιώς νέα καταχώρηση), ώστε να ισχύουν όλοι οι έλεγχοι."""

    def cmd():
        f = request.files.get("file")
        if f is None or not f.filename:
            raise ValueError("Επιλέξτε αρχείο JSON")
        res = svc.import_persons(f.read())
        c = res["counts"]
        parts = [
            f"{c[key]} {label}"
            for key, label in (("created", "νέοι"), ("updated", "ενημερώθηκαν"))
            if c[key]
        ]
        if not parts:
            raise ValueError(
                "Δεν εισήχθη κανένας εργαζόμενος — "
                + (" | ".join(res["errors"][:3]) or "το αρχείο ήταν κενό")
            )
        msg = "Εργαζόμενοι: " + ", ".join(parts)
        if res["errors"]:
            msg += (f" · {len(res['errors'])} σφάλματα: "
                    + " | ".join(res["errors"][:3]))
        return msg

    ok, msg = run_command(cmd)
    html = render_template("partials/_persons_panel.html", rows=q.person_rows())
    return flash_to(html, msg, err=not ok)


@bp.post("/")
def create():
    def cmd():
        f = request.form
        docs = _form_documents(f)  # ΠΡΙΝ την καταχώρηση: χαλασμένο = τίποτα
        person_id = svc.register_person(
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
            **_identity_dates(f),
            education_level=f.get("education_level", ""),
            **form_contact_fields(f),
            **form_third_country_fields(f),
        )
        for kind, content in docs.items():
            svc.attach_document(person_id, kind, content, f"{kind}.pdf")
        return (f"Καταχωρήθηκε ο εργαζόμενος {f.get('last_name')} "
                f"{f.get('first_name')} — κάντε πρόσληψη από το badge «πρόσληψη»")

    ok, msg = run_command(cmd)
    if ok:
        # Επιτυχία: αντικαθιστά όλο το panel (με κενό modal-host) -> κλείνει modal
        html = render_template("partials/_persons_panel.html", rows=q.person_rows())
        return flash_to(html, msg, err=False)

    # ΣΦΑΛΜΑ: ξαναγέμισε το modal με τα δεδομένα του χρήστη ώστε να διορθώσει
    html = render_template(
        "partials/_person_modal.html", form=request.form,
        identity_types=ergani_params.identity_types(),
        nationalities=ergani_params.nationalities(),
        education_levels=ergani_params.education_levels(),
        eu_nationality_codes=ergani_params.eu_nationality_codes(),
    )
    resp = flash_to(html, msg, err=True)
    resp.headers["HX-Retarget"] = "#modal-host"
    resp.headers["HX-Reswap"] = "innerHTML"
    return resp


# ------------------------------------------------- Επεξεργασία προσώπου: modal
@bp.get("/<person_id>/edit")
def edit_modal(person_id):
    """Ανοίγει το modal επεξεργασίας στοιχείων υπάρχοντος προσώπου."""
    p = proj.persons.get(person_id)
    if p is None:
        abort(404)
    return render_template(
        "partials/_person_edit_modal.html", p=p,
        identity_types=ergani_params.identity_types(),
        nationalities=ergani_params.nationalities(),
        education_levels=ergani_params.education_levels(),
        eu_nationality_codes=ergani_params.eu_nationality_codes(),
    )


@bp.get("/<person_id>/info")
def info_modal(person_id):
    """Modal πληροφοριών: περίοδοι απασχόλησης, άδειες, ασθένεια (read-only)."""
    info = q.person_info(person_id, clock.today().year)
    if info is None:
        abort(404)
    education = info["person"].education_level
    education_level_label = next(
        (
            f'{row["code"]} — {row["description"]}'
            for row in ergani_params.education_levels()
            if row["code"] == education
        ),
        education or "—",
    )
    return render_template(
        "partials/_person_info_modal.html",
        education_level_label=education_level_label,
        documents=_documents(person_id),
        cert_year=clock.today().year - 1,  # οι βεβαιώσεις αφορούν συνήθως το προηγούμενο
        **info,
    )


@bp.get("/<person_id>/certificates")
def certificates_modal(person_id):
    """Modal «📄 Βεβαιώσεις» της λίστας εργαζομένων."""
    person = proj.persons.get(person_id)
    if person is None:
        abort(404)
    return render_template("partials/_person_certificates_modal.html",
                           person=person, cert_year=clock.today().year - 1)


@bp.get("/<person_id>/certificate/<kind>")
def certificate(person_id, kind):
    """Βεβαιώσεις εργαζομένου (PDF σε νέα καρτέλα): `tax` = ετήσια
    αποδοχών (?year=), `employment` = εργασίας/προϋπηρεσίας."""
    from .. import pdf_reports

    person = proj.persons.get(person_id)
    if person is None:
        abort(404)
    if kind == "tax":
        year = request.args.get("year", type=int) or clock.today().year - 1
        return pdf_response(lambda: pdf_reports.tax_certificate_pdf(person_id, year),
                            f"vevaiosi_apodoxon_{year}_{person.afm}.pdf")
    if kind == "employment":
        return pdf_response(lambda: pdf_reports.employment_certificate_pdf(person_id),
                            f"vevaiosi_ergasias_{person.afm}.pdf")
    abort(404)


@bp.get("/<person_id>/pay")
def pay_modal(person_id):
    """Modal καρτέλας αποδοχών & πληρωμών του εργαζομένου — ΟΛΕΣ οι
    προσλήψεις του, ανά έτος (read-only)."""
    person = proj.persons.get(person_id)
    if person is None:
        abort(404)
    card = q.employee_pay_card(person_id, clock.today())
    return render_template("partials/_person_pay_modal.html", person=person,
                           hires=card["hires"], pay_years=card["years"])


# ------------------------------------------- Έγγραφα (βάση εγγράφων, όχι events)
def _documents(person_id) -> list[dict]:
    return [{**d, "added": date.fromisoformat(d["added_at"][:10])}
            for d in svc.person_documents(person_id)]


def _documents_html(person_id, msg="", err=False):
    html = render_template("partials/_person_documents.html",
                           person=proj.persons[person_id],
                           documents=_documents(person_id))
    return flash_to(html, msg, err=err) if msg else html


@bp.get("/<person_id>/documents/<kind>")
def document_download(person_id, kind):
    doc = svc.person_document(person_id, kind)
    if doc is None:
        abort(404)
    return send_file(BytesIO(doc["content"]), mimetype="application/pdf",
                     as_attachment=True,
                     download_name=f"{kind}_{proj.persons[person_id].afm}.pdf")


@bp.post("/<person_id>/documents")
def document_upload(person_id):
    """Ανέβασμα για όσα έρχονται εκτός αίτησης: PDF/εικόνες -> ΕΝΑ PDF."""
    if person_id not in proj.persons:
        abort(404)
    files = request.files.getlist("file")
    kind = request.form.get("kind", "")

    def cmd():
        content = amka_pdf.to_pdf([
            {"name": f.filename, "data": base64.b64encode(f.read()).decode()}
            for f in files if f.filename])
        svc.attach_document(person_id, kind, content, f"{kind}.pdf")
        return "Το έγγραφο αποθηκεύτηκε"

    ok, msg = run_command(cmd)
    return _documents_html(person_id, msg, err=not ok)


@bp.post("/<person_id>/documents/<kind>/delete")
def document_delete(person_id, kind):
    if person_id not in proj.persons:
        abort(404)
    ok, msg = run_command(lambda: svc.delete_document(person_id, kind)
                          and "Το έγγραφο διαγράφηκε")
    return _documents_html(person_id, msg, err=not ok)


@bp.post("/<person_id>/correct")
def correct(person_id):
    def cmd():
        f = request.form
        svc.correct_person(
            person_id,
            last_name=f.get("last_name"),
            first_name=f.get("first_name"),
            father_name=f.get("father_name"),
            mother_name=f.get("mother_name"),
            afm=f.get("afm"),
            amka=f.get("amka"),
            ama=f.get("ama"),
            children=f.get("children"),
            email=f.get("email"),
            sex=f.get("sex"),
            nationality_code=f.get("nationality_code"),
            identity_type=f.get("identity_type"),
            identity_number=f.get("identity_number"),
            identity_issuer=f.get("identity_issuer"),
            **_identity_dates(f),
            education_level=f.get("education_level"),
            **form_contact_fields(f),
            **form_third_country_fields(f),
        )
        return "Ενημερώθηκαν τα στοιχεία του προσώπου"

    ok, msg = run_command(cmd)
    if ok:
        # Επιτυχία: το φρέσκο panel έχει κενό modal-host -> κλείνει το modal
        html = render_template("partials/_persons_panel.html", rows=q.person_rows())
        return flash_to(html, msg, err=False)

    # ΣΦΑΛΜΑ: κράτα το modal ανοιχτό με τα δεδομένα του χρήστη
    p = proj.persons.get(person_id)
    html = render_template(
        "partials/_person_edit_modal.html", p=p, form=request.form,
        identity_types=ergani_params.identity_types(),
        nationalities=ergani_params.nationalities(),
        education_levels=ergani_params.education_levels(),
        eu_nationality_codes=ergani_params.eu_nationality_codes(),
    )
    resp = flash_to(html, msg, err=True)
    resp.headers["HX-Retarget"] = "#modal-host"
    resp.headers["HX-Reswap"] = "innerHTML"
    return resp


# ------------------------------------- Μεταβολή στοιχείων (με ημ/νία ισχύος)
# Ίδιο modal με τη διόρθωση, σε mode="change": τα στοιχεία ταυτότητας
# κλειδώνουν και προστίθεται το «Ισχύει από» (PersonDetailsChanged).
@bp.get("/<person_id>/change")
def change_modal(person_id):
    p = proj.persons.get(person_id)
    if p is None:
        abort(404)
    return render_template(
        "partials/_person_edit_modal.html", p=p, mode="change",
        identity_types=ergani_params.identity_types(),
        nationalities=ergani_params.nationalities(),
        education_levels=ergani_params.education_levels(),
        eu_nationality_codes=ergani_params.eu_nationality_codes(),
    )


@bp.post("/<person_id>/change")
def change(person_id):
    def cmd():
        f = request.form
        p = proj.persons.get(person_id)
        was_married = p is not None and p.marital_status == "1"
        svc.change_person_details(
            person_id,
            form_date(f, "effective_from"),
            children=f.get("children"),
            email=f.get("email"),
            identity_type=f.get("identity_type"),
            identity_number=f.get("identity_number"),
            identity_issuer=f.get("identity_issuer"),
            **_identity_dates(f),
            education_level=f.get("education_level"),
            **form_contact_fields(f, birth_place=False),
            **form_third_country_fields(f),
        )
        msg = "Καταχωρήθηκε η μεταβολή στοιχείων"
        # Η οικογ. κατάσταση ΔΕΝ αλλάζει αυτόματα το επίδομα γάμου της
        # απασχόλησης (όρος σύμβασης) — μόνο προειδοποίηση.
        married = p.marital_status == "1"
        emp = next((proj.employments[e] for e in p.employment_ids
                    if e in proj.employments and proj.employments[e].active), None)
        if married != was_married and emp is not None and emp.married != married:
            msg += (" · ⚠️ άλλαξε η οικογενειακή κατάσταση — ελέγξτε το "
                    "επίδομα γάμου στην καρτέλα απασχόλησης")
        return msg

    ok, msg = run_command(cmd)
    if ok:
        html = render_template("partials/_persons_panel.html", rows=q.person_rows())
        return flash_to(html, msg, err=False)

    p = proj.persons.get(person_id)
    html = render_template(
        "partials/_person_edit_modal.html", p=p, mode="change", form=request.form,
        identity_types=ergani_params.identity_types(),
        nationalities=ergani_params.nationalities(),
        education_levels=ergani_params.education_levels(),
        eu_nationality_codes=ergani_params.eu_nationality_codes(),
    )
    resp = flash_to(html, msg, err=True)
    resp.headers["HX-Retarget"] = "#modal-host"
    resp.headers["HX-Reswap"] = "innerHTML"
    return resp


# ------------------------------------------------- Επαναπρόσληψη: modal
@bp.get("/<person_id>/employ")
def employ_modal(person_id):
    """Ανοίγει το modal (επανα)πρόσληψης — νέα περίοδος στο ίδιο πρόσωπο."""
    p = proj.persons.get(person_id)
    if p is None:
        abort(404)
    # ΕΠΑΝΑΠΡΟΣΛΗΨΗ: η προϋπηρεσία συνεχίζει από την προηγούμενη περίοδο
    # (δηλωθείσα + όσο δούλεψε τότε) — προσυμπληρωμένη σε έτη + μήνες.
    fv = {
        **service_years_fields(svc.previous_service_years(person_id)),
        **frozen_service_years_fields(svc.previous_frozen_service_years(person_id)),
    }
    return render_template(
        "partials/_person_employ_modal.html", p=p, form=fv, **form_ctx(fv),
    )


@bp.post("/<person_id>/employ")
def employ(person_id):
    """Πρόσληψη ή ΕΠΑΝΑΠΡΟΣΛΗΨΗ: νέα περίοδος απασχόλησης, ίδιο πρόσωπο."""

    def cmd():
        f = request.form
        pay_type = f.get("pay_type", "monthly")
        amount = form_float(f, "amount")
        # Η ειδικότητα πρέπει να είναι δηλωμένη ΠΡΙΝ ανοίξει η απασχόληση
        specialty_name = f.get("specialty_name", "")
        svc.get_specialty(specialty_name)
        eid = svc.start_employment(
            person_id,
            form_date(f, "hire_date"),
            pay_type=pay_type,
            monthly_gross=(amount if pay_type == "monthly" else 0.0),
            daily_wage=(amount if pay_type == "daily" else 0.0),
            weekly_hours=form_float(f, "weekly_hours", 40.0),
            weekly_system=form_int(f, "weekly_system", 5),
            weekly_pattern=weekly_pattern(f),  # ΥΠΟΧΡΕΩΤΙΚΟ πρόγραμμα εργασίας
            # Προσαυξήσεις κατώτατου: τριετίες / γάμος / λοιπές (πτυχίο κ.λπ.)
            service_years=form_service_years(f),
            frozen_service_years=form_frozen_service_years(f),
            married=bool(f.get("married")),
            extra_allowances_pct=form_float(f, "extra_allowances_pct"),
            draft=True,
            **form_migration(f),
            **form_employment_relationship(f),
            **form_oaed_hire_fields(f),
            **form_hire_status_fields(f),
            **form_digital_card_fields(f),
            **schedule_opts(f),
            today=clock.today(),  # μεταφορά: ως χθες καλύπτει το προηγούμενο πρόγραμμα
        )
        # Οι καλύψεις αντλούνται από τη δηλωμένη ειδικότητα: εδώ δίνονται μόνο
        # ο φορέας επικουρικής και η επιλογή πρόνοιας.
        svc.assign_specialty(
            eid, specialty_name,
            auxiliary_scheme=f.get("auxiliary_scheme"),
            pron_choice=f.get("pron_choice"),
            branch_no=form_int(f, "branch_no", 0),
        )
        n = len(proj.persons[person_id].employment_ids)
        word = "Επαναπρόσληψη" if n > 1 else "Πρόσληψη"
        return f"{word} — το πρόσωπο έχει πλέον {n} περιόδους απασχόλησης"

    ok, msg = run_command(cmd)
    if ok:
        # Επιτυχία: φρέσκο panel Εργαζομένων (κενό modal-host -> κλείνει το
        # modal) — η (επανα)πρόσληψη ανοίγει από τα badges της λίστας
        html = render_template("partials/_persons_panel.html", rows=q.person_rows())
        return flash_to(html, msg, err=False)

    # ΣΦΑΛΜΑ: κράτα το modal ανοιχτό με τα δεδομένα του χρήστη
    p = proj.persons.get(person_id)
    html = render_template(
        "partials/_person_employ_modal.html", p=p, form=request.form,
        **form_ctx(request.form),
    )
    resp = flash_to(html, msg, err=True)
    resp.headers["HX-Retarget"] = "#modal-host"
    resp.headers["HX-Reswap"] = "innerHTML"
    return resp
