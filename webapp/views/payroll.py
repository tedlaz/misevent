"""Μισθοδοσία: ανά περίοδο (preview/εκτέλεση), Δώρα, Επίδομα Αδείας."""

import io

from flask import Blueprint, abort, render_template, request, send_file

from .. import queries as q
from ..clock import today
from ..helpers import flash_to, pdf_response, run_command
from ..system import proj, svc

bp = Blueprint("payroll", __name__, url_prefix="/payroll")

GIFT_KINDS = [
    ("easter", "Δώρο Πάσχα"),
    ("christmas", "Δώρο Χριστουγέννων"),
]
# Το Επίδομα Αδείας ΔΕΝ είναι ετήσιο: υπάρχει μία περίοδος ανά μήνα στον οποίο
# κάποιος πήρε την πρώτη του κανονική άδεια (βλ. scheduler.py).


def _period():
    """Προεπιλεγμένη περίοδος: η ΤΡΕΧΟΥΣΑ της αλυσίδας (η παλαιότερη εκκρεμής
    μισθοδοσία) — αλλιώς ο μήνας της ημερομηνίας αναφοράς."""
    now = today()
    nxt = q.next_payroll_period() or (now.year, now.month)
    y = request.values.get("y", type=int) or nxt[0]
    m = request.values.get("m", type=int) or nxt[1]
    return y, m


def _context():
    # Η περίοδος οδηγεί πλέον ΜΟΝΟ την προεπιλογή έτους για Δώρα/Επίδομα: η
    # προεπισκόπηση της τρέχουσας μισθοδοσίας αφαιρέθηκε από την οθόνη (ο
    # υπολογισμός & η πληρωμή γίνονται από τις Υποχρεώσεις, §5.5).
    y, m = _period()
    year = request.values.get("gy", type=int) or y
    gifts = {}
    for kind, label in GIFT_KINDS:
        grows = q.gift_rows(kind, year, preview=True)
        actual = q.gift_rows(kind, year, preview=False)
        gifts[kind] = {
            "label": label,
            "rows": actual or grows,
            "done": bool(actual),
            "totals": q.money_totals(
                actual or grows, ["Μικτά", "ΕΦΚΑ εργαζ.", "ΦΜΥ", "Καθαρά"]
            ),
        }
    # ΚΑΘΟΛΙΚΟ ΚΑΤΑΒΟΛΩΝ του επιλεγμένου έτους: μισθοδοσία περιόδου, Δώρα,
    # Επίδομα Αδείας, αποζημιώσεις — μία γραμμή ανά μήνα και είδος.
    ledger = q.payroll_ledger(year)
    severances = q.settlement_rows("severance")
    leave_comps = q.settlement_rows("leave")
    return {
        "y": y, "m": m, "gyear": year,
        "gifts": gifts,
        "allowances": q.allowance_periods(year),
        # Εκκαθαρίσεις αποχώρησης (υπολογίζονται αυτόματα στην αποχώρηση)
        "severances": severances,
        "severance_totals": q.settlement_totals(severances),
        "leave_comps": leave_comps,
        "leave_comp_totals": q.settlement_totals(leave_comps),
        # ΜΟΝΟ το επιλεγμένο έτος: ο πίνακας διαβάζεται ως ετήσιος — με όλες
        # τις χρονιές μαζί, το άθροισμά του δεν συμφωνούσε με την ετήσια
        # αναφορά της Επισκόπησης.
        "ledger": ledger,
        "ledger_sum": q.ledger_totals(ledger),
    }


@bp.get("/")
def index():
    return render_template("payroll.html", **_context())


@bp.get("/analysis")
def analysis_modal():
    """Modal ΑΝΑΛΥΣΗΣ μισθοδοσίας μιας περιόδου — πλήρης απόδειξη αποδοχών
    ανά εργαζόμενο (βάση, προσαυξήσεις, ασθένεια, κρατήσεις, καθαρά)."""
    y = request.values.get("y", type=int)
    m = request.values.get("m", type=int)
    rows = q.payroll_analysis(y, m)
    if not rows:
        return "", 404
    return render_template(
        "partials/_payroll_analysis_modal.html", y=y, m=m, rows=rows,
        totals=q.monthly_totals().get((y, m)),
    )


