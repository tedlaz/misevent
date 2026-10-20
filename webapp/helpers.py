"""
Βοηθήματα views: εκτέλεση εντολών με κλείδωμα + toast μέσω htmx.

Μοτίβο: η φόρμα κάνει `hx-post`· το view τρέχει την εντολή, ξαναδίνει το
ανανεωμένο HTML fragment και προσθέτει header `HX-Trigger` με το μήνυμα.
Ένας μικροσκοπικός listener στο base.html το δείχνει ως toast.
"""

import json

from flask import Response, make_response

from .system import lock


def flash_to(html: str, msg: str, err: bool = False):
    """Τυλίγει HTML fragment + toast."""
    resp = make_response(html)
    resp.headers["HX-Trigger"] = json.dumps({"flash": {"msg": msg, "err": err}})
    return resp


def run_command(fn, *args, **kwargs):
    """Εκτελεί εντολή σειριοποιημένα. Επιστρέφει (ok, message).

    Τα commands του services.py/constraints.py πετούν ValueError με
    ελληνικό μήνυμα — το πιάνουμε και το δείχνουμε ως toast, ποτέ 500."""
    from .actions import auto_settle_payments  # actions -> queries -> ... κύκλος

    try:
        with lock:
            msg = fn(*args, **kwargs) or "Ολοκληρώθηκε"
            paid = auto_settle_payments()
        if paid:
            msg = f"{msg} — εξοφλήθηκαν αυτόματα: {', '.join(paid)}"
        return True, msg
    except (ValueError, KeyError) as exc:
        return False, str(exc)


def pdf_response(build, filename):
    """Παράγει το PDF μέσα στο lock (read-only, αλλά τα projections δεν
    πρέπει να μεταβληθούν στη μέση της ανάγνωσης) και το σερβίρει inline.
    Τα `ValueError` των builders («δεν έχει υπολογιστεί…») γίνονται 404."""
    try:
        with lock:
            data = build()
    except ValueError as exc:
        return str(exc), 404
    return Response(
        data,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


def form_date(form, field, default=None):
    """Ημερομηνία από φόρμα (ISO). Κενό -> default."""
    from datetime import date as _date

    raw = (form.get(field) or "").strip()
    if not raw:
        if default is None:
            raise ValueError(f"Απαιτείται ημερομηνία στο πεδίο «{field}»")
        return default
    try:
        return _date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"Μη έγκυρη ημερομηνία: {raw}") from None


def form_optional_date(form, field):
    """Προαιρετική ISO ημερομηνία από φόρμα. Κενό -> None."""
    raw = (form.get(field) or "").strip()
    if not raw:
        return None
    return form_date(form, field)


def form_migration(form) -> dict:
    """ΜΕΤΑΦΟΡΑ ΑΠΟ ΑΛΛΟ ΠΡΟΓΡΑΜΜΑ ΜΙΣΘΟΔΟΣΙΑΣ (πεδία `.migr-fields`).

    Χωρίς το checkbox «Μεταφορά» τα πεδία είναι ΚΡΥΜΜΕΝΑ — και ό,τι δεν βλέπει
    ο χρήστης δεν μετράει: ένα κρυμμένο input εξακολουθεί να υποβάλλεται, οπότε
    μια παλιά τιμή θα ενεργοποιούσε σιωπηλά τη μεταφορά."""
    if not form.get("migrated"):
        return {
            "payroll_from": None,
            "ergani_hire_done": False,
            "ergani_hire_reference": "",
        }
    return {
        "payroll_from": form_optional_date(form, "payroll_from"),
        "ergani_hire_done": bool(form.get("ergani_hire_done")),
        "ergani_hire_reference": form.get("ergani_hire_reference", ""),
    }


def form_employment_relationship(form) -> dict:
    """Σχέση απασχόλησης και διάστημα ορισμένου χρόνου από φόρμα."""
    return {
        "employment_relationship": form.get("employment_relationship", "0"),
        "fixed_term_from": form_optional_date(form, "fixed_term_from"),
        "fixed_term_to": form_optional_date(form, "fixed_term_to"),
    }


def form_oaed_hire_fields(form) -> dict:
    """Πεδία του tab ΟΑΕΔ στη σημασιολογία του Employment."""
    return {
        "oaed_placement": form.get("f_topothetisioaed", "0"),
        "oaed_program": form.get("f_programaoaed", ""),
        "oaed_replacement": form.get("f_replaceprograma", "0"),
        "oaed_replaced_afm": form.get("f_replaceprograma_afm", ""),
        "oaed_replaced_amka": form.get("f_replaceprograma_amka", ""),
        "trial_period": form.get("f_trial_period", "0"),
        "trial_date_to": form_optional_date(form, "f_trial_date_to"),
    }


