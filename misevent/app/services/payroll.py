"""
Κατώτατος (παραβάσεις/προσαρμογές), κλείδωμα περιόδου, ΜΗΝΙΑΙΑ ΜΙΣΘΟΔΟΣΙΑ

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

from calendar import monthrange
from copy import copy
from datetime import date

import misevent.domain.payroll_engine as pe
import misevent.domain.work_hours as work_hours
import misevent.rules.config as config
import misevent.rules.obligation_types as ot
from misevent.domain.events import (
    MonthlyPayrollCalculated,
    PayrollPeriodReopened,
    WageAdjustmentApplied,
)
from misevent.rules.calendar_gr import last_working_day_of_month


class PayrollMixin:
    # ------------------------------------------------------ Μηνιαίο payroll
    def wage_violation_for(self, e, period_end) -> str | None:
        """Παράβαση κατώτατου ΕΝΟΣ εργαζόμενου στην περίοδο (ή None) — ΜΙΑ
        πηγή αλήθειας για τον έλεγχο ΟΛΩΝ (`wage_violations`) και για τη
        μισθοδοσία αποχώρησης, που αφορά μόνο έναν (και μάλιστα ήδη
        αποχωρήσαντα, τον οποίο το φίλτρο `active` θα παρέλειπε)."""
        if e.hire_date is None or e.hire_date > period_end:
            return None
        mw = config.minimum_wage_for(period_end)
        floor, desc = self.minimum_floor(
            e.pay_type,
            e.weekly_hours,
            period_end,
            service_years=e.total_service_years(period_end),
            married=e.married,
            extra_pct=e.extra_allowances_pct,
        )
        suffix = f" [{desc}]" if desc else ""
        if e.pay_type == "monthly" and e.monthly_gross < floor:
            pt = f", μερική {e.weekly_hours:g}ω" if e.weekly_hours < 40 else ""
            return (
                f"{e.full_name}: μισθός {e.monthly_gross:.2f}€ < "
                f"κατώτατου {floor:.2f}€{suffix}{pt} "
                f"(ισχύς από {config.gr_date(mw.valid_from)})"
            )
        if e.pay_type == "daily" and e.daily_wage < floor:
            return (
                f"{e.full_name}: ημερομίσθιο {e.daily_wage:.2f}€ < "
                f"κατώτατου {floor:.2f}€{suffix} "
                f"(ισχύς από {config.gr_date(mw.valid_from)})"
            )
        return None

    def wage_violations(self, year: int, month: int) -> list[str]:
        """Αποδοχές κάτω από τον κατώτατο (ΓΣΣΕ/ΚΥΑ) που ισχύει για
        την περίοδο μισθοδοσίας — προσαυξημένο με τριετίες (που ΩΡΙΜΑΖΟΥΝ
        με τον χρόνο στον εργοδότη), γάμο και λοιπές προσαυξήσεις."""
        period_end = last_working_day_of_month(year, month)
        out = []
        for e in self.proj.employments.values():
            if not e.hire_submitted or not e.active:
                continue
            violation = self.wage_violation_for(e, period_end)
            if violation:
                out.append(violation)
        return out

    def apply_minimum_adjustments(self, year: int, month: int) -> list:
        """Έλεγχος/προσαρμογή στον κατώτατο (ΓΣΣΕ/ΚΥΑ **ή ωρίμανση τριετίας**)
        της περιόδου.

        Για κάθε ΕΝΕΡΓΗ απασχόληση συγκρίνεται το προσωπικό όριο ΤΩΡΑ
        (`f_new`, στο τέλος της περιόδου) με το όριο του ΠΡΟΗΓΟΥΜΕΝΟΥ μήνα
        (`f_old`) — η διαφορά `increment = f_new − f_old` είναι η «αύξηση»
        που έφερε είτε νέα ΓΣΣΕ είτε συμπληρωμένη τριετία.

        Η αντιμετώπιση της **διαφοράς** (όταν ο μισθός είναι πάνω από το όριο)
        ελέγχεται από την προτίμηση «Διάφορα → `keep_wage_excess`»:
        - **συμψηφισμός** (default): ο μισθός ανεβαίνει ΜΟΝΟ αν υπολείπεται,
          και μόνο ως το νέο όριο — η υπάρχουσα διαφορά απορροφά την αύξηση.
        - **διατήρηση**: ο μισθός ανεβαίνει κατά την αύξηση (`+ increment`,
          και τουλάχιστον ως το όριο) — η διαφορά διατηρείται πάνω από το όριο.

        Κάθε προσαρμογή είναι ContractChanged (+ Δήλωση Μεταβολής ΕΡΓΑΝΗ)·
        καταγράφεται WageAdjustmentApplied με τη σύνοψη (κλείνει την υποχρέωση
        WAGE_ADJUSTMENT — και όταν δεν χρειάστηκε καμία προσαρμογή)."""
        period_end = last_working_day_of_month(year, month)
        mw = config.minimum_wage_for(period_end)
        effective = (
            mw.valid_from
            if (mw.valid_from.year, mw.valid_from.month) == (year, month)
            else date(year, month, 1)
        )
        prev_y, prev_m = (year, month - 1) if month > 1 else (year - 1, 12)
        prev_end = last_working_day_of_month(prev_y, prev_m)
        keep = bool(self.proj.misc_settings.get("keep_wage_excess", False))
        adjusted = []
        for e in list(self.proj.employments.values()):
            if (
                not e.hire_submitted
                or not e.active
                or e.hire_date is None
                or e.hire_date > period_end
            ):
                continue
            f_new = config.minimum_floor_value(
                e.pay_type,
                e.weekly_hours,
                period_end,
                e.total_service_years(period_end),
                e.married,
                e.extra_allowances_pct,
            )
            ref_old = max(prev_end, e.hire_date)
            f_old = config.minimum_floor_value(
                e.pay_type,
                e.weekly_hours,
                ref_old,
                e.total_service_years(ref_old),
                e.married,
                e.extra_allowances_pct,
            )
            increment = round(max(0.0, f_new - f_old), 2)
            old = e.daily_wage if e.pay_type == "daily" else e.monthly_gross
            if keep:
                # διατήρηση διαφοράς: περνά η αύξηση, με ελάχιστο το νέο όριο
                new = round(max(old + increment, f_new), 2)
            else:
                # συμψηφισμός: μόνο αν υπολείπεται -> ανεβαίνει ως το όριο
                new = f_new if old < f_new - 0.005 else old
            if abs(new - old) < 0.005:
                continue
            # ΑΙΤΙΑ της προσαρμογής (ώστε να μη μπερδεύεται η ωρίμανση τριετίας
            # με την αλλαγή ΓΣΣΕ): σύγκριση κατώτατου & αριθμού τριετιών ΤΩΡΑ
            # vs στην αναφορά του προηγ. μήνα.
            r = config.allowance_rules_for(period_end)
            tri_max = (
                r.daily_max_triennia
                if e.pay_type == "daily"
                else r.monthly_max_triennia
            )
            matured = config.triennia_count(
                e.total_service_years(period_end), tri_max
            ) > config.triennia_count(e.total_service_years(ref_old), tri_max)
            gsse = (
                config.minimum_wage_for(period_end).valid_from
                != config.minimum_wage_for(ref_old).valid_from
            )
            cause = (
                " + ".join(c for c, on in (("ΓΣΣΕ", gsse), ("τριετία", matured)) if on)
                or "κατώτατο όριο"
            )
            # ο ΙΔΙΟΣ διαχωρισμός ως κωδικός αιτίας πάνω στη μεταβολή, ώστε το
            # ιστορικό συμβάσεων να εξηγεί μόνο του γιατί άλλαξε ο μισθός
            code = (
                " + ".join(
                    c for c, on in (("gsse", gsse), ("triennium", matured)) if on
                ).replace(" + ", "+")
                or "minimum"
            )
            self.change_contract(
                e.employee_id,
                max(effective, e.hire_date),
                e.pay_type,
                monthly_gross=(new if e.pay_type == "monthly" else 0.0),
                daily_wage=(new if e.pay_type == "daily" else 0.0),
                weekly_hours=e.weekly_hours,
                reason=code,
                reason_date=(mw.valid_from if gsse else None),
            )
            adjusted.append(
                {
                    "employment_id": e.employee_id,
                    "name": e.full_name,
                    "old": old,
                    "new": new,
                    "mode": "keep" if keep else "offset",
                    "cause": cause,
                }
            )
        self.store.append(
            "COMPANY", WageAdjustmentApplied(year, month, tuple(adjusted))
        )
        return adjusted

    def _latest_closed_payroll(self):
        """(year, month) της πιο πρόσφατης ΟΛΟΚΛΗΡΩΜΕΝΗΣ μισθοδοσίας, ή None."""
        done = [
            (o.period_year, o.period_month)
            for o in self.proj.obligations.values()
            if o.obligation_type == "MONTHLY_PAYROLL" and o.fulfilled
        ]
        return max(done) if done else None

    # -------------------------------------------------- ΚΛΕΙΔΩΜΑ ΠΕΡΙΟΔΟΥ
    # Μόλις κλείσει η μισθοδοσία ενός μήνα, ο μήνας ΚΑΙ ΚΑΘΕ ΠΡΟΓΕΝΕΣΤΕΡΟΣ
    # παγώνουν: ούτε πρόσληψη/αποχώρηση, ούτε πρόγραμμα, ούτε παρουσίες,
    # ούτε άδειες. Οτιδήποτε από αυτά θα άλλαζε αναδρομικά υπολογισμένα
    # ποσά (μισθοδοσία, Δώρα, ΑΠΔ, ΦΜΥ) που έχουν ήδη υποβληθεί.
    # ΟΛΟΙ οι έλεγχοι περνούν από εδώ — καμία οθόνη δεν τους παρακάμπτει.
    def closed_payroll_period(self):
        """(έτος, μήνας) της τελευταίας ΚΛΕΙΣΜΕΝΗΣ περιόδου, ή None.

        Read-only — το χρησιμοποιεί και το UI για να χρωματίσει/κλειδώσει
        τις κλεισμένες περιόδους στα grid (Πρόγραμμα, Άδειες)."""
        return self._latest_closed_payroll()

    def is_period_closed(self, year: int, month: int) -> bool:
        """True αν η περίοδος (ή οποιαδήποτε προγενέστερη) έχει κλείσει."""
        closed = self._latest_closed_payroll()
        return closed is not None and (year, month) <= closed

    def closed_period_end(self):
        """Η ΤΕΛΕΥΤΑΙΑ ημερολογιακή ημέρα που ανήκει σε κλεισμένη περίοδο
        (ή None). Ό,τι είναι <= από αυτήν είναι κλειδωμένο — μία πράξη για
        όλα τα grid, αντί για έλεγχο ανά μήνα."""
        closed = self._latest_closed_payroll()
        if closed is None:
            return None
        year, month = closed
        return date(year, month, monthrange(year, month)[1])

    def check_open_period(self, when, action: str):
        """Δημόσιο pre-check για το UI: ίδιος έλεγχος με το command layer,
        ώστε η φόρμα πρόσληψης να απορρίπτει ΠΡΙΝ δημιουργηθεί το πρόσωπο
        (αλλιώς μένει ορφανό). ΜΙΑ πηγή αλήθειας — δεν αντιγράφεται κανόνας."""
        self._check_open_period(when, action)

    def _check_open_period(self, when, action: str):
        """Απορρίπτει ενέργεια σε μήνα του οποίου η μισθοδοσία έχει ήδη
        κλείσει (ή σε προγενέστερο). Ισχύει ΜΟΝΟ για νέα commands — το
        ιστορικό δεν επικυρώνεται αναδρομικά (§1)."""
        closed = self._latest_closed_payroll()
        if closed is not None and (when.year, when.month) <= closed:
            raise ValueError(
                f"Η μισθοδοσία {closed[1]}/{closed[0]} έχει ήδη κλείσει — δεν "
                f"επιτρέπεται {action} σε κλεισμένη ή προγενέστερη περίοδο "
                f"({when.month}/{when.year})"
            )

    # Οι υποβολές της περιόδου: όσο ΣΤΕΚΕΙ έστω μία, η μισθοδοσία δεν ανοίγει
    # (πρώτα δηλώνεται λανθασμένη υποβολή — `withdraw_submission`).
    SUBMISSION_TYPES = (
        "APD_SUBMISSION", "APD_TEKA_SUBMISSION", "FMY_SUBMISSION",
    )

    def _obligation(self, otype: str, year: int, month: int):
        """Η υποχρέωση ενός τύπου για την περίοδο (ή None)."""
        return next(
            (o for o in self.proj.obligations.values()
             if o.obligation_type == otype
             and o.period_year == year and o.period_month == month),
            None,
        )

    def reopen_payroll_period(self, year: int, month: int, reason: str = ""):
        """ΑΝΟΙΓΕΙ ΞΑΝΑ υπολογισμένη μισθοδοσία για ΔΙΟΡΘΩΣΗ.

        Μέχρι να υποβληθούν τα αρχεία, ένα λάθος (π.χ. ποσοστό ΚΠΚ που δεν
        είχε ενημερωθεί) πρέπει να διορθώνεται. Χωρίς αυτό, η μόνη διέξοδος
        ήταν το καταστροφικό rollback ολόκληρου του log.

        Επιστρέφει λίστα ΠΡΟΕΙΔΟΠΟΙΗΣΕΩΝ (δεν μπλοκάρουν): αν οι μισθοί έχουν
        ήδη πληρωθεί, τα καθαρά θα αλλάξουν και θα χρειαστεί συμψηφισμός."""
        ob = self._obligation("MONTHLY_PAYROLL", year, month)
        if ob is None or not ob.fulfilled:
            raise ValueError(
                f"Η μισθοδοσία {month}/{year} δεν έχει υπολογιστεί — δεν "
                "υπάρχει τίποτα να ανοίξει"
            )
        # ΜΟΝΟ Ο ΤΕΛΕΥΤΑΙΟΣ: οι περίοδοι υπολογίζονται με τη σειρά και ο ΦΜΥ
        # είναι σωρευτικός — άνοιγμα παλαιότερου μήνα θα άφηνε τους επόμενους
        # υπολογισμένους πάνω σε σαθρή βάση.
        latest = self._latest_closed_payroll()
        if latest is not None and (year, month) < latest:
            raise ValueError(
                f"Ανοίγει μόνο η τελευταία υπολογισμένη περίοδος — άνοιξε "
                f"πρώτα τη μισθοδοσία {latest[1]}/{latest[0]}"
            )
        for otype in self.SUBMISSION_TYPES:
            sub = self._obligation(otype, year, month)
            if sub is not None and sub.fulfilled:
                raise ValueError(
                    f"Έχει ήδη υποβληθεί «{ot.LABELS.get(otype, otype)}» "
                    f"{month}/{year} — δήλωσε πρώτα λανθασμένη υποβολή, αν "
                    "το αρχείο δεν ίσχυσε"
                )
        warnings = []
        pay = self._obligation("SALARY_PAYMENT", year, month)
        if pay is not None and pay.fulfilled:
            warnings.append(
                f"Οι μισθοί {month}/{year} έχουν ήδη πληρωθεί: ο "
                "επανυπολογισμός θα αλλάξει τα καθαρά και η διαφορά θα "
                "χρειαστεί συμψηφισμό — η πληρωμή ΔΕΝ αντιστρέφεται."
            )
        self.store.append(
            "COMPANY", PayrollPeriodReopened(year, month, reason)
        )
        return warnings

    def _check_open_days(self, days, action: str):
        """Ίδιος έλεγχος για ΣΥΝΟΛΟ ημερών (πρόγραμμα, παρουσίες, άδειες):
        απορρίπτει ΟΛΗ την ενέργεια αν έστω μία ημέρα πέφτει σε κλεισμένη
        περίοδο — αναφέροντας την παλαιότερη, ώστε να μη γράφονται μισές
        αλλαγές."""
        end = self.closed_period_end()
        if end is None:
            return
        offending = sorted(d for d in days if d <= end)
        if offending:
            self._check_open_period(offending[0], action)

    def period_has_employment(self, year: int, month: int) -> bool:
        """True αν έστω μία απασχόληση ήταν ΕΝΕΡΓΗ οποιαδήποτε στιγμή του μήνα
        (πρόσληψη έως το τέλος του μήνα ΚΑΙ χωρίς λήξη Ή λήξη από την 1η και
        μετά). Μήνες χωρίς κανέναν εργαζόμενο (π.χ. ανάμεσα σε αποχώρηση και
        επαναπρόσληψη) δεν έχουν μισθοδοσία και ΠΡΟΣΠΕΡΝΙΟΥΝΤΑΙ στην αλυσίδα."""
        start = date(year, month, 1)
        end = last_working_day_of_month(year, month)
        for e in self.proj.employments.values():
            if not e.hire_submitted:
                continue
            # ΑΝΑΛΗΨΗ, όχι πρόσληψη: μήνες πριν τη μεταφορά ανήκουν στο παλιό
            # πρόγραμμα και δεν έχουν υποχρεώσεις εδώ.
            if e.payroll_start is None or e.payroll_start > end:
                continue
            if e.termination_date is not None and e.termination_date < start:
                continue
            return True
        return False

    def current_payroll_period(self):
        """(έτος, μήνας) του **ΤΡΕΧΟΝΤΟΣ ΜΗΝΑ ΜΙΣΘΟΔΟΣΙΑΣ**: η παλαιότερη
        ανεκπλήρωτη περίοδος με απασχόληση — δηλαδή ο μήνας που «τρέχει» τώρα
        (αν έκλεισε ο Απρίλιος, τρέχων είναι ο Μάιος). None όταν δεν έχει
        προγραμματιστεί καμία περίοδο (π.χ. σκέτο setup χωρίς scheduler).

        ΜΙΑ πηγή αλήθειας: τη διαβάζουν ο έλεγχος ημ. αποχώρησης
        (`_check_termination`) και το UI."""
        pending = [
            (o.period_year, o.period_month)
            for o in self.proj.obligations.values()
            if o.obligation_type == "MONTHLY_PAYROLL"
            and not o.fulfilled
            and self.period_has_employment(o.period_year, o.period_month)
        ]
        return min(pending) if pending else None

    def pending_payroll_before(self, year: int, month: int):
        """Η παλαιότερη ΑΝΕΚΠΛΗΡΩΤΗ μισθοδοσία πριν από την περίοδο (ή None).

        Η μισθοδοσία είναι αλυσίδα: δεν υπολογίζεται μήνας αν εκκρεμεί
        προηγούμενος (θα χαλούσαν προϋπηρεσία, Δώρα, ΑΠΔ και σειρά ΦΜΥ).
        Οι κενές περίοδοι (χωρίς κανέναν εργαζόμενο) ΔΕΝ μετρούν ως εκκρεμείς
        — προσπερνιούνται (π.χ. αποχώρηση 5ου, επαναπρόσληψη 7ου → ο 6ος
        δεν μπλοκάρει τη μισθοδοσία του 7ου)."""
        earlier = [
            o
            for o in self.proj.obligations.values()
            if o.obligation_type == "MONTHLY_PAYROLL"
            and not o.fulfilled
            and (o.period_year, o.period_month) < (year, month)
            and self.period_has_employment(o.period_year, o.period_month)
        ]
        if not earlier:
            return None
        return min(earlier, key=lambda o: (o.period_year, o.period_month))

    def run_monthly_payroll(self, year: int, month: int):
        """Υπολογίζει μισθοδοσία περιόδου για όλους τους ενεργούς,
        εφόσον καμία αμοιβή δεν υπολείπεται του κατώτατου ορίου και έχουν
        ολοκληρωθεί όλες οι προηγούμενες περίοδοι."""
        self._require_parameters()
        periods = {
            (o.period_year, o.period_month)
            for o in self.proj.obligations.values()
            if o.obligation_type == "MONTHLY_PAYROLL"
        }
        if periods and (year, month) not in periods:
            raise ValueError(
                f"Η περίοδος {month}/{year} δεν έχει ανοίξει ακόμη — ανοίγει "
                "μόλις ολοκληρωθεί η μισθοδοσία του προηγούμενου μήνα"
            )
        prev = self.pending_payroll_before(year, month)
        if prev is not None:
            raise ValueError(
                f"Εκκρεμεί η μισθοδοσία {prev.period_month}/{prev.period_year} — "
                "οι περίοδοι υπολογίζονται με τη σειρά"
            )
        # ΚΑΜΙΑ ΣΙΩΠΗΛΗ ΕΠΑΝΑΛΗΨΗ: ο επανυπολογισμός αντικαθιστά τα ποσά στο
        # ίδιο φυσικό κλειδί, οπότε χωρίς αυτόν τον έλεγχο θα απέκλιναν
        # αθόρυβα από ό,τι έχει ήδη υποβληθεί/πληρωθεί. Ο κανόνας ζει ΕΔΩ,
        # όχι στο view: το `POST /payroll/run` παρακάμπτει το `trigger`.
        done = self._obligation("MONTHLY_PAYROLL", year, month)
        if done is not None and done.fulfilled:
            raise ValueError(
                f"Η μισθοδοσία {month}/{year} έχει ήδη υπολογιστεί — άνοιξέ "
                "την για διόρθωση (Υποχρεώσεις) πριν ξαναϋπολογίσεις"
            )
        sheet = self.proj.attendance_sheets.get((year, month))
        if sheet is None:
            raise ValueError(
                f"Δεν έχει εγκριθεί το συγκεντρωτικό παρουσιών {month}/{year} — "
                "η μισθοδοσία ανοίγει μετά την έγκρισή του"
            )
        violations = self.wage_violations(year, month)
        if violations:
            raise ValueError("Παράβαση κατώτατου μισθού — " + " | ".join(violations))
        period_end = last_working_day_of_month(year, month)
        results = []
        for emp in self.proj.employments.values():
            if not emp.hire_submitted:
                continue
            # ΣΥΜΠΕΡΙΛΑΜΒΑΝΕΤΑΙ και όποιος αποχώρησε ΜΕΣΑ στον μήνα (πληρώνεται
            # για τις ημέρες που δούλεψε) — ίδιο κριτήριο με το attendance_draft.
            # (Το `active` αποκλείει ΚΑΘΕ αποχωρήσαντα — γι' αυτό ο μεσομηνιαία
            #  αποχωρών δεν πληρωνόταν, παρότι είχαν υπολογιστεί οι ημέρες του.)
            # ΑΝΑΛΗΨΗ, όχι πρόσληψη (μεταφορά από άλλο πρόγραμμα).
            if emp.payroll_start is None or emp.payroll_start > period_end:
                continue
            if emp.termination_date and emp.termination_date < date(year, month, 1):
                continue  # αποχώρησε ΠΡΙΝ την περίοδο — καμία μισθοδοσία
            # Οι ημέρες προέρχονται από το ΕΓΚΕΚΡΙΜΕΝΟ συγκεντρωτικό — όχι
            # απευθείας από παρουσίες/άδειες (μπορεί να έχει διορθωθεί).
            line = sheet.lines.get(emp.employee_id)
            if line is None:
                continue
            calc = self.payroll_calc_for(emp, year, month, line)
            if calc is None:
                continue  # καμία ημέρα ΕΦΚΑ -> καμία μισθοδοσία
            self._append_payroll(emp, year, month, calc)
            results.append((emp.employee_id, calc["result"]))
        return results

    @staticmethod
    def split_amount(total: float, weights) -> list:
        """Σπάει ένα ΣΥΝΟΛΟ σε μερίδια κατ' αναλογία, ΧΩΡΙΣ απώλεια λεπτών:
        το υπόλοιπο της στρογγυλοποίησης πάει στο ΠΡΩΤΟ μερίδιο, ώστε το
        άθροισμα να ισούται ΑΚΡΙΒΩΣ με το σύνολο.

        ΜΙΑ πηγή για κάθε επιμερισμό εισφορών (ανά κάλυψη, ανά γραμμή ΑΠΔ):
        τα υποβαλλόμενα αρχεία πρέπει να αθροίζουν στο ευρώ που πληρώθηκε."""
        weights = list(weights)
        if not weights:
            return []
        s = sum(weights)
        if not s:
            return [pe.r2(total)] + [0.0] * (len(weights) - 1)
        out = [pe.r2(total * w / s) for w in weights]
        out[0] = pe.r2(out[0] + (total - sum(out)))
        return out

    def contribution_lines(self, prof, on, total_employee, total_employer):
        """Οι ΥΠΟΛΟΓΙΣΜΕΝΕΣ εισφορές, σπασμένες ΑΝΑ ΚΑΛΥΨΗ (ταμείο/ΚΠΚ).

        Ο επιμερισμός γίνεται με τα ποσοστά της κάθε κάλυψης, αλλά τα ποσά
        αθροίζουν ΑΚΡΙΒΩΣ στα σύνολα του υπολογισμού — αυτά που θα δηλωθούν
        και θα καταβληθούν."""
        rows = self.contribution_breakdown(prof, on)
        emp_parts = self.split_amount(total_employee, [r["employee"] for r in rows])
        er_parts = self.split_amount(total_employer, [r["employer"] for r in rows])
        return [
            {
                "fund": r["fund"], "kpk": r["kpk"],
                "employee": e, "employer": g,
                "employee_rate": r["employee"], "employer_rate": r["employer"],
            }
            for r, e, g in zip(rows, emp_parts, er_parts, strict=True)
        ]

    def payroll_calc_for(self, emp, year: int, month: int, line: dict):
        """Ο υπολογισμός μισθοδοσίας ΕΝΟΣ εργαζόμενου για την περίοδο, **ΧΩΡΙΣ
        event** — ΜΙΑ πηγή αλήθειας για τον εταιρικό κύκλο
        (`run_monthly_payroll`), για τη μισθοδοσία της ΑΠΟΧΩΡΗΣΗΣ
        (`exit_payroll_preview`) και για τις προεπισκοπήσεις τους.

        `line`: η γραμμή του συγκεντρωτικού (εγκεκριμένου ή προσχεδίου).
        Επιστρέφει None όταν δεν υπάρχει καμία ημέρα ΕΦΚΑ."""
        period_end = last_working_day_of_month(year, month)
        unpaid, ins_days = self.payroll_days(emp, line)
        if ins_days == 0:
            return None
        sick = self.sick_deduction(emp, line, year, month)

        # Επιπλέον αμοιβές ωρών από το φύλλο (κλάση x ημέρα/νύχτα x
        # εργάσιμη/αργία) — γραμμές έτοιμες για απόδειξη
        wh_rules = config.work_hour_rules_for(period_end)
        hourly = work_hours.hourly_rate(
            emp.pay_type,
            emp.monthly_gross,
            emp.daily_wage,
            emp.weekly_hours,
        )
        premium = work_hours.pay_supplement(hourly, line, wh_rules)

        # Κρατήσεις από τα ΚΠΚ των καλύψεων της απασχόλησης (ΕΦΚΑ κύρια
        # + επικουρική + άλλα ταμεία) — βλ. contribution_rates().
        rates = self.contribution_rates(emp.insurance_profile, period_end)
        if emp.pay_type == "daily":
            res = pe.calc_monthly_daily(
                emp.daily_wage,
                ins_days,
                emp.children,
                period_end,
                sick=sick,
                premiums=premium["total"],
                rates=rates,
                birth_date=emp.birth_date,
            )
        else:
            res = pe.calc_monthly(
                emp.monthly_gross,
                emp.children,
                period_end,
                unpaid_days=unpaid,
                sick=sick,
                premiums=premium["total"],
                rates=rates,
                base_days=pe.monthly_insurance_days(emp.weekly_hours),
                birth_date=emp.birth_date,
            )

        # ΠΛΗΡΗΣ ΑΝΑΛΥΣΗ του υπολογισμού — η απόδειξη αποδοχών
        analysis = {
            "pay_type": emp.pay_type,
            "monthly_gross": emp.monthly_gross,
            "daily_wage": emp.daily_wage,
            "hourly_rate": hourly,
            "efka_days": ins_days,
            "unpaid_days": unpaid,
            "base_gross": res.base_gross,  # βάση μετά τις άνευ αποδοχών
            "sick_deduction": sick,
            "sick_half_days": line.get("sick_half", 0),
            "sick_benefit": line.get("sick_benefit", 0.0),
            "premium_lines": premium["lines"],  # ώρες/ποσοστό/ποσό ανά είδος
            "premium_total": premium["total"],
            "gross": res.gross,
            "efka_employee": res.efka_employee,
            "efka_employer": res.efka_employer,
            "taxable": res.taxable,
            "fmy": res.fmy,
            "net": res.net,
            # ΤΑ ΠΟΣΟΣΤΑ ΠΟΥ ΧΡΗΣΙΜΟΠΟΙΗΘΗΚΑΝ — χωρίς αυτά ο υπολογισμός δεν
            # είναι ανιχνεύσιμος: το event κρατά ευρώ, και μια εκ των υστέρων
            # αλλαγή ΚΠΚ γίνεται αόρατη (βλ. `contribution_drift`).
            "rates": {"employee": rates.employee, "employer": rates.employer},
            # ΟΙ ΕΙΣΦΟΡΕΣ ΑΝΑ ΚΑΛΥΨΗ, σε ευρώ: από εδώ τις παίρνει η ΑΠΔ —
            # δεν τις ξαναϋπολογίζει. Αθροίζουν ακριβώς στα σύνολα πάνω.
            "contributions": self.contribution_lines(
                emp.insurance_profile, period_end,
                res.efka_employee, res.efka_employer,
            ),
        }
        return {
            "result": res,
            "analysis": analysis,
            "unpaid": unpaid,
            "efka_days": ins_days,
            "sick": sick,
            "premium": premium,
        }

    def _append_payroll(self, emp, year, month, calc, exit_settlement=False):
        """Γράφει το `MonthlyPayrollCalculated` ενός υπολογισμού.

        `exit_settlement`: η μισθοδοσία αφορά ΜΟΝΟ τον αποχωρούντα — δεν
        κλείνει την υποχρέωση της περιόδου ούτε ανοίγει τον επόμενο μήνα
        (scheduler), αφού οι υπόλοιποι δεν έχουν υπολογιστεί."""
        res = calc["result"]
        self.store.append(
            emp.employee_id,
            MonthlyPayrollCalculated(
                emp.employee_id,
                year,
                month,
                res.gross,
                res.efka_employee,
                res.efka_employer,
                res.taxable,
                res.fmy,
                res.net,
                calc["unpaid"],
                calc["efka_days"],
                emp.daily_wage,
                calc["sick"],
                calc["premium"]["total"],
                calc["analysis"],
                exit_settlement,
            ),
        )

    # ------------------------------------- Μισθοδοσία ΑΠΟΧΩΡΗΣΗΣ (ενός)
    def exit_payroll_preview(self, employment_id, term=None):
        """Η μισθοδοσία του ΜΗΝΑ ΑΠΟΧΩΡΗΣΗΣ για **ΕΝΑΝ** εργαζόμενο, χωρίς
        event — ΜΙΑ πηγή αλήθειας για την προεπισκόπηση του modal και για την
        εκκαθάριση (`_settle_exit_accruals`).

        Ο εργαζόμενος πληρώνεται στην αποχώρηση για τις ημέρες που δούλεψε
        μέσα στον τρέχοντα μήνα, ΧΩΡΙΣ να περιμένει το τέλος του μήνα και την
        έγκριση του συγκεντρωτικού ΟΛΩΝ: οι ημέρες ΕΦΚΑ έρχονται από το
        εγκεκριμένο φύλλο αν υπάρχει, αλλιώς από το **προσχέδιο**
        (`attendance_draft` — ήδη pro-rata στην αποχώρηση, §7).

        Επιστρέφει `{"year","month","payroll","blocked","already"}`. Το
        `blocked` εξηγεί γιατί ΔΕΝ μπορεί να υπολογιστεί τώρα (π.χ. εκκρεμεί
        προηγούμενος μήνας) — η ΑΠΟΧΩΡΗΣΗ δεν ματαιώνεται γι' αυτό: η
        μισθοδοσία θα γίνει στον τακτικό κύκλο."""
        emp = self.proj.employments.get(employment_id)
        if emp is None:
            raise ValueError(f"Άγνωστη απασχόληση {employment_id}")
        term = term or emp.termination_date
        if term is None:
            raise ValueError("Η μισθοδοσία αποχώρησης απαιτεί ημερομηνία λήξης")
        if emp.termination_date != term:
            # ΥΠΟΘΕΤΙΚΗ αποχώρηση (προεπισκόπηση πριν την καταχώρηση): αντίγραφο
            # της κατάστασης «σαν να είχε λήξει τότε», ώστε πρόγραμμα/άδειες/
            # ημέρες ΕΦΚΑ να κοπούν ΑΚΡΙΒΩΣ όπως μετά την αποχώρηση — αλλιώς η
            # προεπισκόπηση θα έδειχνε ολόκληρο τον μήνα.
            emp = copy(emp)
            emp.termination_date = term
        year, month = term.year, term.month
        out = {
            "year": year,
            "month": month,
            "payroll": None,
            "blocked": "",
            "already": any(
                p.employee_id == employment_id
                and (p.period_year, p.period_month) == (year, month)
                for p in self.proj.payrolls
            ),
        }
        if out["already"]:
            return out
        if emp.payroll_start is None or emp.payroll_start > last_working_day_of_month(
            year, month
        ):
            # Μεταφορά που δεν έχει αναληφθεί ακόμη (§2): καμία μισθοδοσία εδώ
            out["blocked"] = "Η μισθοδοσία δεν έχει αναληφθεί για αυτή την περίοδο"
            return out

        try:
            self._require_parameters()
        except ValueError as exc:
            out["blocked"] = str(exc)
            return out

        periods = {
            (o.period_year, o.period_month)
            for o in self.proj.obligations.values()
            if o.obligation_type == "MONTHLY_PAYROLL"
        }
        if periods and (year, month) not in periods:
            out["blocked"] = (
                f"Η περίοδος {month}/{year} δεν έχει ανοίξει ακόμη — ανοίγει "
                "μόλις ολοκληρωθεί η μισθοδοσία του προηγούμενου μήνα"
            )
            return out
        prev = self.pending_payroll_before(year, month)
        if prev is not None:
            out["blocked"] = (
                f"Εκκρεμεί η μισθοδοσία {prev.period_month}/{prev.period_year} — "
                "οι προηγούμενοι μήνες πρέπει να έχουν κλείσει πρώτα"
            )
            return out
        violation = self.wage_violation_for(emp, last_working_day_of_month(year, month))
        if violation:
            out["blocked"] = "Παράβαση κατώτατου μισθού — " + violation
            return out

        # ΟΙ ΗΜΕΡΕΣ ΜΕΤΡΟΥΝ ΜΕΧΡΙ ΚΑΙ ΤΗΝ ΗΜΕΡΟΜΗΝΙΑ ΑΠΟΧΩΡΗΣΗΣ, ΟΧΙ ΟΛΟΣ Ο
        # ΜΗΝΑΣ: το `emp` εδώ κουβαλά τη λήξη, οπότε το προσχέδιο κόβεται
        # pro-rata (§7). ⚠️ Αν υπάρχει ΕΓΚΕΚΡΙΜΕΝΟ φύλλο, εγκρίθηκε για ΟΛΟΝ
        # τον μήνα χωρίς να γνωρίζει την αποχώρηση — κρατάμε τις χειροκίνητες
        # διορθώσεις του (ασθένεια, ώρες) αλλά οι ημέρες ΕΦΚΑ δεν επιτρέπεται
        # να ξεπερνούν τις pro-rata (η αποχώρηση μόνο μειώνει).
        draft = self.attendance_draft_line(emp, year, month)
        sheet = self.proj.attendance_sheets.get((year, month))
        sheet_line = sheet.lines.get(employment_id) if sheet else None
        if sheet_line is None:
            line = draft
        else:
            line = dict(sheet_line)
            line["efka_days"] = min(
                int(line.get("efka_days", 0)), int(draft["efka_days"])
            )
        calc = self.payroll_calc_for(emp, year, month, line)
        if calc is None:
            out["blocked"] = "Καμία ημέρα ασφάλισης στον μήνα της αποχώρησης"
            return out
        out["payroll"] = calc
        return out
