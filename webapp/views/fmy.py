"""Αρχεία ΦΜΥ (JL10) — λίστα, ανάλυση και λήψη.

Ακριβώς η ΙΔΙΑ διαχείριση με τα «Αρχεία ΑΠΔ» (`views/eapd.py`):

⚠️ **Κανένα αρχείο δεν αποθηκεύεται στον δίσκο.** Το JL10 είναι παράγωγο των
events και **παράγεται on the fly** από την περίοδο του `FMYFileGenerated`
(`svc.fmy_file_bytes`). Η **λήψη** το δίνει πακεταρισμένο σε ZIP
(`svc.fmy_zip_bytes` — μέσα του το `JL10` με το κανονικό του όνομα, όπως το
περιμένει η ΑΑΔΕ).

Read-only: καμία εντολή, κανένα event. Η ΔΗΛΩΣΗ ΦΜΥ εκτελείται από τις
Υποχρεώσεις (`actions.py::_submit_fmy`).
Δεν υπάρχει vendored viewer για το JL10 (όπως ο eΑΠΔ), οπότε η «Ανάλυση»
είναι δικό μας fragment: οι αναλυτικές αμοιβές σε πίνακα + οι ωμές εγγραφές.
"""

from flask import Blueprint, Response, abort, render_template, request

from misevent.integrations import fmy_files

from ..system import proj, svc

bp = Blueprint("fmy", __name__, url_prefix="/fmy")

REC_LABELS = {
    "0": "Κεφαλίδα",
    "1": "Στοιχεία δηλούντος",
    "2": "Σύνολα",
    "3": "Αναλυτική αμοιβή",
}


def _files():
    """Τα παραχθέντα αρχεία ΦΜΥ, νεότερα πρώτα — ένα ανά περίοδο.

    Η επαναπαραγωγή αντικαθιστά το παλιό (κανόνας #2: κρατάμε τον τελευταίο
    υπολογισμό ανά φυσικό κλειδί).
    """
    latest = {}
    for f in proj.fmy_files:
        latest[(f.period_year, f.period_month)] = f
    rows = [
        {
            "year": y,
            "month": m,
            # Λογικό όνομα λήψης — τίποτα δεν υπάρχει στον δίσκο.
            "name": fmy_files.zip_name(y, m),
            "employees": f.employee_count,
            "gross": f.total_gross,
            "tax": f.total_tax,
        }
        for (y, m), f in latest.items()
    ]
    rows.sort(key=lambda r: (r["year"], r["month"]), reverse=True)
    return rows


def _row(year, month):
    """Η γραμμή του αρχείου — ΜΟΝΟ από γνωστές (event-based) περιόδους."""
    try:
        y, m = int(year), int(month)
    except (TypeError, ValueError):
        return None
    for r in _files():
        if r["year"] == y and r["month"] == m:
            return r
    return None


def _requested_row():
    return _row(request.args.get("year"), request.args.get("month"))


def _generated(row, zipped: bool):
    """Παράγει ΤΩΡΑ το αρχείο της γραμμής (JL10 ή ZIP) — χωρίς δίσκο."""
    try:
        if zipped:
            return svc.fmy_zip_bytes(row["year"], row["month"])
        return svc.fmy_file_bytes(row["year"], row["month"])
    except ValueError as exc:  # η περίοδος δεν δίνει πλέον αναλυτικές αμοιβές
        abort(404, str(exc))


def _analysis(r) -> dict:
    """Τα δεδομένα του modal «Ανάλυση»: εγγραφές του αρχείου + αναλυτικές
    αμοιβές. Παράγονται ΤΩΡΑ, από τα ίδια bytes που θα κατέβαζε ο χρήστης."""
    text = _generated(r, zipped=False).decode(fmy_files.ENCODING)
    return {
        "r": r,
        "records": [
            {
                "kind": ln[0],
                "label": REC_LABELS.get(ln[0], "—"),
                "len": len(ln),
                "raw": ln,
            }
            for ln in text.split(fmy_files.CRLF)
            if ln
        ],
        "earnings": svc.fmy_rows(r["year"], r["month"]),
    }


@bp.get("/")
def index():
    # `?year=&month=` = deep link: ανοίγει κατευθείαν το modal ανάλυσης
    sel = _requested_row()
    return render_template(
        "fmy.html",
        rows=_files(),
        selected=sel,
        analysis=_analysis(sel) if sel else None,
    )


@bp.get("/modal")
def modal():
    """Το modal «Ανάλυση αρχείου»: αναλυτικές αμοιβές + ωμές εγγραφές."""
    r = _requested_row()
    if r is None:
        abort(404)
    return render_template("partials/_fmy_modal.html", **_analysis(r))


@bp.get("/modal/close")
def modal_close():
    return ""


@bp.get("/raw")
def raw():
    """Το ΩΜΟ αρχείο JL10 (cp1253), παραγόμενο on the fly."""
    r = _requested_row()
    if r is None:
        abort(404)
    return Response(_generated(r, zipped=False), mimetype="application/octet-stream")


@bp.get("/download")
def download():
    """Λήψη: το JL10 μέσα σε ZIP, παραγόμενο on the fly."""
    r = _requested_row()
    if r is None:
        abort(404)
    return Response(
        _generated(r, zipped=True),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{r["name"]}"'},
    )
