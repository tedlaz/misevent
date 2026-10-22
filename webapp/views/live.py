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

from datetime import date, timedelta

from flask import Blueprint, jsonify, render_template, request

from misevent.domain.events import LeaveType

from .. import queries as q
from ..clock import is_simulated, today
from ..labels import LEAVE_LABELS

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


@bp.get("/pulse")
def pulse():
    """Ο ΣΦΥΓΜΟΣ της κάρτας: ένας αριθμός που αλλάζει σε κάθε νέα διέλευση.

    Το `live-board.js` τον ρωτά κάθε λίγα δευτερόλεπτα και ξαναζητά τις
    γραμμές ΜΟΝΟ όταν άλλαξε — έτσι ένα χτύπημα (ακόμη κι από άλλη οθόνη)
    εμφανίζεται σχεδόν αμέσως, χωρίς να κατεβαίνει όλο το παράθυρο."""
    return jsonify(v=q.card_pulse())
