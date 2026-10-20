"""
Obligation Scheduler & Process Manager.

Όλες οι υποχρεώσεις είναι πλέον ΕΠΙΠΕΔΟΥ ΕΤΑΙΡΕΙΑΣ (μία ανά περίοδο),
γιατί η μισθοδοσία υπολογίζεται για όλους τους εργαζόμενους μαζί και
οι υποβολές γίνονται ανά ασφαλιστικό οργανισμό για όλους μαζί:

Ανά μήνα:
  ATTENDANCE_APPROVAL  Έγκριση συγκεντρωτικού παρουσιών/απουσιών — ΠΡΟΗΓΕΙΤΑΙ
                       της μισθοδοσίας και την ξεκλειδώνει
  MONTHLY_PAYROLL      Υπολογισμός μισθοδοσίας όλων (τελ. εργάσιμη μήνα)
  SALARY_PAYMENT       Πληρωμή μισθών όλων (τελ. εργάσιμη μήνα)
  APD_SUBMISSION       Υποβολή ΑΠΔ e-ΕΦΚΑ (τελ. εργάσιμη επόμενου μήνα)
  EFKA_PAYMENT         Καταβολή εισφορών e-ΕΦΚΑ (ίδια προθεσμία)
  APD_TEKA_SUBMISSION  Υποβολή ΑΠΔ ΤΕΚΑ     ┐ ΜΟΝΟ αν στην περίοδο
  TEKA_PAYMENT         Καταβολή εισφορών ΤΕΚΑ ┘ υπάρχει ασφαλισμένος ΤΕΚΑ
  FMY_SUBMISSION       Δήλωση ΦΜΥ (τέλος 2ου μήνα μετά την παρακράτηση)
  FMY_PAYMENT          Καταβολή ΦΜΥ (ίδια προθεσμία)

Ανά έτος (μέσα στην αλυσίδα — βλ. dependencies.py):
  EASTER_GIFT     (Μ. Τετάρτη) — ξεκλειδώνει με τη ΜΙΣΘΟΔΟΣΙΑ ΜΑΡΤΙΟΥ
  CHRISTMAS_GIFT  (21/12)      — ξεκλειδώνει με τη ΜΙΣΘΟΔΟΣΙΑ ΝΟΕΜΒΡΙΟΥ

ΑΝΤΙΔΡΑΣΤΙΚΑ:
  LEAVE_ALLOWANCE — ΔΕΝ προγραμματίζεται εκ των προτέρων. Το Επίδομα Αδείας
  καταβάλλεται όταν ο εργαζόμενος πάρει την άδειά του, άρα η υποχρέωση
  γεννιέται στην ΠΕΡΙΟΔΟ (μήνα) της ΠΡΩΤΗΣ κανονικής άδειας ΚΑΘΕ εργαζόμενου.
  Άρα υπάρχουν ΠΟΛΛΑΠΛΕΣ υποχρεώσεις Επιδόματος Αδείας μέσα στο έτος (έως
  12, μία ανά μήνα)· η καθεμία είναι ενιαία (επιπέδου εταιρείας) και καλύπτει
  όσους πήραν την πρώτη τους άδεια εκείνον τον μήνα.
  ➜ ΥΠΟΧΡΕΩΤΙΚΟ ΤΟΝ ΔΕΚΕΜΒΡΙΟ: όποιος δεν πήρε άδεια μέσα στο έτος (ούτε
  αποχώρησε) έχει περίοδο τον Δεκέμβριο — η υποχρέωση γεννιέται μόλις
  κλείσει η μισθοδοσία Νοεμβρίου (`_ensure_year_end_allowance`).

Η μισθοδοσία είναι ΚΥΛΙΟΜΕΝΗ: μόλις ολοκληρωθεί η μισθοδοσία ενός μήνα,
προγραμματίζεται αυτόματα η περίοδος του επόμενου (και τα Δώρα της νέας
χρονιάς, όταν περνάμε σε επόμενο έτος).

Οι υποχρεώσεις ΤΕΚΑ δημιουργούνται ΑΝΤΙΔΡΑΣΤΙΚΑ: όταν καταγραφεί
InsuranceProfileAssigned με teka_member=True, προστίθενται (εφόσον δεν
υπάρχουν) για τις περιόδους από την πρόσληψη του ασφαλισμένου και μετά.
Αν κανένας εργαζόμενος δεν είναι ΤΕΚΑ, δεν δημιουργείται κανένα event
δήλωσης ή καταβολής ΤΕΚΑ.

Τα Δώρα/Επίδομα Αδείας ΔΕΝ έχουν δική τους υποβολή: περιλαμβάνονται
στο αρχείο ΑΠΔ της επόμενης μισθολογικής περιόδου (βλ. services).
Π.χ. Δώρο Χριστουγέννων -> αρχείο περιόδου Ιανουαρίου -> υποβολή έως
τέλος Φεβρουαρίου.

Ο scheduler κάνει bootstrap από τα projections κατά την εκκίνηση, ώστε
σε restart να μη δημιουργούνται διπλές υποχρεώσεις.
"""

