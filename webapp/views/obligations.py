"""Υποχρεώσεις: χρονολόγιο, λεπτομέρειες, ενεργοποίηση."""

from flask import Blueprint, abort, redirect, render_template, request, url_for

from .. import queries as q
from ..actions import trigger
from ..clock import today
from ..helpers import flash_to, form_date, run_command
from ..labels import TRACKS
from ..system import proj, svc
from .ergani import pending_count

bp = Blueprint("obligations", __name__, url_prefix="/obligations")

# Η ημερομηνία αναφοράς είναι καθολική (webapp/clock.py) — ό,τι ορίζει ο
# χρήστης στο header ισχύει και εδώ (εκπρόθεσμες / λήγουν σύντομα).
_ref = today


def _locked() -> bool:
    """Εμφάνιση και των κλειδωμένων (όσων περιμένουν προαπαιτούμενο event)."""
    return request.values.get("locked") == "1"


VIEWS = [("active", "Εκκρεμείς"), ("done", "Ολοκληρωμένες"), ("all", "Όλες")]


def _view() -> str:
    """Προεπιλογή: ΕΚΚΡΕΜΕΙΣ — αυτές που περιμένουν ενέργεια."""
    v = request.values.get("view", "active")
    return v if v in dict(VIEWS) else "active"


def _panel(selected: str | None, ref, show_locked: bool = False, view: str = "active"):
    """Το κομμάτι που ανανεώνεται με htmx: χρονολόγιο (3 στήλες) + το modal
    λεπτομερειών, αν έχει ζητηθεί υποχρέωση (`selected`)."""
    obs = q.obligation_list(ref, show_locked=show_locked, view=view)
    return {
        "obs": obs,
        "counts": q.status_counts(obs),
        "ref": ref,
        "locked_n": q.locked_count(),
        "show_locked": show_locked,
        "view": view,
        "views": VIEWS,
        # Οι στήλες έρχονται από το ΜΗΤΡΩΟ τύπων (obligation_types.TRACKS)
        # (χωρίς τη στήλη «Πληρωμές» όταν εξοφλούνται αυτόματα)
        "tracks": [
            t for t in TRACKS
            if not (t[0] == "payment" and proj.misc_settings.get("auto_payments"))
        ],
        "detail": q.detail_payload(selected, ref) if selected else None,
        # εκκρεμείς υποβολές ΕΡΓΑΝΗ (γίνονται ΜΟΝΟ από την οθόνη ΕΡΓΑΝΗ)
        "ergani_pending": pending_count(),
    }


@bp.get("/")
def index():
    """Το χρονολόγιο ζει πλέον στην Επισκόπηση — οι παλιοί σύνδεσμοι
    (`?id=`, `?view=`) οδηγούνται εκεί με τα ίδια ορίσματα."""
    return redirect(url_for("dashboard.index", **request.args))


@bp.get("/modal")
def detail_modal():
    """htmx: ανοίγει τις «Λεπτομέρειες ενέργειας» σε MODAL."""
    detail = q.detail_payload(request.args.get("id"), _ref())
    if detail is None:
        abort(404)
    return render_template("partials/_ob_detail_modal.html", detail=detail)


@bp.get("/modal/close")
def modal_close():
    """Κλείσιμο του modal (Κλείσιμο) — αδειάζει τον host."""
    return ""


@bp.get("/panel")
def panel():
    """htmx: ανανέωση μόνο του panel (επιλογή υποχρέωσης, φίλτρα)."""
    return render_template(
        "partials/_ob_panel.html",
        **_panel(request.args.get("id"), _ref(), _locked(), _view()),
    )


