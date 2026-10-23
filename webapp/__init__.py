"""
Web UI Μισθοδοσίας — Flask + Jinja2 + htmx.

Μία σελίδα ανά οντότητα/ενέργεια (blueprint ανά σελίδα):
  /              Επισκόπηση        /payroll       Μισθοδοσία
  /obligations   Υποχρεώσεις       /payments      Πληρωμές (προβολή μόνο)
  /persons       Πρόσωπα           /eapd · /fmy   Αρχεία ΑΠΔ · Αρχεία ΦΜΥ
  /live          Ζωντανά (ημέρα)    /work-schedules Πρόγραμμα → ΕΡΓΑΝΗ
  /employments   Απασχολήσεις      /ergani        ΕΡΓΑΝΗ
                                   /company       Εταιρεία
                                   /audit         Event log

Σε επίπεδο ΣΥΣΤΗΜΑΤΟΣ (πάνω από τις εταιρείες):
  /login /logout /password /invite/<token> /reset/<token>
  /companies     Ποια εταιρεία είναι ενεργή · δημιουργία νέας
  /settings      Παράμετροι: νομοθετικές παράμετροι & Μητρώο ΚΠΚ, ΚΟΙΝΑ για
                 όλες τις εταιρείες (ΜΟΝΟ διαχειριστές)
  /users         Χρήστες & δικαιώματα (ΜΟΝΟ διαχειριστές)

Οι ΥΠΟΒΟΛΕΣ (ΑΠΔ/ΦΜΥ) και οι ΚΑΤΑΒΟΛΕΣ εκτελούνται από τις Υποχρεώσεις.

Εκτέλεση:  uv run -m webapp   ->  http://localhost:8000

Χρωματικός κώδικας κατάστασης υποχρεώσεων:
  ΓΚΡΙ ολοκληρωμένη · ΠΡΑΣΙΝΟ διαθέσιμη · ΠΟΡΤΟΚΑΛΙ λήγει σύντομα ·
  ΚΟΚΚΙΝΟ εκπρόθεσμη/μπλοκαρισμένη
"""

import secrets
from datetime import timedelta

from flask import abort, g, redirect, render_template_string, request, session, url_for

from . import clock, filters
from .labels import EMP_LIFECYCLE, MONTHS_GEN, NAV_HOME, SUBSIDY_LABELS, nav_for
from .system import auth, registry, set_tenant
from .tenancy import data_dir

# Σελίδες που δεν απαιτούν ΕΝΕΡΓΗ ΕΤΑΙΡΕΙΑ (είναι πάνω από αυτές).
# Οι «Παράμετροι» ανήκουν εδώ: οι νομοθετικές παράμετροι και το Μητρώο ΚΠΚ
# γράφονται στο ΚΟΙΝΟ store (SHARED_EVENT_TYPES) και ισχύουν για ΟΛΕΣ τις
# εταιρείες — η ενεργή εταιρεία δεν αλλάζει τίποτε σε αυτή τη σελίδα.
SYSTEM_ENDPOINTS = ("auth.", "companies.", "users.", "settings.", "backup.")

# Σελίδες ΜΟΝΟ για διαχειριστές. Οι «Παράμετροι» είναι ΚΟΙΝΕΣ για όλες τις
# εταιρείες (νέα ΓΣΣΕ/φορολογική κλίμακα) — δεν τις αλλάζει απλός χρήστης.
ADMIN_ENDPOINTS = ("users.", "settings.", "backup.")


def _secret_key() -> bytes:
    """Σταθερό κλειδί υπογραφής session — αλλιώς κάθε επανεκκίνηση θα
    αποσύνδεε τους πάντες. Δεν είναι δεδομένο του domain, οπότε δεν είναι
    event: απλό αρχείο δίπλα στις βάσεις."""
    path = data_dir() / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
    return path.read_bytes()


