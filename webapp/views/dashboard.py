"""Επισκόπηση (προσωπικό τώρα, υποχρεώσεις) και Στατιστικά (γραφήματα κόστους)."""

import json
from datetime import datetime

from flask import Blueprint, render_template, request

from misevent.rules import config

from .. import labels
from .. import queries as q
from ..actions import seed_demo
from ..clock import today
from ..helpers import flash_to, pdf_response, run_command
from ..system import proj
from . import live, obligations

bp = Blueprint("dashboard", __name__)


def _context():
    ref = today()
    now = datetime.now()
    return {
        # Χρονολόγιο υποχρεώσεων (ίδιο panel/htmx με το blueprint «obligations»)
        **obligations._panel(
            request.args.get("id"), ref, obligations._locked(), obligations._view(),
        ),
        # «Ζωντανά»: αποδίδεται ΧΩΡΙΣΤΑ — το context του (`detail`, `rows`,
        # `day`…) θα συγκρουόταν με του χρονολογίου (`detail` = modal)
        "live_html": render_template(
            "partials/_live_board.html", **live.board_context(),
        ),
        "staff": q.staff_now(ref, now.hour * 60 + now.minute),
        "min_wage": config.minimum_wage_for(ref),
        "empty": not proj.persons and not proj.obligations,
    }


def _stats_context():
    ref = today()

    # «Μηνιαία εξέλιξη»: η ΕΤΗΣΙΑ ΑΝΑΦΟΡΑ ΚΟΣΤΟΥΣ του έτους αναφοράς — 12
    # μήνες × 4 συνιστώσες (μισθοδοσία, Δώρα, Επίδομα Αδείας, αποζημιώσεις),
    # με ΠΡΟΒΛΕΨΗ για ό,τι δεν έχει υπολογιστεί ακόμη. Το έτος ακολουθεί την
    # ημερομηνία αναφοράς της κεφαλίδας (ρολόι) — καμία δεύτερη επιλογή.
    report = q.annual_cost_report(ref.year)
    # Τα ΙΔΙΑ γραφήματα σε τρεις οπτικές (κόστος / μικτά / καθαρά): ο πελάτης
    # τις έχει ΟΛΕΣ και εναλλάσσει χωρίς αίτημα στον server.
    def series_of(ms):
        cells = [m["by"][ms["key"]] for m in report["months"]]
        return {
            "label": ms["label"],
            "note": ms["note"],
            "series": [
                {
                    "key": c["key"],
                    "label": c["label"],
                    "color": c["color"],
                    "data": [b["parts"][c["key"]] for b in cells],
                    "fore": [b["fore"][c["key"]] for b in cells],
                }
                for c in ms["components"]
            ],
            "estimated": [b["estimated"] for b in cells],
            "totals": [b["total"] for b in cells],
            "cumulative": [b["cumulative"] for b in cells],
            "first_forecast": ms["first_forecast"],
        }

    chart = {
        "labels": [m["label"] for m in report["months"]],
        "long": [m["long"] for m in report["months"]],
        "order": [ms["key"] for ms in report["measures"]],
        "measures": {ms["key"]: series_of(ms) for ms in report["measures"]},
        "year": report["year"],
        # Κόστος εργατοώρας: ΔΥΟ μεγέθη ανά εργαζόμενο, ώστε το γράφημα να
        # κάνει ακριβώς τη σύγκριση που ζητά ο τίτλος του.
        "hourly": {
            "names": [r["name"] for r in report["hourly"]["rows"]],
            "nominal": [r["nominal"] for r in report["hourly"]["rows"]],
            "rate": [r["rate"] for r in report["hourly"]["rows"]],
            "ratio": [r["ratio"] for r in report["hourly"]["rows"]],
            "hours": [r["hours"] for r in report["hourly"]["rows"]],
        },
    }
    return {
        "report": report,
        "months_gen": labels.MONTHS_GEN,
        "chart_json": json.dumps(chart, ensure_ascii=False),
        "empty": not proj.persons and not proj.obligations,
    }


@bp.get("/")
def index():
    return render_template("dashboard.html", **_context())


@bp.get("/staff")
def staff_table():
    """Ο πίνακας «Προσωπικό» μόνος του — τον ξαναζητά το `lb-refresh`."""
    now = datetime.now()
    return render_template("partials/_staff_table.html",
                           staff=q.staff_now(today(), now.hour * 60 + now.minute))


@bp.get("/stats")
def stats():
    return render_template("stats.html", **_stats_context())


@bp.post("/seed")
def seed():
    ok, msg = run_command(seed_demo)
    html = render_template("dashboard.html", **_context())
    return flash_to(html, msg, err=not ok)


# ── Εκτυπώσεις της επισκόπησης ────────────────────────────────────────────
# Οι δύο αναφορές της σελίδας σε PDF. Το `?measure=` κουβαλά την οπτική που
# βλέπει ο χρήστης (κόστος / μικτά / καθαρά) — ο επιλογέας της κάρτας
# ενημερώνει τον σύνδεσμο, ώστε το χαρτί να δείχνει ό,τι και η οθόνη.
@bp.get("/print/cost-report")
def print_cost_report():
    """ΜΗΝΙΑΙΑ ΕΞΕΛΙΞΗ: ετήσια αναφορά κόστους σε PDF (νέα καρτέλα)."""
    from .. import pdf_reports

    year = request.values.get("year", type=int) or today().year
    measure = request.values.get("measure") or "cost"
    return pdf_response(
        lambda: pdf_reports.annual_cost_pdf(year, measure),
        f"miniaia_exelixi_{year}_{measure}.pdf",
    )


@bp.get("/print/hourly-cost")
def print_hourly_cost():
    """ΠΡΑΓΜΑΤΙΚΟ ΚΟΣΤΟΣ ΕΡΓΑΤΟΩΡΑΣ: ανά εργαζόμενο, σε PDF."""
    from .. import pdf_reports

    year = request.values.get("year", type=int) or today().year
    return pdf_response(
        lambda: pdf_reports.hourly_cost_pdf(year),
        f"kostos_ergatooras_{year}.pdf",
    )
