"""Πληρωμές — ΜΙΑ γραμμή ανά περίοδο (Μισθοί / ΕΦΚΑ / ΤΕΚΑ / ΦΜΥ / Σύνολο).

**ΠΡΟΒΟΛΗ ΜΟΝΟ**: καμία εντολή, κανένα event. Οι υποβολές (ΑΠΔ/ΦΜΥ) και οι
καταβολές εκτελούνται από τη σελίδα **Υποχρεώσεις** (ενεργοποίηση της
αντίστοιχης υποχρέωσης → `webapp/actions.py::COMMANDS`)· τα παραγόμενα αρχεία
ζουν στις σελίδες «Αρχεία ΑΠΔ» και «Αρχεία ΦΜΥ».
"""

from flask import Blueprint, render_template

from .. import queries as q

bp = Blueprint("payments", __name__, url_prefix="/payments")


@bp.get("/")
def index():
    rows, totals = q.payments_by_period()
    return render_template(
        "payments.html", rows=rows, totals=totals, columns=q.PAYMENT_COLUMNS
    )
