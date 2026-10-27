"""
Εταιρείες: ποιες βλέπει ο χρήστης, ποια είναι ΕΝΕΡΓΗ, δημιουργία νέας.

Η ενεργή εταιρεία ζει στο session (`company_id`) και τη διαβάζει ο guard του
`create_app`, ο οποίος στήνει το `g.tenant`. Η δημιουργία νέας εταιρείας
φτιάχνει ΜΟΝΟ τον χώρο της (κενό event log) — τα στοιχεία του εργοδότη
καταχωρούνται μετά, από τη σελίδα «Εταιρεία».
"""

from flask import Blueprint, abort, redirect, render_template, request, session, url_for

from ..helpers import run_command
from ..system import auth, lock, registry, set_tenant
from .auth import current_user

bp = Blueprint("companies", __name__, url_prefix="/companies")


def accessible(user) -> list:
    return auth.companies_for(user)


def may_create(user) -> bool:
    return bool(user and (user.is_admin or user.can_create_companies))


def _rows(user) -> list:
    return [
        {
            "company_id": c.company_id,
            "name": registry.display_name(c.company_id),
            "official": registry.official_name(c.company_id),
            "active": c.company_id == session.get("company_id"),
        }
        for c in accessible(user)
    ]


def _page(user, status: int = 200, error: str | None = None):
    """Χωρίς καμία προσβάσιμη εταιρεία ΔΕΝ υπάρχει φλοιός εφαρμογής (ούτε
    nav, ούτε ενεργή εταιρεία) — ο νεοπροσκεκλημένος χρήστης βλέπει μια
    αυτόνομη σελίδα που εξηγεί ότι εκκρεμούν δικαιώματα."""
    rows = _rows(user)
    template = "companies.html" if rows else "companies_none.html"
    archived = [c for c in auth.companies.values() if c.archived] if user.is_admin else []
    return (
        render_template(
            template, rows=rows, may_create=may_create(user), user=user, error=error,
            archived=archived,
        ),
        status,
    )


@bp.get("/")
def index():
    user = current_user()
    if not user:
        return redirect(url_for("auth.login"))
    return _page(user)


@bp.post("/select/<company_id>")
def select(company_id):
    user = current_user()
    if not user:
        return redirect(url_for("auth.login"))
    if company_id not in {c.company_id for c in accessible(user)}:
        abort(403)
    session["company_id"] = company_id
    return redirect(request.form.get("next") or url_for("dashboard.index"))


@bp.post("/new")
def create():
    user = current_user()
    if not may_create(user):
        abort(403)
    name = (request.form.get("name") or "").strip()
    try:
        with lock:
            company_id = registry.create_company(name, created_by=user.user_id)
    except ValueError as exc:
        return _page(user, status=400, error=str(exc))
    session["company_id"] = company_id
    return redirect(url_for("company.index"))


@bp.post("/demo")
def create_demo():
    """Εταιρεία «DEMO» με εργαζόμενους και ιστορικό του τρέχοντος έτους."""
    user = current_user()
    if not may_create(user):
        abort(403)
    try:
        with lock:
            company_id = registry.create_company("DEMO", created_by=user.user_id)
    except ValueError as exc:
        return _page(user, status=400, error=str(exc))
    return open_demo(company_id)


def open_demo(company_id):
    """Γεμίζει τη (νέα, κενή) εταιρεία με τα δεδομένα DEMO και την ανοίγει."""
    from ..demo_company import seed_demo_company

    session["company_id"] = company_id
    set_tenant(company_id)
    ok, msg = run_command(seed_demo_company)
    if not ok:
        return _page(current_user(), status=400, error=f"DEMO: {msg}")
    return redirect(url_for("dashboard.index"))


@bp.post("/rename/<company_id>")
def rename(company_id):
    """Μετονομασία του ΟΝΟΜΑΤΟΣ ΜΕΝΟΥ (όχι της επίσημης ονομασίας — αυτή
    ζει στα στοιχεία εργοδότη)."""
    user = current_user()
    if not user or not (user.is_admin or user.can_access(company_id)):
        abort(403)
    run_command(registry.auth_svc.rename_company, company_id, request.form.get("name", ""))
    return redirect(url_for("companies.index"))


@bp.post("/delete/<company_id>")
def delete(company_id):
    """ΟΡΙΣΤΙΚΗ διαγραφή εταιρείας από παντού (`Registry.purge_company`).
    Μόνο διαχειριστής, με πληκτρολόγηση της επωνυμίας ως επιβεβαίωση."""
    user = current_user()
    if not (user and user.is_admin):
        abort(403)
    if company_id not in auth.companies:
        abort(404)
    # Δεκτό και το όνομα μενού και η επίσημη ονομασία.
    names = {registry.display_name(company_id), registry.official_name(company_id)} - {""}
    if (request.form.get("confirm") or "").strip() not in names:
        return _page(user, status=400, error="Λάθος επιβεβαίωση — τίποτα δεν διαγράφηκε")
    try:
        with lock:
            registry.purge_company(company_id)
    except ValueError as exc:
        return _page(user, status=400, error=str(exc))
    if session.get("company_id") == company_id:
        session.pop("company_id")
    return redirect(url_for("companies.index"))


@bp.post("/unarchive/<company_id>")
def unarchive(company_id):
    user = current_user()
    if not (user and user.is_admin):
        abort(403)
    try:
        with lock:
            registry.auth_svc.unarchive_company(company_id, by=user.user_id)
    except ValueError as exc:
        return _page(user, status=400, error=str(exc))
    return redirect(url_for("companies.index"))
