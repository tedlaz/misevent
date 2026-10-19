"""
Ημέρες μισθοδοσίας, ασθένεια, κανονική άδεια, αποζημιώσεις αποχώρησης

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

from datetime import date
from math import ceil, floor

import misevent.domain.payroll_engine as pe
import misevent.domain.work_hours as work_hours
import misevent.rules.config as config
from misevent.domain.events import LeaveCompensationCalculated, LeaveType, SeveranceCalculated

from ._base import (
    LONG_SERVICE_ANY_EMPLOYER_YEARS,
    LONG_SERVICE_LEAVE_DAYS,
    LONG_SERVICE_SAME_EMPLOYER_YEARS,
)


class EntitlementsMixin:
    @staticmethod
    def payroll_days(emp, line: dict) -> tuple[int, int]:
        """(ημέρες άνευ αποδοχών, ημέρες ασφάλισης) της μισθοδοσίας.

        Πηγή αλήθειας: οι **ημέρες ΕΦΚΑ** του εγκεκριμένου συγκεντρωτικού.
        Ο μισθωτός πληρώνεται `μισθός x ημέρες ΕΦΚΑ / συμβατική βάση` (25 σε
        πλήρη απασχόληση, οι ημέρες προγράμματος του μήνα σε μερική —
        `pe.monthly_insurance_days`),
        ο ημερομίσθιος `ημερομίσθιο x ημέρες ΕΦΚΑ`.

        """
        efka = int(line["efka_days"])
        if emp.pay_type == "daily":
            return 0, efka
        base = pe.monthly_insurance_days(emp.weekly_hours, line.get("month_planned"))
        return max(base - efka, 0), efka

    @staticmethod
    def sick_deduction(emp, line: dict, year: int, month: int) -> float:
        """Η περικοπή μικτών λόγω ασθένειας, από το ΕΓΚΕΚΡΙΜΕΝΟ συγκεντρωτικό.

        Αν το φύλλο ΔΕΝ έχει καθόλου δεδομένα ασθενείας, υπολογίζονται εκ νέου
        από τις άδειες. Χωρίς αυτό η ασθένεια θα αγνοούνταν σιωπηλά σε δύο
        περίπτωση άδειας ασθενείας που καταχωρήθηκε ΜΕΤΑ την έγκριση του φύλλου.

        Φύλλο με έστω ένα μη μηδενικό πεδίο θεωρείται ρητή δήλωση του χρήστη
        και ΥΠΕΡΙΣΧΥΕΙ (μπορεί να το έχει διορθώσει χειροκίνητα)."""
        half = int(line.get("sick_half", 0))
        full = int(line.get("sick_full", 0))
        benefit = float(line.get("sick_benefit", 0.0))
        if not half and not full and not benefit:
            sick = emp.sick_days_in_month(year, month)
            half, benefit = sick["half"], sick["benefit"]
        if not half and not benefit:
            return 0.0
        return pe.sick_deduction(EntitlementsMixin._sick_daily_rate(emp), half, benefit)

    @staticmethod
    def _sick_daily_rate(emp) -> float:
        """Το ημερομίσθιο με το οποίο αποτιμώνται οι ημέρες ασθενείας — ΜΙΑ
        πηγή αλήθειας για την περικοπή (`sick_deduction`) ΚΑΙ για τις
        «Αποδοχές ασθενείας» της ΑΠΔ (`sickness_apd_lines`)."""
        if emp.pay_type == "daily":
            return emp.daily_wage
        if emp.weekly_hours and emp.weekly_hours < 40:
            # Μερική απασχόληση: ημερομίσθιο = ωρομίσθιο (ίδιος τύπος με την
            # πλήρη) x μέσες συμβατικές ώρες ημέρας προγράμματος.
            hourly = work_hours.hourly_rate(
                emp.pay_type, emp.monthly_gross, emp.daily_wage, emp.weekly_hours
            )
            return hourly * emp.weekly_hours / emp.scheduled_days_per_week()
        return emp.monthly_gross / 25.0

    def sick_totals(self, emp, year: int, month: int) -> tuple[int, int, float]:
        """(τριήμερο, κανονικές, επίδομα ΕΦΚΑ) του μήνα, με την ΙΔΙΑ
        προτεραιότητα που εφαρμόζει η μισθοδοσία: το ΕΓΚΕΚΡΙΜΕΝΟ φύλλο
        υπερισχύει· αν δεν έχει κανένα δεδομένο ασθενείας, υπολογίζονται εκ
        νέου από τις άδειες (βλ. `sick_deduction`)."""
        sheet = self.proj.attendance_sheets.get((year, month))
        line = (sheet.lines.get(emp.employee_id) or {}) if sheet else {}
        half = int(line.get("sick_half", 0))
        full = int(line.get("sick_full", 0))
        benefit = float(line.get("sick_benefit", 0.0))
        if not half and not full and not benefit:
            s = emp.sick_days_in_month(year, month)
            half, full, benefit = s["half"], s["full"], s["benefit"]
        return half, full, benefit

    def sickness_apd_lines(self, emp, year: int, month: int) -> list[dict]:
        """Οι γραμμές **«008 Αποδοχές ασθενείας»** της περιόδου — ΜΙΑ ανά άδεια
        ασθενείας: `days` = οι ΠΛΗΡΩΜΕΝΕΣ ημέρες ασθενείας του μήνα (τριήμερο +
        κανονικές· οι `over` είναι άνευ αποδοχών), `date_from`/`date_to` =
        έναρξη/λήξη της ασθένειας μέσα στον μήνα, `gross` = ό,τι πλήρωσε
        πράγματι ο εργοδότης γι' αυτές.

        Οι αποδοχές ασθενείας **ΑΦΑΙΡΟΥΝΤΑΙ από τις τακτικές (001)** — μαζί με
        τις ημέρες τους: το άθροισμα 001+008 ισούται πάντα με τα μικτά και τις
        ημέρες ασφάλισης της μισθοδοσίας. Ποσό = ημερομίσθιο × (ημέρες) μείον
        την περικοπή του `pe.sick_deduction` (μισό ημερομίσθιο στο τριήμερο,
        μείον το επίδομα ΕΦΚΑ) — ΜΙΑ πηγή αλήθειας με τη μισθοδοσία.

        Κενή λίστα όταν δεν υπάρχουν καταχωρημένες άδειες ασθενείας του μήνα:
        χωρίς άδεια δεν υπάρχει διάστημα «από–έως» να δηλωθεί, οπότε τα ποσά
        μένουν στις τακτικές αποδοχές (π.χ. φύλλο συμπληρωμένο χειροκίνητα)."""
        from calendar import monthrange
        from datetime import date as _d

        half, full, benefit = self.sick_totals(emp, year, month)
        if half + full <= 0:
            return []
        daily = self._sick_daily_rate(emp)
        total = pe.r2(daily * (half + full) - pe.sick_deduction(daily, half, benefit))
        if total <= 0:
            return []
        # Ημέρες & βάρη ανά άδεια, από την κατάταξη των ημερών του μήνα
        # (`half` μισό ημερομίσθιο, `full` ολόκληρο, `over` καθόλου).
        groups = {}
        for d, (cls, lid) in emp.sick_day_map().items():
            if d.year != year or d.month != month or cls == "over":
                continue
            g = groups.setdefault(lid, {"days": 0, "weight": 0.0})
            g["days"] += 1
            g["weight"] += 0.5 if cls == "half" else 1.0
        if not groups:
            return []
        first, last = _d(year, month, 1), _d(year, month, monthrange(year, month)[1])
        leaves = {lv.leave_id: lv for lv in emp.active_leaves()}
        out = []
        for lid, g in groups.items():
            lv = leaves.get(lid)
            out.append(
                {
                    "leave_id": lid,
                    "days": g["days"],
                    "weight": g["weight"],
                    # Το διάστημα της ΑΠΔ είναι ο μήνας: η άδεια που «κόβεται» από
                    # τον μήνα δηλώνεται στο κομμάτι της που πέφτει εδώ.
                    "date_from": max(lv.date_from, first) if lv else first,
                    "date_to": min(lv.date_to, last) if lv else last,
                    "gross": 0.0,
                }
            )
        out.sort(key=lambda r: r["date_from"])
        # Κατανομή του ΣΥΝΟΛΟΥ αναλογικά προς τα βάρη — το υπόλοιπο
        # στρογγυλοποίησης στην τελευταία, ώστε το άθροισμα να είναι ακριβές.
        wsum = sum(r["weight"] for r in out) or 1.0
        acc = 0.0
        for r in out[:-1]:
            r["gross"] = pe.r2(total * r["weight"] / wsum)
            acc = pe.r2(acc + r["gross"])
        out[-1]["gross"] = pe.r2(total - acc)
        return [r for r in out if r["gross"] > 0 or r["days"] > 0]

    # ------------------------------------------------- Κανονικές άδειες
    def annual_leave_entitlement(self, employee_id, year: int) -> int:
        """Δικαιούμενες εργάσιμες ημέρες κανονικής άδειας (πενθήμερο):
        1ο ημερολογιακό έτος αναλογία 20 ημερών, 2ο έτος 21, 3ο+ 22 — και
        **25** με μακρά υπηρεσία (βλ. `_long_service_leave`).

        ⚠️ Η ΜΑΚΡΑ ΥΠΗΡΕΣΙΑ ΔΕΝ ΠΕΡΙΜΕΝΕΙ ΤΟ 3ο ΕΤΟΣ: μετρά ΣΥΝΟΛΙΚΗ
        προϋπηρεσία, οπότε κάποιος που προσλαμβάνεται έχοντας ήδη 12 έτη
        αλλού δικαιούται την κλίμακα των 25 από την πρώτη μέρα — στο πρώτο
        ημερολογιακό έτος **αναλογικά προς τον μήνα πρόσληψης** (πρόσληψη
        Σεπτεμβρίου → 25 × 4/12 → 9 ημέρες, όχι 20 × 4/12 → 7)."""
        emp = self.proj.employments[employee_id]
        if emp.hire_date is None or emp.hire_date.year > year:
            return 0
        service_years = year - emp.hire_date.year
        if self._long_service_leave(emp, year):
            base = LONG_SERVICE_LEAVE_DAYS
        else:
            base = {0: 20, 1: 21}.get(service_years, 22)
        if service_years == 0:
            months = 12 - emp.hire_date.month + 1
            return ceil(base * months / 12)
        return base

    @staticmethod
    def _long_service_leave(emp, year: int) -> bool:
        """Δικαίωμα **25** ημερών (αντί 20/21/22) λόγω μακράς υπηρεσίας: **12 έτη
        ΣΥΝΟΛΙΚΗΣ** εργασίας σε οποιονδήποτε εργοδότη **Ή 10 έτη ΣΤΟΝ ΙΔΙΟ**.

        Ο συνολικός χρόνος είναι ο ΠΡΑΓΜΑΤΙΚΟΣ χρόνος εργασίας — δηλωθείσα
        προϋπηρεσία + **παγωμένος χρόνος 2012-2023** + χρόνος εδώ
        (`EmploymentState.full_service_years`): το πάγωμα αφορά ΜΟΝΟ τις
        τριετίες κατώτατου, όχι την άδεια (§7). Η συμπλήρωση κρίνεται στο
        ΤΕΛΟΣ του έτους αναφοράς (ή στην αποχώρηση, αν προηγείται): όποιος
        συμπληρώνει μέσα στο έτος, το δικαιούται για ΟΛΟ το έτος."""
        on = date(year, 12, 31)
        if emp.termination_date and emp.termination_date < on:
            on = emp.termination_date
        if emp.full_service_years(on) >= LONG_SERVICE_ANY_EMPLOYER_YEARS:
            return True
        same = (on - emp.hire_date).days / 365.25
        return same >= LONG_SERVICE_SAME_EMPLOYER_YEARS

    def annual_leave_taken(self, employee_id, year: int) -> int:
        """Εργάσιμες ημέρες κανονικής άδειας που έχουν προγραμματιστεί
        εντός του έτους."""
        from datetime import date as _d

        from misevent.rules.calendar_gr import working_days_in_range

        emp = self.proj.employments[employee_id]
        total = 0
        for lv in emp.active_leaves():
            if lv.leave_type != LeaveType.ANNUAL:
                continue
            a = max(lv.date_from, _d(year, 1, 1))
            b = min(lv.date_to, _d(year, 12, 31))
            if a <= b:
                total += working_days_in_range(a, b)
        return total

    def annual_leave_accrued(self, employee_id, on) -> int:
        """Σωρευμένο δικαίωμα κανονικής άδειας έως την ``on``.

        Στο πρώτο έτος το συνολικό δικαίωμα είναι ``ceil(μήνες απασχόλησης ×
        ετήσιο / 12)`` και μοιράζεται αναλογικά, σε ακέραιες σωρευμένες
        ημέρες, στους εναπομένοντες μήνες. Στο δεύτερο έτος μοιράζονται
        αντίστοιχα οι 21 (ή 25 με μακρά υπηρεσία) στους 12 μήνες. Η ενδιάμεση
        στρογγυλοποίηση είναι half-up, ώστε η τελευταία περίοδος να καταλήγει
        ακριβώς στο συνολικό δικαίωμα. Από το τρίτο έτος το πλήρες δικαίωμα
        (22 ημέρες, ή 25 με μακρά υπηρεσία) είναι διαθέσιμο από την πρώτη
        ημέρα του έτους.
        """
        emp = self.proj.employments[employee_id]
        if emp.hire_date is None or on < emp.hire_date:
            return 0
        year = on.year
        full = self.annual_leave_entitlement(employee_id, year)
        service_year = year - emp.hire_date.year
        if service_year >= 2:
            return full

        effective = min(on, emp.termination_date) if emp.termination_date else on
        first_month = emp.hire_date.month if service_year == 0 else 1
        periods = 13 - first_month
        elapsed_periods = effective.month - first_month + 1
        if elapsed_periods <= 0:
            return 0
        # Ακέραιη, αναλογική σωρευμένη κατανομή με εμπορική στρογγυλοποίηση.
        accrued = floor(full * min(elapsed_periods, periods) / periods + 0.5)
        return min(full, accrued)

    def annual_leave_taken_until(self, employee_id, on, exclude_leave_id=None) -> int:
        """Εργάσιμες ημέρες κανονικής άδειας του έτους έως την ``on``."""
        from datetime import date as _d

        from misevent.rules.calendar_gr import working_days_in_range

        emp = self.proj.employments[employee_id]
        total = 0
        for lv in emp.active_leaves():
            if lv.leave_type != LeaveType.ANNUAL or lv.leave_id == exclude_leave_id:
                continue
            start = max(lv.date_from, _d(on.year, 1, 1))
            end = min(lv.date_to, on)
            if start <= end:
                total += working_days_in_range(start, end)
        return total

    def plan_annual_leave(self, employee_id, date_from, date_to, notes=""):
        """Προγραμματίζει κανονική άδεια με έλεγχο υπολοίπου."""
        from misevent.rules.calendar_gr import working_days_in_range

        emp = self.proj.employments.get(employee_id)
        if emp is None or emp.hire_date is None:
            raise ValueError(f"Άγνωστος εργαζόμενος {employee_id}")
        if date_from < emp.hire_date:
            raise ValueError("Η άδεια προηγείται της πρόσληψης")
        days = working_days_in_range(date_from, date_to)
        if days == 0:
            raise ValueError("Το διάστημα δεν περιέχει εργάσιμες ημέρες")
        year = date_from.year
        ent = self.annual_leave_entitlement(employee_id, year)
        taken = self.annual_leave_taken(employee_id, year)
        if taken + days > ent:
            raise ValueError(
                f"Υπέρβαση δικαιώματος αδείας: ζητούνται {days} ημέρες, "
                f"υπόλοιπο {ent - taken} από {ent} για το {year}"
            )
        self.record_leave(employee_id, LeaveType.ANNUAL, date_from, date_to, notes)
        return {"days": days, "remaining": ent - taken - days, "entitlement": ent}

    def leave_compensation_preview(self, employment_id, term=None):
        """Ανάλυση της αποζημίωσης μη ληφθείσας αδείας (χωρίς event) — ή None
        αν δεν οφείλεται. Δικαίωμα: 1ο/2ο έτος υπηρεσίας αναλογικά 2 ημέρες
        ανά μήνα υπηρεσίας στο έτος αποχώρησης (cap το ετήσιο), 3ο έτος+
        ολόκληρο το ετήσιο δικαίωμα. Υπόλοιπο = δικαίωμα − ληφθείσες του έτους·
        ημέρες αποζημίωσης = υπόλοιπο × 1,2 (πενθήμερο→εξαήμερο· εξαήμερο ×1)·
        ποσό = ημέρες × ημερήσια αμοιβή (μισθός/25 ή ημερομίσθιο), ΜΟΝΟ ΦΜΥ.

        Το `term` είναι ΥΠΟΘΕΤΙΚΗ ημ. αποχώρησης (προεπισκόπηση πριν την
        καταχώρησή της)."""
        emp = self.proj.employments.get(employment_id)
        if emp is None or emp.hire_date is None:
            raise ValueError(f"Άγνωστη απασχόληση {employment_id}")
        term = term or emp.termination_date
        if term is None:
            raise ValueError("Η αποζημίωση αδείας υπολογίζεται μόνο μετά την αποχώρηση")
        year = term.year
        full = self.annual_leave_entitlement(employment_id, year)
        if pe.is_third_service_year(year, emp.hire_date):
            # 3ο έτος υπηρεσίας+: δικαιούται ολόκληρο το ετήσιο δικαίωμα
            ent = full
        else:
            # 1ο/2ο έτος: αναλογικά 2 ημέρες ανά μήνα υπηρεσίας στο έτος
            # (από την πρόσληψη/αρχή έτους έως την αποχώρηση), cap το ετήσιο
            months = pe._allowance_months(year, emp.hire_date, term)
            ent = min(2 * months, full)
        leave_days = ent - self.annual_leave_taken(employment_id, year)
        if leave_days <= 0:
            return None
        factor = 1.0 if emp.scheduled_days_per_week() >= 6 else 1.2
        comp_days = round(leave_days * factor, 2)
        daily = emp.daily_wage if emp.pay_type == "daily" else emp.monthly_gross / 25.0
        gross = round(comp_days * daily, 2)
        fmy = pe.monthly_fmy(gross, emp.children, term, emp.birth_date)
        return {
            "leave_days": leave_days,
            "comp_days": comp_days,
            "daily": round(daily, 2),
            "gross": gross,
            "fmy": fmy,
            "net": round(gross - fmy, 2),
        }

    # -------------------------------------------- Αποζημίωση απόλυσης
    def severance_preview(self, employment_id, with_notice=False, term=None):
        """Ανάλυση αποζημίωσης απόλυσης (χωρίς event) — ή None αν δεν
        οφείλεται (<1 έτος). Μήνες από τον πίνακα ανά χρόνο υπηρεσίας ΣΤΟΝ
        ΕΡΓΟΔΟΤΗ (πρόσληψη→απόλυση — ΟΧΙ η δηλωθείσα προϋπηρεσία, ΜΕ
        προειδοποίηση = μισό) × μηνιαίες αποδοχές × 7/6· αυτοτελής φόρος,
        καμία ασφαλιστική κράτηση.

        Το `term` είναι ΥΠΟΘΕΤΙΚΗ ημ. αποχώρησης (προεπισκόπηση πριν την
        καταχώρησή της)."""
        emp = self.proj.employments.get(employment_id)
        if emp is None or emp.hire_date is None:
            raise ValueError(f"Άγνωστη απασχόληση {employment_id}")
        term = term or emp.termination_date
        if term is None:
            raise ValueError("Η αποζημίωση απόλυσης υπολογίζεται μόνο σε αποχώρηση")
        # Η αποζημίωση απόλυσης μετρά ΜΟΝΟ τον χρόνο στον συγκεκριμένο
        # εργοδότη (πρόσληψη→απόλυση), όχι τη δηλωθείσα προϋπηρεσία αλλού.
        years = (term - emp.hire_date).days / 365.25
        # ⚠️ ΟΙ ΚΑΝΟΝΕΣ ΤΗΣ ΗΜΕΡΟΜΗΝΙΑΣ ΑΠΟΛΥΣΗΣ, όχι οι σημερινοί: ο πίνακας
        # μηνών και η κλίμακα φόρου είναι πλέον χρονικά κλιμακωτή παράμετρος
        # (οικογένεια «severance»), οπότε ένας μελλοντικός νόμος ΔΕΝ αλλάζει
        # αναδρομικά μια αποζημίωση που ήδη υπολογίστηκε.
        months = config.severance_months(years, with_notice, on=term)
        if months <= 0:
            return None
        monthly = (
            emp.monthly_gross
            if emp.pay_type != "daily"
            else round(emp.daily_wage * 25.0, 2)
        )
        gross = round(months * monthly * config.severance_gift_factor(term), 2)
        fmy = config.severance_tax(gross, on=term)
        return {
            "service_years": round(years, 2),
            "months": months,
            "with_notice": with_notice,
            "monthly_salary": monthly,
            "gross": gross,
            "fmy": fmy,
            "net": round(gross - fmy, 2),
        }

    def run_severance(self, employment_id, with_notice=False):
        """Υπολογίζει & καταγράφει την αποζημίωση απόλυσης
        (SeveranceCalculated) — ξεχωριστή εκκαθάριση, χωρίς ασφαλιστικά."""
        a = self.severance_preview(employment_id, with_notice)
        if a is None:
            raise ValueError("Δεν οφείλεται αποζημίωση απόλυσης (προϋπηρεσία < 1 έτος)")
        emp = self.proj.employments[employment_id]
        self.store.append(
            employment_id,
            SeveranceCalculated(
                employment_id,
                emp.termination_date,
                a["service_years"],
                a["months"],
                with_notice,
                a["monthly_salary"],
                a["gross"],
                a["fmy"],
                a["net"],
            ),
        )
        return a

    def severance(self, employment_id):
        """Η καταγεγραμμένη αποζημίωση απόλυσης (event) ή None."""
        return next(
            (s for s in self.proj.severances if s.employee_id == employment_id),
            None,
        )

    def leave_compensation(self, employment_id):
        """Η καταγεγραμμένη αποζημίωση αδείας της απασχόλησης (event) ή None."""
        return next(
            (
                lc
                for lc in self.proj.leave_compensations
                if lc.employee_id == employment_id
            ),
            None,
        )

    def run_leave_compensation(self, employment_id):
        """Υπολογίζει & καταγράφει την αποζημίωση μη ληφθείσας αδείας
        (LeaveCompensationCalculated) — ξεχωριστή «μισθοδοσία», ΜΟΝΟ ΦΜΥ."""
        a = self.leave_compensation_preview(employment_id)
        if a is None:
            raise ValueError("Δεν υπάρχει υπόλοιπο κανονικής άδειας προς αποζημίωση")
        emp = self.proj.employments[employment_id]
        self.store.append(
            employment_id,
            LeaveCompensationCalculated(
                employment_id,
                emp.termination_date,
                emp.termination_date.year,
                a["leave_days"],
                a["comp_days"],
                a["gross"],
                a["fmy"],
                a["net"],
            ),
        )
        return a
