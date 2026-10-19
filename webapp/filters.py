"""Jinja filters & globals — μορφοποίηση κατά τα ελληνικά."""

import re
from datetime import date, datetime


def money(v) -> str:
    """1234.5 -> '1.234,50' (ελληνική μορφή)."""
    if v is None or v == "":
        return "—"
    try:
        s = f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)
    # en-US separators -> el-GR
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def hours_gr(v) -> str:
    """1136.0 -> '1.136' (ελληνικοί χιλιάδες, χωρίς δεκαδικά· 0,5 ώρες -> '0,5').

    Οι ώρες εργασίας διαβάζονται ως πλήθος, όχι ως ποσό: τα δεκαδικά μπαίνουν
    μόνο όταν υπάρχουν πραγματικά (μισάωρα)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v or "—")
    s = f"{f:,.0f}" if abs(f - round(f)) < 0.005 else f"{f:,.1f}"
    # en-US separators -> el-GR (ίδιο τέχνασμα με το `money`)
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def date_gr(d) -> str:
    """date -> '14/07/2026'."""
    if not isinstance(d, date):
        return str(d or "—")
    return d.strftime("%d/%m/%Y")


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def val_gr(v):
    """ΟΠΟΙΑΔΗΠΟΤΕ τιμή -> ελληνική εμφάνιση ημερομηνιών, **αναδρομικά**.

    Για ωμά δεδομένα που εμφανίζονται αυτούσια (payload του Event log): οι
    `date`/`datetime` και τα ISO strings (ακόμη και ως ΚΛΕΙΔΙΑ dict, π.χ. το
    ημερήσιο πρόγραμμα) γράφονται `ΗΗ/ΜΜ/ΕΕΕΕ`· ό,τι άλλο μένει ως έχει."""
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y %H:%M:%S")
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, str):
        return date_gr(date.fromisoformat(v)) if _ISO_DATE.match(v) else v
    if isinstance(v, dict):
        return {val_gr(k): val_gr(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [val_gr(x) for x in v]
    return v


def yes_no(v) -> str:
    return "Ναι" if v else "Όχι"


def service_gr(v) -> str:
    """Δεκαδικά έτη προϋπηρεσίας -> 'X έτη Y μήνες' (π.χ. 2.9167 -> '2 έτη
    11 μήνες'). Οι μήνες στρογγυλοποιούνται· 12 μεταφέρονται σε έτος."""
    try:
        years_f = float(v or 0)
    except (TypeError, ValueError):
        return str(v or "—")
    years = int(years_f)
    months = round((years_f - years) * 12)
    if months >= 12:
        years += 1
        months = 0
    y = f"{years} έτ{'ος' if years == 1 else 'η'}"
    if months:
        return f"{y} {months} μήν{'ας' if months == 1 else 'ες'}"
    return y


def day_segments(s) -> list:
    """Ωράριο ημέρας -> τμήματα για εμφάνιση (σπαστό ωράριο / τηλεεργασία).

    Μη έγκυρη μορφή εγείρει ``ValueError``."""
    from misevent.rules import constraints

    return constraints.parse_day_schedule(s)


def register(app):
    app.jinja_env.filters["money"] = money
    app.jinja_env.filters["hours_gr"] = hours_gr
    app.jinja_env.filters["date_gr"] = date_gr
    app.jinja_env.filters["val_gr"] = val_gr
    app.jinja_env.filters["yes_no"] = yes_no
    app.jinja_env.filters["day_segments"] = day_segments
    app.jinja_env.filters["service_gr"] = service_gr
