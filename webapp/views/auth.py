"""
Σύνδεση, αλλαγή κωδικού, αποδοχή πρόσκλησης, επαναφορά κωδικού.

Οι σελίδες αυτές είναι ΕΞΩ από τον φλοιό της εφαρμογής (δεν έχουν nav ούτε
ενεργή εταιρεία), γι' αυτό χρησιμοποιούν το `auth_base.html`. Είναι απλές
φόρμες με POST + redirect — ΟΧΙ htmx: μια αποτυχημένη σύνδεση πρέπει να
δουλεύει και χωρίς JavaScript.
"""

import time

from flask import (
    Blueprint,
    abort,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from misevent.app.auth import new_token

from .. import mailer
from ..system import auth, auth_svc, lock, registry
from ..tenancy import saas_mode

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
    "auth.setup",  # ΜΟΝΟ χωρίς χρήστες — ελέγχει μόνο του το `needs_setup`
    "auth.setup_post",
    "auth.signup",  # ΜΟΝΟ online (MISEVENT_SAAS) — αλλιώς 404
    "auth.signup_post",
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


def safe_next(url: str | None, default: str) -> str:
    """`next=` ΜΟΝΟ προς σελίδα ΤΗΣ εφαρμογής — αλλιώς ένας σύνδεσμος
    `/login?next=https://κακόβουλο` θα έστελνε τον χρήστη αλλού μετά τη σύνδεση."""
    if url and url.startswith("/") and not url.startswith(("//", "/\\")):
        return url
    return default


def login_user(user):
    session.clear()
    session["user_id"] = user.user_id
    session.permanent = True


def _home(user):
    """Μετά τη σύνδεση: ο οδηγός «Ξεκινώντας» όσο καμία εταιρεία δεν έχει
    δεδομένα — μόνο σε όποιον μπορεί να διαλέξει κάτι εκεί (διαχειριστής ή
    δικαίωμα δημιουργίας εταιρειών) — αλλιώς η επισκόπηση."""
    if not (user.is_admin or user.can_create_companies):
        return url_for("dashboard.index")
    with lock:
        fresh = registry.is_fresh(user)
    return url_for("welcome.index") if fresh else url_for("dashboard.index")


# --------------------------------------------------------- αρχική ρύθμιση
# Νέα εγκατάσταση = ΚΑΝΕΝΑΣ χρήστης: ο πρώτος (διαχειριστής) ορίζεται εδώ με
# δικό του email/κωδικό. Κανένας προεπιλεγμένος λογαριασμός.
@bp.get("/setup")
def setup():
    if not registry.needs_setup():
        return redirect(url_for("auth.login"))
    return render_template("setup.html")


@bp.post("/setup")
def setup_post():
    email = request.form.get("email", "")
    try:
        with lock:
            user_id = auth_svc.setup_first_admin(
                email, request.form.get("password", ""), request.form.get("repeat", ""))
    except ValueError as exc:
        if not registry.needs_setup():
            return redirect(url_for("auth.login"))
        return render_template("setup.html", email=email, error=str(exc)), 400
    login_user(auth.users[user_id])
    return redirect(url_for("welcome.index"))


# ------------------------------------------------- εγγραφή πελάτη (online)
# Νέος ΟΡΓΑΝΙΣΜΟΣ με αυτόν τον χρήστη διαχειριστή του· μετά ο οδηγός
# «Ξεκινώντας» για την πρώτη εταιρεία. Στη desktop έκδοση δεν υπάρχει.
@bp.get("/signup")
def signup():
    if not saas_mode():
        abort(404)
    return render_template("signup.html", verify=mailer.configured())


@bp.post("/signup")
def signup_post():
    if not saas_mode():
        abort(404)
    email = request.form.get("email", "")
    verify = mailer.configured()
    try:
        with lock:
            if verify:
                # ΕΠΙΒΕΒΑΙΩΣΗ email: λογαριασμός με τυχαίο (άγνωστο) κωδικό· ο
                # χρήστης ορίζει τον δικό του από τον σύνδεσμο που του στάλθηκε —
                # ο ίδιος μηχανισμός με την επαναφορά, κανένα νέο event.
                user_id = auth_svc.signup_organization(email, new_token(), None)
                _, token = auth_svc.issue_password_reset(user_id)
            else:
                user_id = auth_svc.signup_organization(
                    email, request.form.get("password", ""), request.form.get("repeat", ""))
    except ValueError as exc:
        return render_template("signup.html", email=email, verify=verify,
                               error=str(exc)), 400
    if verify:
        send_reset_link(auth.users[user_id].email, token, new_account=True)
        return render_template("signup.html", sent=auth.users[user_id].email)
    login_user(auth.users[user_id])
    return redirect(url_for("welcome.index"))


# ------------------------------------------------------------------ σύνδεση
def _login_page(**context):
    return render_template("login.html", saas=saas_mode(), **context)


@bp.get("/login")
def login():
    if registry.needs_setup():
        return redirect(url_for("auth.setup"))
    if current_user():
        return redirect(_home(current_user()))
    return _login_page(
        email=request.args.get("email", ""),
        restored=request.args.get("restored"),
        restore_error=request.args.get("restore_error", ""),
    )


# Κλείδωμα σύνδεσης: MAX_FAILS αποτυχίες ανά (email, IP) μέσα σε LOCK_SECONDS.
# ponytail: στη μνήμη της ΜΙΑΣ διεργασίας (μία replica, βλ. docker-compose)·
# σε πολλές διεργασίες θέλει κοινή αποθήκη.
MAX_FAILS, LOCK_SECONDS = 5, 15 * 60
_fails: dict[tuple[str, str], list[float]] = {}


@bp.post("/login")
def login_post():
    email = request.form.get("email", "")
    key = (email.strip().lower(), request.remote_addr or "")
    now = time.monotonic()
    recent = [t for t in _fails.get(key, []) if now - t < LOCK_SECONDS]
    if len(recent) >= MAX_FAILS:
        return _login_page(email=email, error="Πολλές αποτυχημένες προσπάθειες — "
                           "δοκιμάστε ξανά σε 15 λεπτά."), 429
    try:
        user = auth_svc.authenticate(email, request.form.get("password", ""))
    except ValueError as exc:
        _fails[key] = [*recent, now]
        return _login_page(email=email, error=str(exc)), 401
    _fails.pop(key, None)
    login_user(user)
    if user.must_change_password:
        return redirect(url_for("auth.password"))
    return redirect(safe_next(request.form.get("next"), _home(user)))


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
    email = request.form.get("email", "")
    if mailer.configured():
        # Self-service: ο σύνδεσμος πάει ΚΑΤΕΥΘΕΙΑΝ στο email — καμία
        # αναμονή διαχειριστή. Ίδια απάντηση είτε υπάρχει ο λογαριασμός είτε όχι.
        user = auth.by_email(email)
        if user and user.active:
            with lock:
                _, token = auth_svc.issue_password_reset(user.user_id)
            send_reset_link(user.email, token)
        return render_template("forgot.html", done="Αν υπάρχει λογαριασμός με αυτό το "
                               "email, στάλθηκε σύνδεσμος για νέο κωδικό.")
    with lock:
        msg = auth_svc.request_password_reset(email)
    return render_template("forgot.html", done=msg)


def send_reset_link(email: str, token: str, new_account: bool = False) -> bool:
    link = url_for("auth.reset", token=token, _external=True)
    what = "ενεργοποίηση του λογαριασμού σας" if new_account else "ορισμό νέου κωδικού"
    return mailer.send(email, "Μισθοδοσία — " + what,
                       f"Για {what} ανοίξτε τον σύνδεσμο:\n\n{link}\n\n"
                       "Αν δεν το ζητήσατε εσείς, αγνοήστε αυτό το μήνυμα.")


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
