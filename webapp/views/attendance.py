"""
Παρουσίες: μηνιαίο συγκεντρωτικό παρουσιών/απουσιών.

Είναι το ΒΗΜΑ ΠΡΙΝ τη μισθοδοσία: το προσχέδιο παράγεται από τις
καταγεγραμμένες παρουσίες ή (όπου δεν υπάρχουν) τεκμαίρεται από το πρόγραμμα
— εβδομαδιαίο ή ημερήσιο, με το ημερήσιο να υπερισχύει. Ο χρήστης μπορεί να
το διορθώσει χειροκίνητα και μετά το εγκρίνει: η έγκριση (`AttendanceSheetApproved`)
κλείνει την υποχρέωση ATTENDANCE_APPROVAL και ΑΝΟΙΓΕΙ τη μισθοδοσία.

UI: read-only πίνακας εργαζομένων· η επεξεργασία κάθε εργαζόμενου γίνεται σε
MODAL. Οι διορθώσεις «σταδιάζονται» σε κρυφά inputs του #sheet-form (χωρίς
event) και ΟΛΕΣ μαζί γράφονται με το κουμπί «Έγκριση».
"""

from flask import Blueprint, render_template, request

from .. import queries as q
from ..clock import today
from ..helpers import flash_to, run_command
from ..system import proj, svc

bp = Blueprint("attendance", __name__, url_prefix="/attendance")


def _cast(f, name):
    """Τιμή input από φόρμα, με τον τύπο του πεδίου (int/float, 0 αν κενό)."""
    _key, cast = q._INPUT_MAP[name]
    raw = (f.get(name) or "").strip()
    if not raw:
        return cast(0)
    try:
        return cast(float(raw)) if cast is int else cast(raw)
    except ValueError:
        raise ValueError(f"Μη έγκυρη τιμή στο πεδίο «{name}»") from None


def _period():
    """Προεπιλογή: η περίοδος της παλαιότερης εκκρεμούς έγκρισης."""
    now = today()
    pend = sorted(
        (o.period_year, o.period_month)
        for o in proj.obligations.values()
        if o.obligation_type == "ATTENDANCE_APPROVAL" and not o.fulfilled
        and q.period_has_employment(o.period_year, o.period_month)
    )
    dflt = pend[0] if pend else (now.year, now.month)
    y = request.values.get("y", type=int) or dflt[0]
    m = request.values.get("m", type=int) or dflt[1]
    return y, m


def _context():
    y, m = _period()
    return {"y": y, "m": m, **q.attendance_sheet(y, m)}


@bp.get("/")
def index():
    return render_template("attendance.html", **_context())


def _staged_override(eid):
    """Οι τρέχουσες (σταδιακά επεξεργασμένες) τιμές από τα κρυφά inputs
    `{name}_{eid}` που στέλνει το hx-include."""
    override = {}
    for name, key, _cast_ in q.SHEET_INPUTS:
        raw = request.args.get(f"{name}_{eid}")
        if raw not in (None, ""):
            override[key] = raw
    return override


@bp.get("/edit/<eid>")
def edit_modal(eid):
    """Ανοίγει το modal επεξεργασίας ενός εργαζόμενου, γεμάτο με τις τρέχουσες
    (σταδιακά επεξεργασμένες) τιμές των κρυφών inputs του πίνακα."""
    y, m = _period()
    row = q.attendance_row(y, m, eid, _staged_override(eid))
    if row is None:
        return "", 404
    return render_template(
        "partials/_attendance_modal.html", r=row, y=y, m=m,
        inputs=q.SHEET_INPUTS, sources=q.attendance_sheet(y, m)["sources"],
        class_rates=q.class_hourly_rates(row["hourly_rate"], y, m),
    )


@bp.get("/premium/<eid>")
def premium_modal(eid):
    """Modal ΑΝΑΛΥΣΗΣ προσαυξήσεων (read-only) — εμφανίζεται με κλικ στο ποσό.
    Δείχνει ανά είδος: ώρες × ωρομίσθιο × συντελεστή = ποσό."""
    y, m = _period()
    br = q.attendance_premium(y, m, eid, _staged_override(eid))
    if br is None:
        return "", 404
    return render_template("partials/_attendance_premium.html", y=y, m=m, **br)


@bp.post("/stage/<eid>")
def stage(eid):
    """«Αποθήκευση» του modal: ΔΕΝ γράφει event — ενημερώνει (OOB) τη γραμμή
    και τα κρυφά inputs του πίνακα με τις νέες τιμές. Η οριστικοποίηση γίνεται
    με το κουμπί «Έγκριση» (όλοι μαζί)."""
    y, m = _period()

    def cmd():
        override = {q._INPUT_MAP[name][0]: _cast(request.form, name)
                    for name, _key, _cast_ in q.SHEET_INPUTS}
        override["source"] = "manual"
        row = q.attendance_row(y, m, eid, override)
        if row is None:
            raise ValueError("Άγνωστη απασχόληση στην περίοδο")
        return render_template(
            "partials/_attendance_row.html", r=row, y=y, m=m,
            inputs=q.SHEET_INPUTS, sources=q.attendance_sheet(y, m)["sources"],
            oob=True,
        )

    ok, html = run_command(cmd)
    if not ok:
        # html = μήνυμα σφάλματος. HX-Reswap:none -> το modal ΜΕΝΕΙ ανοιχτό,
        # δείχνουμε μόνο toast (ο χρήστης δεν χάνει ό,τι πληκτρολόγησε).
        resp = flash_to("", html, err=True)
        resp.headers["HX-Reswap"] = "none"
        return resp
    # html: το OOB <div> της γραμμής· το (κενό) primary swap κλείνει το modal
    return html


