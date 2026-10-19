"""
Ελληνικό εργασιακό ημερολόγιο: Ορθόδοξο Πάσχα, επίσημες αργίες,
τελευταία εργάσιμη μήνα (για προθεσμίες ΑΠΔ κ.λπ.).
"""

from datetime import date, timedelta


def orthodox_easter(year: int) -> date:
    """Κυριακή του Πάσχα (Ιουλιανός υπολογισμός + μετατροπή σε Γρηγοριανό)."""
    a = year % 4
    b = year % 7
    c = year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month = (d + e + 114) // 31
    day = ((d + e + 114) % 31) + 1
    julian = date(year, month, day)
    # Διαφορά Ιουλιανού-Γρηγοριανού για 1900-2099: 13 ημέρες
    return julian + timedelta(days=13)


def holy_wednesday(year: int) -> date:
    """Μεγάλη Τετάρτη (Πάσχα − 4 ημέρες) — η ημέρα ΚΑΤΑΒΟΛΗΣ του Δώρου
    Πάσχα, άρα και η αφετηρία της ημερομηνίας αναφοράς του μισθού του."""
    return orthodox_easter(year) - timedelta(days=4)


def public_holidays(year: int) -> set[date]:
    easter = orthodox_easter(year)
    return {
        date(year, 1, 1),  # Πρωτοχρονιά
        date(year, 1, 6),  # Θεοφάνια
        easter - timedelta(days=48),  # Καθαρά Δευτέρα
        date(year, 3, 25),  # 25η Μαρτίου
        easter - timedelta(days=2),  # Μεγάλη Παρασκευή
        easter + timedelta(days=1),  # Δευτέρα του Πάσχα
        date(year, 5, 1),  # Πρωτομαγιά (απλοποίηση: αν μετατεθεί)
        easter + timedelta(days=50),  # Αγίου Πνεύματος
        date(year, 8, 15),  # Δεκαπενταύγουστος
        date(year, 10, 28),  # 28η Οκτωβρίου
        date(year, 12, 25),  # Χριστούγεννα
        date(year, 12, 26),  # Σύναξη Θεοτόκου
    }


def is_working_day(d: date) -> bool:
    return d.weekday() < 5 and d not in public_holidays(d.year)


def last_working_day_of_month(year: int, month: int) -> date:
    if month == 12:
        d = date(year, 12, 31)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    while not is_working_day(d):
        d -= timedelta(days=1)
    return d


def working_days_in_range(start: date, end: date) -> int:
    n, d = 0, start
    while d <= end:
        if is_working_day(d):
            n += 1
        d += timedelta(days=1)
    return n
