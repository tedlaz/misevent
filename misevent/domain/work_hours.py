"""
Κατάταξη ωρών εργασίας & προσαυξήσεις (βλ. parousies.md) — καθαρές συναρτήσεις.

Κάθε ώρα (λεπτό) εργασίας κατατάσσεται σε ΤΡΕΙΣ ανεξάρτητους άξονες:

  1. Ημέρα ή Νύχτα (22:00–06:00, προσαύξηση 25%)
  2. Εργάσιμη ή Αργία (Κυριακές & επίσημες αργίες, προσαύξηση 75%)
  3. Κλάση ωρομισθίου, χρονολογικά μέσα στην εβδομάδα (Δευ–Κυρ):
       normal    εντός συμβατικού ωραρίου — καμία προσαύξηση
       extra     πρόσθετη εργασία (μερική απασχόληση, ώρες > συμβατικών
                 έως 40/εβδ.) — 12%
       overwork  υπερεργασία — 20%. Δύο πηγές: (α) εβδομαδιαία 40 → 45
                 πενθήμερο / 48 εξαήμερο, (β) ΗΜΕΡΗΣΙΑ, πάνω από 8ω/ημέρα
                 και έως το ημερήσιο νόμιμο (πενθήμερο: η 9η ώρα· εξαήμερο:
                 κενή ζώνη). Πάνω από 8ω/ημέρα κυριαρχεί ο ημερήσιος κανόνας.
       overtime  νόμιμη υπερωρία (πέραν του νόμιμου εβδομαδιαίου Ή πέραν
                 του νόμιμου ημερήσιου 9/8 ωρών) — 40%
       illegal   παράνομη υπερωρία (πάνω από 3 ώρες/ημέρα ή 150 ώρες/έτος
                 υπερωρίας) — 80%

Οι προσαυξήσεις των αξόνων ΑΘΡΟΙΖΟΝΤΑΙ: π.χ. υπερεργασία νύχτα σε αργία =
20% + 25% + 75%. Η αναφορά δίνει το ΕΠΙΠΛΕΟΝ κόστος (προσαύξηση) πάνω από
το κανονικό ωρομίσθιο, που ήδη καλύπτεται από τον μισθό.

Απλοποιήσεις (σημειωμένες για μελλοντική επέκταση):
- Ώρες την 6η ημέρα πενθημέρου (Σάββατο) ή Κυριακή ΔΕΝ μετρούν στην
  υπερεργασία (parousies.md §Υπερεργασία)· κατατάσσονται `normal` (με την
  προσαύξηση αργίας όπου ισχύει) και δεν προωθούν τον εβδομαδιαίο μετρητή.
- Η νομιμότητα υπερωρίας κρίνεται ΜΟΝΟ από τα όρια (3 ώρες/ημέρα,
  150/έτος) — δεν ελέγχεται αναγγελία Ε8 στο ΕΡΓΑΝΗ.
- Βάρδια που περνά τα μεσάνυχτα ανήκει στην εργάσιμη ημέρα που ΑΡΧΙΣΕ
  (μετρητές ημέρας/εβδομάδας), αλλά νύχτα/αργία κρίνονται από το πραγματικό
  ρολόι/ημερολόγιο κάθε λεπτού.

Χωρίς πρόσβαση σε store/projections: δέχεται dict βαρδιών, επιστρέφει buckets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from misevent.rules.calendar_gr import public_holidays
from misevent.rules.config import WorkHourRules, work_hour_rules_for

RATE_LABELS = {
    "normal": "Εντός ωραρίου",
    "extra": "Πρόσθετη εργασία",
    "overwork": "Υπερεργασία",
    "overtime": "Υπερωρία",
    "illegal": "Παράνομη υπερωρία",
}


@dataclass(frozen=True)
class HourBucket:
    """Λεπτά εργασίας μιας εργάσιμης ημέρας με κοινή κατάταξη."""

    day: date  # η εργάσιμη ημέρα (όπου ΑΡΧΙΣΕ η βάρδια)
    rate: str  # normal | extra | overwork | overtime | illegal
    night: bool
    holiday: bool
    minutes: int

    @property
    def hours(self) -> float:
        return round(self.minutes / 60.0, 2)


def _parse_shift(s: str) -> tuple[int, int]:
    """«HH:MM-HH:MM» -> (έναρξη, λήξη) σε λεπτά από τα μεσάνυχτα της
    εργάσιμης ημέρας. Λήξη <= έναρξης σημαίνει πέρασμα μεσανύχτων (+24ω)."""
    a, _, b = s.partition("-")
    h1, _, m1 = a.strip().partition(":")
    h2, _, m2 = b.strip().partition(":")
    start = int(h1) * 60 + int(m1)
    end = int(h2) * 60 + int(m2)
    if end <= start:
        end += 24 * 60
    return start, end


def _is_holiday(d: date, holidays: set[date]) -> bool:
    return d.weekday() == 6 or d in holidays


def classify_shifts(
    shifts_by_day: dict[date, list[str]],
    weekly_hours: float,
    weekly_system: int = 5,
    rules: WorkHourRules | None = None,
) -> list[HourBucket]:
    """Κατατάσσει ΟΛΕΣ τις βάρδιες σε buckets (ημέρα, κλάση, νύχτα, αργία).

    Δώσε τις βάρδιες ΟΛΟΥ του έτους ως το ζητούμενο σημείο, ώστε το ετήσιο
    όριο των 150 ωρών υπερωρίας να μετρήσει σωστά· φίλτραρε μετά ανά μήνα."""
    if not shifts_by_day:
        return []
    holidays: set[date] = set()
    for y in {d.year for d in shifts_by_day} | {d.year + 1 for d in shifts_by_day}:
        holidays |= public_holidays(y)

    contract_wk = round(weekly_hours * 60)  # συμβατικά λεπτά/εβδομάδα
    out: dict[tuple[date, str, bool, bool], int] = {}
    overtime_year = 0  # λεπτά υπερωρίας ανά έτος (για το όριο 150 ωρών)
    year_seen = None

    # Ομαδοποίηση σε εβδομάδες Δευ-Κυρ, χρονολογικά
    weeks: dict[date, list[date]] = {}
    for d in sorted(shifts_by_day):
        weeks.setdefault(d - timedelta(days=d.weekday()), []).append(d)

    for monday in sorted(weeks):
        r = rules or work_hour_rules_for(monday)
        legal_wk = (r.weekly_legal_5 if weekly_system == 5 else r.weekly_legal_6) * 60
        legal_day = (r.daily_legal_5 if weekly_system == 5 else r.daily_legal_6) * 60
        overwork_day = r.daily_overwork_start * 60  # ημερήσια ζώνη υπερεργασίας
        night_from, night_to = r.night_start * 60, r.night_end * 60
        weekly_worked = 0  # λεπτά που μετρούν στον εβδομαδιαίο μετρητή

        for d in weeks[monday]:
            if year_seen != d.year:  # νέο έτος: μηδενίζει το όριο των 150 ωρών
                year_seen, overtime_year = d.year, 0
            daily_worked = 0
            overtime_day = 0
            # Ώρες 6ης ημέρας πενθημέρου (Σάββατο) ή Κυριακής: εκτός
            # υπερεργασίας — δεν προωθούν τον εβδομαδιαίο μετρητή.
            counts_weekly = not (
                d.weekday() == 6 or (weekly_system == 5 and d.weekday() == 5)
            )
            for shift in shifts_by_day[d]:
                start, end = _parse_shift(shift)
                for m in range(start, end):
                    clock = m % (24 * 60)
                    clock_day = d + timedelta(days=m // (24 * 60))
                    night = clock >= night_from or clock < night_to
                    holiday = _is_holiday(clock_day, holidays)

                    if daily_worked >= legal_day or (
                        counts_weekly and weekly_worked >= legal_wk
                    ):
                        # Υπερωρία: νόμιμη έως 3 ώρες/ημέρα ΚΑΙ 150 ώρες/έτος
                        if (
                            overtime_day < r.overtime_day_cap * 60
                            and overtime_year < r.overtime_year_cap * 60
                        ):
                            rate = "overtime"
                        else:
                            rate = "illegal"
                        overtime_day += 1
                        overtime_year += 1
                    elif daily_worked >= overwork_day:
                        # Ημερήσια υπερεργασία: ώρες πάνω από 8/ημέρα και έως το
                        # ημερήσιο νόμιμο (πενθήμερο: η 9η ώρα· εξαήμερο: κενή
                        # ζώνη, αφού 8=8 -> η 9η ώρα πιάστηκε ήδη ως υπερωρία).
                        # Ισχύει ΣΕ ΚΑΘΕ ΠΕΡΙΠΤΩΣΗ (και σε ημέρες εκτός του
                        # εβδομαδιαίου μετρητή) και κυριαρχεί του εβδομαδιαίου.
                        rate = "overwork"
                    elif not counts_weekly:
                        rate = "normal"
                    elif weekly_worked >= 40 * 60:
                        rate = "overwork"
                    elif weekly_worked >= contract_wk:
                        rate = "extra"  # μερική απασχόληση: πρόσθετη εργασία
                    else:
                        rate = "normal"

                    daily_worked += 1
                    if counts_weekly:
                        weekly_worked += 1
                    key = (d, rate, night, holiday)
                    out[key] = out.get(key, 0) + 1

    return [
        HourBucket(day, rate, night, holiday, mins)
        for (day, rate, night, holiday), mins in sorted(out.items())
    ]


def hourly_rate(
    pay_type: str, monthly_gross: float, daily_wage: float, weekly_hours: float
) -> float:
    """Το ΚΑΝΟΝΙΚΟ ωρομίσθιο (parousies.md: μισθός / 25 x 6 / 40 για πλήρη
    απασχόληση — γενικεύεται στις συμβατικές εβδομαδιαίες ώρες)."""
    if pay_type == "daily":
        return round(daily_wage * 6.0 / 40.0, 4)
    wk = weekly_hours or 40.0
    return round(monthly_gross / 25.0 * 6.0 / wk, 4)


# ------------------------------------------------- Πεδία συγκεντρωτικού
# Κάθε ώρα ορίζεται από ΤΡΕΙΣ παραμέτρους (parousies.md):
#   1. κλάση: εντός/πρόσθετη/υπερεργασία/υπερωρία/παράνομη
#   2. ημέρα ή νύχτα
#   3. εργάσιμη ή Κυριακή/αργία
# Οι συνδυασμοί ΔΕΝ είναι αθροιστικοί μεταξύ των αξόνων: το ωρομίσθιο της
# κλάσης πολλαπλασιάζεται με (1 + νύχτα 25% + αργία 75%) — π.χ. υπερεργασία
# νύχτα = 1.20 x 1.25 = 1.50, ΟΧΙ 1 + 0.20 + 0.25. Γι' αυτό το φύλλο κρατά
# τον ΠΛΗΡΗ πίνακα 5 x 2 x 2 = 20 πεδίων, όχι σύνολα ανά άξονα.
RATE_CLASSES = ("normal", "extra", "overwork", "overtime", "illegal")

# κατάληξη πεδίου: (νύχτα;, αργία;) — "" = ημέρα/εργάσιμη
VARIANTS = {
    "": (False, False),
    "_n": (True, False),
    "_h": (False, True),
    "_nh": (True, True),
}

VARIANT_LABELS = {
    "": "ημέρα, εργάσιμη",
    "_n": "νύχτα",
    "_h": "αργία",
    "_nh": "νύχτα + αργία",
}

# π.χ. "overwork_nh" = ώρες υπερεργασίας, νύχτα, σε αργία
SHEET_HOUR_FIELDS = tuple(f"{cls}{v}" for cls in RATE_CLASSES for v in VARIANTS)


def sheet_fields_from_buckets(buckets: list[HourBucket]) -> dict:
    """Συμπτύσσει τα buckets της κατάταξης στα 20 πεδία του φύλλου (ώρες)."""
    out = dict.fromkeys(SHEET_HOUR_FIELDS, 0.0)
    for b in buckets:
        v = {
            (False, False): "",
            (True, False): "_n",
            (False, True): "_h",
            (True, True): "_nh",
        }[(b.night, b.holiday)]
        out[f"{b.rate}{v}"] = round(out[f"{b.rate}{v}"] + b.minutes / 60.0, 2)
    return out


def _class_pct(rules: WorkHourRules) -> dict:
    return {
        "normal": 0.0,
        "extra": rules.extra_premium,
        "overwork": rules.overwork_premium,
        "overtime": rules.overtime_premium,
        "illegal": rules.illegal_premium,
    }


def field_multiplier(field: str, rules: WorkHourRules) -> float:
    """Ο συντελεστής ΕΠΙΠΛΕΟΝ αμοιβής ενός πεδίου, επί το κανονικό ωρομίσθιο.

    - Ώρες ΕΝΤΟΣ ωραρίου (`normal*`): καλύπτονται ήδη από τον μισθό — extra
      είναι μόνο η προσαύξηση νύχτας/αργίας (0.25 + 0.75, αθροιστικά).
    - Ώρες ΠΕΡΑΝ του ωραρίου (πρόσθετη/υπερεργασία/υπερωρίες): ΔΕΝ τις
      καλύπτει ο μισθός — πληρώνεται ολόκληρο το ωρομίσθιο της κλάσης,
      προσαυξημένο κατά νύχτα/αργία: (1 + κλάση%) x (1 + 0.25ν + 0.75α)."""
    cls = next(c for c in RATE_CLASSES if field.startswith(c))
    night, holiday = VARIANTS[field[len(cls) :]]
    daytype = (
        1.0
        + (rules.night_premium if night else 0.0)
        + (rules.holiday_premium if holiday else 0.0)
    )
    if cls == "normal":
        return daytype - 1.0  # μόνο η προσαύξηση — η ώρα πληρώνεται ήδη
    return (1.0 + _class_pct(rules)[cls]) * daytype


def class_rate(cls: str, hourly: float, rules: WorkHourRules) -> float:
    """Το ωρομίσθιο της ΚΛΑΣΗΣ: κανονικό ωρομίσθιο x (1 + ποσοστό κλάσης),
    ΠΡΙΝ την προσαύξηση νύχτας/αργίας. π.χ. υπερωρία = ωρομίσθιο x 1.40."""
    return round(hourly * (1.0 + _class_pct(rules)[cls]), 4)


def pay_supplement(rate: float, hours: dict, rules: WorkHourRules) -> dict:
    """Γραμμές επιπλέον αμοιβών από τα 20 πεδία ωρών του φύλλου.

    Επιστρέφει {"lines": {field: {label, hours, mult, amount, premium}},
    "total", "premium"} — οι γραμμές της απόδειξης αποδοχών. Ποσό = κανονικό
    ωρομίσθιο x mult x ώρες. Το σκέτο `normal` (ημέρα, εργάσιμη) έχει mult 0
    και δεν βγάζει γραμμή.

    `premium` = το καθαρό ποσό ΠΡΟΣΑΥΞΗΣΗΣ της γραμμής (Εγκ. e-ΕΦΚΑ 21/2025):
    εντός ωραρίου ΟΛΟ το ποσό (η ώρα πληρώνεται ήδη με τον μισθό)· πέραν του
    ωραρίου ό,τι υπερβαίνει την απλή αξία της ώρας (ωρομίσθιο x ώρες)."""
    lines = {}
    for f in SHEET_HOUR_FIELDS:
        h = float(hours.get(f, 0) or 0)
        if h <= 0:
            continue
        mult = field_multiplier(f, rules)
        if mult <= 0:
            continue
        cls = next(c for c in RATE_CLASSES if f.startswith(c))
        variant = f[len(cls) :]
        night, holiday = VARIANTS[variant]
        label = RATE_LABELS[cls]
        if variant:
            label += f" ({VARIANT_LABELS[variant]})"
        amount = round(rate * mult * h, 2)
        plain = 0.0 if cls == "normal" else round(rate * h, 2)
        lines[f] = {
            "label": label,
            "cls": cls,
            "class_label": RATE_LABELS[cls],
            "night": night,
            "holiday": holiday,
            "hours": round(h, 2),
            "mult": round(mult, 4),
            "amount": amount,
            "premium": round(max(amount - plain, 0.0), 2),
        }
    return {
        "lines": lines,
        "total": round(sum(x["amount"] for x in lines.values()), 2),
        "premium": round(sum(x["premium"] for x in lines.values()), 2),
    }