def form_digital_card_fields(form) -> dict:
    """Πεδία Ψηφιακής κάρτας του tab Προγράμματος εργασίας."""
    return {
        "working_card": form.get("f_working_card", "1"),
        "flexible_arrival_minutes": form.get("f_euelikto_wrario_minutes", "120"),
        "break_within_schedule": form.get("f_dialeimma_entos_wrariou", "0"),
        "break_minutes": form.get("f_dialeimma_minutes", "15"),
    }


def form_contact_fields(form, birth_place=True) -> dict:
    """Επικοινωνία & τόπος γέννησης από φόρμα (`_person_contact_fields.html`).
    `birth_place=False` στη μεταβολή: ο τόπος γέννησης μόνο διορθώνεται."""
    names = ("address", "postal_code", "municipality", "prefecture", "phone")
    out = {k: form.get(k, "") for k in names}
    if birth_place:
        out["birth_place"] = form.get("birth_place", "")
    return out


def form_third_country_fields(form) -> dict:
    """Πεδία WebE3N αδειών/θεώρησης πολιτών τρίτων χωρών από φόρμα."""
    return {
        "marital_status": form.get("marital_status", "0"),
        "res_permit_inst": form.get("res_permit_inst", ""),
        "res_permit_inst_type": form.get("res_permit_inst_type", ""),
        "res_permit_inst_number": form.get("res_permit_inst_number", ""),
        "res_permit_inst_expiry_date": form_optional_date(
            form, "res_permit_inst_expiry_date"
        ),
        "res_permit_ap": form.get("res_permit_ap", ""),
        "res_permit_ap_type": form.get("res_permit_ap_type", ""),
        "res_permit_ap_number": form.get("res_permit_ap_number", ""),
        "res_permit_ap_expiry_date": form_optional_date(
            form, "res_permit_ap_expiry_date"
        ),
        "res_permit_visa": form.get("res_permit_visa", ""),
        "res_permit_visa_number": form.get("res_permit_visa_number", ""),
        "res_permit_visa_from": form_optional_date(form, "res_permit_visa_from"),
        "res_permit_visa_to": form_optional_date(form, "res_permit_visa_to"),
    }


def form_float(form, field, default=0.0) -> float:
    raw = (form.get(field) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"Μη έγκυρος αριθμός: {raw}") from None


def form_int(form, field, default=0) -> int:
    raw = (form.get(field) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Μη έγκυρος ακέραιος: {raw}") from None


def form_service_years(form) -> float:
    """Προϋπηρεσία από τα πεδία «έτη» + «μήνες» της φόρμας, ως δεκαδικά έτη
    (έτη + μήνες/12). Οι μήνες πάνω από 11 μεταφέρονται σε έτη. Έτσι ο
    χρήστης δίνει π.χ. «2 έτη 11 μήνες» αντί για δεκαδικό 2,9167."""
    return form_year_months(form, "service")


def form_frozen_service_years(form) -> float:
    """Τμήμα της δηλωθείσας προϋπηρεσίας που έπεσε στο πάγωμα 2012-2023."""
    return form_year_months(form, "frozen_service")


def form_year_months(form, prefix: str) -> float:
    """Γενικός parser για ζεύγος πεδίων `<prefix>_years` + `<prefix>_months`."""
    years = form_int(form, f"{prefix}_years", 0)
    months = form_int(form, f"{prefix}_months", 0)
    if years < 0 or months < 0:
        raise ValueError("Η προϋπηρεσία δεν μπορεί να είναι αρνητική")
    return years + months / 12.0


def service_years_fields(value) -> dict:
    """Το ΑΝΤΙΣΤΡΟΦΟ του `form_service_years`: δεκαδικά έτη -> τιμές των δύο
    πεδίων της φόρμας. Χρησιμοποιείται για την προσυμπλήρωση της προϋπηρεσίας
    στην ΕΠΑΝΑΠΡΟΣΛΗΨΗ (ίδια στρογγυλοποίηση με το φίλτρο `service_gr`)."""
    return year_month_fields(value, "service")


def frozen_service_years_fields(value) -> dict:
    """Το αντίστροφο του `form_frozen_service_years` για τη φόρμα πρόσληψης."""
    return year_month_fields(value, "frozen_service")


def year_month_fields(value, prefix: str) -> dict:
    """Γενικό inverse: δεκαδικά έτη -> `<prefix>_years`/`<prefix>_months`."""
    total = float(value or 0.0)
    years = int(total)
    months = round((total - years) * 12)
    if months >= 12:
        years, months = years + 1, 0
    return {f"{prefix}_years": years, f"{prefix}_months": months}
