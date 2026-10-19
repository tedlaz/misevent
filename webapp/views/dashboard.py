"""Επισκόπηση: στατιστικά, γραφήματα, επόμενες υποχρεώσεις."""

import json

from flask import Blueprint, render_template, request

from misevent.rules import config

from .. import labels
from .. import queries as q
from ..actions import seed_demo
from ..clock import today
from ..helpers import flash_to, pdf_response, run_command
from ..system import proj, store

bp = Blueprint("dashboard", __name__)


def _context():
    ref = today()
    obs = q.obligation_list(ref, view="all")  # η επισκόπηση μετράει και τις ολοκληρωμένες
    counts = q.status_counts(obs)
    mw = config.minimum_wage_for(ref)

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
        "counts": counts,
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
    upcoming = [o for o in obs if o["status"] != "done"][:8]
    return {
        "counts": counts,
        "upcoming": upcoming,
        "leave_alerts": q.leave_compliance(ref.year),
        "ref_year": ref.year,
        "report": report,
        "months_gen": labels.MONTHS_GEN,
        "chart_json": json.dumps(chart, ensure_ascii=False),
        "min_wage": mw,
        "persons_n": sum(
            1
            for person in proj.persons.values()
            if any(
                eid in proj.employments and proj.employments[eid].hire_submitted
                for eid in person.employment_ids
            )
        ),
        "employments_n": sum(
            1 for e in proj.employments.values() if e.hire_submitted and e.active
        ),
        "events_n": len(store.load()),
        "empty": not proj.persons and not proj.obligations,
    }


@bp.get("/")
def index():
    return render_template("dashboard.html", **_context())


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