GIFT_LABELS = dict(GIFT_KINDS) | {"allowance": "Επίδομα Αδείας"}
# Σύντομο σήμα είδους για το όνομα του PNG (ίδιο με τα ZIP των αποδείξεων)
GIFT_SLUGS = {"easter": "ΔΠ", "christmas": "ΔΧ", "allowance": "ΕΑ"}


@bp.get("/gift/analysis")
def gift_analysis_modal():
    """Modal ΑΝΑΛΥΣΗΣ Δώρου / Επιδόματος Αδείας — ΙΔΙΑ ανάλυση με τη μηνιαία
    μισθοδοσία (κοινό fragment `_analysis_slip.html`)."""
    kind = request.values.get("kind", "")
    if kind not in GIFT_LABELS:
        abort(404)
    year = request.values.get("gy", type=int) or today().year
    month = request.values.get("gm", type=int)  # μόνο για το Επίδομα Αδείας
    rows = q.gift_analysis(kind, year, month)
    if not rows:
        return "", 404
    label = GIFT_LABELS[kind] + (f" {month}/{year}" if month else f" {year}")
    return render_template(
        "partials/_gift_analysis_modal.html",
        rows=rows, y=year, label=label,
        png_period=(f"{GIFT_SLUGS[kind]}-{month:02d}-{year}" if month
                    else f"{GIFT_SLUGS[kind]}-{year}"),
        net_total=round(sum(r["a"]["net"] for r in rows), 2),
    )


