"""
Σύνδεση, αλλαγή κωδικού, αποδοχή πρόσκλησης, επαναφορά κωδικού.

Οι σελίδες αυτές είναι ΕΞΩ από τον φλοιό της εφαρμογής (δεν έχουν nav ούτε
ενεργή εταιρεία), γι' αυτό χρησιμοποιούν το `auth_base.html`. Είναι απλές
φόρμες με POST + redirect — ΟΧΙ htmx: μια αποτυχημένη σύνδεση πρέπει να
δουλεύει και χωρίς JavaScript.
"""

from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..system import auth, auth_svc, lock

bp = Blueprint("auth", __name__)

# Διαδρομές που επιτρέπονται ΧΩΡΙΣ σύνδεση (τις διαβάζει ο guard).
# ⚠️ Κάθε φόρμα έχει ΔΥΟ endpoints (GET + POST) — αν λείψει το POST, ο guard
# ανακατευθύνει την ίδια την υποβολή της σύνδεσης πίσω στη σύνδεση.
PUBLIC_ENDPOINTS = {
    "auth.login",
    "auth.login_post",
    "auth.forgot",
    "auth.forgot_post",
    "auth.invite",
    "auth.invite_post",
    "auth.reset",
    "auth.reset_post",
    "static",
}


def current_user():
    """Ο συνδεδεμένος χρήστης ή None. ΜΙΑ πηγή αλήθειας για όλο το UI."""
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = auth.users.get(user_id)
    if user is None or not user.active:
        return None
    return user


def login_user(user):
    session.clear()
    session["user_id"] = user.user_id
    session.permanent = True


# ------------------------------------------------------------------ σύνδεση
@bp.get("/login")
def login():
    if current_user():
        return redirect(url_for("dashboard.index"))
    return render_template("login.html", email=request.args.get("email", ""))


@bp.post("/login")
def login_post():
    email = request.form.get("email", "")
    try:
        user = auth_svc.authenticate(email, request.form.get("password", ""))
    except ValueError as exc:
        return render_template("login.html", email=email, error=str(exc)), 401
    login_user(user)
    if user.must_change_password:
        return redirect(url_for("auth.password"))
    return redirect(request.form.get("next") or url_for("dashboard.index"))


@bp.post("/logout")
@bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------- αλλαγή κωδικού
@bp.get("/password")
def password():
    user = current_user()
    if not user:
        return redirect(url_for("auth.login"))
    return render_template("password.html", user=user, forced=user.must_change_password)


@bp.post("/password")
def password_post():
    user = current_user()
    if not user:
        return redirect(url_for("auth.login"))
    forced = user.must_change_password
    try:
        with lock:
            auth_svc.change_password(
                user.user_id,
                request.form.get("new_password", ""),
                current_password=request.form.get("current_password", ""),
                password_repeat=request.form.get("repeat", ""),
            )
    except ValueError as exc:
        return (
            render_template("password.html", user=user, forced=forced, error=str(exc)),
            400,
        )
    return redirect(url_for("dashboard.index"))


# ------------------------------------------------------------ ξέχασα κωδικό
@bp.get("/forgot")
def forgot():
    return render_template("forgot.html")


@bp.post("/forgot")
def forgot_post():
    with lock:
        msg = auth_svc.request_password_reset(request.form.get("email", ""))
    return render_template("forgot.html", done=msg)


# --------------------------------------------------------------- πρόσκληση
@bp.get("/invite/<token>")
def invite(token):
    try:
        auth_svc.check_invitation(token)
    except ValueError as exc:
        return render_template("invite.html", token=token, blocked=str(exc)), 400
    return render_template("invite.html", token=token)


@bp.post("/invite/<token>")
def invite_post(token):
    email = request.form.get("email", "")
    try:
        with lock:
            user_id = auth_svc.accept_invitation(
                token,
                email,
                request.form.get("password", ""),
                password_repeat=request.form.get("repeat", ""),
            )
    except ValueError as exc:
        return (
            render_template("invite.html", token=token, email=email, error=str(exc)),
            400,
        )
    login_user(auth.users[user_id])
    # Ο νέος χρήστης ξεκινά ΧΩΡΙΣ πρόσβαση σε εταιρεία — ο guard θα τον
    # οδηγήσει στη σελίδα «Εταιρείες», που εξηγεί ότι εκκρεμούν δικαιώματα.
    return redirect(url_for("companies.index"))


# ------------------------------------------------------------- επαναφορά
@bp.get("/reset/<token>")
def reset(token):
    try:
        auth_svc.check_reset(token)
    except ValueError as exc:
        return render_template("reset.html", token=token, blocked=str(exc)), 400
    return render_template("reset.html", token=token)


@bp.post("/reset/<token>")
def reset_post(token):
    try:
        with lock:
            auth_svc.use_reset(
                token,
                request.form.get("password", ""),
                password_repeat=request.form.get("repeat", ""),
            )
    except ValueError as exc:
        return render_template("reset.html", token=token, error=str(exc)), 400
    return redirect(url_for("auth.login"))
