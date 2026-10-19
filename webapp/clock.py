"""
Ημερομηνία αναφοράς — το «σήμερα» του UI.

Κανονικά είναι η πραγματική σημερινή ημερομηνία. Για **δοκιμές** μπορεί να
οριστεί άλλη (ταξίδι στον χρόνο): αποθηκεύεται σε cookie και τη διαβάζουν
ΟΛΕΣ οι σελίδες (υποχρεώσεις, επισκόπηση, μισθοδοσία, υποβολές, άδειες).

ΔΕΝ αγγίζει τον πυρήνα: τα events εξακολουθούν να καταγράφονται με τις
ημερομηνίες που δίνουν οι εντολές — αλλάζει μόνο η οπτική γωνία του UI
(τι είναι εκπρόθεσμο, ποια περίοδος προεπιλέγεται).
"""

from datetime import UTC, date, datetime

from flask import request

COOKIE = "ref_date"


def real_today() -> date:
    return datetime.now(UTC).date()


def parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def today() -> date:
    """Το «σήμερα» του UI: ?ref= (per-request) > cookie > πραγματικό σήμερα."""
    return (
        parse_date(request.values.get("ref"))
        or parse_date(request.cookies.get(COOKIE))
        or real_today()
    )


def is_simulated() -> bool:
    return today() != real_today()