import uuid
from datetime import UTC, date, datetime

import misevent.rules.config as config
import misevent.rules.obligation_types as ot
from misevent.domain.events import (
    APDSubmitted,
    AttendanceSheetApproved,
    EmploymentStarted,
    ErganiSubmissionRecorded,
    FMYSubmitted,
    GiftCalculated,
    InsuranceProfileAssigned,
    LeaveAllowanceCalculated,
    LeaveRecorded,
    LeaveType,
    MinimumWageDefined,
    MonthlyPayrollCalculated,
    ObligationFulfilled,
    ObligationScheduled,
    PaymentMade,
    WageAdjustmentApplied,
)
from misevent.rules.calendar_gr import last_working_day_of_month

COMPANY = "COMPANY"

# Παράγονται από το ΜΗΤΡΩΟ ΤΥΠΩΝ (obligation_types.py) — όχι hardcoded εδώ
PERIOD_TYPES = [t.code for t in ot.by_spawn("period")]
TEKA_TYPES = [t.code for t in ot.by_spawn("period_teka")]
ANNUAL_TYPES = [t.code for t in ot.by_spawn("annual")]

# Ποιο event κλείνει ποια υποχρέωση: PaymentMade.payment_type -> τύπος
PAYMENT_TYPES = {
    "salary": "SALARY_PAYMENT",
    "efka": "EFKA_PAYMENT",
    "teka": "TEKA_PAYMENT",
    "fmy": "FMY_PAYMENT",
    "easter_gift": "EASTER_GIFT_PAYMENT",
    "christmas_gift": "CHRISTMAS_GIFT_PAYMENT",
    "leave_allowance": "LEAVE_ALLOWANCE_PAYMENT",
}


def _oid() -> str:
    return str(uuid.uuid4())[:8]


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _next(y, m, k=1):
    for _ in range(k):
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return y, m


