"""
Ζωντανή εικόνα ημέρας — grid πραγματικού χρόνου.

ΜΙΑ γραμμή ανά ενεργή απασχόληση: οριζόντια μπάρα από την ώρα έναρξης έως
την ώρα λήξης του ΠΡΟΓΡΑΜΜΑΤΟΣ, από κάτω η ΠΡΑΓΜΑΤΙΚΗ παρουσία με τα
χτυπήματα κάρτας (Κάρτα Εργασίας ΕΡΓΑΝΗ), και μια κάθετη γραμμή στο κέντρο
που δείχνει την τρέχουσα ώρα — το grid κυλά ομαλά από δεξιά προς αριστερά
καθώς περνά ο χρόνος.

**ΠΡΟΒΟΛΗ ΜΟΝΟ** — καμία εντολή, κανένα event. Οι γραμμές ανανεώνονται με
htmx (`hx-trigger="every 60s"`), η κύλιση/ώρα από το `static/live-board.js`.

➜ Η προβολή είναι **ΠΑΡΑΘΥΡΟ ΗΜΕΡΩΝ** (`base` … `base+days`), όχι μία ημέρα:
έτσι η ελεύθερη μετακίνηση περνά ομαλά στην προηγούμενη/επόμενη ημερομηνία.
Ο client ζητά νέο παράθυρο (`/live/rows?base=…&days=…`) μόλις πλησιάσει την
άκρη — η ημέρα ΑΝΑΦΟΡΑΣ (στήλη ονομάτων, σύνολα) μένει πάντα το «σήμερα»
του UI (`clock.today()`).

Η ΩΡΑ είναι πάντα η πραγματική ώρα του browser· η ΗΜΕΡΑ είναι η ημερομηνία
αναφοράς, οπότε σε ταξίδι στον χρόνο η σελίδα δείχνει ιστορική εικόνα και ο
δείκτης «τώρα» αποσύρεται (βλ. `live` flag).
"""

import json
from datetime import date, timedelta

from flask import Blueprint, jsonify, render_template, request

from misevent.domain.events import LeaveType
from misevent.integrations import card_punches as cp

from .. import queries as q
from ..clock import is_simulated, today
from ..helpers import flash_to, form_date, run_command
from ..labels import LEAVE_LABELS
from ..system import svc

bp = Blueprint("live", __name__, url_prefix="/live")

WINDOW_DAYS = 3  # ημέρες ανά παράθυρο: χθες · σήμερα · αύριο


def _window(day: date):
    """(base, days) του ζητούμενου παραθύρου — προεπιλογή γύρω από την ημέρα
    αναφοράς. Άκυρες τιμές αγνοούνται σιωπηλά (πρόκειται για προβολή)."""
    days = request.args.get("days", type=int) or WINDOW_DAYS
    try:
        base = date.fromisoformat(request.args["base"])
    except (KeyError, ValueError):
        base = day - timedelta(days=1)
    return base, days


def _context():
    day = today()
    base, days = _window(day)
    return {**q.live_board(day, base, days), "live": not is_simulated()}


@bp.get("/")
def index():
    # Μενού τύπου άδειας (μήνας: double click αρχή -> double click τέλος)
    leave_types = [(k.name, LEAVE_LABELS[k.value]) for k in LeaveType]
    return render_template("live.html", leave_types=leave_types, **_context())


@bp.get("/rows")
def rows():
    """Μόνο το παράθυρο (χάρακας + γραμμές) — το ζητά το htmx κάθε λεπτό για
    ανανέωση, και το live-board.js όταν η μετακίνηση φτάσει στην άκρη."""
    return render_template("partials/_live_rows.html", **_context())


# --------------------------------------------------- ΨΗΦΙΑΚΗ ΚΑΡΤΑ ΕΡΓΑΣΙΑΣ
def _punch_ctx(day: date, eid: str = "", form=None) -> dict:
    """Τα δεδομένα του modal «Χτύπημα κάρτας»: ο κατάλογος εργαζομένων, οι
    ήδη καταχωρημένες διελεύσεις της ημέρας και οι ΒΑΡΔΙΕΣ που παράγουν."""
    return {
        "day": day,
        "eid": eid,
        "employees": q.card_employees(day),
        "card": q.card_day(day, eid),
        "form": form or {},
    }


