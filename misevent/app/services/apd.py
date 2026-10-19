"""
ΑΠΔ: γραμμές ανά κάλυψη, σύνολα, παραγωγή αρχείου CSL01

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

from datetime import date as _date

import misevent.domain.payroll_engine as pe
import misevent.rules.config as config
from misevent.domain.events import APDFileGenerated, APDSubmitted
from misevent.domain.projections import profile_coverages
from misevent.integrations.apd_files import (
    APDFileBuilder,
    EmployerInfo,
    InsuranceRow,
    InsuredInfo,
    SubsidyRow,
)
from misevent.rules.calendar_gr import last_working_day_of_month

from ._base import ApdEarnings


class ApdMixin:
    # -------------------------------------------- Υποβολές & πληρωμές
    def submit_apd(self, year: int, month: int, fund="efka", reference=""):
        """Καταγράφει την υποβολή ΑΠΔ (μετά το ανέβασμα του CSL01)."""
        totals = self.apd_fund_totals(year, month)
        if fund not in totals:
            raise ValueError(
                f"Δεν υπάρχουν εγγραφές {fund.upper()} για την περίοδο {month}/{year}"
            )
        total = totals[fund]["contrib"]
        self.store.append("COMPANY", APDSubmitted(fund, year, month, total, reference))
        return total

    def payroll_earnings_split(self, payroll) -> list:
        """Πώς σπάει ΜΙΑ μηνιαία μισθοδοσία σε αμοιβές της ΑΠΔ:
        **001 Τακτικές** + μία **008 Αποδοχές ασθενείας** ανά ασθένεια (με τις
        ημέρες και το διάστημά της). Οι αποδοχές/ημέρες ασθενείας ΑΦΑΙΡΟΥΝΤΑΙ
        από τις τακτικές, ώστε το άθροισμα να ισούται πάντα με τα μικτά και τις
        ημέρες ασφάλισης του υπολογισμού.

        Κάθε αμοιβή κουβαλά ΚΑΙ ΤΙΣ ΕΙΣΦΟΡΕΣ ΤΗΣ (`contrib`), σπασμένες από
        τα ΥΠΟΛΟΓΙΣΜΕΝΑ σύνολα του event — η ΑΠΔ δεν τις ξαναϋπολογίζει.

        ΜΙΑ πηγή αλήθειας: τη χρησιμοποιούν και το αρχείο ΑΠΔ
        (`_apd_rows_for_period`) και η «Ανάλυση μισθοδοσίας» του UI."""
        y, m = payroll.period_year, payroll.period_month
        emp = self.proj.employments.get(payroll.employee_id)
        sick = self.sickness_apd_lines(emp, y, m) if emp else []
        sick_days = sum(s["days"] for s in sick)
        sick_gross = pe.r2(sum(s["gross"] for s in sick))
        if sick_gross > payroll.gross or sick_days > payroll.insurance_days:
            sick, sick_days, sick_gross = [], 0, 0.0  # ασφαλής υποχώρηση
        # Οι προσαυξήσεις ΧΩΡΙΣ εισφορές ΔΕΝ δηλώνονται (Εγκ. e-ΕΦΚΑ 21/2025):
        # η ΑΠΔ γράφει μόνο το ποσό που έχει εισφορές.
        exempt = (payroll.analysis or {}).get("premium_exempt", 0.0)
        out = [
            ApdEarnings(
                "001",
                pe.r2(payroll.gross - sick_gross - exempt),
                payroll.insurance_days - sick_days,
            )
        ]
        out += [
            ApdEarnings("008", s["gross"], s["days"], s["date_from"], s["date_to"])
            for s in sick
        ]
        return self._with_contributions(out, payroll, emp, y, m)

    def _with_contributions(self, rows: list, payroll, emp, year: int, month: int):
        """Μοιράζει τις ΥΠΟΛΟΓΙΣΜΕΝΕΣ εισφορές του μήνα στις αμοιβές του.

        Η μισθοδοσία κρατά ΕΝΑ ζεύγος συνόλων για τον μήνα και (από την
        ανάλυσή της) το σπάσιμο ανά κάλυψη· εδώ κατανέμεται επιπλέον στις
        γραμμές (τακτικές/ασθενείας) κατ' αναλογία των αποδοχών. Το άθροισμα
        μένει ΑΚΡΙΒΩΣ ίσο με ό,τι υπολογίστηκε — και θα καταβληθεί."""
        lines = (payroll.analysis or {}).get("contributions")
        if not lines:
            # ΠΑΛΙΑ events (χωρίς ανάλυση ανά κάλυψη): κρατάμε ΤΑ ΣΥΝΟΛΑ του
            # υπολογισμού και τα σπάμε με τα ποσοστά που ισχύουν σήμερα.
            if emp is None or emp.insurance_profile is None:
                return rows
            lines = self.contribution_lines(
                emp.insurance_profile, last_working_day_of_month(year, month),
                payroll.efka_employee, payroll.efka_employer,
            )
        grosses = [r.gross for r in rows]
        per_row = [{} for _ in rows]
        for ln in lines:
            emp_parts = self.split_amount(ln["employee"], grosses)
            er_parts = self.split_amount(ln["employer"], grosses)
            for i, cell in enumerate(per_row):
                # Η επιδότηση (σταθερό ποσό) πάει ΟΛΗ στις τακτικές (1η γραμμή)
                sub = ln.get("subsidy", 0.0) if i == 0 else 0.0
                cell[(ln["fund"], ln["kpk"])] = (emp_parts[i], er_parts[i], sub)
        return [r._replace(contrib=c) for r, c in zip(rows, per_row, strict=True)]

    def _extra_contributions(self, emp, gross, efka_employee, efka_employer, on,
                             subsidy=0.0):
        """Εισφορές μιας ΕΚΤΑΚΤΗΣ αμοιβής (Δώρο/Επίδομα Αδείας) ανά κάλυψη.

        Το εργοδοτικό σκέλος λείπει από τα ΠΑΛΙΑ events (γράφτηκε αργότερα):
        τότε ανασυντίθεται από τα ποσοστά της ημέρας καταβολής — ίδια λογική
        με το `_gift_cost` της ετήσιας αναφοράς."""
        if emp is None or emp.insurance_profile is None:
            return None
        if not efka_employer:
            rates = self.contribution_rates(emp.insurance_profile, on)
            efka_employer = pe.r2(gross * rates.employer)
        return {
            (ln["fund"], ln["kpk"]): (ln["employee"], ln["employer"], ln.get("subsidy", 0.0))
            for ln in self.contribution_lines(
                emp.insurance_profile, on, efka_employee, efka_employer, subsidy)
        }

    # ------------------------------------------- Παραγωγή αρχείων ΑΠΔ
    def _apd_rows_for_period(self, year: int, month: int):
        """Αποδοχές που περιλαμβάνονται στο αρχείο ΑΠΔ της περιόδου:
        τακτικές του μήνα + Δώρα/Επίδομα Αδείας του προηγούμενου μήνα
        (ενιαίο αρχείο ανά ταμείο, όχι ξεχωριστή υποβολή Δώρων)."""
        rows = {}  # employee_id -> [ApdEarnings, ...]
        for p in self.proj.payrolls:
            if p.period_year == year and p.period_month == month:
                rows.setdefault(p.employee_id, []).extend(
                    self.payroll_earnings_split(p)
                )
        # Δώρα/Επίδομα: στο αρχείο της ΕΠΟΜΕΝΗΣ μισθολογικής περιόδου,
        # μαζί με τη μηνιαία μισθοδοσία (ΔΧ Δεκεμβρίου -> περίοδος 1/Υ+1,
        # που υποβάλλεται έως τέλος Φεβρουαρίου· ΔΠ Απριλίου -> 5/Υ).
        for g in self.proj.gifts:
            tgt = (g.year + 1, 1) if g.gift_type == "christmas" else (g.year, 5)
            if (year, month) == tgt:
                et = "003" if g.gift_type == "christmas" else "004"
                emp = self.proj.employments.get(g.employee_id)
                rows.setdefault(g.employee_id, []).append(
                    ApdEarnings(et, g.gross, 0, contrib=self._extra_contributions(
                        emp, g.gross, g.efka_employee, g.efka_employer,
                        pe.gift_payment_date(g.gift_type, g.year),
                        g.subsidy_employee))
                )
        for a in self.proj.allowances:
            # Το Επίδομα Αδείας δεν έχει σταθερό μήνα: μπαίνει στο αρχείο του
            # μήνα ΜΕΤΑ την άδεια του ΚΑΘΕ εργαζόμενου.
            if (year, month) == self._allowance_apd_period(a.employee_id, a.year):
                emp = self.proj.employments.get(a.employee_id)
                rows.setdefault(a.employee_id, []).append(
                    ApdEarnings("005", a.gross, 0, contrib=self._extra_contributions(
                        emp, a.gross, a.efka_employee, a.efka_employer,
                        _date(a.year, 7, 31), a.subsidy_employee))
                )
        return rows

    def _allowance_apd_period(self, employee_id: str, year: int) -> tuple[int, int]:
        """Περίοδος ΑΠΔ που περιλαμβάνει το Επίδομα Αδείας του εργαζόμενου:
        ο μήνας μετά την περίοδο του επιδόματος (πρώτη άδεια ή αποχώρηση —
        `allowance_period_month`)."""
        emp = self.proj.employments.get(employee_id)
        m = emp.allowance_period_month(year) if emp else None
        if m is None:
            raise ValueError(
                f"Δεν έχει οριστεί μήνας επιδόματος αδείας για {employee_id}"
            )
        return (year + 1, 1) if m == 12 else (year, m + 1)

    def build_apd_rows(self, year: int, month: int):
        """Δομεί τις εγγραφές ΑΠΔ ανά ταμείο για την περίοδο:
        {"efka": [(InsuredInfo, InsuranceRow), ...], "teka": [...]}.
        Χρησιμοποιείται και για preview και για την παραγωγή αρχείων."""
        from calendar import monthrange
        from datetime import date as _d

        period_rows = self._apd_rows_for_period(year, month)
        # ΔΥΟ ΔΙΑΦΟΡΕΤΙΚΕΣ ημερομηνίες — μην τις μπερδέψεις:
        #   rates_on   = μέσα στην περίοδο, για lookup συντελεστών/ΚΠΚ
        #   period_end = ΤΕΛΕΥΤΑΙΑ ΗΜΕΡΑ ΤΟΥ ΜΗΝΑ, για το No 41 «Έως ημ/νία
        #                απασχόλησης»: το διάστημα της ΑΠΔ είναι ο ΠΛΗΡΗΣ μήνας
        #                (Μάιος 2026 -> 31/05/2026), εκτός αν ο εργαζόμενος
        #                αποχώρησε νωρίτερα.
        # ΙΔΙΑ ημερομηνία με τη μισθοδοσία (`payroll_calc_for`): αλλιώς ένα
        # valid_from μεταξύ 29 και τέλους μήνα θα έδινε ΑΛΛΟ ποσοστό στην ΑΠΔ
        # και ΑΛΛΟ στον υπολογισμό, για την ίδια περίοδο.
        rates_on = last_working_day_of_month(year, month)
        period_end = _d(year, month, monthrange(year, month)[1])
        out = {"efka": [], "teka": []}

        def kpk_rate(fund, kpk):
            """(εργαζ.%, εργοδ.%) από το μητρώο ΚΠΚ."""
            k = self.kpk_rates_for(fund, kpk, rates_on)
            return (k.employee, k.employer)

        for eid, earnings in period_rows.items():
            emp = self.proj.employments[eid]
            prof = emp.insurance_profile
            if prof is None:
                raise ValueError(
                    f"Λείπει ασφαλιστικό προφίλ (ΑΜΑ/ΚΑΔ/ΚΠΚ) για τον εργαζόμενο {eid}"
                )
            # Καλύψεις του εργαζομένου: κύρια ΕΦΚΑ / επικουρική ΕΦΚΑ / ΤΕΚΑ.
            # Οι καλύψεις «άλλου ταμείου» μένουν ΕΚΤΟΣ ΑΠΔ (η υποβολή τους
            # γίνεται εκτός συστήματος — μετρούν μόνο στις κρατήσεις).
            covs = profile_coverages(prof)
            main_cov = next(c for c in covs if config.fund_branch(c["fund"]) == "main")
            # Οι υπόλοιπες καλύψεις (πλην κύριας) δρομολογούνται στην ΑΠΔ βάσει
            # του ΦΟΡΕΑ τους (config.fund_apd): efka → δεύτερη εγγραφή τύπου 3
            # στην ΑΠΔ ΕΦΚΑ· teka → ΑΠΔ ΤΕΚΑ· None → εκτός ΑΠΔ (μόνο κρατήσεις).
            extra_covs = [c for c in covs if c is not main_cov]
            has_teka = any(config.fund_apd(c["fund"]) == "teka" for c in extra_covs)
            # No 36 της ΚΥΡΙΑΣ εγγραφής από το δηλωτικό μητρώο κανόνων:
            # ΤΕΚΑ επικουρική -> 09, πρόσθετη πρόνοια μέσα στον e-ΕΦΚΑ -> 10.
            # Ο κανόνας (ΤΕΚΑ 09, πρόνοια ΕΦΚΑ 10) πρώτα· αλλιώς ό,τι κράτησε το
            # πακέτο από τον Ο.Σ.Υ.Κ. (ΕΦΚΑ σε συνδυασμό χωρίς επικουρική -> 10).
            special_case = config.insurance_special_case(extra_covs)
            if special_case == "00":
                special_case = main_cov.get("special_case") or "00"
            info = InsuredInfo(
                ama=emp.ama,
                amka=emp.amka,
                last_name=emp.last_name,
                first_name=emp.first_name,
                father_name=emp.father_name,
                mother_name=emp.mother_name,
                birth_date=emp.birth_date,
                afm=emp.afm,
            )
            is_daily = emp.pay_type == "daily"

            def mkrow(
                et,
                gross,
                days,
                contrib,
                cov,
                special="00",
                aux_code="",
                repeats=False,
                span=(None, None),
                prf=prof,
                empl=emp,
                is_day=is_daily,
            ):
                # Κάθε εγγραφή τύπου 3 γράφεται με την τριάδα (ΚΑΔ/
                # ειδικότητα/ΚΠΚ) της ΚΑΛΥΨΗΣ της και τα ποσοστά του ΚΠΚ της.
                return InsuranceRow(
                    branch_no=prf.branch_no,
                    kad=cov["kad"],
                    full_schedule=empl.weekly_hours >= 40,
                    all_working_days=not is_day,
                    sundays=0,
                    specialty_code=cov["specialty_code"],
                    special_case=special,
                    kpk=cov["kpk"],
                    period_month=month,
                    period_year=year,
                    # Διάστημα: το δικό του, όταν ο τύπος αποδοχών το ορίζει
                    # (008 Ασθένεια → έναρξη/λήξη της ασθένειας)· αλλιώς ο
                    # ΠΛΗΡΗΣ μήνας απασχόλησης για τις τακτικές (001) και
                    # τίποτα για Δώρα/Επίδομα.
                    date_from=(
                        span[0]
                        if span[0]
                        else (
                            max(empl.hire_date, _d(year, month, 1))
                            if et == "001"
                            else None
                        )
                    ),
                    date_to=(
                        span[1]
                        if span[1]
                        else (
                            min(empl.termination_date or period_end, period_end)
                            if et == "001"
                            else None
                        )
                    ),
                    earnings_type=et,
                    insurance_days=days,
                    # Ημερομίσθιο/ένδειξη ημερομισθίου: στις αμοιβές που
                    # αφορούν ΗΜΕΡΕΣ απασχόλησης — τακτικές (001) και
                    # αποδοχές ασθενείας (008). Τα Δώρα/Επίδομα δεν έχουν.
                    daily_wage=(
                        empl.daily_wage if (is_day and et in ("001", "008")) else 0.0
                    ),
                    gross=gross,
                    is_daily_paid=(is_day and et in ("001", "008")),
                    # ΑΠΟ ΤΟΝ ΥΠΟΛΟΓΙΣΜΟ, ΟΧΙ ΞΑΝΑΫΠΟΛΟΓΙΣΜΟΣ: ό,τι δηλώνεται
                    # είναι ό,τι κρατήθηκε και θα καταβληθεί.
                    # Η ΑΠΔ δηλώνει την ΠΛΗΡΗ εισφορά εργαζομένου και χωριστά
                    # την επιδότηση (No 49)· καταβλητέο = πλήρεις − επιδότηση.
                    contrib_employee=pe.r2(contrib[0] + sub(contrib)),
                    contrib_employer=contrib[1],
                    subsidy_employee=sub(contrib),
                    aux_fund_code=aux_code,
                    weekly_system=empl.weekly_system,
                    repeats_earnings=repeats,
                )

            def sub(contrib):
                return contrib[2] if len(contrib) > 2 else 0.0

            def amounts(earning, cov, gross):
                """Οι εισφορές της αμοιβής για ΑΥΤΗ την κάλυψη — από τα events.

                Υποχώρηση (μόνο όταν λείπει εντελώς η ανάλυση: αμοιβή χωρίς
                αντίστοιχο event): τα ποσοστά του μητρώου ΚΠΚ."""
                cell = earning.contrib or {}
                got = cell.get((cov["fund"], cov["kpk"]))
                if got is not None:
                    return got
                e_rate, r_rate = kpk_rate(cov["fund"], cov["kpk"])
                return (pe.r2(gross * e_rate), pe.r2(gross * r_rate))

            main_target = config.fund_apd(main_cov["fund"])
            for earning in earnings:
                et, gross, days = (
                    earning.earnings_type, earning.gross, earning.days)
                span = (earning.date_from, earning.date_to)
                # Ποια αρχεία ΑΠΔ έχουν ΗΔΗ μετρήσει ΑΥΤΕΣ τις αποδοχές: η
                # δεύτερη κάλυψη στο ΙΔΙΟ αρχείο (π.χ. επικουρική/πρόνοια ΕΦΚΑ
                # δίπλα στην κύρια) γράφει τις ΙΔΙΕΣ αποδοχές/ημέρες, οπότε δεν
                # ξαναμετρούν στα σύνολα της κεφαλίδας (No 16/17). Το ΤΕΚΑ πάει
                # σε ΔΙΚΟ του αρχείο, άρα εκεί μετράει κανονικά.
                counted = set()
                # Κύρια εγγραφή ΕΦΚΑ με το ΠΛΗΡΕΣ ποσοστό του ΚΠΚ της. Η ειδική
                # περίπτωση (No 36) δηλώνει ΠΟΥ ασφαλίζεται επικουρικά: 09 σε
                # ΞΕΧΩΡΙΣΤΟ φορέα (ΤΕΚΑ — αποδίδεται στη δική του ΑΠΔ, δεν
                # αφαιρείται από εδώ, + φορέας 01 στο No 54), 10 μέσα στον
                # e-ΕΦΚΑ (2η εγγραφή τύπου 3 παρακάτω), 00 χωρίς επικουρική.
                out["efka"].append(
                    (
                        info,
                        mkrow(
                            et,
                            gross,
                            days,
                            amounts(earning, main_cov, gross),
                            main_cov,
                            special=special_case,
                            span=span,
                            # No 54 «Φορέας Επικ. Ασφάλισης ΕΚΤΟΣ e-ΕΦΚΑ»:
                            # συμπληρώνεται μόνο για ξεχωριστό φορέα (ΤΕΚΑ=01)·
                            # η επικουρική ΕΦΚΑ είναι ΕΝΤΟΣ, μένει κενό.
                            aux_code="01" if has_teka else "",
                            repeats=main_target in counted,
                        ),
                    )
                )
                counted.add(main_target)
                # Οι υπόλοιπες καλύψεις δρομολογούνται βάσει του φορέα τους:
                # apd "efka" → δεύτερη εγγραφή τύπου 3 στην ΑΠΔ ΕΦΚΑ· apd
                # "teka" → ΑΠΔ ΤΕΚΑ· None → εκτός ΑΠΔ (μόνο κρατήσεις).
                for c in extra_covs:
                    target = config.fund_apd(c["fund"])
                    if target is None:
                        continue
                    out[target].append(
                        (
                            info,
                            mkrow(
                                et,
                                gross,
                                days,
                                amounts(earning, c, gross),
                                c,
                                repeats=target in counted,
                                span=span,
                            ),
                        )
                    )
                    counted.add(target)
        if not out["teka"]:
            del out["teka"]
        return out

    def contribution_drift(self, year: int, month: int) -> list[dict]:
        """ΑΣΥΜΦΩΝΙΑ ΠΟΣΟΣΤΩΝ ανάμεσα στον ΥΠΟΛΟΓΙΣΜΟ και στο ΜΗΤΡΩΟ ΚΠΚ.

        Η ΑΠΔ δηλώνει πλέον ΟΤΙ ΥΠΟΛΟΓΙΣΤΗΚΕ (οι εισφορές έρχονται από τα
        events), οπότε μια αλλαγή ποσοστού ΜΕΤΑ τον υπολογισμό δεν αλλάζει
        σιωπηλά το αρχείο — αφήνει όμως τη μισθοδοσία με το ΠΑΛΙΟ ποσοστό.
        Αυτό είναι που πρέπει να δει ο χρήστης πριν υποβάλει: αν ισχύει το νέο
        ποσοστό, ανοίγει την περίοδο και ξαναϋπολογίζει.

        Συγκρίνονται ΠΟΣΟΣΤΑ, όχι ποσά: τα ποσά διαφέρουν έτσι κι αλλιώς λόγω
        στρογγυλοποίησης ανά γραμμή και πλαφόν, και ένας θορυβώδης ανιχνευτής
        θα ήταν χειρότερος από κανέναν.

        Τα ποσοστά του υπολογισμού έρχονται από το `analysis["rates"]`. Παλιά
        events δεν το έχουν: εκεί η σύγκριση γίνεται με το ΤΕΚΜΑΡΤΟ ποσοστό
        (`efka_employee / gross`) και σημαδεύεται `estimated`."""
        rates_on = last_working_day_of_month(year, month)
        out = []
        for p in self.proj.payrolls:
            if (p.period_year, p.period_month) != (year, month):
                continue
            emp = self.proj.employments.get(p.employee_id)
            if emp is None or emp.insurance_profile is None:
                continue
            now = self.contribution_rates(emp.insurance_profile, rates_on)
            used = (p.analysis or {}).get("rates")
            estimated = used is None
            if estimated:
                if not p.gross:
                    continue
                used = {
                    "employee": round(p.efka_employee / p.gross, 4),
                    "employer": round(p.efka_employer / p.gross, 4),
                }
            # Ανοχή: 0,01 της εκατοστιαίας μονάδας για τα καταγεγραμμένα,
            # 0,05 για τα τεκμαρτά (κουβαλούν στρογγυλοποίηση και πλαφόν)
            tol = 0.0005 if estimated else 0.0001
            if (abs(used["employee"] - now.employee) <= tol
                    and abs(used["employer"] - now.employer) <= tol):
                continue
            out.append({
                "eid": p.employee_id,
                "name": emp.full_name,
                "used": (used["employee"], used["employer"]),
                "now": (now.employee, now.employer),
                "estimated": estimated,
            })
        return out

    def apd_fund_totals(self, year: int, month: int) -> dict:
        """{fund: {'gross':..,'contrib':..,'insured':..}} για την περίοδο."""
        totals = {}
        for fund, pairs in self.build_apd_rows(year, month).items():
            t = totals.setdefault(
                fund, {"gross": 0.0, "contrib": 0.0, "insured": set()}
            )
            for info, row in pairs:
                # Ίδιος κανόνας με την κεφαλίδα του αρχείου (No 17): οι
                # αποδοχές μιας δεύτερης κάλυψης πάνω στις ΙΔΙΕΣ αποδοχές δεν
                # ξαναμετρούν — οι εισφορές όμως αθροίζονται πάντα.
                if not row.repeats_earnings:
                    t["gross"] = pe.r2(t["gross"] + row.gross)
                t["contrib"] = pe.r2(t["contrib"] + row.payable)
                t["insured"].add(info.ama)
        return {
            f: {
                "gross": v["gross"],
                "contrib": v["contrib"],
                "insured": len(v["insured"]),
            }
            for f, v in totals.items()
        }

    def apd_builder(
        self,
        fund: str,
        year: int,
        month: int,
        employer: EmployerInfo | None = None,
        submission_date=None,
    ) -> APDFileBuilder | None:
        """Ο builder του CSL01 μιας περιόδου/ταμείου — ΧΩΡΙΣ δίσκο.

        ΜΙΑ πηγή αλήθειας για το περιεχόμενο του αρχείου: το ίδιο καλούν
        τόσο ο υπολογισμός των συνόλων (`generate_apd_files`) όσο και η
        παραγωγή on the fly για προβολή/λήψη (`apd_file_bytes`/`apd_zip`).
        None αν η περίοδος δεν έχει εγγραφές για το ταμείο.
        """
        pairs = self.build_apd_rows(year, month).get(fund)
        if not pairs:
            return None
        b = APDFileBuilder(
            fund,
            employer or self.employer_info(),
            month,
            year,
            submission_date=submission_date,
        )
        # Μία γραμμή τη φορά· ο builder τις ΟΜΑΔΟΠΟΙΕΙ ανά ασφαλισμένο
        # (ΜΙΑ εγγραφή τύπου 2 και από κάτω όλες οι τύπου 3 του).
        for info, row in pairs:
            subs = None
            if row.subsidy_employee:
                pct = config.labor_subsidy_for(
                    last_working_day_of_month(year, month)).pct
                subs = {0: [SubsidyRow(
                    "102", pe.r2(pct * 100), row.subsidy_employee, 0.0, 0.0)]}
            b.add_insured(info, [row], subs)
        return b

    def apd_file_bytes(
        self, fund: str, year: int, month: int, submission_date=None
    ) -> bytes:
        """Το ΩΜΟ CSL01 (cp1253) της περιόδου, παραγόμενο on the fly."""
        b = self.apd_builder(fund, year, month, submission_date=submission_date)
        if b is None:
            raise ValueError(
                f"Καμία εγγραφή ΑΠΔ {fund.upper()} για την περίοδο {month}/{year}"
            )
        return b.encoded()

    def apd_zip_bytes(
        self, fund: str, year: int, month: int, submission_date=None
    ) -> bytes:
        """Το CSL01 της περιόδου μέσα σε ZIP (bytes) — για κατέβασμα."""
        b = self.apd_builder(fund, year, month, submission_date=submission_date)
        if b is None:
            raise ValueError(
                f"Καμία εγγραφή ΑΠΔ {fund.upper()} για την περίοδο {month}/{year}"
            )
        return b.zip_bytes()

    def generate_apd_files(
        self,
        year: int,
        month: int,
        employer: EmployerInfo | None = None,
        submission_date=None,
        funds=None,
    ) -> dict:
        """Καταγράφει την παραγωγή ΑΠΔ ανά ταμείο. Επιστρέφει {fund: όνομα}.

        ⚠️ **Τίποτα δεν γράφεται στον δίσκο**: το αρχείο είναι παράγωγο των
        events και παράγεται on the fly όποτε ζητηθεί (οθόνη «Αρχεία ΑΠΔ»
        → προβολή/λήψη). Εδώ καταγράφονται μόνο τα σύνολα και το **λογικό
        όνομα λήψης** (`apd_files.zip_name`) στο `APDFileGenerated`.

        `funds`: ΠΟΙΑ ταμεία να παραχθούν (π.χ. `("efka",)`). None = όλα.
        ➜ Κάθε ΑΠΔ έχει ΔΙΚΗ της υποχρέωση υποβολής: η «Υποβολή ΑΠΔ e-ΕΦΚΑ»
        δεν επιτρέπεται να παράγει και το αρχείο ΤΕΚΑ (θα έγραφε
        `APDFileGenerated` για ταμείο που ο χρήστης δεν ζήτησε ακόμη).
        """
        if employer is None:
            employer = self.employer_info()
        fund_rows = self.build_apd_rows(year, month)
        if funds is not None:
            wanted = set(funds)
            fund_rows = {f: r for f, r in fund_rows.items() if f in wanted}
        totals = self.apd_fund_totals(year, month)
        names = {}
        for fund in fund_rows:
            b = self.apd_builder(
                fund, year, month, employer, submission_date=submission_date
            )
            # Επικυρώνεται ΤΩΡΑ (μήκη εγγραφών, 20MB, codepage) — δεν θέλουμε
            # να μάθουμε για σφάλμα γραμμογράφησης τη στιγμή της λήψης.
            b.encoded()
            name = b.zip_name()
            t = totals[fund]
            self.store.append(
                "COMPANY",
                APDFileGenerated(
                    fund, year, month, t["gross"], t["contrib"], t["insured"]
                ),
            )
            names[fund] = name
        return names
