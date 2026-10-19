"""Προβολή/επεξεργασία αρχείων ΑΠΔ (CSL01) με τον eΑΠΔ viewer.

Ο viewer (`webapp/eapd/`) είναι **αυτόνομη browser-only εφαρμογή** (vendored,
χωρίς backend). Εδώ απλώς:
  * σερβίρεται στατικά κάτω από `/eapd/app/` (τα relative paths του
    `index.html` δουλεύουν αυτούσια χάρη στο trailing slash),
  * εκτίθεται το ΠΕΡΙΕΧΟΜΕΝΟ των αρχείων ΑΠΔ (`/eapd/raw`), ώστε το
    `bridge.js` να τα φορτώνει κατευθείαν στη φόρμα του viewer,
  * η σελίδα `/eapd/` δείχνει τα αρχεία ΑΠΔ των events και τα ανοίγει σε
    iframe (χωρίς JS — `<a target="...">` πάνω στο iframe name).

⚠️ **Κανένα αρχείο ΑΠΔ δεν αποθηκεύεται στον δίσκο.** Το CSL01 είναι
παράγωγο των events και **παράγεται on the fly** εδώ, από την περίοδο και
το ταμείο του `APDFileGenerated` (`svc.apd_file_bytes`). Η **λήψη** το
δίνει πακεταρισμένο σε ZIP (`svc.apd_zip_bytes` — μέσα του το CSL01 με το
κανονικό του όνομα, όπως το περιμένει ο e-ΕΦΚΑ/ΤΕΚΑ).

Read-only: καμία εντολή, κανένα event.
"""

import os

from flask import Blueprint, Response, abort, render_template, request, send_from_directory

from misevent.integrations import apd_files

from .. import clock
from ..system import proj, svc

bp = Blueprint("eapd", __name__, url_prefix="/eapd")

# Ο φάκελος της vendored εφαρμογής (webapp/eapd)
EAPD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eapd")

FUND_LABELS = {"efka": "e-ΕΦΚΑ", "teka": "ΤΕΚΑ"}


def _files():
    """Τα παραχθέντα αρχεία ΑΠΔ, νεότερα πρώτα — ένα ανά (ταμείο, περίοδος).

    Ο επανυπολογισμός/επαναπαραγωγή αντικαθιστά το παλιό αρχείο (κανόνας #2:
    κρατάμε τον τελευταίο υπολογισμό ανά φυσικό κλειδί).
    """
    latest = {}
    for f in proj.apd_files:
        latest[(f.fund, f.period_year, f.period_month)] = f
    rows = []
    for (fund, y, m), f in latest.items():
        rows.append(
            {
                "fund": fund,
                "fund_label": FUND_LABELS.get(fund, fund.upper()),
                "year": y,
                "month": m,
                # Λογικό όνομα λήψης — τίποτα δεν υπάρχει στον δίσκο.
                "name": apd_files.zip_name(fund, y, m),
                "insured": f.insured_count,
                "gross": f.total_gross,
                "contrib": f.total_contributions,
            }
        )
    rows.sort(key=lambda r: (r["year"], r["month"], r["fund"]), reverse=True)
    return rows


def _row(fund: str, year, month):
    """Η γραμμή του αρχείου — ΜΟΝΟ από γνωστά (event-based) κλειδιά."""
    try:
        y, m = int(year), int(month)
    except (TypeError, ValueError):
        return None
    for r in _files():
        if r["fund"] == fund and r["year"] == y and r["month"] == m:
            return r
    return None


def _requested_row():
    """Η ζητούμενη γραμμή από τα query params (`fund`/`year`/`month`)."""
    return _row(
        request.args.get("fund", ""),
        request.args.get("year"),
        request.args.get("month"),
    )


def _generated(row, zipped: bool):
    """Παράγει ΤΩΡΑ το αρχείο της γραμμής (CSL01 ή ZIP) — χωρίς δίσκο."""
    args = (row["fund"], row["year"], row["month"])
    try:
        if zipped:
            return svc.apd_zip_bytes(*args, submission_date=clock.today())
        return svc.apd_file_bytes(*args, submission_date=clock.today())
    except ValueError as exc:  # η περίοδος δεν δίνει πλέον εγγραφές
        abort(404, str(exc))


@bp.get("/")
def index():
    rows = _files()
    # `?fund=&year=&month=` = deep link: ανοίγει κατευθείαν το modal ανάλυσης
    return render_template("eapd.html", rows=rows, selected=_requested_row())


@bp.get("/modal")
def modal():
    """Το modal «Ανάλυση αρχείου» (eΑΠΔ viewer σε iframe)."""
    r = _requested_row()
    if r is None:
        abort(404)
    return render_template("partials/_eapd_modal.html", r=r)


@bp.get("/modal/close")
def modal_close():
    return ""


@bp.get("/raw")
def raw():
    """Το ΩΜΟ αρχείο ΑΠΔ (cp1253), παραγόμενο on the fly — για τον viewer."""
    r = _requested_row()
    if r is None:
        abort(404)
    return Response(_generated(r, zipped=False), mimetype="application/octet-stream")


@bp.get("/download")
def download():
    """Λήψη: το CSL01 μέσα σε ZIP, παραγόμενο on the fly."""
    r = _requested_row()
    if r is None:
        abort(404)
    return Response(
        _generated(r, zipped=True),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{r["name"]}"'},
    )


@bp.get("/app/")
def app_index():
    # Η ΣΕΛΙΔΑ δεν κρατιέται στην cache (τα query params αλλάζουν κάθε φορά)
    return send_from_directory(EAPD_DIR, "index.html", max_age=0)


@bp.get("/app/<path:filename>")
def app_asset(filename):
    # Τα assets πιάνουν ~830KB (μητρώα ΟΣΥΚ). ΔΕΝ βάζουμε μακρά cache — μια
    # τοπική αλλαγή στον viewer θα έμενε αόρατη· η επικύρωση με ETag αρκεί
    # (304, μηδέν bytes) και τα scripts κατεβαίνουν πλέον ΠΑΡΑΛΛΗΛΑ (loader.js).
    return send_from_directory(EAPD_DIR, filename, max_age=0)