@bp.post("/<ob_id>/run")
def run(ob_id):
    ref = _ref()
    ob = proj.obligations.get(ob_id)
    late = q.days_late(ob, ref) if ob else 0

    # «Εισαγωγή κάρτας περιόδου»: το αρχείο εισάγεται ΜΟΝΟ από τις Παρουσίες
    # — εδώ η ενεργοποίηση είναι ο (επαν)έλεγχος μετά από διορθώσεις.
    ok, msg = run_command(lambda: trigger(ob_id))
    if ok and late:
        msg = f"{msg} — ΕΚΠΡΟΘΕΣΜΑ κατά {late} ημέρες"
    # Μετά την ενεργοποίηση, ό,τι ξεκλείδωσε εμφανίζεται πλέον στο χρονολόγιο.
    # ΕΠΙΤΥΧΙΑ -> κλείνει το modal (selected=None, άρα κενό #modal-host) και ο
    # χρήστης βλέπει το ανανεωμένο χρονολόγιο· ΣΦΑΛΜΑ -> μένει ανοιχτό, ώστε ο
    # λόγος να είναι μπροστά του μαζί με το κουμπί.
    html = render_template(
        "partials/_ob_panel.html",
        **_panel(None if ok else ob_id, _ref(), _locked(), _view()),
    )
    return flash_to(html, msg, err=not ok)


@bp.post("/<ob_id>/reopen")
def reopen(ob_id):
    """ΑΝΟΙΓΜΑ ΜΙΣΘΟΔΟΣΙΑΣ ΓΙΑ ΔΙΟΡΘΩΣΗ (PayrollPeriodReopened) — όσο δεν
    στέκει καμία υποβολή της περιόδου. Οι κανόνες ζουν στο service."""

    def cmd():
        ob = proj.obligations.get(ob_id)
        if ob is None:
            raise ValueError("Άγνωστη υποχρέωση")
        warnings = svc.reopen_payroll_period(
            ob.period_year, ob.period_month,
            request.form.get("reason", "").strip(),
        )
        msg = (f"Άνοιξε η μισθοδοσία {ob.period_month}/{ob.period_year} για "
               "διόρθωση — ξαναϋπολόγισέ την όταν τελειώσεις")
        return f"{msg}. {' '.join(warnings)}" if warnings else msg

    ok, msg = run_command(cmd)
    html = render_template(
        "partials/_ob_panel.html", **_panel(ob_id, _ref(), _locked(), _view())
    )
    return flash_to(html, msg, err=not ok)


@bp.post("/<ob_id>/withdraw")
def withdraw(ob_id):
    """ΔΗΛΩΣΗ ΛΑΝΘΑΣΜΕΝΗΣ ΥΠΟΒΟΛΗΣ (SubmissionWithdrawn): το αρχείο ανέβηκε
    χειροκίνητα, άρα η υποβολή είναι δήλωση — και ανακαλείται."""

    def cmd():
        ob = proj.obligations.get(ob_id)
        if ob is None:
            raise ValueError("Άγνωστη υποχρέωση")
        svc.withdraw_submission(
            ob.obligation_type, ob.period_year, ob.period_month,
            request.form.get("reason", "").strip(),
        )
        return (f"Η υποβολή {ob.period_month}/{ob.period_year} δηλώθηκε "
                "λανθασμένη — η υποχρέωση ξαναγίνεται εκκρεμής")

    ok, msg = run_command(cmd)
    html = render_template(
        "partials/_ob_panel.html", **_panel(ob_id, _ref(), _locked(), _view())
    )
    return flash_to(html, msg, err=not ok)


@bp.post("/<ob_id>/extend")
def extend(ob_id):
    """Παράταση προθεσμίας (ObligationExtended) — μόνο για τύπους που τη
    δηλώνουν (extension.allowed, σελίδα Παράμετροι)."""

    def cmd():
        new_due = form_date(request.form, "new_due")
        svc.extend_obligation(
            ob_id, new_due, request.form.get("reason", "").strip()
        )
        return f"Η προθεσμία παρατάθηκε έως {new_due.strftime('%d/%m/%Y')}"

    ok, msg = run_command(cmd)
    html = render_template(
        "partials/_ob_panel.html", **_panel(ob_id, _ref(), _locked(), _view())
    )
    return flash_to(html, msg, err=not ok)