class ObligationScheduler:
    def __init__(self, store, projections, horizon_months: int = 12):
        self.store = store
        self.proj = projections
        self.horizon_months = horizon_months
        self._company_periods: set[tuple[int, int]] = set()
        self._teka_periods: set[tuple[int, int]] = set()
        self._gift_keys: set[tuple[str, int]] = set()
        self._allowance_periods: set[tuple[int, int]] = set()
        self._wage_adjust_periods: set[tuple[int, int]] = set()
        self._bootstrap()

    def _bootstrap(self):
        """Αρχικοποίηση από ήδη καταγεγραμμένες υποχρεώσεις (restart)."""
        for ob in self.proj.obligations.values():
            key = (ob.period_year, ob.period_month)

            if ob.obligation_type == "MONTHLY_PAYROLL":
                self._company_periods.add(key)

            elif ob.obligation_type in TEKA_TYPES:
                self._teka_periods.add(key)

            elif ob.obligation_type in ANNUAL_TYPES:
                self._gift_keys.add((ob.obligation_type, ob.period_year))

            elif ob.obligation_type == "LEAVE_ALLOWANCE":
                self._allowance_periods.add(key)

            elif ob.obligation_type == "WAGE_ADJUSTMENT":
                self._wage_adjust_periods.add(key)

    def rebuild(self):
        """Ξαναχτίζει την εσωτερική απομνημόνευση μετά από event rollback."""
        self._company_periods.clear()
        self._teka_periods.clear()
        self._gift_keys.clear()
        self._allowance_periods.clear()
        self._wage_adjust_periods.clear()
        self._bootstrap()

    # ------------------------------------------------------------------
    def handle(self, aggregate_id: str, event):
        if isinstance(event, EmploymentStarted):
            # Η UI πρόσληψη είναι draft μέχρι να ολοκληρωθεί το WebE3N.
            # ➜ Οι περίοδοι ξεκινούν από την ΑΝΑΛΗΨΗ της μισθοδοσίας, όχι από
            #   την πρόσληψη: μεταφερμένος εργαζόμενος με παλιά πρόσληψη δεν
            #   γεννά υποχρεώσεις για χρήσεις άλλου προγράμματος.
            if not event.draft:
                self._on_hired(event.payroll_from or event.hire_date)

        elif isinstance(event, ErganiSubmissionRecorded):
            if event.doc_type in ("WebE3N", "E3") and event.status != "failed":
                emp = self.proj.employments.get(event.employee_id)
                if emp and emp.payroll_start:
                    start = emp.payroll_start
                    self._on_hired(start)
                    if emp.insurance_profile and emp.insurance_profile.teka_member:
                        self._ensure_teka(since=(start.year, start.month))

        elif isinstance(event, InsuranceProfileAssigned):
            emp = self.proj.employments.get(event.employee_id)
            if event.teka_member and emp and emp.hire_submitted:
                start = (
                    emp.payroll_start
                    if emp and emp.payroll_start
                    else datetime.now(UTC).date()
                )
                self._ensure_teka(since=(start.year, start.month))

        elif isinstance(event, AttendanceSheetApproved):
            self._fulfill(
                "ATTENDANCE_APPROVAL", event.period_year, event.period_month
            )

        elif isinstance(event, MonthlyPayrollCalculated):
            y, m = event.period_year, event.period_month
            # ⚠️ Η μισθοδοσία ΑΠΟΧΩΡΗΣΗΣ αφορά ΕΝΑΝ εργαζόμενο: δεν εκπληρώνει
            # τη μισθοδοσία της περιόδου (οι υπόλοιποι εκκρεμούν) ούτε ανοίγει
            # τον επόμενο μήνα — αλλιώς η αλυσίδα θα προχωρούσε χωρίς αυτούς.
            if not event.exit_settlement:
                self._fulfill("MONTHLY_PAYROLL", y, m)
                # Κυλιόμενη αλυσίδα: η μισθοδοσία του μήνα «ανοίγει» τον επόμενο
                self._ensure_period(*_next(y, m))
                if m == 11:  # κλείνει ο Νοέμβριος -> υποχρεωτικό επίδομα Δεκ.
                    self._ensure_year_end_allowance(y)

        elif isinstance(event, LeaveRecorded):
            if event.leave_type == LeaveType.ANNUAL:
                self._on_annual_leave(event.employee_id, event.date_from.year)

        elif isinstance(event, GiftCalculated):
            t = "CHRISTMAS_GIFT" if event.gift_type == "christmas" else "EASTER_GIFT"
            # ⚠️ Η ΕΚΚΑΘΑΡΙΣΗ ΑΠΟΧΩΡΗΣΗΣ ΔΕΝ ΚΛΕΙΝΕΙ ΤΗΝ ΕΤΑΙΡΙΚΗ ΥΠΟΧΡΕΩΣΗ:
            # αφορά ΜΟΝΟ αυτόν που αποχωρεί — οι υπόλοιποι δεν έχουν
            # υπολογιστεί ακόμη και το Δώρο τους θα χανόταν.
            if not event.exit_settlement:
                self._fulfill(t, event.year, 0)
            # Ο υπολογισμός του Δώρου γεννά την ΚΑΤΑΒΟΛΗ του (όπως η μισθοδοσία
            # γεννά την πληρωμή μισθών) — και στην αποχώρηση, αφού οφείλεται
            self._ensure_payouts(t, event.year, 0)

        elif isinstance(event, LeaveAllowanceCalculated):
            # Κλείνει η υποχρέωση της περιόδου του επιδόματος: μήνας πρώτης
            # άδειας Ή (αν αποχώρησε χωρίς άδεια) μήνας αποχώρησης. Στη 2η
            # περίπτωση η υποχρέωση μπορεί να μην έχει γεννηθεί (δεν υπήρξε
            # LeaveRecorded) — τη δημιουργούμε τώρα ώστε να φαίνεται στη /payroll.
            emp = self.proj.employments.get(event.employee_id)
            month = emp.allowance_period_month(event.year) if emp else None
            if month:
                self._ensure_allowance_period(event.year, month)
                # Ίδιος κανόνας με τα Δώρα: η εκκαθάριση αποχώρησης δεν
                # εκπληρώνει την υποχρέωση των υπόλοιπων δικαιούχων του μήνα.
                if not event.exit_settlement:
                    self._fulfill("LEAVE_ALLOWANCE", event.year, month)
                self._ensure_payouts("LEAVE_ALLOWANCE", event.year, month)

        elif isinstance(event, APDSubmitted):
            t = "APD_SUBMISSION" if event.fund == "efka" else "APD_TEKA_SUBMISSION"
            self._fulfill(t, event.period_year, event.period_month)

        elif isinstance(event, FMYSubmitted):
            self._fulfill("FMY_SUBMISSION", event.period_year, event.period_month)

        elif isinstance(event, MinimumWageDefined):
            # Νέα ΓΣΣΕ από τη σελίδα Παραμέτρων: αν η περίοδος ισχύος της
            # έχει ήδη ανοίξει, γεννιέται ΤΩΡΑ η υποχρέωση προσαρμογής.
            key = (event.valid_from.year, event.valid_from.month)
            if key in self._company_periods:
                self._ensure_wage_adjustment(*key)

        elif isinstance(event, WageAdjustmentApplied):
            self._fulfill(
                "WAGE_ADJUSTMENT", event.period_year, event.period_month
            )

        elif isinstance(event, PaymentMade):
            t = PAYMENT_TYPES.get(event.payment_type)
            if t:
                self._fulfill(t, event.period_year, event.period_month)

    # ------------------------------------------------------------------
    def _schedule(self, otype: str, due: date, py: int, pm: int):
        self.store.append(COMPANY, ObligationScheduled(_oid(), otype, due, "", py, pm))

    def _fulfill(self, otype: str, py: int, pm: int):
        for ob in self.proj.obligations.values():
            if (
                not ob.fulfilled
                and ob.obligation_type == otype
                and ob.period_year == py
                and ob.period_month == pm
            ):
                self.store.append(COMPANY, ObligationFulfilled(ob.obligation_id, ""))
                return

    # ------------------------------------------------------------------
    def _schedule_period(self, y: int, m: int):
        """Όλοι οι τύποι με spawn="period" — προθεσμίες από το μητρώο."""
        for code in PERIOD_TYPES:
            self._schedule(code, ot.due_date(code, y, m), y, m)

    def _schedule_teka_period(self, y: int, m: int):
        for code in TEKA_TYPES:
            self._schedule(code, ot.due_date(code, y, m), y, m)

    def _teka_since(self):
        """Παλαιότερη περίοδος (y,m) με απασχολούμενο ασφαλισμένο ΤΕΚΑ,
        ή None αν δεν υπάρχει κανένας."""
        hires = [
            e.hire_date
            for e in self.proj.employments.values()
            if e.hire_submitted and e.hire_date
            and e.insurance_profile and e.insurance_profile.teka_member
        ]
        if not hires:
            return None
        h = min(hires)
        return (h.year, h.month)

    def _ensure_teka(self, since: tuple[int, int]):
        """Δημιουργεί υποχρεώσεις ΤΕΚΑ για κάθε υπάρχουσα περίοδο
        εταιρείας >= since που δεν τις έχει ήδη."""
        for y, m in sorted(self._company_periods):
            if (y, m) >= since and (y, m) not in self._teka_periods:
                self._teka_periods.add((y, m))
                self._schedule_teka_period(y, m)

    def _first_hire(self) -> date | None:
        """Από πότε «τρέχει» η εταιρεία σε ΑΥΤΟ το σύστημα — η πρώτη ΑΝΑΛΗΨΗ
        μισθοδοσίας (= πρόσληψη, εκτός αν πρόκειται για μεταφορά). Ορίζει και
        ποια Δώρα προγραμματίζονται."""
        hires = [
            e.payroll_start
            for e in self.proj.employments.values()
            if e.hire_submitted and e.payroll_start
        ]
        return min(hires) if hires else None

    def _ensure_period(self, y: int, m: int):
        """Προγραμματίζει την περίοδο (y,m) — μαζί με ΤΕΚΑ και τα Δώρα του
        έτους — αν δεν υπάρχει ήδη."""
        if (y, m) in self._company_periods:
            return
        self._company_periods.add((y, m))
        self._schedule_period(y, m)
        self._ensure_wage_adjustment(y, m)

        teka_since = self._teka_since()
        if teka_since and (y, m) >= teka_since:
            self._teka_periods.add((y, m))
            self._schedule_teka_period(y, m)

        self._ensure_gifts(y)
        if m == 12:
            self._ensure_year_end_allowance(y)

    def _ensure_year_end_allowance(self, year: int):
        """ΥΠΟΧΡΕΩΤΙΚΟ Επίδομα Αδείας του Δεκεμβρίου: όποιος δεν πήρε κανονική
        άδεια μέσα στο έτος (ούτε αποχώρησε) έχει περίοδο επιδόματος τον
        Δεκέμβριο (`allowance_period_month`).

        ⚠️ Γεννιέται μόλις ΚΛΕΙΣΕΙ η μισθοδοσία ΝΟΕΜΒΡΙΟΥ, όχι με το απλό
        άνοιγμα του Δεκεμβρίου (ο ορίζοντας τον ανοίγει ήδη από την πρόσληψη):
        ως τότε μια άδεια μπορεί να μεταφέρει το επίδομα σε άλλον μήνα και η
        υποχρέωση Δεκεμβρίου θα έμενε χωρίς δικαιούχους — ανοιχτή για πάντα,
        μπλοκάροντας την ΑΠΔ Ιανουαρίου. Μετά, οι μήνες ως τον Νοέμβριο είναι
        κλειστοί (§5.6) και ο μόνος δυνατός μήνας είναι ο Δεκέμβριος."""
        if (year, 12) not in self._company_periods:
            return
        nov = [o for o in self.proj.obligations.values()
               if o.obligation_type == "MONTHLY_PAYROLL"
               and (o.period_year, o.period_month) == (year, 11)]
        if any(not o.fulfilled for o in nov):
            return
        if any(e.hire_submitted and e.allowance_period_month(year) == 12
               for e in self.proj.employments.values()):
            self._ensure_allowance_period(year, 12)

    def _ensure_gifts(self, year: int):
        """Οι ετήσιοι τύποι (Δώρα) του έτους — spawn="annual" στο μητρώο.
        Δεν προγραμματίζονται αν η προθεσμία τους είναι πριν την 1η πρόσληψη."""
        since = self._first_hire()
        if since is None:
            return
        for code in ANNUAL_TYPES:
            due = ot.due_date(code, year, 0)
            if due >= since and (code, year) not in self._gift_keys:
                self._gift_keys.add((code, year))
                self._schedule(code, due, year, 0)

    def _ensure_wage_adjustment(self, y: int, m: int):
        """Γεννιέται υποχρέωση ελέγχου/προσαρμογής των ενεργών συμβάσεων
        (WAGE_ADJUSTMENT) όταν στην περίοδο:
        - τίθεται σε ισχύ ΝΕΟΣ κατώτατος (ΓΣΣΕ/ΚΥΑ — config.MINIMUM_WAGE...), Ή
        - **ωριμάζει τριετία** ενεργού εργαζόμενου (το προσωπικό όριο ανέβηκε
          σε σχέση με τον προηγούμενο μήνα, ή ο μισθός υπολείπεται του ορίου).
        Προαπαιτούμενο της μισθοδοσίας — ούτως ή άλλως το wage_violations θα
        την μπλόκαρε στο command layer."""
        if (y, m) in self._wage_adjust_periods:
            return
        gsse = any(
            w.valid_from.year == y and w.valid_from.month == m
            for w in config.minimum_wage_entries()
        )
        if not gsse and not self._floor_rose(y, m):
            return
        self._wage_adjust_periods.add((y, m))
        self._schedule(
            "WAGE_ADJUSTMENT", ot.due_date("WAGE_ADJUSTMENT", y, m), y, m
        )

    def _floor_rose(self, y: int, m: int) -> bool:
        """True αν χρειάζεται προσαρμογή μισθού λόγω ωρίμανσης τριετίας. Η
        πολιτική «Διάφορα → keep_wage_excess» καθορίζει πότε «χρειάζεται»:
        - **συμψηφισμός** (default): μόνο αν ο μισθός ΥΠΟΛΕΙΠΕΤΑΙ του νέου
          ορίου (πραγματική παράβαση) — αλλιώς η διαφορά απορροφά την αύξηση
          και δεν χρειάζεται obligation.
        - **διατήρηση**: όποτε το όριο ΑΝΕΒΗΚΕ σε σχέση με τον προηγούμενο
          μήνα (η αύξηση πρέπει να περάσει στον μισθό).
        (Η αλλαγή ΓΣΣΕ γεννά WAGE_ADJUSTMENT ξεχωριστά — βλ. καλούσα.)"""
        keep = bool(self.proj.misc_settings.get("keep_wage_excess", False))
        period_end = last_working_day_of_month(y, m)
        prev_y, prev_m = (y, m - 1) if m > 1 else (y - 1, 12)
        prev_end = last_working_day_of_month(prev_y, prev_m)
        for e in self.proj.employments.values():
            if (
                not e.hire_submitted
                or not e.active
                or e.hire_date is None
                or e.hire_date > period_end
            ):
                continue
            f_new = config.minimum_floor_value(
                e.pay_type, e.weekly_hours, period_end,
                e.total_service_years(period_end), e.married, e.extra_allowances_pct,
            )
            salary = e.daily_wage if e.pay_type == "daily" else e.monthly_gross
            if salary < f_new - 0.005:  # παράβαση -> πάντα χρειάζεται
                return True
            if keep:
                ref_old = max(prev_end, e.hire_date)
                f_old = config.minimum_floor_value(
                    e.pay_type, e.weekly_hours, ref_old,
                    e.total_service_years(ref_old), e.married,
                    e.extra_allowances_pct,
                )
                if f_new > f_old + 0.005:  # ανέβηκε το όριο -> πέρασμα αύξησης
                    return True
        return False

    def _ensure_payouts(self, source_type: str, y: int, m: int):
        """Ο υπολογισμός έκλεισε -> γεννιούνται οι καταβολές του (spawn=
        "on_source" στο μητρώο), με την ΙΔΙΑ προθεσμία."""
        source_due = next(
            (
                ob.due_date
                for ob in self.proj.obligations.values()
                if ob.obligation_type == source_type
                and ob.period_year == y
                and ob.period_month == m
            ),
            last_working_day_of_month(y, m or 12),
        )
        for code, src in ot.PAYOUT_SOURCE.items():
            if src != source_type:
                continue
            exists = any(
                ob.obligation_type == code
                and ob.period_year == y
                and ob.period_month == m
                for ob in self.proj.obligations.values()
            )
            if not exists:
                self._schedule(code, ot.due_date(code, y, m, source_due), y, m)

    def _on_annual_leave(self, employee_id: str, year: int):
        """Κανονική άδεια -> Επίδομα Αδείας στην περίοδο της ΠΡΩΤΗΣ άδειας
        του συγκεκριμένου εργαζόμενου μέσα στο έτος.

        Πολλαπλές περίοδοι ανά έτος (έως 12): κάθε μήνας που κάποιος παίρνει
        την πρώτη του άδεια γεννά μία ενιαία υποχρέωση για όλους όσους την
        πήραν τότε. Αν άλλος εργαζόμενος έχει ήδη «ανοίξει» τον μήνα, δεν
        δημιουργείται δεύτερη."""
        emp = self.proj.employments.get(employee_id)
        first = emp.first_annual_leave(year) if emp else None
        if first is None:
            return
        self._ensure_allowance_period(year, first.month)

    def _ensure_allowance_period(self, year: int, month: int):
        """Γεννά (αν λείπει) την υποχρέωση Επιδόματος Αδείας της περιόδου
        (year, month). Κοινό για: πρώτη κανονική άδεια εργαζόμενου ΚΑΙ
        εκκαθάριση αποχώρησης χωρίς άδεια."""
        key = (year, month)
        if key in self._allowance_periods:
            return
        self._allowance_periods.add(key)
        self._schedule(
            "LEAVE_ALLOWANCE",
            ot.due_date("LEAVE_ALLOWANCE", year, month),
            year,
            month,
        )

    def _on_hired(self, hire_date: date):
        h = hire_date
        ey, em = _next(h.year, h.month, self.horizon_months - 1)
        end = date(ey, em, 1)
        for y, m in _months(h, end):
            self._ensure_period(y, m)
        # Πρόσληψη ΜΕΤΑ το άνοιγμα του Δεκεμβρίου: το _ensure_period δεν
        # ξανατρέχει για υπάρχουσα περίοδο — ο νέος χρωστά κι αυτός επίδομα.
        for y, m in sorted(self._company_periods):
            if m == 12 and y >= h.year:
                self._ensure_year_end_allowance(y)
