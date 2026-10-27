"""
Payroll Engine — καθαρές συναρτήσεις υπολογισμού.

Απλοποιήσεις (σημειωμένες για μελλοντική επέκταση):
- ΦΜΥ: αναγωγή σε ετήσιο, εφαρμογή κλίμακας & μείωσης τέκνων, διαίρεση με
  τον ίδιο συντελεστή (η κλασική μέθοδος παρακράτησης — `periodic_fmy`).
  Ολόκληρος μισθός (μηνιαία μισθοδοσία, **Δώρο Χριστουγέννων**) → **x14**·
  αμοιβή ~μισού μισθού (**Δώρο Πάσχα**, **Επίδομα Αδείας**) → **x28**.
  Δεν υπολογίζεται ειδική εισφορά αλληλεγγύης (καταργημένη) ούτε ειδικές
  κατηγορίες.
- Ασθένεια: το τριήμερο εργοδότη και η αφαίρεση του επιδόματος ΕΦΚΑ
  εφαρμόζονται ως ΠΕΡΙΚΟΠΗ μικτών (`sick_deduction`). Η ταξινόμηση των ημερών
  (τριήμερο / πληρωμένες / πάνω από το όριο) γίνεται στα projections
  (`EmploymentState.sick_day_map`) — εδώ έρχονται έτοιμα νούμερα.
- Δώρα: αναλογία με βάση ημερολογιακές ημέρες απασχόλησης στην περίοδο
  αναφοράς, με προσαύξηση 0,04166.
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta

from misevent.rules.calendar_gr import holy_wednesday
from misevent.rules.config import (
    ANNUAL_SALARIES,
    GIFT_LEAVE_COEFFICIENT,
    efka_rates_for,
    labor_subsidy_for,
    minimum_wage_for,
    tax_scale_for,
)


def r2(x: float) -> float:
    return round(x + 1e-9, 2)


# ------------------------------------------------------------------ ΦΜΥ
def annual_income_tax(
    annual_taxable: float,
    children: int,
    on: date,
    birth_date: date | None = None,
) -> float:
    """Ετήσιος φόρος: η ΛΟΓΙΚΗ ζει στο ΚΑΘΕΣΤΩΣ της κλίμακας που ισχύει την
    `on` (config.TAX_MODELS — π.χ. το καθεστώς 2026 έχει συντελεστές ανά
    τέκνα/ηλικία). `birth_date`: για καθεστώτα με παράμετρο ηλικίας —
    ``None`` χρησιμοποιεί τον συντελεστή ενηλίκου."""
    scale = tax_scale_for(on)
    return scale.annual_tax(annual_taxable, children, birth_date, on)


def periodic_fmy(
    taxable: float,
    children: int,
    on: date,
    birth_date: date | None = None,
    periods: float = ANNUAL_SALARIES,
) -> float:
    """Παρακράτηση ΦΜΥ με **αναγωγή σε ετήσιο εισόδημα**: το φορολογητέο
    πολλαπλασιάζεται επί `periods`, εφαρμόζεται η κλίμακα, και ο ετήσιος
    φόρος διαιρείται πάλι διά `periods`.

    `periods` = πόσες φορές «χωράει» η αμοιβή σε ένα έτος:
    - **14** (`ANNUAL_SALARIES`) για ολόκληρο μισθό — μηνιαία μισθοδοσία,
      Δώρο Χριστουγέννων.
    - **28** για αμοιβή περίπου ΜΙΣΟΥ μισθού — Δώρο Πάσχα, Επίδομα Αδείας."""
    annual = taxable * periods
    return r2(annual_income_tax(annual, children, on, birth_date) / periods)


def monthly_fmy(
    monthly_taxable: float,
    children: int,
    on: date,
    birth_date: date | None = None,
) -> float:
    return periodic_fmy(monthly_taxable, children, on, birth_date, ANNUAL_SALARIES)


# --------------------------------------------------------------- Μηνιαία
def monthly_insurance_days(weekly_hours: float, month_days: int | None = None) -> int:
    """Ημέρες ασφάλισης (ΕΦΚΑ) ΠΛΗΡΟΥΣ μήνα για ΜΙΣΘΩΤΟ.

    Πλήρης απασχόληση (40 ώρες/εβδομάδα): **25 ημέρες — παραδοχή**, όχι
    μέτρηση εργασίμων.
    Μερική απασχόληση: οι ημέρες **πραγματικής απασχόλησης** του μήνα κατά
    το πρόγραμμα (`month_days` — πενθήμερο 20–23, οι αργίες μετρούν), με
    ανώτατο τις 25 (εξαήμερο). ΠΗΓΗ: Εγκ. e-ΕΦΚΑ 34/2018 §2(1) — π.χ. 4ωρο
    πενθήμερο → 21-22-23, εξάωρο ΤΡΙΗΜΕΡΟ → 12-13 (πραγματικές ημέρες).
    ⚠️ ΚΑΜΙΑ «+1 ημέρα/εβδομάδα» για 3+ ημέρες: ισχύει ΜΟΝΟ κάτω από την
    1η ειδική κλάση (§2(3), ν.1892/90). Προϋπόθεση: ημερήσια αποδοχή ≥
    τεκμαρτό 1ης ασφ. κλάσης (κάτω από αυτό ο κανόνας είναι άλλος — δεν
    υποστηρίζεται, βλ. ARCH_REF §7).

    `month_days=None`: ΜΟΝΟ για παλιά εγκεκριμένα φύλλα χωρίς
    `month_planned` — ο παλιός τύπος ⌈ώρες/6,66⌉ x 4,33, ώστε να μένουν
    συνεπή με τις ημέρες ΕΦΚΑ που εγκρίθηκαν τότε."""
    if not weekly_hours or weekly_hours >= 40:
        return 25
    if not month_days:
        return min(round(math.ceil(weekly_hours * 6 / 40) * 4.33), 25)
    return min(month_days, 25)


@dataclass(frozen=True)
class PayrollResult:
    gross: float
    efka_employee: float
    efka_employer: float
    taxable: float
    fmy: float
    net: float
    # Ανάλυση των μικτών: βάση (πριν περικοπές/προσαυξήσεις), προσαυξήσεις
    # ωρών, περικοπή ασθενείας — ό,τι χρειάζεται η απόδειξη αποδοχών.
    base_gross: float = 0.0
    premiums: float = 0.0
    sick_deduction: float = 0.0
    # Επιδότηση εργατικής εισφοράς (ΑΠΔ κωδ. 102): ΔΕΝ παρακρατήθηκε — το
    # `efka_employee` είναι ήδη ΜΕΙΩΜΕΝΟ κατά αυτήν (= ό,τι κρατήθηκε).
    subsidy_employee: float = 0.0
    # Κωδ. ΑΠΔ της επιδότησης εργαζομένου: 102 ή 103 (μητέρα) — ΔΕΝ
    # σωρεύονται, ισχύει η μεγαλύτερη (`_employee_subsidy`).
    subsidy_code: str = ""
    # Επιδότηση εργοδοτικής εισφοράς νέων ≤25 (ΑΠΔ κωδ. 107): το
    # `efka_employer` είναι ήδη ΜΕΙΩΜΕΝΟ κατά αυτήν (= ό,τι καταβάλλεται).
    subsidy_employer: float = 0.0
    # Προσαυξήσεις ΧΩΡΙΣ εισφορές (Εγκ. e-ΕΦΚΑ 21/2025): μέσα στα μικτά και
    # στο φορολογητέο, ΕΚΤΟΣ της βάσης εισφορών και της ΑΠΔ.
    premium_exempt: float = 0.0


def labor_subsidy_full(
    monthly_gross: float, floor: float, weekly_hours: float, on: date,
) -> float:
    """Επιδότηση εργατικής εισφοράς μισθωτού (αρ. 14 ν. 2837/2000) για
    ΠΛΗΡΗ μήνα — ΑΣΤΡΟΓΓΥΛΗ, την αναλογίζει ο καλών.

    Δικαίωμα: πλήρης απασχόληση και μισθός ΑΚΡΙΒΩΣ ίσος με το κατώτατο όριο
    (`floor` = κατώτατος + τριετίες/γάμος — ούτε λεπτό πάνω ή κάτω).
    Ποσό: ποσοστό × ΣΚΕΤΟΣ κατώτατος, χωρίς καμία προσαύξηση.
    Αναλογία: μισθοδοσία → ημέρες εργασίας ÷25 (`calc_monthly`)· Δώρα/
    Επίδομα → το κλάσμα μισθού της βάσης τους, ΧΩΡΙΣ τον συντελεστή 0,04166
    (`_gift_result`). Κατάργηση ή έλλειψη κανόνα = καμία επιδότηση."""
    if weekly_hours < 40 or not monthly_gross or r2(monthly_gross) != r2(floor):
        return 0.0
    try:
        pct = labor_subsidy_for(on).pct
    except ValueError:
        return 0.0
    return pct * minimum_wage_for(on).monthly


def _employee_subsidy(
    labor: float, base: float, mother_pct: float, full_emp: float,
) -> tuple[float, str]:
    """Επιδότηση εργαζομένου και κωδ. ΑΠΔ: 102 (κατώτατος, σταθερό ποσό) ή
    103 (μητέρα, ποσοστό επί `base`). Αφορούν και οι δύο τη σύνταξη
    εργαζομένου, άρα ΔΕΝ σωρεύονται — ισχύει η μεγαλύτερη."""
    labor, mother = r2(labor), r2(base * mother_pct)
    amount, code = (mother, "103") if mother > labor else (labor, "102")
    amount = min(amount, full_emp)
    return amount, code if amount else ""


def sick_deduction(
    daily_rate: float, half_days: int, efka_benefit: float = 0.0
) -> float:
    """Περικοπή μικτών λόγω άδειας ασθενείας.

    Το **τριήμερο** του εργοδότη πληρώνεται ΜΙΣΟ ημερομίσθιο, άρα κόβεται το
    μισό ανά ημέρα. Οι ημέρες που πληρώνονται κανονικά κόβονται κατά το
    **επίδομα που κατέβαλε ο ΕΦΚΑ** στον εργαζόμενο (δεν το πληρώνει δεύτερη
    φορά ο εργοδότης).

    Οι ημέρες πάνω από το ετήσιο όριο ΔΕΝ περνούν από εδώ: είναι άνευ αποδοχών
    και αφαιρούνται ως `unpaid_days`, ώστε να κόψουν και ημέρες ασφάλισης."""
    return r2(0.5 * daily_rate * half_days + efka_benefit)


def calc_monthly(
    monthly_gross: float,
    children: int,
    period_end: date,
    unpaid_days: int = 0,
    sick: float = 0.0,
    premiums: float = 0.0,
    rates=None,
    base_days: int = 25,
    birth_date: date | None = None,
    subsidy: float = 0.0,
    exempt: float = 0.0,
    mother_pct: float = 0.0,
    youth_pct: float = 0.0,
) -> PayrollResult:
    """Μηνιαίος υπολογισμός. Βάση `base_days` ημερομισθίων/μήνα για περικοπές
    (25 για πλήρη απασχόληση, οι ημέρες προγράμματος του μήνα για μερική —
    `monthly_insurance_days` — ώστε ο μισθός να αντιστοιχεί σε ΠΛΗΡΕΙΣ
    ημέρες ΕΦΚΑ).

    Μικτά = βάση (μισθός − άνευ αποδοχών) − ασθένεια + προσαυξήσεις ωρών.
    `rates`: συντελεστές κρατήσεων της απασχόλησης (από τα ΚΠΚ των καλύψεών
    της — services.contribution_rates)· None = καθολικοί συντελεστές."""
    daily = monthly_gross / base_days
    base = max(0.0, monthly_gross - unpaid_days * daily)
    gross = max(0.0, base - sick) + premiums
    rates = rates or efka_rates_for(period_end)
    exempt = min(r2(exempt), r2(premiums))
    insurable = min(max(gross - exempt, 0.0), rates.monthly_cap)
    full_emp = r2(insurable * rates.employee)
    subsidy, code = _employee_subsidy(subsidy, insurable, mother_pct, full_emp)
    efka_emp = r2(full_emp - subsidy)  # ό,τι ΠΑΡΑΚΡΑΤΕΙΤΑΙ
    full_er = r2(insurable * rates.employer)
    sub_er = min(r2(insurable * youth_pct), full_er)
    efka_er = r2(full_er - sub_er)  # ό,τι ΚΑΤΑΒΑΛΛΕΙ ο εργοδότης
    taxable = r2(gross - efka_emp)
    fmy = monthly_fmy(taxable, children, period_end, birth_date)
    return PayrollResult(
        r2(gross),
        efka_emp,
        efka_er,
        taxable,
        fmy,
        r2(taxable - fmy),
        base_gross=r2(base),
        premiums=r2(premiums),
        sick_deduction=r2(sick),
        subsidy_employee=subsidy,
        subsidy_code=code,
        subsidy_employer=sub_er,
        premium_exempt=exempt,
    )


def daily_day_pay(daily_wage: float, weekly_hours: float, days_per_week: int) -> float:
    """ΑΜΟΙΒΗ ΗΜΕΡΑΣ ημερομισθίου = ωρομίσθιο × ώρες ημέρας.

    Το συμβατικό ημερομίσθιο είναι του ΕΞΑΗΜΕΡΟΥ (6 × 6:40 = 40ω), άρα
    ωρομίσθιο = ημερομίσθιο × 6/40 (`work_hours.hourly_rate`) και ώρες ημέρας
    = συμβατικές εβδομαδιαίες / ημέρες προγράμματος. Έτσι: εξαήμερο ×1,
    πενθήμερο 8ωρο ×1,2 (6/5), 4ωρο ×0,6, 3 οκτάωρα/εβδ. ×1,2."""
    hours = (weekly_hours or 40.0) / (days_per_week or 5)
    return round(daily_wage * 6.0 / 40.0 * hours, 4)


# Ανώτατες ημέρες ασφάλισης ημερομισθίου/μήνα: 31 ημέρες − 4 Κυριακές.
DAILY_MAX_INSURANCE_DAYS = 27


def daily_insurance_days(
    worked: set, year: int, month: int, weekly_hours: float, days_per_week: int
) -> int:
    """Ημέρες ασφάλισης ΗΜΕΡΟΜΙΣΘΙΟΥ του μήνα.

    Πλήρης απασχόληση σε ΠΕΝΘΗΜΕΡΟ: οι ημέρες εργασίας **+1 ανά εβδομάδα**
    στην οποία δούλεψε ≥3 ημέρες — πλήρης εβδομάδα = 6 (5 + 1 προσαύξηση).
    Πηγή: Κανονισμός Ασφάλισης ΙΚΑ άρθρο 18 παρ. 2, Εγκ. ΙΚΑ 44/1975 και
    114/1981. Άρα ο μήνας δίνει 24–27 (Φεβ. 20+4 = 24, 5 Σάββατα → 27).
    Εξαήμερο ή μερική: οι πραγματικές ημέρες (Εγκ. e-ΕΦΚΑ 34/2018 §2(1)).

    `worked`: οι ΗΜΕΡΟΜΗΝΙΕΣ εργασίας — του μήνα, ΚΑΙ όσες της εβδομάδας του
    1ου Σαββάτου πέφτουν στον προηγούμενο μήνα (κρίνουν το «≥3»). Η εβδομάδα
    (Δευ–Παρ) ανήκει στον μήνα του Σαββάτου της.
    ⚠️ ΔΕΝ οδηγούν την αμοιβή: αυτή είναι αμοιβή ημέρας × ημέρες ΕΡΓΑΣΙΑΣ
    (`daily_day_pay`) — αλλιώς η προσαύξηση θα μετρούσε δύο φορές."""
    days = sum(1 for d in worked if (d.year, d.month) == (year, month))
    if (weekly_hours and weekly_hours < 40) or (days_per_week or 5) >= 6:
        return days
    first = date(year, month, 1)
    sat = first + timedelta((5 - first.weekday()) % 7)
    while sat.month == month:
        if sum(1 for k in range(1, 6) if sat - timedelta(k) in worked) >= 3:
            days += 1
        sat += timedelta(7)
    return min(days, DAILY_MAX_INSURANCE_DAYS)


def daily_gift_ratio(weekly_hours: float) -> float:
    """Δώρα/Επίδομα αδείας ημερομισθίου μερικής: ΑΝΑΛΟΓΙΑ ΑΠΟΔΟΧΩΝ προς την
    πλήρη απασχόληση (40ω) — τα 15/25/13 ημερομίσθια × αυτόν τον λόγο."""
    return min(weekly_hours or 40.0, 40.0) / 40.0


def calc_monthly_daily(
    daily_wage: float,
    days_worked: int,
    children: int,
    period_end: date,
    sick: float = 0.0,
    premiums: float = 0.0,
    rates=None,
    birth_date: date | None = None,
    exempt: float = 0.0,
) -> PayrollResult:
    """Μηνιαίος υπολογισμός ημερομισθίου: μικτά = αμοιβή ημέρας × ημέρες
    − ασθένεια + προσαυξήσεις ωρών. ⚠️ Το `daily_wage` εδώ είναι η ΑΜΟΙΒΗ
    ΗΜΕΡΑΣ (`daily_day_pay`), όχι το συμβατικό ημερομίσθιο.
    `rates`/`exempt`: βλ. calc_monthly."""
    base = daily_wage * days_worked
    gross = r2(max(0.0, base - sick) + premiums)
    rates = rates or efka_rates_for(period_end)
    exempt = min(r2(exempt), r2(premiums))
    insurable = min(max(gross - exempt, 0.0), rates.monthly_cap)
    efka_emp = r2(insurable * rates.employee)
    efka_er = r2(insurable * rates.employer)
    taxable = r2(gross - efka_emp)
    fmy = monthly_fmy(taxable, children, period_end, birth_date)
    return PayrollResult(
        gross,
        efka_emp,
        efka_er,
        taxable,
        fmy,
        r2(taxable - fmy),
        base_gross=r2(base),
        premiums=r2(premiums),
        sick_deduction=r2(sick),
        premium_exempt=exempt,
    )


# ------------------------------------------------------------------ Δώρα
# Αναγωγή σε ετήσιο για την παρακράτηση ΦΜΥ αμοιβής ~ΜΙΣΟΥ μισθού
# (Δώρο Πάσχα, Επίδομα Αδείας): 2 x 14 μισθοί.
HALF_SALARY_PERIODS = 2 * ANNUAL_SALARIES

# ---------------------------------------------- Δώρο Πάσχα: ημερομηνίες
# Το Δώρο Πάσχα ΚΑΤΑΒΑΛΛΕΤΑΙ τη **Μεγάλη Τετάρτη** και υπολογίζεται με βάση
# τον μισθό που ΙΣΧΥΕΙ **15 ημέρες ΠΡΙΝ** την καταβολή — όχι τον μισθό της
# στιγμής του υπολογισμού. Οι δύο ημερομηνίες είναι ΔΙΑΚΡΙΤΕΣ και δεν πρέπει
# να μπερδεύονται:
#   `easter_gift_payment_date`  → καταβολή (συντελεστές ΕΦΚΑ/ΦΜΥ της ημέρας)
#   `easter_gift_wage_date`     → ποιος ΜΙΣΘΟΣ μπαίνει στη βάση
EASTER_GIFT_WAGE_LEAD_DAYS = 15


def easter_gift_payment_date(year: int) -> date:
    """Ημέρα καταβολής του Δώρου Πάσχα: Μεγάλη Τετάρτη."""
    return holy_wednesday(year)


def easter_gift_wage_date(year: int) -> date:
    """Ημερομηνία αναφοράς του ΜΙΣΘΟΥ για το Δώρο Πάσχα: 15 ημέρες πριν
    την καταβολή (Μ. Τετάρτη − 15)."""
    return holy_wednesday(year) - timedelta(days=EASTER_GIFT_WAGE_LEAD_DAYS)


def gift_payment_date(gift_type: str, year: int) -> date:
    """Η ημέρα καταβολής του Δώρου — ΜΙΑ πηγή αλήθειας για συντελεστές/ΦΜΥ."""
    return (easter_gift_payment_date(year) if gift_type == "easter"
            else date(year, 12, 21))


def gift_wage_date(gift_type: str, year: int) -> date:
    """Η ημερομηνία του ΜΙΣΘΟΥ πάνω στον οποίο υπολογίζεται το Δώρο."""
    return (easter_gift_wage_date(year) if gift_type == "easter"
            else gift_payment_date(gift_type, year))


def _employment_overlap_days(
    hire: date, term: date | None, start: date, end: date
) -> int:
    a = max(hire, start)
    b = min(term or end, end)
    return max(0, (b - a).days + 1)


def calc_christmas_gift(
    monthly_gross: float, children: int, year: int, hire: date,
    term: date | None = None, rates=None, birth_date: date | None = None,
    subsidy_full: float = 0.0,
    mother_pct: float = 0.0, youth_pct: float = 0.0,
) -> PayrollResult:
    """Δώρο Χριστουγέννων: 1 μισθός για απασχόληση 1/5–31/12,
    αλλιώς 2/25 μισθού ανά 19 ημερολογιακές ημέρες. Προσαύξηση 0,04166."""
    start, end = date(year, 5, 1), date(year, 12, 31)
    days = _employment_overlap_days(hire, term, start, end)
    if days >= (end - start).days + 1:
        base = monthly_gross
    else:
        base = (days / 19.0) * (2.0 / 25.0) * monthly_gross
        base = min(base, monthly_gross)
    return _gift_result(base, children, date(year, 12, 21), rates=rates,
                        birth_date=birth_date,
                        subsidy=_gift_subsidy(subsidy_full, base, monthly_gross),
                        mother_pct=mother_pct, youth_pct=youth_pct)


def calc_easter_gift(
    monthly_gross: float, children: int, year: int, hire: date,
    term: date | None = None, rates=None, birth_date: date | None = None,
    subsidy_full: float = 0.0,
    mother_pct: float = 0.0, youth_pct: float = 0.0,
) -> PayrollResult:
    """Δώρο Πάσχα: μισός μισθός για απασχόληση 1/1–30/4,
    αλλιώς 1/15 του μισού μισθού ανά 8 ημερολογιακές ημέρες.

    ⚠️ Ο `monthly_gross` πρέπει να είναι ο μισθός που ΙΣΧΥΕ την
    `easter_gift_wage_date(year)` (15 ημέρες πριν τη Μ. Τετάρτη)."""
    start, end = date(year, 1, 1), date(year, 4, 30)
    days = _employment_overlap_days(hire, term, start, end)
    half = monthly_gross / 2.0
    if days >= (end - start).days + 1:
        base = half
    else:
        base = (days / 8.0) * (1.0 / 15.0) * half
        base = min(base, half)
    return _gift_result(base, children, easter_gift_payment_date(year), rates=rates,
                        birth_date=birth_date,
                        fmy_periods=HALF_SALARY_PERIODS,
                        subsidy=_gift_subsidy(subsidy_full, base, monthly_gross),
                        mother_pct=mother_pct, youth_pct=youth_pct)


def _allowance_months(year: int, hire: date, term: date | None) -> int:
    """Μήνες υπηρεσίας στο έτος για το Επίδομα Αδείας — pro-rata από τον μήνα
    πρόσληψης (αν η πρόσληψη έγινε ΜΕΣΑ στο έτος) **έως τον Δεκέμβριο**, ή
    έως τον μήνα αποχώρησης όταν αυτή είναι γνωστή.

    ⚠️ ΟΧΙ μέχρι τη λήψη της άδειας. Ο μήνας της πρώτης άδειας ορίζει ΜΟΝΟ
    την περίοδο (μήνα) της υποχρέωσης — όχι το ποσό. Μετρώντας μέχρι εκεί,
    όποιος έπαιρνε άδεια πριν συμπληρώσει ~7 μήνες έπαιρνε λιγότερο από τον
    μισό μισθό και ΔΕΝ υπήρχε καμία διαδρομή να λάβει το υπόλοιπο: το
    επίδομα είναι ένα και μόνο ανά (εργαζόμενο, έτος)."""
    start = hire.month if hire.year == year else 1
    end = term.month if (term is not None and term.year == year) else 12
    return max(0, end - start + 1)


def is_third_service_year(year: int, hire: date) -> bool:
    """True αν το `year` είναι το 3ο ημερολογιακό έτος υπηρεσίας ή μετά
    (έτος πρόσληψης = 1ο). Από το 3ο έτος ο εργαζόμενος δικαιούται ολόκληρη
    την κανονική άδεια / το Επίδομα Αδείας, χωρίς αναλογικό επιμερισμό."""
    return (year - hire.year) >= 2


def calc_leave_allowance(
    monthly_gross: float, children: int, year: int, hire: date,
    term: date | None = None, rates=None, birth_date: date | None = None,
    full_year: bool = False, subsidy_full: float = 0.0,
    mother_pct: float = 0.0, youth_pct: float = 0.0,
) -> PayrollResult:
    """Επίδομα Αδείας: αποδοχές αδείας έως μισό μισθό.
    1ο/2ο έτος: 2 ημερομίσθια ανά μήνα υπηρεσίας στο έτος — έως Δεκέμβριο ή
    έως την αποχώρηση (`_allowance_months`), cap μισός μισθός.
    3ο έτος+ (`full_year`): ολόκληρο το επίδομα (μισός μισθός), χωρίς
    αναλογικό επιμερισμό."""
    months = 12 if full_year else _allowance_months(year, hire, term)
    base = min(months * 2 * (monthly_gross / 25.0), monthly_gross / 2.0)
    return _gift_result(base, children, date(year, 7, 31), leave_coeff=False,
                        rates=rates, birth_date=birth_date,
                        fmy_periods=HALF_SALARY_PERIODS,
                        subsidy=_gift_subsidy(subsidy_full, base, monthly_gross),
                        mother_pct=mother_pct, youth_pct=youth_pct)


def calc_christmas_gift_daily(
    daily_wage: float, children: int, year: int, hire: date,
    term: date | None = None, rates=None, birth_date: date | None = None,
) -> PayrollResult:
    """Δώρο Χριστουγέννων ημερομισθίων: 25 ημερομίσθια για πλήρη περίοδο
    1/5-31/12, αλλιώς 1 ημερομίσθιο ανά 8 ημερολογιακές ημέρες."""
    start, end = date(year, 5, 1), date(year, 12, 31)
    days = _employment_overlap_days(hire, term, start, end)
    full = (end - start).days + 1
    base = 25 * daily_wage if days >= full else min(days / 8.0, 25) * daily_wage
    return _gift_result(base, children, date(year, 12, 21), rates=rates,
                        birth_date=birth_date)


def calc_easter_gift_daily(
    daily_wage: float, children: int, year: int, hire: date,
    term: date | None = None, rates=None, birth_date: date | None = None,
) -> PayrollResult:
    """Δώρο Πάσχα ημερομισθίων: 15 ημερομίσθια για πλήρη περίοδο 1/1-30/4,
    αλλιώς 1 ημερομίσθιο ανά 8 ημερολογιακές ημέρες.

    ⚠️ Το `daily_wage` πρέπει να είναι το ημερομίσθιο που ΙΣΧΥΕ την
    `easter_gift_wage_date(year)` (15 ημέρες πριν τη Μ. Τετάρτη)."""
    start, end = date(year, 1, 1), date(year, 4, 30)
    days = _employment_overlap_days(hire, term, start, end)
    full = (end - start).days + 1
    base = 15 * daily_wage if days >= full else min(days / 8.0, 15) * daily_wage
    return _gift_result(base, children, easter_gift_payment_date(year), rates=rates,
                        birth_date=birth_date,
                        fmy_periods=HALF_SALARY_PERIODS)


def calc_leave_allowance_daily(
    daily_wage: float, children: int, year: int, hire: date,
    term: date | None = None, rates=None, birth_date: date | None = None,
    full_year: bool = False,
) -> PayrollResult:
    """Επίδομα Αδείας ημερομισθίων: 1ο/2ο έτος 2 ημερομίσθια/μήνα υπηρεσίας
    στο έτος — έως Δεκέμβριο ή έως την αποχώρηση, με ανώτατο τα 13
    ημερομίσθια· 3ο έτος+ (`full_year`) ολόκληρα τα 13, χωρίς επιμερισμό."""
    months = 12 if full_year else _allowance_months(year, hire, term)
    base = min(months * 2, 13) * daily_wage
    return _gift_result(base, children, date(year, 7, 31), leave_coeff=False,
                        rates=rates, birth_date=birth_date,
                        fmy_periods=HALF_SALARY_PERIODS)


def _gift_subsidy(subsidy_full: float, base: float, monthly_gross: float) -> float:
    """Επιδότηση Δώρου/Επιδόματος: η πλήρης μηνιαία × το κλάσμα μισθού της
    ΒΑΣΗΣ (πριν τον συντελεστή 0,04166 — καμία προσαύξηση)."""
    return r2(subsidy_full * base / monthly_gross) if subsidy_full else 0.0


def _gift_result(
    base: float, children: int, on: date, leave_coeff: bool = True, rates=None,
    birth_date: date | None = None, fmy_periods: float = ANNUAL_SALARIES,
    subsidy: float = 0.0, mother_pct: float = 0.0, youth_pct: float = 0.0,
) -> PayrollResult:
    gross = base * (1 + GIFT_LEAVE_COEFFICIENT) if leave_coeff else base
    rates = rates or efka_rates_for(on)
    full_emp = r2(gross * rates.employee)
    subsidy, code = _employee_subsidy(subsidy, gross, mother_pct, full_emp)
    efka_emp = r2(full_emp - subsidy)  # ό,τι ΠΑΡΑΚΡΑΤΕΙΤΑΙ
    full_er = r2(gross * rates.employer)
    sub_er = min(r2(gross * youth_pct), full_er)
    efka_er = r2(full_er - sub_er)  # ό,τι ΚΑΤΑΒΑΛΛΕΙ ο εργοδότης
    taxable = r2(gross - efka_emp)
    # Παρακράτηση με αναγωγή σε ετήσιο εισόδημα: ΟΛΟΚΛΗΡΟΣ μισθός (Δώρο
    # Χριστουγέννων) → x14 όπως η μισθοδοσία· ΜΙΣΟΣ μισθός (Δώρο Πάσχα,
    # Επίδομα Αδείας) → x28, και ο ετήσιος φόρος διαιρείται πάλι διά 28.
    fmy = periodic_fmy(taxable, children, on, birth_date, fmy_periods)
    return PayrollResult(r2(gross), efka_emp, efka_er, taxable, fmy, r2(taxable - fmy),
                         subsidy_employee=subsidy, subsidy_code=code,
                         subsidy_employer=sub_er)
