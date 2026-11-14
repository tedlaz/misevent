"""
Οργανισμοί (πελάτες της online έκδοσης) — ΜΟΝΟ ο διαχειριστής του συστήματος
(`PLATFORM_ENDPOINTS`): χρήση απέναντι στα όρια του πλάνου, ορισμός ορίων,
οριστική διαγραφή πελάτη (GDPR).

Η πληρωμή γίνεται ΕΚΤΟΣ εφαρμογής (τιμολόγιο/κατάθεση)· εδώ απλώς ορίζονται
τα όρια που συμφωνήθηκαν.
"""

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from ..helpers import run_command
from ..system import auth, lock, registry
from .auth import send_reset_link

bp = Blueprint("orgs", __name__, url_prefix="/organizations")


def _rows() -> list[dict]:
    rows = []
    for org_id in auth.orgs():
        if not org_id:  # ο οργανισμός της εγκατάστασης (πάροχος) δεν έχει πλάνο
            continue
        users = auth.users_in(org_id)
        rows.append({
            "org_id": org_id,
            "admins": [u.email for u in users if u.is_admin],
            "users": len(users),
            "created_at": min(u.created_at for u in users)[:10],
            **registry.usage(org_id),
        })
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)


def _page(status: int = 200, **ctx):
    with lock:
        rows = _rows()
    return render_template("orgs.html", rows=rows, **ctx), status


@bp.get("/")
def index():
    return _page(error=request.args.get("error"))


@bp.post("/new")
def create():
    """Νέος πελάτης: οργανισμός + ο διαχειριστής του, με σύνδεσμο ενεργοποίησης
    (ορισμός κωδικού). Ο σύνδεσμος φαίνεται ΜΙΑ φορά εδώ — και στέλνεται με
    email αν υπάρχει SMTP. Απάντηση σελίδα, ΟΧΙ redirect: το token δεν πρέπει
    να μείνει σε URL/ιστορικό."""
    email = (request.form.get("email") or "").strip()
    try:
        with lock:
            user_id, token = registry.auth_svc.create_organization(email)
    except ValueError as exc:
        return _page(400, error=str(exc), email=email)
    email = auth.users[user_id].email
    sent = send_reset_link(email, token, new_account=True)
    return _page(created=email, sent=sent,
                 link=url_for("auth.reset", token=token, _external=True))


@bp.post("/<org_id>/limits")
def limits(org_id):
    f = request.form
    ok, msg = run_command(registry.auth_svc.set_limits, org_id, f.get("max_companies"),
                          f.get("max_employees"), by=g.user.user_id)
    return redirect(url_for("orgs.index", **({} if ok else {"error": msg})))


@bp.post("/<org_id>/delete")
def delete(org_id):
    """Επιβεβαίωση: πληκτρολόγηση του email ενός διαχειριστή του οργανισμού."""
    admins = {u.email for u in auth.users_in(org_id) if u.is_admin}
    if not org_id or not admins:
        abort(404)
    if (request.form.get("confirm") or "").strip().lower() not in admins:
        return redirect(url_for("orgs.index", error="Λάθος επιβεβαίωση — τίποτα δεν διαγράφηκε"))
    with lock:
        registry.purge_organization(org_id)
    return redirect(url_for("orgs.index"))