def create_app():
    from flask import Flask

    app = Flask(__name__)
    app.secret_key = _secret_key()
    app.permanent_session_lifetime = timedelta(days=14)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

    filters.register(app)
    # ΣΤΑΘΕΡΑ (όχι ανά αίτημα) — ως jinja global ώστε να το βλέπουν ΚΑΙ τα
    # macros του `_macros.html` (τα imported macros δεν έχουν context).
    app.jinja_env.globals["EMP_LIFECYCLE"] = EMP_LIFECYCLE
    # Μήνες σε γενική («Μισθοδοσία Ιουνίου 2026») — ΜΙΑ πηγή αλήθειας
    app.jinja_env.globals["months_gen"] = MONTHS_GEN
    app.jinja_env.globals["SUBSIDY_LABELS"] = SUBSIDY_LABELS
    # Λίστες «Είδος άδειας» της καρτέλας τρίτων χωρών (3 forms την κάνουν include)
    from misevent.integrations import ergani_parameters

    app.jinja_env.globals["residence_permit_types"] = ergani_parameters.residence_permit_types
    # Είδη εγγράφων προσώπου (modal νέου προσώπου + πληροφορίες προσώπου)
    from misevent.app.services._base import PERSON_DOCUMENT_KINDS

    app.jinja_env.globals["PERSON_DOCUMENT_KINDS"] = PERSON_DOCUMENT_KINDS
    # Λωρίδα χρόνου (`time_bar` του `_macros.html`): υπολογίζεται ΜΟΝΟ στις
    # σελίδες που τη ζητούν — function, όχι context processor.
    from . import queries

    app.jinja_env.globals["time_strip"] = lambda: queries.time_strip(clock.today())

    from .views import (
        attendance,
        audit,
        backup,
        companies,
        company,
        dashboard,
        eapd,
        employments,
        ergani,
        fmy,
        leaves,
        live,
        month,
        obligations,
        payments,
        payroll,
        persons,
        settings,
        users,
        work_schedules,
    )
    from .views import (
        auth as auth_view,
    )
    from .views import clock as clock_view

    for mod in (
        dashboard,
        obligations,
        persons,
        employments,
        live,
        month,
        attendance,
        leaves,
        work_schedules,
        payroll,
        payments,
        eapd,
        fmy,
        ergani,
        company,
        settings,
        audit,
        backup,
        clock_view,
        auth_view,
        companies,
        users,
    ):
        app.register_blueprint(mod.bp)

    @app.before_request
    def guard():
        """ΜΙΑ πηγή αλήθειας για «ποιος είσαι» και «σε ποια εταιρεία είσαι».

        Σειρά: δημόσια σελίδα → σύνδεση → υποχρεωτική αλλαγή κωδικού →
        δικαιώματα διαχειριστή → ΕΝΕΡΓΗ εταιρεία (`g.tenant`).
        """
        endpoint = request.endpoint or ""
        if endpoint in auth_view.PUBLIC_ENDPOINTS:
            return None

        user = auth_view.current_user()
        if not user:
            if request.headers.get("HX-Request"):
                # Λήξη session μέσα σε htmx: πλήρης επαναφόρτωση, αλλιώς ο
                # χρήστης θα έβλεπε τη φόρμα σύνδεσης μέσα σε ένα fragment.
                return "", 401, {"HX-Redirect": url_for("auth.login")}
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        g.user = user

        if user.must_change_password and not endpoint.startswith(
            ("auth.password", "auth.logout")
        ):
            return redirect(url_for("auth.password"))

        if endpoint.startswith(ADMIN_ENDPOINTS) and not user.is_admin:
            abort(403)

        if endpoint.startswith(SYSTEM_ENDPOINTS):
            # Οι σελίδες συστήματος δεν χρειάζονται εταιρεία — αλλά αν υπάρχει
            # ενεργή, τη στήνουμε ώστε ο φλοιός (nav/επωνυμία) να είναι πλήρης.
            _resolve_tenant(user, required=False)
            return None

        return _resolve_tenant(user, required=True)

    def _resolve_tenant(user, required: bool):
        allowed = auth.companies_for(user)
        if not allowed:
            return redirect(url_for("companies.index")) if required else None
        chosen = session.get("company_id")
        if not chosen or not user.can_access(chosen) or chosen not in auth.companies:
            chosen = allowed[0].company_id
            session["company_id"] = chosen
        set_tenant(chosen)
        g.company_id = chosen
        return None

    @app.after_request
    def refresh_time_bar(resp):
        """Κάθε εντολή (htmx μη-GET) από σελίδα με λωρίδα χρόνου: προσθέτει
        τη λωρίδα ανανεωμένη ως OOB — άμεση ενημέρωση χωρίς κώδικα ανά view.
        Αν η απάντηση είναι ολόκληρη σελίδα, έχει ήδη τη φρέσκια λωρίδα."""
        if (
            request.method != "GET"
            and request.headers.get("X-Time-Bar")
            and getattr(g, "tenant", None)
            and resp.status_code == 200
            and resp.mimetype == "text/html"
            and not resp.direct_passthrough
            and b'id="tbar"' not in resp.get_data()
        ):
            resp.set_data(resp.get_data() + render_template_string(
                '{% from "_macros.html" import time_bar %}{{ time_bar(oob=True, open_y=y) }}',
                y=request.headers.get("X-Time-Bar", type=int),
            ).encode())
        return resp

    @app.context_processor
    def inject_globals():
        """Διαθέσιμα σε ΚΑΘΕ template: nav, χρήστης, ενεργή εταιρεία, ημερομηνία."""
        user = getattr(g, "user", None)
        company_id = getattr(g, "company_id", None)
        return {
            "NAV_HOME": NAV_HOME,
            "nav": nav_for(bool(user and user.is_admin), request.endpoint or ""),
            "current_user": user,
            # Η ετικέτα κάθε εταιρείας είναι η ΕΠΩΝΥΜΙΑ που καταχωρήθηκε μέσα
            # της (`display_name`) — η ονομασία του χώρου είναι απλώς το
            # εφεδρικό όνομα όσο δεν έχουν μπει στοιχεία εργοδότη.
            "my_companies": (
                [
                    {"company_id": c.company_id, "name": registry.display_name(c.company_id)}
                    for c in auth.companies_for(user)
                ]
                if user
                else []
            ),
            "company_id": company_id,
            "company_name": (
                registry.display_name(company_id) if company_id else None
            ),
            "today": clock.today(),  # το «σήμερα» του UI (μπορεί να είναι εικονικό)
            "real_today": clock.real_today(),
            "time_travel": clock.is_simulated(),
        }

    return app