@bp.get("/close")
def modal_close():
    """Κλείσιμο του modal (Ακύρωση) — αδειάζει τον host χωρίς αλλαγές."""
    return ""


@bp.post("/approve")
def approve():
    y, m = _period()

    def cmd():
        # Ψηφιακή κάρτα: ίδιο προαπαιτούμενο με την ενεργοποίηση από το
        # χρονολόγιο (requires της ATTENDANCE_APPROVAL)
        if any(o.obligation_type == "CARD_IMPORT" and not o.fulfilled
               and (o.period_year, o.period_month) == (y, m)
               for o in proj.obligations.values()):
            raise ValueError(f"Προαπαιτείται η «Εισαγωγή κάρτας περιόδου» {m}/{y} "
                             "(Υποχρεώσεις)")
        f = request.form
        lines = {}
        for eid in f.getlist("eid"):
            lines[eid] = {
                q._INPUT_MAP[name][0]: _cast_form(f, f"{name}_{eid}", name)
                for name, _key, _cast_ in q.SHEET_INPUTS
            }
        res = svc.approve_attendance(y, m, lines)
        return (
            f"Εγκρίθηκε το συγκεντρωτικό παρουσιών {m}/{y} "
            f"({len(res)} εργαζόμενοι) — άνοιξε η μισθοδοσία"
        )

    ok, msg = run_command(cmd)
    return flash_to(
        render_template("partials/_attendance_panel.html", **_context()),
        msg,
        err=not ok,
    )


def _cast_form(f, field, name):
    """Όπως _cast αλλά για suffixed input name (`{name}_{eid}`) του πίνακα."""
    _key, cast = q._INPUT_MAP[name]
    raw = (f.get(field) or "").strip()
    if not raw:
        return cast(0)
    try:
        return cast(float(raw)) if cast is int else cast(raw)
    except ValueError:
        raise ValueError(f"Μη έγκυρη τιμή στο πεδίο «{name}»") from None


@bp.post("/import")
def import_karta():
    """Εισαγωγή πραγματικών παρουσιών από αρχείο «Κάρτας Εργασίας» ΕΡΓΑΝΗ
    (xlsx). Οι παρουσίες αντιστοιχίζονται με ΑΦΜ."""

    def cmd():
        up = request.files.get("karta")
        if up is None or not up.filename:
            raise ValueError("Δεν επιλέχθηκε αρχείο καρτών (xlsx)")
        y, m = _period()
        res = svc.import_karta_attendance(up.read(), y, m)
        periods = ", ".join(f"{mm}/{yy}" for yy, mm in res["periods"]) or "—"
        msg = (
            f"Εισήχθησαν {res['recorded']} ημέρες παρουσίας για "
            f"{res['employees']} εργαζόμενους (περίοδοι: {periods})"
        )
        if res["skipped"]:
            msg += (f" · ΠΑΡΑΛΕΙΦΘΗΚΑΝ {len(res['skipped'])} ημέρες εκτός {m}/{y} "
                    f"({res['skipped'][0]:%d/%m/%Y}–{res['skipped'][-1]:%d/%m/%Y})")
        if res["blind"]:
            fixed = sum(b["fixed"] for b in res["blind"])
            msg += (f" · ΤΥΦΛΑ ΧΤΥΠΗΜΑΤΑ: {len(res['blind'])} "
                    f"(διορθώθηκαν από πρόγραμμα: {fixed})")
            msg += "".join(
                f" · {b['name']} {b['day'].strftime('%d/%m')}: {b['fix']}"
                for b in res["blind"] if not b["fixed"]
            )
        if res["on_leave"]:
            msg += " · ⚠️ ΧΤΥΠΗΜΑΤΑ ΣΕ ΗΜΕΡΕΣ ΑΔΕΙΑΣ: " + ", ".join(
                f"{b['name']} {b['day'].strftime('%d/%m')}" for b in res["on_leave"]
            )
        if res["unmatched"]:
            afms = ", ".join(
                f"{afm} {name}".strip() for afm, name in res["unmatched"].items()
            )
            msg += f" · ΑΓΝΩΣΤΑ ΑΦΜ (χωρίς ενεργή απασχόληση): {afms}"
        return msg

    ok, msg = run_command(cmd)
    return flash_to(
        render_template("partials/_attendance_panel.html", **_context()),
        msg,
        err=not ok,
    )
