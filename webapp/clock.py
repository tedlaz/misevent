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

from flask import g, has_app_context, request

from .tenancy import saas_mode

COOKIE = "ref_date"


def enabled() -> bool:
    """Επιτρέπεται το ταξίδι στον χρόνο; Desktop: πάντα. Online: μόνο στην
    ΕΝΕΡΓΗ εταιρεία με τη ρύθμιση «Δοκιμές → Αλλαγή ημερομηνίας»
    (`time_travel`, §6β) — αλλιώς ένας πελάτης θα έβλεπε προθεσμίες/περιόδους
    άλλης ημερομηνίας. Σελίδες χωρίς εταιρεία: πραγματική ημερομηνία."""
    if not saas_mode():
        return True
    tenant = g.get("tenant") if has_app_context() else None
    return bool(tenant and tenant.proj.misc_settings.get("time_travel"))


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
    """Το «σήμερα» του UI: ?ref= (per-request) > cookie > πραγματικό σήμερα.
    Χωρίς `enabled()` ΠΑΝΤΑ το πραγματικό."""
    if not enabled():
        return real_today()
    return (
        parse_date(request.values.get("ref"))
        or parse_date(request.cookies.get(COOKIE))
        or real_today()
    )


def is_simulated() -> bool:
    return today() != real_today()