def _zip_response(build):
    """Παράγει το ZIP μέσα στο lock (read-only) και το κατεβάζει. Το ZIP
    παράγεται on the fly· η αποθήκευση στον φάκελο apodeijeis γίνεται μόνο
    αν είναι ενεργή η προτίμηση «Διάφορα → save_zip» (default: όχι).
    send_file: σωστό UTF-8 όνομα (filename*) για τις καταλήξεις ΔΠ/ΔΧ."""
    from .. import pdf_reports
    from ..system import lock

    try:
        with lock:
            filename, data = build()
            if proj.misc_settings.get("save_zip"):
                pdf_reports.save_export(filename, data)
    except ValueError as e:
        return str(e), 404
    return send_file(
        io.BytesIO(data),
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


@bp.get("/print/slips/zip")
def print_slips_zip():
    """ZIP με μία απόδειξη ανά εργαζόμενο — κατέβασμα (και αποθήκευση στο
    apodeijeis μόνο αν το ζητά η προτίμηση «Διάφορα»)."""
    from .. import pdf_reports

    y = request.values.get("y", type=int)
    m = request.values.get("m", type=int)
    return _zip_response(lambda: pdf_reports.payroll_slips_zip(y, m))


@bp.get("/print/statement")
def print_statement():
    """Αναλυτική μισθοδοτική κατάσταση περιόδου σε PDF (νέα καρτέλα)."""
    from .. import pdf_reports

    y = request.values.get("y", type=int)
    m = request.values.get("m", type=int)
    return pdf_response(
        lambda: pdf_reports.payroll_statement_pdf(y, m),
        f"misthodotiki_katastasi_{y}-{m:02d}.pdf",
    )


@bp.get("/print/slips")
def print_slips():
    """Αποδείξεις αποδοχών περιόδου (όλες, ή ενός εργαζόμενου με ?eid=)."""
    from .. import pdf_reports

    y = request.values.get("y", type=int)
    m = request.values.get("m", type=int)
    eid = request.values.get("eid") or None
    return pdf_response(
        lambda: pdf_reports.payroll_slips_pdf(y, m, eid),
        f"apodeixeis_apodoxon_{y}-{m:02d}.pdf",
    )


GIFT_FILENAMES = {
    "easter": "doro_pasxa",
    "christmas": "doro_christougennon",
    "allowance": "epidoma_adeias",
}


def _gift_args(kind):
    year = request.values.get("gy", type=int) or today().year
    month = request.values.get("gm", type=int) if kind == "allowance" else None
    stem = GIFT_FILENAMES.get(kind, kind) + f"_{year}"
    if month:
        stem += f"-{month:02d}"
    return year, month, stem


@bp.get("/print/gift/<kind>/statement")
def print_gift_statement(kind):
    """Κατάσταση Δώρου/Επιδόματος Αδείας σε PDF (νέα καρτέλα)."""
    from .. import pdf_reports

    year, month, stem = _gift_args(kind)
    return pdf_response(
        lambda: pdf_reports.gift_statement_pdf(kind, year, month),
        f"katastasi_{stem}.pdf",
    )


@bp.get("/print/gift/<kind>/slips")
def print_gift_slips(kind):
    """Αποδείξεις Δώρου/Επιδόματος (όλες, ή ενός εργαζόμενου με ?eid=)."""
    from .. import pdf_reports

    year, month, stem = _gift_args(kind)
    eid = request.values.get("eid") or None
    return pdf_response(
        lambda: pdf_reports.gift_slips_pdf(kind, year, month, eid),
        f"apodeixeis_{stem}.pdf",
    )


@bp.get("/print/settlement/<kind>/<employment_id>")
def print_settlement(kind, employment_id):
    """Απόδειξη εκκαθάρισης αποχώρησης (νέα καρτέλα): αποζημίωση μη ληφθείσας
    αδείας (`leave`) ή αποζημίωση απόλυσης (`severance`) ενός εργαζόμενου."""
    from .. import pdf_reports

    emp = proj.employments.get(employment_id)
    afm = emp.afm if emp is not None and emp.afm else employment_id
    if kind == "leave":
        def build():
            return pdf_reports.leave_compensation_slip_pdf(employment_id)
        fn = f"apozimiosi_adeias_{afm}.pdf"
    elif kind == "severance":
        def build():
            return pdf_reports.severance_slip_pdf(employment_id)
        fn = f"apozimiosi_apolysis_{afm}.pdf"
    else:
        abort(404)
    return pdf_response(build, fn)


def _settlement_args(kind):
    """(έτος, μήνας) μιας γραμμής αποζημίωσης (`leave`/`severance`) του
    καθολικού — 404 για άγνωστο είδος ή χωρίς ?y=&m=."""
    y = request.values.get("y", type=int)
    m = request.values.get("m", type=int)
    if kind not in ("leave", "severance") or not y or not m:
        abort(404)
    return y, m


@bp.get("/modal/settlement-analysis/<kind>")
def settlement_analysis_modal(kind):
    """Modal ΑΝΑΛΥΣΗΣ αποζημιώσεων ενός μήνα: κανόνας υπολογισμού και ποσά
    ανά εργαζόμενο."""
    from ..pdf_reports import SETTLEMENT_TITLES

    y, m = _settlement_args(kind)
    rows = q.settlement_rows(kind, y, m)
    if not rows:
        return "", 404
    return render_template(
        "partials/_settlement_analysis_modal.html", rows=rows,
        totals=q.settlement_totals(rows),
        label=f"{SETTLEMENT_TITLES[kind][0]} {m}/{y}",
    )


@bp.get("/print/settlements/<kind>/statement")
def print_settlement_statement(kind):
    """Κατάσταση αποζημιώσεων ενός μήνα σε PDF (νέα καρτέλα)."""
    from .. import pdf_reports

    y, m = _settlement_args(kind)
    return pdf_response(
        lambda: pdf_reports.settlement_statement_pdf(kind, y, m),
        f"katastasi_{kind}_{y}-{m:02d}.pdf",
    )


@bp.get("/print/settlements/<kind>/slips")
def print_settlement_slips(kind):
    """Όλες οι αποδείξεις αποζημίωσης ενός μήνα σε ένα PDF."""
    from .. import pdf_reports

    y, m = _settlement_args(kind)
    return pdf_response(
        lambda: pdf_reports.settlement_slips_pdf(kind, y, m),
        f"apodeixeis_{kind}_{y}-{m:02d}.pdf",
    )


@bp.get("/print/settlements/<kind>/zip")
def print_settlement_slips_zip(kind):
    """ZIP με μία απόδειξη αποζημίωσης ανά εργαζόμενο του μήνα."""
    from .. import pdf_reports

    y, m = _settlement_args(kind)
    return _zip_response(lambda: pdf_reports.settlement_slips_zip(kind, y, m))


@bp.get("/print/exit-settlement/<employment_id>")
def print_exit_settlement(employment_id):
    """ΟΛΕΣ οι αποδείξεις της εκκαθάρισης αποχώρησης σε ΕΝΑ PDF: συγκεντρωτικό
    στην 1η σελίδα (με το καταβλητέο της ημέρας αποχώρησης και χωριστά το
    Δώρο) και από κάτω μία υπογράψιμη απόδειξη ανά υπολογισμό."""
    from .. import pdf_reports

    emp = proj.employments.get(employment_id)
    afm = emp.afm if emp is not None and emp.afm else employment_id
    return pdf_response(
        lambda: pdf_reports.exit_settlement_pdf(employment_id),
        f"ekkatharisi_apoxorisis_{afm}.pdf",
    )


@bp.get("/print/gift/<kind>/slips/zip")
def print_gift_slips_zip(kind):
    """ZIP με μία απόδειξη Δώρου/Επιδόματος ανά εργαζόμενο — κατέβασμα (και
    αποθήκευση στο apodeijeis μόνο αν το ζητά η προτίμηση «Διάφορα»)."""
    from .. import pdf_reports

    year, month, _stem = _gift_args(kind)
    return _zip_response(lambda: pdf_reports.gift_slips_zip(kind, year, month))


@bp.get("/close")
def modal_close():
    """Κλείσιμο του modal — αδειάζει τον host χωρίς αλλαγές."""
    return ""


@bp.post("/run")
def run():
    y, m = _period()

    def cmd():
        res = svc.run_monthly_payroll(y, m)
        return f"Υπολογίστηκε μισθοδοσία {m}/{y} για {len(res)} απασχολήσεις"

    ok, msg = run_command(cmd)
    return flash_to(render_template("partials/_payroll_panel.html", **_context()),
                    msg, err=not ok)


@bp.post("/pay")
def pay():
    y, m = _period()

    def cmd():
        svc.pay_salaries(y, m)
        return f"Πληρώθηκαν οι μισθοί {m}/{y}"

    ok, msg = run_command(cmd)
    return flash_to(render_template("partials/_payroll_panel.html", **_context()),
                    msg, err=not ok)


@bp.post("/gift/<kind>")
def gift(kind):
    year = request.values.get("gy", type=int) or today().year
    month = request.values.get("gm", type=int)  # μόνο για το Επίδομα Αδείας

    def cmd():
        if kind == "easter":
            svc.run_easter_gifts(year)
            return f"Υπολογίστηκε Δώρο Πάσχα {year}"
        if kind == "christmas":
            svc.run_christmas_gifts(year)
            return f"Υπολογίστηκε Δώρο Χριστουγέννων {year}"
        if kind == "allowance":
            if not month:
                raise ValueError("Το Επίδομα Αδείας απαιτεί περίοδο (μήνα)")
            n = len(svc.allowance_beneficiaries(year, month))
            svc.run_leave_allowance(year, month)
            return f"Υπολογίστηκε Επίδομα Αδείας {month}/{year} για {n} εργαζόμενους"
        raise ValueError(f"Άγνωστος τύπος: {kind}")

    ok, msg = run_command(cmd)
    return flash_to(render_template("partials/_payroll_panel.html", **_context()),
                    msg, err=not ok)
