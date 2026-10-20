"""Οθόνη ΜΗΝΑ — ανοίγει με κλικ σε μήνα της λωρίδας χρόνου του header.

**ΠΡΟΒΟΛΗ ΜΟΝΟ**: όλα όσα αφορούν μία περίοδο (παρουσίες, άδειες,
μισθοδοσία, Δώρα/Επίδομα, υποχρεώσεις, υποβολές, πληρωμές) σε μία σελίδα.
Οι ενέργειες μένουν στις σελίδες τους (Υποχρεώσεις κ.λπ.).
"""

from flask import Blueprint, abort, render_template

from .. import queries as q
from ..clock import today

bp = Blueprint("month", __name__, url_prefix="/month")


@bp.get("/<int:y>/<int:m>")
def detail(y, m):
    if not (1 <= m <= 12 and 2000 <= y <= 2100):
        abort(404)
    return render_template("month.html", d=q.month_detail(y, m, today()))
