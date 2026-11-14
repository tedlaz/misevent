"""
Διαχείριση χρηστών — ΜΟΝΟ για admin.

Ό,τι γράφει event περνά από `run_command` (κλείδωμα + ValueError → toast),
όπως όλες οι εντολές του UI. Η οθόνη είναι ένα panel που ανανεώνεται με htmx
(`#users-panel`), με το ίδιο μοτίβο των υπόλοιπων σελίδων.

⚠️ Τα ΩΜΑ tokens (πρόσκλησης/επαναφοράς) φαίνονται ΜΙΑ φορά, στην απάντηση
της ενέργειας που τα δημιούργησε. Δεν αποθηκεύονται πουθενά — στο event
μπαίνει μόνο το hash τους.
"""

from flask import Blueprint, abort, render_template, request, url_for

from .. import mailer
from ..helpers import flash_to, run_command
from ..system import auth, auth_svc, lock, registry
from .auth import current_user, send_reset_link

bp = Blueprint("users", __name__, url_prefix="/users")


def _require_admin():
    user = current_user()
    if not user or not user.is_admin:
        abort(403)
    return user


def _rows(org_id: str):
    """ΜΟΝΟ ο οργανισμός του διαχειριστή — χρήστες, εταιρείες, προσκλήσεις."""
    companies = auth.active_companies(org_id)
    rows = []
    for u in auth.users_in(org_id):
        rows.append(
            {
                "user": u,
                "names": [
                    registry.display_name(c.company_id)
                    for c in companies
                    if c.company_id in u.companies
                ],
                "pending_reset": auth.reset_requests.get(u.email),
            }
        )
    return rows, companies


def _panel(link: str | None = None, link_label: str = ""):
    org_id = current_user().org_id
    rows, companies = _rows(org_id)
    mine = {u.user_id for u in auth.users_in(org_id)}
    invitations = sorted(
        (i for i in auth.invitations.values() if i.created_by in mine),
        key=lambda i: i.expires_at, reverse=True,
    )
    return render_template(
        "partials/_users_panel.html",
        rows=rows,
        companies=companies,
        invitations=invitations,
        link=link,
        link_label=link_label,
    )


@bp.get("/")
def index():
    _require_admin()
    return render_template("users.html", panel=_panel())


# ---------------------------------------------------------------- ενέργειες
@bp.post("/invite")
def invite():
    admin = _require_admin()
    email = (request.form.get("email") or "").strip()
    try:
        with lock:
            _, token = auth_svc.issue_invitation(
                admin.user_id, note=request.form.get("note", "") or email
            )
    except ValueError as exc:
        return flash_to(_panel(), str(exc), err=True)
    link = url_for("auth.invite", token=token, _external=True)
    sent = email and mailer.send(
        email, "Πρόσκληση στη Μισθοδοσία",
        f"Ο/Η {admin.email} σας προσκαλεί. Δημιουργήστε τον λογαριασμό σας εδώ:\n\n{link}")
    return flash_to(
        _panel(link=link, link_label="Σύνδεσμος πρόσκλησης — στείλ' τον στον χρήστη"),
        f"Η πρόσκληση στάλθηκε στο {email}" if sent else "Η πρόσκληση δημιουργήθηκε",
    )


@bp.post("/invite/<invite_id>/revoke")
def revoke(invite_id):
    admin = _require_admin()
    ok, msg = run_command(auth_svc.revoke_invitation, invite_id, by=admin.user_id)
    return flash_to(_panel(), msg, err=not ok)


@bp.post("/<user_id>/access")
def access(user_id):
    admin = _require_admin()
    scope = request.form.get("scope", "none")
    ok, msg = run_command(
        auth_svc.set_access,
        user_id,
        all_companies=(scope == "all"),
        companies=request.form.getlist("companies") if scope == "some" else [],
        can_create_companies=bool(request.form.get("can_create")),
        by=admin.user_id,
    )
    return flash_to(_panel(), msg, err=not ok)


@bp.post("/<user_id>/admin")
def admin_flag(user_id):
    admin = _require_admin()
    ok, msg = run_command(
        auth_svc.set_admin, user_id, bool(request.form.get("is_admin")), by=admin.user_id
    )
    return flash_to(_panel(), msg, err=not ok)


@bp.post("/<user_id>/active")
def active(user_id):
    admin = _require_admin()
    ok, msg = run_command(
        auth_svc.set_active, user_id, bool(request.form.get("active")), by=admin.user_id
    )
    return flash_to(_panel(), msg, err=not ok)


@bp.post("/<user_id>/reset")
def reset(user_id):
    """Επαναφορά κωδικού ΚΑΤΟΠΙΝ ΑΙΤΗΜΑΤΟΣ: ο admin εκδίδει προσωπικό
    σύνδεσμο — δεν βλέπει και δεν ορίζει ο ίδιος κωδικό."""
    admin = _require_admin()
    try:
        with lock:
            _, token = auth_svc.issue_password_reset(user_id, by=admin.user_id)
    except ValueError as exc:
        return flash_to(_panel(), str(exc), err=True)
    link = url_for("auth.reset", token=token, _external=True)
    email = auth.users[user_id].email
    sent = send_reset_link(email, token)
    return flash_to(
        _panel(link=link, link_label="Σύνδεσμος επαναφοράς — στείλ' τον στον χρήστη"),
        f"Ο σύνδεσμος στάλθηκε στο {email}" if sent else "Ο σύνδεσμος επαναφοράς δημιουργήθηκε",
    )
