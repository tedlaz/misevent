"""
Δώρα, Επίδομα Αδείας και ΕΚΚΑΘΑΡΙΣΗ ΑΠΟΧΩΡΗΣΗΣ

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

from datetime import date

import misevent.domain.payroll_engine as pe
import misevent.rules.obligation_types as ot
from misevent.domain.events import GiftCalculated, LeaveAllowanceCalculated

from ._base import DISMISSAL_REASON


class GiftsMixin:
    # ------------------------------------------------------------------ Δώρα
    def _require_wage_adjusted(self, ob_code, year, month):
        """Η προσαρμογή ΓΣΣΕ ΠΡΟΗΓΕΙΤΑΙ Δώρων/Επιδόματος (ώστε να υπολογιστούν
        στους προσαρμοσμένους μισθούς). Απορρίπτει αν υπάρχει ανεκπλήρωτη
        υποχρέωση WAGE_ADJUSTMENT στην περίοδο που ορίζει το μητρώο (requires).

        Ο κανόνας ζει στο command layer — καμία οθόνη δεν τον παρακάμπτει
        (ούτε το κουμπί Δώρου της σελίδας /payroll)."""
        wanted = {
            k
            for k in ot.prerequisite_keys(ob_code, year, month)
            if k[0] == "WAGE_ADJUSTMENT"
        }
        for ob in self.proj.obligations.values():
            key = (ob.obligation_type, ob.period_year, ob.period_month)
            if key in wanted and not ob.fulfilled:
                raise ValueError(
                    "Προηγείται η προσαρμογή στον κατώτατο (ΓΣΣΕ) "
                    f"{ob.period_month}/{ob.period_year}: τρέξτε την πρώτα, "
                    "ώστε το ποσό να υπολογιστεί στους προσαρμοσμένους μισθούς"
                )

    def gift_preview_for(self, emp, gift_type: str, year: int, term=None):
        """Ο υπολογισμός του Δώρου ΕΝΟΣ εργαζόμενου, ΧΩΡΙΣ event — ΜΙΑ πηγή
        αλήθειας για την προεπισκόπηση (`/payroll`) και για τον υπολογισμό.

        ⚠️ Η αμοιβή ΔΕΝ είναι η τρέχουσα, αλλά αυτή που **ίσχυε την ημερομηνία
        αναφοράς** του Δώρου (`pe.gift_wage_date`): το Δώρο Πάσχα καταβάλλεται
        τη Μ. Τετάρτη και υπολογίζεται στον μισθό **15 ημέρες πριν** — μια
        μεταβολή σύμβασης μέσα σε αυτό το δεκαπενθήμερο ΔΕΝ το επηρεάζει.

        Το `term` είναι ΥΠΟΘΕΤΙΚΗ ημ. αποχώρησης (προεπισκόπηση πριν την
        καταχώρησή της)· χωρίς αυτό μετρά η αποχώρηση των projections."""
        on = pe.gift_payment_date(gift_type, year)
        terms = emp.contract_on(pe.gift_wage_date(gift_type, year))
        rates = self.contribution_rates(emp.insurance_profile, on)
        daily = terms.pay_type == "daily"
        fn = {
            ("easter", True): pe.calc_easter_gift_daily,
            ("easter", False): pe.calc_easter_gift,
            ("christmas", True): pe.calc_christmas_gift_daily,
            ("christmas", False): pe.calc_christmas_gift,
        }[(gift_type, daily)]
        extra = {} if daily else {"subsidy_full": self.labor_subsidy_full(
            emp, terms.wage, terms.weekly_hours, pe.gift_wage_date(gift_type, year))}
        return fn(
            terms.wage,
            emp.children,
            year,
            emp.hire_date,
            term or emp.termination_date,
            rates=rates,
            birth_date=emp.birth_date,
            **extra,
        )

    def _calc_gift_for(self, emp, gift_type, year, exit_settlement=False):
        """Υπολογίζει & καταγράφει το Δώρο (easter/christmas) ΕΝΟΣ εργαζόμενου
        (pro-rata βάσει πρόσληψης/αποχώρησης). Κοινό για τα company-wide runs
        και για την αυτόματη εκκαθάριση της αποχώρησης — που όμως σημαδεύεται
        (`exit_settlement`) ώστε να ΜΗΝ κλείσει την εταιρική υποχρέωση Δώρων
        για τους υπόλοιπους εργαζόμενους (scheduler)."""
        res = self.gift_preview_for(emp, gift_type, year)
        if res.gross > 0:
            self.store.append(
                emp.employee_id,
                GiftCalculated(
                    emp.employee_id,
                    gift_type,
                    year,
                    res.gross,
                    res.efka_employee,
                    res.fmy,
                    res.net,
                    exit_settlement,
                    efka_employer=res.efka_employer,
                    subsidy_employee=res.subsidy_employee,
                ),
            )
            return res
        return None

    def allowance_preview_for(self, emp, year: int, term=None):
        """Ο υπολογισμός του Επιδόματος Αδείας ΕΝΟΣ εργαζόμενου, ΧΩΡΙΣ event —
        ΜΙΑ πηγή αλήθειας για τον υπολογισμό και για την προεπισκόπηση.

        Το `term` είναι ΥΠΟΘΕΤΙΚΗ ημ. αποχώρησης (προεπισκόπηση πριν την
        καταχώρησή της) — μαζί με την καταχωρημένη αποχώρηση είναι ΤΟ ΜΟΝΟ
        πράγμα που κόβει την αναλογία των μηνών.

        ⚠️ Το 1ο/2ο έτος η αναλογία μετριέται **έως 31/12** (ή έως την
        αποχώρηση), ΟΧΙ έως τη λήψη της άδειας: ο μήνας της πρώτης άδειας
        ορίζει την ΠΕΡΙΟΔΟ της υποχρέωσης, όχι το ποσό. Όσο ο εργαζόμενος
        δεν έχει αποχωρήσει, το ποσό στηρίζεται στην παραδοχή ότι θα
        εργαστεί έως το τέλος του έτους — γι' αυτό η οθόνη προειδοποιεί
        (`queries.allowance_warnings`) και ο υπολογισμός μπορεί να
        αναβληθεί έως 31/12 (παράταση προθεσμίας)."""
        rates = self.contribution_rates(emp.insurance_profile, date(year, 7, 31))
        daily = emp.pay_type == "daily"
        wage = emp.daily_wage if daily else emp.monthly_gross
        fn = pe.calc_leave_allowance_daily if daily else pe.calc_leave_allowance
        extra = {} if daily else {"subsidy_full": self.labor_subsidy_full(
            emp, wage, emp.weekly_hours, date(year, 7, 31))}
        return fn(
            wage,
            emp.children,
            year,
            emp.hire_date,
            term=term or emp.termination_date,
            rates=rates,
            birth_date=emp.birth_date,
            full_year=pe.is_third_service_year(year, emp.hire_date),
            **extra,
        )

    def _calc_allowance_for(self, emp, year, exit_settlement=False):
        """Υπολογίζει & καταγράφει το Επίδομα Αδείας ΕΝΟΣ εργαζόμενου
        (`exit_settlement`: βλ. `_calc_gift_for` — δεν κλείνει την υποχρέωση
        των υπόλοιπων δικαιούχων της περιόδου)."""
        res = self.allowance_preview_for(emp, year)
        if res.gross > 0:
            self.store.append(
                emp.employee_id,
                LeaveAllowanceCalculated(
                    emp.employee_id,
                    year,
                    res.gross,
                    res.efka_employee,
                    res.fmy,
                    res.net,
                    exit_settlement,
                    efka_employer=res.efka_employer,
                    subsidy_employee=res.subsidy_employee,
                ),
            )
            return res
        return None

    def _settle_exit_accruals(self, emp, with_notice=False):
        """ΕΚΚΑΘΑΡΙΣΗ ΑΠΟΧΩΡΗΣΗΣ — ΟΛΟΙ οι υπολογισμοί γίνονται ΑΥΤΟΜΑΤΑ με την
        επιβεβαίωση της αποχώρησης, χωρίς χειροκίνητα κουμπιά:

        0. **μισθοδοσία του ΜΗΝΑ ΑΠΟΧΩΡΗΣΗΣ** (μόνο γι' αυτόν, ΕΩΣ ΚΑΙ την
           ημερομηνία λήξης — όχι ολόκληρος ο μήνας)·
        1. pro-rata **Δώρο** (Πάσχα αν η αποχώρηση είναι έως 30/4, αλλιώς
           Χριστουγέννων) και **Επίδομα Αδείας**, εφόσον δεν έχουν ήδη
           υπολογιστεί για το έτος — σημαδεμένα ως `exit_settlement`, ώστε να
           αφορούν **ΑΠΟΚΛΕΙΣΤΙΚΑ** αυτόν που αποχωρεί και να μην κλείνουν τον
           εταιρικό κύκλο Δώρων/Επιδόματος των υπόλοιπων εργαζομένων·
        2. **αποζημίωση μη ληφθείσας αδείας**, αν υπάρχει υπόλοιπο·
        3. **αποζημίωση απόλυσης**, ΜΟΝΟ σε καταγγελία σύμβασης και εφόσον
           οφείλεται (υπηρεσία ≥ 1 έτος).

        Ισχύει για ΚΑΘΕ είδος αποχώρησης (οικιοθελής/καταγγελία/λήξη)."""
        eid = emp.employee_id
        year, month = emp.termination_date.year, emp.termination_date.month

        # 0. Η ΜΙΣΘΟΔΟΣΙΑ ΤΟΥ ΤΡΕΧΟΝΤΟΣ ΜΗΝΑ, ΜΟΝΟ γι' αυτόν τον εργαζόμενο
        # (πληρώνεται στην αποχώρηση, δεν περιμένει το τέλος του μήνα). Αν
        # δεν γίνεται (π.χ. εκκρεμεί προηγούμενος μήνας) η αποχώρηση ΔΕΝ
        # ματαιώνεται — θα υπολογιστεί στον τακτικό κύκλο.
        exit_payroll = self.exit_payroll_preview(eid)
        if exit_payroll["payroll"] is not None:
            self._append_payroll(
                emp, year, month, exit_payroll["payroll"], exit_settlement=True
            )

        gift_type = "easter" if month <= 4 else "christmas"
        if not any(
            g.employee_id == eid and g.gift_type == gift_type and g.year == year
            for g in self.proj.gifts
        ):
            self._calc_gift_for(emp, gift_type, year, exit_settlement=True)
        if not any(a.employee_id == eid and a.year == year for a in self.proj.allowances):
            self._calc_allowance_for(emp, year, exit_settlement=True)

        # Αποζημίωση μη ληφθείσας αδείας — μόνο αν υπάρχει υπόλοιπο
        if self.leave_compensation(eid) is None and self.leave_compensation_preview(eid):
            self.run_leave_compensation(eid)

        # Αποζημίωση απόλυσης — μόνο σε καταγγελία και μόνο αν οφείλεται
        if (
            emp.termination_reason == DISMISSAL_REASON
            and self.severance(eid) is None
            and self.severance_preview(eid, with_notice)
        ):
            self.run_severance(eid, with_notice)

    def run_christmas_gifts(self, year: int):
        self._require_wage_adjusted("CHRISTMAS_GIFT", year, 0)
        for emp in self.proj.employments.values():
            if emp.hire_submitted and emp.hire_date is not None:
                self._calc_gift_for(emp, "christmas", year)

    def run_easter_gifts(self, year: int):
        self._require_wage_adjusted("EASTER_GIFT", year, 0)
        for emp in self.proj.employments.values():
            if emp.hire_submitted and emp.hire_date is not None:
                self._calc_gift_for(emp, "easter", year)

    def allowance_beneficiaries(self, year: int, month: int):
        """Οι δικαιούχοι Επιδόματος Αδείας της περιόδου: όσοι πήραν την ΠΡΩΤΗ
        κανονική άδεια του έτους μέσα σε αυτόν τον μήνα (το επίδομα
        καταβάλλεται όταν ο εργαζόμενος πάρει την άδειά του) — ΚΑΙ όσοι
        αποχώρησαν εκείνον τον μήνα χωρίς να πάρουν άδεια (εκκαθάριση
        αποχώρησης, βλ. `EmploymentState.allowance_period_month`)."""
        out = []
        for emp in self.proj.employments.values():
            if not emp.hire_submitted or emp.hire_date is None:
                continue
            if emp.allowance_period_month(year) != month:
                continue
            out.append(emp)
        return out

    def run_leave_allowance(self, year: int, month: int):
        self._require_wage_adjusted("LEAVE_ALLOWANCE", year, month)
        for emp in self.allowance_beneficiaries(year, month):
            self._calc_allowance_for(emp, year)
