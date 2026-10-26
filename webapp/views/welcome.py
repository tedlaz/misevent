"""
Οδηγός «Ξεκινώντας»: ανοίγει όσο ΚΑΜΙΑ εταιρεία δεν έχει δεδομένα (νέα
εγκατάσταση, αμέσως μετά την αρχική ρύθμιση). Τρεις δρόμοι:

1. Νέα εταιρεία → ο κενός χώρος παίρνει το όνομα → σελίδα «Εταιρεία».
2. Restore      → το κανονικό restore της σελίδας Backup (διαχειριστής).
3. Από ΑΠΔ      → ο οδηγός «Εισαγωγή από ΑΠΔ».
"""

from flask import Blueprint, abort, redirect, render_template, request, session, url_for

from ..system import lock, registry
from .auth import current_user
from .companies import may_create

bp = Blueprint("welcome", __name__, url_prefix="/welcome")


@bp.get("/")
def index():
    user = current_user()
    return render_template("welcome.html", may_create=may_create(user),
                           is_admin=user.is_admin, error=request.args.get("error"))


@bp.post("/company")
def new_company():
    user = current_user()
    if not may_create(user):
        abort(403)
    try:
        with lock:
            company_id = registry.claim_company(request.form.get("name", ""), user)
    except ValueError as exc:
        return redirect(url_for("welcome.index", error=str(exc)))
    session["company_id"] = company_id
    return redirect(url_for("company.index"))
