"""
Κατώτατος μισθός, προϋπηρεσία, μεταβολή σύμβασης, αποχώρηση

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

import misevent.domain.payroll_engine as pe
import misevent.integrations.ergani as ergani_mod
import misevent.rules.config as config
import misevent.rules.constraints as constraints
from misevent.domain.events import ContractChanged, EmployeeTerminated, EmploymentDeleted

from ._base import DISMISSAL_REASON, FIXED_TERM_END_REASON, VOLUNTARY_EXIT_REASON


class ContractMixin:
    @staticmethod
    def minimum_floor(
        pay_type, weekly_hours, on_date, service_years=0.0, married=False, extra_pct=0.0
    ) -> tuple[float, str]:
        """(κατώτατο όριο, περιγραφή προσαυξήσεων) για την ημερομηνία ελέγχου.

        Όριο = βασικός κατώτατος ΓΣΣΕ/ΚΥΑ x αναλογία ωρών (μερική απασχόληση)
        x συντελεστής προσαυξήσεων (τριετίες/γάμος/λοιπές —
        config.seniority_factor). Ο αριθμός γίνεται από την ΜΙΑ πηγή
        `config.minimum_floor_value`· εδώ προστίθεται η περιγραφή."""
        floor = config.minimum_floor_value(
            pay_type, weekly_hours, on_date, service_years, married, extra_pct
        )
        parts = []
        r = config.allowance_rules_for(on_date)
        tri_max = (
            r.daily_max_triennia if pay_type == "daily" else r.monthly_max_triennia
        )
        tri = config.triennia_count(service_years, tri_max)
        if tri:
            pct = (
                r.daily_triennium_pct
                if pay_type == "daily"
                else r.monthly_triennium_pct
            )
            parts.append(f"{tri} τριετίες +{tri * pct:.0%}")
        if married:
            parts.append(f"γάμος +{r.marriage_pct:.0%}")
        if extra_pct:
            parts.append(f"λοιπές +{extra_pct:g}%")
        return floor, ", ".join(parts)

    @staticmethod
    def wage_check_basis(
        hire_date,
        payroll_from,
        service_years=0.0,
        frozen_service_years=0.0,
    ):
        """ΠΟΤΕ και ΜΕ ΠΟΣΗ προϋπηρεσία ελέγχεται ο κατώτατος στην πρόσληψη —
        ΜΙΑ πηγή αλήθειας για το command layer ΚΑΙ για τη φόρμα.

        Κανονική πρόσληψη: την ημ. πρόσληψης, με τη δηλωθείσα προϋπηρεσία.

        **ΜΕΤΑΦΟΡΑ** (`payroll_from` μετά την πρόσληψη): ΓΣΣΕ και τριετίες
        είναι χρονοσειρές, οπότε ο έλεγχος γίνεται στην **ΑΝΑΛΗΨΗ** — ο
        κατώτατος μιας πρόσληψης ετών πριν συνήθως ούτε καν υπάρχει στο
        μητρώο παραμέτρων — και η προϋπηρεσία **ΩΡΙΜΑΖΕΙ on the fly**:
        δηλωθείσα + τα χρόνια πρόσληψη→ανάληψη. Έτσι το όριο της πρόσληψης
        συμφωνεί με αυτό που θα εφαρμόσει αμέσως μετά η μισθοδοσία
        (`wage_violations` → `EmploymentState.total_service_years`), αντί να
        περνά η πρόσληψη και να μπλοκάρει ο πρώτος μήνας."""
        constraints.check_service_allowances(service_years, frozen_service_years)
        on = hire_date
        if payroll_from and hire_date and payroll_from > hire_date:
            on = payroll_from
        total = config.triennia_service_years(service_years, hire_date, on)
        return on, total

    def previous_service_years(self, person_id) -> float:
        """Προϋπηρεσία που κουβαλά ένα πρόσωπο σε ΕΠΑΝΑΠΡΟΣΛΗΨΗ, σε δεκαδικά
        έτη: η δηλωθείσα στην ΤΕΛΕΥΤΑΙΑ απασχόληση **συν** τον χρόνο που
        δούλεψε σε αυτήν (πρόσληψη → αποχώρηση).

        ΙΔΙΑ βάση με το κατώτατο όριο (`EmploymentState.total_service_years`),
        ώστε οι τριετίες να συνεχίζουν από εκεί που σταμάτησαν. Επιστρέφει
        0.0 όταν δεν υπάρχει προηγούμενη (ολοκληρωμένη) απασχόληση."""
        person = self.proj.persons.get(person_id)
        if person is None:
            return 0.0
        ends = []
        for eid in person.employment_ids:
            emp = self.proj.employments.get(eid)
            if emp is None or emp.hire_date is None or emp.active:
                continue
            ends.append((emp.termination_date, emp))
        if not ends:
            return 0.0
        end, emp = max(ends, key=lambda row: row[0])
        return round(emp.total_service_years(end), 4)

    def previous_frozen_service_years(self, person_id) -> float:
        """Ο ΠΑΓΩΜΕΝΟΣ χρόνος (14/02/2012–31/12/2023) που κουβαλά ένα πρόσωπο
        σε ΕΠΑΝΑΠΡΟΣΛΗΨΗ: ο δηλωμένος στην ΤΕΛΕΥΤΑΙΑ απασχόληση **συν** την
        επικάλυψη εκείνης της περιόδου με το πάγωμα.

        Ταξιδεύει χωριστά από το `previous_service_years` (που μετρά μόνο τις
        τριετίες), ώστε το ΣΥΝΟΛΟ της προϋπηρεσίας να μη χάνεται στη νέα
        πρόσληψη — και κόβεται στο μήκος του ίδιου του παγώματος."""
        person = self.proj.persons.get(person_id)
        if person is None:
            return 0.0
        ends = []
        for eid in person.employment_ids:
            emp = self.proj.employments.get(eid)
            if emp is None or emp.hire_date is None or emp.active:
                continue
            ends.append((emp.termination_date, emp))
        if not ends:
            return 0.0
        end, emp = max(ends, key=lambda row: row[0])
        start = max(emp.hire_date, config.TRIENNIUM_FREEZE_START)
        stop = min(end, config.TRIENNIUM_FREEZE_RESUME)
        overlap = max(0, (stop - start).days) / 365.25
        total = (emp.frozen_service_years or 0.0) + overlap
        return round(min(total, config.freeze_span_years()), 4)

    @staticmethod
    def check_wage_registry(on_date):
        """Υπάρχει ΓΣΣΕ σε ισχύ την `on_date`; Το σκέτο «Δεν υπάρχει κατώτατος
        μισθός για …» του config δεν λέει στον χρήστη ΤΙ να κάνει — και η
        συνηθέστερη αιτία είναι εργαζόμενος που έρχεται από άλλο πρόγραμμα
        μισθοδοσίας (§2, μεταφορά), οπότε το μήνυμα τον κατευθύνει εκεί.
        Καλείται ΚΑΙ από την αποθήκευση ΚΑΙ από τη ζωντανή ένδειξη της φόρμας."""
        entries = config.minimum_wage_entries()
        earliest = min((w.valid_from for w in entries), default=None)
        if earliest is not None and on_date < earliest:
            raise ValueError(
                f"Δεν υπάρχει κατώτατος μισθός για "
                f"{on_date.strftime('%d/%m/%Y')} — το μητρώο ΓΣΣΕ ξεκινά "
                f"{earliest.strftime('%d/%m/%Y')}. Αν ο εργαζόμενος έρχεται "
                "από άλλο πρόγραμμα μισθοδοσίας, σημειώστε «Μεταφορά από άλλο "
                "πρόγραμμα μισθοδοσίας» και δώστε την ημερομηνία έναρξης — ο "
                "κατώτατος θα ελεγχθεί τότε, με ωριμασμένες τις τριετίες."
            )

    def check_minimum(
        self,
        pay_type,
        monthly_gross,
        daily_wage,
        weekly_hours,
        on_date,
        service_years=0.0,
        married=False,
        extra_pct=0.0,
    ):
        """Κατώτατος (ΓΣΣΕ/ΚΥΑ): αναλογία ωρών για μερική απασχόληση,
        προσαυξημένος με τριετίες/γάμο/λοιπές προσαυξήσεις."""
        self.check_wage_registry(on_date)
        floor, desc = self.minimum_floor(
            pay_type, weekly_hours, on_date, service_years, married, extra_pct
        )
        mw = config.minimum_wage_for(on_date)
        suffix = f" ({desc})" if desc else ""
        if pay_type == "monthly":
            if monthly_gross < floor:
                extra = (
                    f" (μερική απασχόληση {weekly_hours:g} ώρες: "
                    f"αναλογία του κατώτατου {mw.monthly:.2f}€)"
                    if weekly_hours < 40
                    else ""
                )
                raise ValueError(
                    f"Ο μισθός {monthly_gross:.2f}€ υπολείπεται του "
                    f"κατώτατου {floor:.2f}€{suffix}{extra} — ΓΣΣΕ/ΚΥΑ σε "
                    f"ισχύ από {config.gr_date(mw.valid_from)}"
                )
        elif daily_wage < floor:
            raise ValueError(
                f"Το ημερομίσθιο {daily_wage:.2f}€ υπολείπεται του "
                f"κατώτατου {floor:.2f}€{suffix} (ΓΣΣΕ/ΚΥΑ σε ισχύ από "
                f"{config.gr_date(mw.valid_from)})"
            )

    def change_contract(
        self,
        employee_id,
        effective_from,
        pay_type,
        monthly_gross=0.0,
        daily_wage=0.0,
        weekly_hours=40.0,
        weekly_system=None,
        reason="",
        reason_date=None,
    ):
        """Αλλαγή σύμβασης (αποδοχές / τύπος αμοιβής / πλήρης-μερική /
        πενθήμερο-εξαήμερο). Υποβάλλει Δήλωση Μεταβολής στο ΕΡΓΑΝΗ.

        `weekly_system`: 5/6 ή None = αμετάβλητο.
        `reason`: κωδικός αιτίας (`config.CONTRACT_CHANGE_REASONS`) — κενός για
        χειροκίνητη μεταβολή· `reason_date` η ημερομηνία που την τεκμηριώνει."""
        emp = self.proj.employments.get(employee_id)
        if emp is None or not emp.active:
            raise ValueError(f"Ανενεργός/άγνωστος εργαζόμενος {employee_id}")
        # Ο έλεγχος κατώτατου με τις προσαυξήσεις της απασχόλησης (τριετίες
        # ωριμασμένες ως την ημερομηνία ισχύος, γάμος, λοιπές)
        self.check_minimum(
            pay_type,
            monthly_gross,
            daily_wage,
            weekly_hours,
            effective_from,
            service_years=emp.total_service_years(effective_from),
            married=emp.married,
            extra_pct=emp.extra_allowances_pct,
        )
        weekly_system = constraints.check_weekly_system(weekly_system)
        self.store.append(
            employee_id,
            ContractChanged(
                employee_id,
                effective_from,
                pay_type,
                monthly_gross,
                daily_wage,
                weekly_hours,
                weekly_system,
                reason,
                reason_date,
            ),
        )
        if self.proj.company:
            self._ergani_submit(
                "E4_METAVOLI",
                employee_id,
                ergani_mod.build_metavoli(
                    self.proj.company,
                    self.proj.employments[employee_id],
                    effective_from,
                ),
            )

    def delete_employment(self, employment_id, reason=""):
        """ΔΙΑΓΡΑΦΗ περιόδου απασχόλησης που καταχωρήθηκε ΕΚ ΠΑΡΑΔΡΟΜΗΣ.

        ΔΕΝ είναι αποχώρηση: η σχέση δεν υπήρξε ποτέ. Επιτρέπεται μόνο όσο δεν
        έχει «πατήσει» πουθενά — καμία υποβολή στο ΕΡΓΑΝΗ (η αναγγελία είναι
        δημόσια πράξη· ανακαλείται με τα έντυπα του ΕΡΓΑΝΗ, όχι με διαγραφή),
        κανένας υπολογισμός (μισθοδοσία/Δώρο/Επίδομα/αποζημίωση) και κανένα
        εγκεκριμένο συγκεντρωτικό. Το event log μένει άθικτο (κανόνας #2): η
        διαγραφή γίνεται με αντισταθμιστικό `EmploymentDeleted`."""
        emp = self.proj.employments.get(employment_id)
        if emp is None:
            raise ValueError(f"Άγνωστη απασχόληση {employment_id}")
        if emp.hire_submitted or any(
            s.employee_id == employment_id and s.status != "failed"
            for s in self.proj.ergani_submissions
        ):
            raise ValueError(
                "Η απασχόληση έχει υποβληθεί στο ΕΡΓΑΝΗ και δεν διαγράφεται "
                "— χρησιμοποίησε τα έντυπα ανάκλησης/λήξης"
            )
        blocking = [
            (label, rows)
            for label, rows in (
                ("μισθοδοσία", self.proj.payrolls),
                ("Δώρο", self.proj.gifts),
                ("Επίδομα Αδείας", self.proj.allowances),
                ("αποζημίωση αδείας", self.proj.leave_compensations),
                ("αποζημίωση απόλυσης", self.proj.severances),
            )
            if any(getattr(r, "employee_id", None) == employment_id for r in rows)
        ]
        if blocking:
            raise ValueError(
                "Η απασχόληση έχει ήδη υπολογισμούς ("
                + ", ".join(label for label, _ in blocking)
                + ") και δεν διαγράφεται"
            )
        self.store.append(employment_id, EmploymentDeleted(employment_id, reason))

    def _check_termination(self, employee_id, termination_date):
        """Οι έλεγχοι της αποχώρησης, ΧΩΡΙΣ event — ΜΙΑ πηγή αλήθειας για την
        εντολή (`terminate`) και για την προεπισκόπησή της
        (`termination_preview`), ώστε το modal επιβεβαίωσης να δείχνει το ίδιο
        σφάλμα που θα έδινε η καταχώρηση."""
        emp = self.proj.employments.get(employee_id)

        if emp is None or not emp.active:
            raise ValueError(f"Ανενεργός/άγνωστος εργαζόμενος {employee_id}")

        # ΔΕΝ ΑΠΟΧΩΡΕΙ ΟΠΟΙΟΣ ΔΕΝ ΠΡΟΣΛΗΦΘΗΚΕ: όσο εκκρεμεί η αναγγελία
        # πρόσληψης (§5.7α) η σχέση δεν έχει δηλωθεί πουθενά — η λήξη της θα
        # ήταν αναγγελία λήξης για πρόσληψη που το ΕΡΓΑΝΗ δεν γνωρίζει. Η
        # σωστή διόρθωση είναι η ΔΙΑΓΡΑΦΗ της απασχόλησης (§1 κανόνας #2).
        if not emp.hire_submitted:
            raise ValueError(
                "Η πρόσληψη δεν έχει ολοκληρωθεί (εκκρεμεί η αναγγελία Ε3) — "
                "δεν υπάρχει σχέση προς λήξη. Υπόβαλε πρώτα το Ε3 ή, αν η "
                "καταχώρηση έγινε εκ παραδρομής, διάγραψε την απασχόληση"
            )

        if termination_date < emp.hire_date:
            raise ValueError("Η λήξη προηγείται της πρόσληψης")
        self._check_open_period(termination_date, "αποχώρηση")

        # Η ΑΠΟΧΩΡΗΣΗ ΓΙΝΕΤΑΙ ΜΕΣΑ ΣΤΟΝ ΤΡΕΧΟΝΤΑ ΜΗΝΑ ΜΙΣΘΟΔΟΣΙΑΣ: η εκκαθάριση
        # περιλαμβάνει τη μισθοδοσία αυτού του μήνα (έως την ημ. λήξης), οπότε
        # μια λήξη σε επόμενο μήνα θα έμενε χωρίς μισθοδοσία και θα «πάγωνε»
        # τους ενδιάμεσους μήνες. Ο προγενέστερος μήνας κόβεται ήδη παραπάνω
        # (κλεισμένη περίοδος) — εδώ κόβεται ο μελλοντικός.
        current = self.current_payroll_period()
        if current is not None and (termination_date.year, termination_date.month) != current:
            raise ValueError(
                f"Η αποχώρηση επιτρέπεται μόνο μέσα στον τρέχοντα μήνα "
                f"μισθοδοσίας ({current[1]}/{current[0]}) — δόθηκε "
                f"{termination_date.strftime('%d/%m/%Y')}"
            )
        return emp

    def termination_preview(
        self, employee_id, termination_date, reason="", with_notice=False
    ):
        """ΤΙ ΘΑ ΥΠΟΛΟΓΙΣΤΕΙ αν καταχωρηθεί η αποχώρηση — ΧΩΡΙΣ κανένα event.

        Ίδιοι έλεγχοι (`_check_termination`) και ίδιοι υπολογισμοί με την
        `terminate`/`_settle_exit_accruals`: Δώρο, Επίδομα Αδείας, αποζημίωση
        μη ληφθείσας αδείας και — σε καταγγελία — αποζημίωση απόλυσης. Το κάθε
        στοιχείο κουβαλά αν έχει ΗΔΗ υπολογιστεί, ώστε το modal να μη δείχνει
        ποσό που δεν θα ξαναγραφτεί."""
        emp = self._check_termination(employee_id, termination_date)
        year, month = termination_date.year, termination_date.month

        gift_type = "easter" if month <= 4 else "christmas"
        gift_done = any(
            g.employee_id == employee_id and g.gift_type == gift_type
            and g.year == year
            for g in self.proj.gifts
        )
        gift = None if gift_done else self.gift_preview_for(
            emp, gift_type, year, term=termination_date
        )
        allowance_done = any(
            a.employee_id == employee_id and a.year == year
            for a in self.proj.allowances
        )
        allowance = None if allowance_done else self.allowance_preview_for(
            emp, year, term=termination_date
        )

        dismissal = reason == DISMISSAL_REASON
        gift = gift if gift and gift.gross > 0 else None
        allowance = allowance if allowance and allowance.gross > 0 else None
        leave_comp = (
            None
            if self.leave_compensation(employee_id)
            else self.leave_compensation_preview(employee_id, term=termination_date)
        )
        severance = (
            self.severance_preview(employee_id, with_notice, term=termination_date)
            if dismissal and self.severance(employee_id) is None
            else None
        )

        # Η μισθοδοσία του ΜΗΝΑ ΤΗΣ ΑΠΟΧΩΡΗΣΗΣ, μόνο γι' αυτόν τον εργαζόμενο
        exit_payroll = self.exit_payroll_preview(employee_id, term=termination_date)
        payroll = exit_payroll["payroll"]

        # ΣΥΝΟΛΟ ΑΜΕΣΗΣ ΚΑΤΑΒΟΛΗΣ (ημέρα αποχώρησης) = ΟΛΑ ΕΚΤΟΣ του Δώρου, που
        # καταβάλλεται στη δική του ημερομηνία. Μετρά μόνο ό,τι θα γραφτεί τώρα
        # (ό,τι έχει ήδη υπολογιστεί δεν ξαναγράφεται). Οι αποζημιώσεις δεν
        # έχουν ασφαλιστικές κρατήσεις.
        parts = [
            (
                payroll["result"].gross,
                payroll["result"].efka_employee,
                payroll["result"].fmy,
                payroll["result"].net,
            )
        ] if payroll else []
        parts += [
            (x.gross, x.efka_employee, x.fmy, x.net) for x in (allowance,) if x
        ] + [
            (x["gross"], 0.0, x["fmy"], x["net"])
            for x in (leave_comp, severance)
            if x is not None
        ]
        total = {
            "gross": pe.r2(sum(p[0] for p in parts)),
            "efka": pe.r2(sum(p[1] for p in parts)),
            "fmy": pe.r2(sum(p[2] for p in parts)),
            "net": pe.r2(sum(p[3] for p in parts)),
        }

        return {
            "termination_date": termination_date,
            "reason": reason,
            "year": year,
            # Μισθοδοσία μήνα αποχώρησης (μόνο αυτού του εργαζόμενου)
            "payroll": payroll,
            "payroll_year": exit_payroll["year"],
            "payroll_month": exit_payroll["month"],
            "payroll_blocked": exit_payroll["blocked"],
            "payroll_done": exit_payroll["already"],
            "gift_type": gift_type,
            "gift": gift,
            "gift_done": gift_done,
            # Το Δώρο ΔΕΝ καταβάλλεται στην αποχώρηση αλλά στην ημερομηνία του
            "gift_pay_date": pe.gift_payment_date(gift_type, year),
            "allowance": allowance,
            "allowance_done": allowance_done,
            "leave_comp": leave_comp,
            "leave_comp_done": self.leave_compensation(employee_id) is not None,
            "is_dismissal": dismissal,
            "with_notice": with_notice,
            "severance": severance,
            "severance_done": self.severance(employee_id) is not None,
            "total": total,
            "ergani": self.proj.company is not None,
        }

    def terminate(self, employee_id, termination_date, reason="", with_notice=False,
                  resignation_pdf: bytes | None = None) -> str:
        """Αποχώρηση/λήξη εργασιακής σχέσης + Ψηφιακή Αναγγελία Λήξης.

        Έντυπο ΕΡΓΑΝΗ ανά αιτία (σχήματα στο data/ergani_schemas/):
          - οικειοθελής      -> WebE5N, με τη δήλωση του εργαζομένου (PDF)
                                ΥΠΟΧΡΕΩΤΙΚΑ όταν η αποστολή είναι πραγματική·
          - λήξη ορισμένου   -> WebE7N (χωρίς συνημμένο)·
          - καταγγελία       -> ΚΑΜΙΑ αποστολή: δεν διατίθεται μέσω API.
        Επιστρέφει σημείωση για τον χρήστη (κενή αν δεν χρειάζεται)."""
        emp = self._check_termination(employee_id, termination_date)
        reason = reason or VOLUNTARY_EXIT_REASON
        # Έλεγχοι ΠΡΙΝ το event: καμία μισή αποχώρηση
        if reason == FIXED_TERM_END_REASON and emp.employment_relationship != "1":
            raise ValueError("Η «Λήξη ορισμένου χρόνου» αφορά μόνο σύμβαση "
                             "ορισμένου χρόνου")
        if resignation_pdf is not None and not resignation_pdf.startswith(b"%PDF"):
            raise ValueError("Η δήλωση αποχώρησης πρέπει να είναι PDF")
        if (self.proj.company and reason == VOLUNTARY_EXIT_REASON
                and not self.ergani.simulate and not resignation_pdf):
            raise ValueError(
                "Επισυνάψτε τη δήλωση αποχώρησης με υπογραφή του εργαζομένου "
                "(PDF) — το ΕΡΓΑΝΗ την απαιτεί στην οικειοθελή αποχώρηση")

        self.store.append(
            employee_id, EmployeeTerminated(employee_id, termination_date, reason)
        )

        note = ""
        if self.proj.company:
            profile = emp.insurance_profile
            branch = self.get_branch(getattr(profile, "branch_no", 0) or 0)
            args = (self.proj.company, emp, profile, termination_date)
            if reason == VOLUNTARY_EXIT_REASON:
                self._ergani_submit("E5_LIXI", employee_id, ergani_mod.build_web_e5n(
                    *args, branch=branch, resignation_pdf=resignation_pdf),
                    endpoint="WebE5N")
            elif reason == FIXED_TERM_END_REASON:
                self._ergani_submit("E5_LIXI", employee_id, ergani_mod.build_web_e7n(
                    *args, branch=branch), endpoint="WebE7N")
            else:
                note = ("η Ψηφιακή Αναγγελία Λήξης για καταγγελία ΔΕΝ διατίθεται "
                        "μέσω API — υποβάλετέ την στο portal του ΕΡΓΑΝΗ εντός 4 "
                        "εργάσιμων")

        # ΟΛΟΙ οι υπολογισμοί της εκκαθάρισης γίνονται ΕΔΩ, αυτόματα: Δώρο +
        # Επίδομα Αδείας pro-rata, αποζημίωση μη ληφθείσας αδείας και (σε
        # καταγγελία) αποζημίωση απόλυσης — κανένα χειροκίνητο κουμπί.
        self._settle_exit_accruals(emp, with_notice)
        return note
