"""ΕΡΓΑΝΗ ΙΙ: ιστορικό υποβολών και προβολή του JSON payload."""

import json

from flask import Blueprint, render_template

from ..labels import ERGANI_LABELS
from ..queries import emp_name
from ..system import proj

bp = Blueprint("ergani", __name__, url_prefix="/ergani")


@bp.get("/")
def index():
    subs = [
        {
            "doc": ev.doc_type,
            "label": ERGANI_LABELS.get(ev.doc_type, ev.doc_type),
            "who": emp_name(ev.employee_id) if ev.employee_id else "ΕΤΑΙΡΕΙΑ",
            "status": ev.status,
            "reference": ev.reference,
            "environment": ev.environment,
            "payload": _pretty_json(ev.payload),
        }
        for ev in reversed(proj.ergani_submissions)
    ]
    return render_template("ergani.html", subs=subs)


def _pretty_json(payload: str) -> str:
    """Pretty-print χωρίς να κρύβει παλιές ή μη έγκυρες εγγραφές."""
    try:
        return json.dumps(json.loads(payload), ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return payload or "{}"