def _punch_day() -> date:
    """Η ημέρα της φόρμας — προεπιλογή η ημερομηνία αναφοράς του UI."""
    src = request.form if request.method == "POST" else request.args
    try:
        return date.fromisoformat(src["pdate"])
    except (KeyError, ValueError):
        return today()


@bp.get("/punch/modal")
def punch_modal():
    """Χειροκίνητη καταχώρηση διέλευσης (μοτίβο modal §9)."""
    return render_template(
        "partials/_live_punch_modal.html",
        **_punch_ctx(_punch_day(), request.args.get("eid", "")),
    )


@bp.get("/punch/modal/close")
def punch_modal_close():
    return ""


@bp.post("/punch/panel")
def punch_panel():
    """Ξαναχτίζει ΜΟΝΟ το σώμα του modal (αλλαγή εργαζομένου/ημέρας) — καθαρό
    htmx, ώστε η λίστα διελεύσεων να δείχνει πάντα τη σωστή ημέρα."""
    return render_template(
        "partials/_live_punch_body.html",
        **_punch_ctx(_punch_day(), request.form.get("eid", "")),
    )


def _punch_response(day: date, eid: str, ok: bool, msg: str):
    """Απάντηση καταγραφής/ακύρωσης: ανανεωμένο σώμα modal + toast + εντολή
    στο grid να ξαναφορτώσει ΤΩΡΑ (`HX-Trigger: lb-refresh`) — ο χρήστης
    βλέπει το χτύπημα στη θέση του χωρίς να περιμένει τον επόμενο σφυγμό."""
    html = render_template(
        "partials/_live_punch_body.html", **_punch_ctx(day, eid)
    )
    resp = flash_to(html, msg, err=not ok)
    if ok:
        resp.headers["HX-Trigger"] = json.dumps(
            {"flash": {"msg": msg, "err": False}, "lb-refresh": True}
        )
    return resp


@bp.post("/punch")
def punch():
    """ΚΑΤΑΓΡΑΦΗ ΔΙΕΛΕΥΣΗΣ — η μόνη εντολή αυτής της σελίδας.

    Ο έλεγχος ζει στο command layer (`svc.record_card_punch`): εδώ μόνο
    διαβάζονται τα πεδία και επιστρέφεται το ανανεωμένο modal."""
    f = request.form
    eid = f.get("eid", "")
    try:
        day = form_date(f, "pdate", today())
    except ValueError as exc:
        return _punch_response(today(), eid, False, str(exc))
    ok, msg = run_command(
        svc.record_card_punch, eid, day, f.get("time", ""),
        f.get("kind", cp.IN), "manual", (f.get("note") or "").strip(),
    )
    if ok:
        kind = "Είσοδος" if f.get("kind", cp.IN) == cp.IN else "Έξοδος"
        msg = f"{kind} {f.get('time', '')} καταγράφηκε"
    return _punch_response(day, eid, ok, msg)


@bp.post("/punch/cancel")
def punch_cancel():
    """Ακύρωση λανθασμένης διέλευσης (αντισταθμιστικό event, όχι διαγραφή)."""
    f = request.form
    eid = f.get("eid", "")
    day = _punch_day()
    ok, msg = run_command(svc.cancel_card_punch, f.get("punch_id", ""))
    return _punch_response(day, eid, ok, "Η διέλευση ακυρώθηκε" if ok else msg)


@bp.get("/pulse")
def pulse():
    """Ο ΣΦΥΓΜΟΣ της κάρτας: ένας αριθμός που αλλάζει σε κάθε νέα διέλευση.

    Το `live-board.js` τον ρωτά κάθε λίγα δευτερόλεπτα και ξαναζητά τις
    γραμμές ΜΟΝΟ όταν άλλαξε — έτσι ένα χτύπημα (ακόμη κι από άλλη οθόνη)
    εμφανίζεται σχεδόν αμέσως, χωρίς να κατεβαίνει όλο το παράθυρο."""
    return jsonify(v=q.card_pulse())
