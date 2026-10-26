"""
Εργασιακή σχέση: πρόσληψη/διόρθωση + υποβολές πρόσληψης στο ΕΡΓΑΝΗ

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

import uuid
from datetime import timedelta

import misevent.integrations.ergani as ergani_mod
import misevent.integrations.ergani_parameters as ergani_parameters
import misevent.rules.constraints as constraints
from misevent.domain.events import (
    EmploymentHireDetailsCorrected,
    EmploymentScheduleModeSet,
    EmploymentStarted,
    ErganiSubmissionRecorded,
    ScheduleDaysSubmitted,
    ScheduleDeclaredToErgani,
    WorkScheduleAssigned,
)

from ._base import PERSON_DOCUMENT_KINDS


class EmploymentMixin:
    # ------------------------------------------------------ Εργασιακή σχέση
    def start_employment(
        self,
        person_id,
        hire_date,
        payroll_from=None,
        ergani_hire_done=False,
        ergani_hire_reference="",
        pay_type="monthly",
        monthly_gross=0.0,
        daily_wage=0.0,
        weekly_hours=40.0,
        weekly_system=5,
        weekly_pattern: dict | None = None,
        service_years=0.0,
        frozen_service_years=0.0,
        married=False,
        extra_allowances_pct=0.0,
        schedule_mode="fixed",
        schedule_end_date=None,
        schedule_weeks=0,
        draft=False,
        employment_relationship="0",
        fixed_term_from=None,
        fixed_term_to=None,
        oaed_placement="0",
        oaed_program="",
        oaed_replacement="0",
        oaed_replaced_afm="",
        oaed_replaced_amka="",
        trial_period="0",
        trial_date_to=None,
        minor_booklet_number="",
        unemployment_card_number="",
        working_card="1",
        flexible_arrival_minutes="120",
        break_within_schedule="0",
        break_minutes="15",
        today=None,
    ):
        """Ξεκινά ΠΕΡΙΟΔΟ ΑΠΑΣΧΟΛΗΣΗΣ για υπάρχον πρόσωπο (πρόσληψη ή
        επαναπρόσληψη). Επιστρέφει το νέο employment_id.

        Το `weekly_pattern` (πρόγραμμα εργασίας) είναι ΥΠΟΧΡΕΩΤΙΚΟ, όπως η
        σύμβαση: χωρίς αυτό δεν τεκμαίρονται παρουσίες ούτε υποβάλλεται ΟΧΕ.

        Προσαυξήσεις κατώτατου ορίου (δηλώνονται εδώ): `service_years` =
        προϋπηρεσία σε έτη που ΜΕΤΡΑ ΣΤΙΣ ΤΡΙΕΤΙΕΣ, `frozen_service_years` =
        ο ΕΠΙΠΛΕΟΝ παγωμένος χρόνος 14/02/2012–31/12/2023 (μετρά στον
        συνολικό χρόνο εργασίας, ΟΧΙ στις τριετίες — σύνολο προϋπηρεσίας =
        το άθροισμα των δύο), `married` = επίδομα γάμου 10%,
        `extra_allowances_pct` = λοιπές (πτυχίο κ.λπ.) σε % — ανεβάζουν το
        ελάχιστο επιτρεπτό όριο αποδοχών (config.seniority_factor).

        ➜ **ΜΕΤΑΦΟΡΑ ΑΠΟ ΑΛΛΟ ΠΡΟΓΡΑΜΜΑ ΜΙΣΘΟΔΟΣΙΑΣ** (εταιρεία με χρήσεις
        στο παρελθόν): `payroll_from` = η 1η του μήνα από τον οποίο αναλαμβάνει
        ΑΥΤΟ το σύστημα (π.χ. πρόσληψη 15/5/2024, `payroll_from` 1/1/2026). Η
        `hire_date` μένει η ΠΡΑΓΜΑΤΙΚΗ — τριετίες, Δώρα, δικαίωμα αδείας και
        αποζημίωση απόλυσης μετρούν από εκεί· απλώς δεν γεννιούνται περίοδοι/
        υποχρεώσεις πριν την ανάληψη. `ergani_hire_done=True` σημαδεύει την
        αναγγελία πρόσληψης ως **ήδη υποβληθείσα εκτός συστήματος** (δεν
        ξαναστέλνεται στο ΕΡΓΑΝΗ), με προαιρετικό αρ. πρωτοκόλλου
        `ergani_hire_reference`. Με `today` (το «σήμερα» του UI) οι ημέρες
        προγράμματος από την ανάληψη έως χθες καταγράφονται ως δηλωμένες από
        το προηγούμενο πρόγραμμα — η ΟΧΕ από εδώ ισχύει από σήμερα."""
        person = self.proj.persons.get(person_id)
        if person is None:
            raise ValueError(
                f"Άγνωστο πρόσωπο {person_id} — καλέστε πρώτα register_person()"
            )

        existing = [
            self.proj.employments[e]
            for e in person.employment_ids
            if e in self.proj.employments
        ]
        constraints.check_service_allowances(
            service_years, frozen_service_years, extra_allowances_pct
        )
        if schedule_mode not in ("fixed", "variable"):
            raise ValueError("Άγνωστος τύπος προγράμματος (fixed/variable)")
        # Η ανάληψη μισθοδοσίας επικυρώνεται ΠΡΩΤΗ: το πρόγραμμα ισχύει από
        # εκεί (μεταφορά), οπότε και η λήξη του κρίνεται ως προς αυτήν.
        payroll_from = constraints.check_payroll_from(payroll_from, hire_date)
        if schedule_mode == "fixed":
            if schedule_end_date is not None and schedule_end_date < (
                payroll_from or hire_date
            ):
                raise ValueError(
                    "Η ημερομηνία λήξης του σταθερού εβδομαδιαίου δεν μπορεί "
                    "να είναι πριν την έναρξη ισχύος του προγράμματος"
                )
        else:  # variable: απαιτείται πλήθος πλήρων εβδομάδων (1–10)
            schedule_weeks = int(schedule_weeks or 0)
            if not (1 <= schedule_weeks <= 10):
                raise ValueError(
                    "Για ημερήσιο πρόγραμμα δηλώστε πλήθος εβδομάδων 1 έως 10"
                )
        constraints.check_rehire(person, existing, hire_date)
        if ergani_hire_done or (payroll_from and payroll_from > hire_date):
            self.check_migration_open()
        # ΚΛΕΙΔΩΜΑ: κρίνεται από την ΑΝΑΛΗΨΗ, όχι από την πρόσληψη — μια παλιά
        # πρόσληψη που μεταφέρεται δεν αγγίζει καμία κλεισμένη περίοδο.
        self._check_open_period(payroll_from or hire_date, "πρόσληψη")
        constraints.check_contract(pay_type, monthly_gross, daily_wage, weekly_hours)
        # ΟΡΟΣ ΣΥΜΒΑΣΗΣ (όχι ασφαλιστικό στοιχείο): πενθήμερο/εξαήμερο
        weekly_system = constraints.check_weekly_system(weekly_system) or 5
        employment_relationship, fixed_term_from, fixed_term_to = (
            constraints.check_employment_relationship(
                employment_relationship,
                fixed_term_from,
                fixed_term_to,
                hire_date=hire_date,
                payroll_from=payroll_from,
            )
        )
        (
            oaed_placement,
            oaed_program,
            oaed_replacement,
            oaed_replaced_afm,
            oaed_replaced_amka,
            trial_period,
            trial_date_to,
        ) = constraints.check_oaed_hire_fields(
            oaed_placement,
            oaed_program,
            oaed_replacement,
            oaed_replaced_afm,
            oaed_replaced_amka,
            trial_period,
            trial_date_to,
            hire_date,
        )
        minor_booklet_number, unemployment_card_number = (
            constraints.check_hire_status_fields(
                minor_booklet_number, unemployment_card_number,
                minor=(constraints.age_on(person.birth_date, hire_date) or 99) < 18,
                required=not ergani_hire_done,
            )
        )
        (
            working_card,
            flexible_arrival_minutes,
            break_within_schedule,
            break_minutes,
        ) = constraints.check_digital_card_fields(
            working_card,
            flexible_arrival_minutes,
            break_within_schedule,
            break_minutes,
        )
        if weekly_pattern is None:
            raise ValueError(
                "Απαιτείται πρόγραμμα εργασίας (τουλάχιστον μία ημέρα με ωράριο) — "
                "υποβάλλεται ως ΟΧΕ στο ΕΡΓΑΝΗ"
            )
        constraints.check_schedule(weekly_pattern, weekly_hours)
        # ΜΕΤΑΦΟΡΑ: έλεγχος κατώτατου στην ΑΝΑΛΗΨΗ, με τις τριετίες
        # ωριμασμένες από την πραγματική πρόσληψη (βλ. wage_check_basis).
        wage_on, wage_service = self.wage_check_basis(
            hire_date,
            payroll_from,
            service_years,
            frozen_service_years=frozen_service_years,
        )
        self.check_minimum(
            pay_type,
            monthly_gross,
            daily_wage,
            weekly_hours,
            wage_on,
            service_years=wage_service,
            married=married,
            extra_pct=extra_allowances_pct,
        )

        employment_id = "E" + uuid.uuid4().hex[:8]
        self.store.append(
            employment_id,
            EmploymentStarted(
                employment_id,
                person_id,
                hire_date,
                payroll_from,
                pay_type,
                monthly_gross,
                daily_wage,
                weekly_hours,
                weekly_system,
                float(service_years),
                float(frozen_service_years),
                bool(married),
                float(extra_allowances_pct),
                draft=bool(draft),
                employment_relationship=employment_relationship,
                fixed_term_from=fixed_term_from,
                fixed_term_to=fixed_term_to,
                oaed_placement=oaed_placement,
                oaed_program=oaed_program,
                oaed_replacement=oaed_replacement,
                oaed_replaced_afm=oaed_replaced_afm,
                oaed_replaced_amka=oaed_replaced_amka,
                trial_period=trial_period,
                trial_date_to=trial_date_to,
                minor_booklet_number=minor_booklet_number,
                unemployment_card_number=unemployment_card_number,
                working_card=working_card,
                flexible_arrival_minutes=flexible_arrival_minutes,
                break_within_schedule=break_within_schedule,
                break_minutes=break_minutes,
            ),
        )
        # ΣΤΑΘΕΡΟ: το εβδομαδιαίο template φέρει τη (προαιρετική) λήξη — μετά
        # από αυτήν δεν ισχύει πρόγραμμα (κενή = δεν λήγει ποτέ). ΜΕΤΑΒΛΗΤΟ:
        # το εβδομαδιαίο είναι ΜΟΝΟ template του grid (χωρίς λήξη).
        # ➜ Ισχύς από την ΑΝΑΛΗΨΗ: σε μεταφορά δεν έχει νόημα (ούτε δηλώνεται
        #   στο ΕΡΓΑΝΗ) πρόγραμμα αναδρομικά από μια πρόσληψη ετών πριν.
        schedule_from = payroll_from or hire_date
        schedule_end = schedule_end_date if schedule_mode == "fixed" else None
        # ➜ ΜΕΤΑΦΟΡΑ με ανάληψη στο παρελθόν: το ΕΡΓΑΝΗ δεν δέχεται αναδρομική
        #   ΟΧΕ, και τις ημέρες έως χθες τις έχει ήδη δηλώσει το προηγούμενο
        #   πρόγραμμα. Το σταθερό σπάει σε [ανάληψη, χθες] (καλύπτεται αμέσως,
        #   παρακάτω) + [σήμερα, λήξη] (εκκρεμεί η ΟΧΕ από εδώ).
        covered_until = (
            today - timedelta(days=1)
            if ergani_hire_done and today and schedule_from < today
            else None
        )
        periods = [(schedule_from, schedule_end)]
        if (covered_until and schedule_mode == "fixed"
                and (schedule_end is None or schedule_end >= today)):
            periods = [(schedule_from, covered_until), (today, schedule_end)]
        for start, end in periods:
            self.store.append(
                employment_id,
                WorkScheduleAssigned(employment_id, start, weekly_pattern, end),
            )
        self.store.append(
            employment_id,
            EmploymentScheduleModeSet(employment_id, schedule_mode),
        )

        # ΜΕΤΑΒΛΗΤΟ: υλοποιούμε ΑΜΕΣΩΣ ημερήσιο πρόγραμμα από το template, από
        # την ημ. πρόσληψης για `schedule_weeks` ΠΛΗΡΕΙΣ εβδομάδες (Δευ–Κυρ).
        # Αν η πρόσληψη πέφτει μεσοβδόμαδα, καλύπτεται και το υπόλοιπο της
        # τρέχουσας εβδομάδας ΕΠΙΠΛΕΟΝ των πλήρων (βλ. _daily_from_template).
        days = {}
        if schedule_mode == "variable":
            days = self._daily_from_template(
                schedule_from, weekly_pattern, schedule_weeks
            )
            self.set_daily_schedule(employment_id, days)
            # Οι ημέρες μένουν αδήλωτες (pending): η δήλωση γίνεται ΜΟΝΟ από
            # το «Πρόγραμμα → ΕΡΓΑΝΗ», μετά την αναγγελία πρόσληψης.

        # ΜΕΤΑΦΟΡΑ: η αναγγελία πρόσληψης έχει ήδη σταλεί από το προηγούμενο
        # πρόγραμμα — καταγράφεται ΩΣ ΥΠΟΒΛΗΘΕΙΣΑ χωρίς καμία κλήση στο ΕΡΓΑΝΗ.
        # Έτσι δεν ξαναστέλνεται και δεν μπλοκάρει τις παρουσίες (§5.7α).
        if ergani_hire_done:
            self.record_external_hire_submission(
                employment_id, reference=ergani_hire_reference
            )
        # ΚΑΛΥΨΗ από το προηγούμενο πρόγραμμα (βλ. `covered_until`): ΚΑΜΙΑ κλήση
        # στο ΕΡΓΑΝΗ — ίδια λογική με το external WebE3N. Ημερήσια: batch 0 =
        # «εκτός συστήματος» (οι πραγματικές υποβολές αριθμούνται από το 1).
        if covered_until and schedule_mode == "fixed":
            self.store.append(employment_id, ScheduleDeclaredToErgani(
                employment_id, weekly_pattern, schedule_from,
                min(covered_until, schedule_end or covered_until)))
        elif covered_until:
            past = {d.isoformat(): h for d, h in days.items() if d <= covered_until}
            if past:
                self.store.append(
                    employment_id, ScheduleDaysSubmitted(employment_id, 0, past))

        # Η υποβολή στο ΕΡΓΑΝΗ ΔΕΝ γίνεται αυτόματα στην πρόσληψη (§5.7):
        #   - ΣΤΑΘΕΡΟ  → κουμπί «Υποβολή ΟΧΕ» στην καρτέλα (standing εβδομαδιαίο,
        #                με τη λήξη του αν έχει) — καλύπτει όλες τις ημέρες.
        #   - ΜΕΤΑΒΛΗΤΟ → δήλωση ρητά ανά εβδομάδα από το «Πρόγραμμα».
        # Και τα δύο είναι προαπαιτούμενο της «Έγκρισης Παρουσιών».
        return employment_id

    def migration_closed_by(self):
        """(έτος, μήνας) της ΠΡΩΤΗΣ υπολογισμένης μισθοδοσίας, ή None.

        Μετά από αυτήν δεν εισάγεται υπάρχων εργαζόμενος (μεταφορά από άλλο
        πρόγραμμα): η μισθοδοσία εκδόθηκε, άρα έπρεπε να τον περιλαμβάνει.
        Read-only — το διαβάζει και η φόρμα πρόσληψης για να κρύψει την επιλογή."""
        return min(((p.period_year, p.period_month) for p in self.proj.payrolls),
                   default=None)

    def check_migration_open(self):
        """Απορρίπτει εισαγωγή υπάρχοντος εργαζομένου μετά την 1η μισθοδοσία."""
        first = self.migration_closed_by()
        if first is not None:
            raise ValueError(
                "Η εισαγωγή υπάρχοντος εργαζομένου (μεταφορά από άλλο πρόγραμμα) "
                "επιτρέπεται μόνο πριν από την πρώτη μισθοδοσία — έχει ήδη "
                f"εκδοθεί μισθοδοσία {first[1]:02d}/{first[0]}, που θα έπρεπε "
                "να περιλαμβάνει όλους τους ενεργούς εργαζομένους. Καταχωρήστε "
                "τον ως νέα πρόσληψη."
            )

    def record_external_hire_submission(self, employment_id, reference=""):
        """Σημαδεύει την αναγγελία πρόσληψης ως **ήδη υποβληθείσα ΕΚΤΟΣ του
        συστήματος** (προηγούμενο πρόγραμμα μισθοδοσίας). ΚΑΜΙΑ κλήση στο
        ΕΡΓΑΝΗ — μόνο το event, ώστε (α) να μην ξανασταλεί, (β) να μη
        μπλοκάρει τις παρουσίες, (γ) να φαίνεται στο ιστορικό ΤΙ ακριβώς
        συνέβη (`status="external"`, όχι ψεύτικο `submitted`)."""
        if self.ergani_hire_submitted(
            employment_id, "WebE3N"
        ) or self.ergani_hire_submitted(employment_id, "E3"):
            raise ValueError("Η αναγγελία πρόσληψης έχει ήδη υποβληθεί")
        self.store.append(
            employment_id,
            ErganiSubmissionRecorded(
                "WebE3N",
                employment_id,
                "{}",
                "external",
                str(reference or "").strip() or "ΕΚΤΟΣ ΣΥΣΤΗΜΑΤΟΣ",
                self.ergani.environment,
            ),
        )

    def correct_hire_details(
        self,
        employment_id,
        hire_date,
        pay_type="monthly",
        monthly_gross=0.0,
        daily_wage=0.0,
        weekly_hours=40.0,
        weekly_system=5,
        weekly_pattern=None,
        service_years=0.0,
        frozen_service_years=0.0,
        married=False,
        extra_allowances_pct=0.0,
        schedule_mode="fixed",
        schedule_end_date=None,
        schedule_weeks=0,
        employment_relationship="0",
        fixed_term_from=None,
        fixed_term_to=None,
        oaed_placement="0",
        oaed_program="",
        oaed_replacement="0",
        oaed_replaced_afm="",
        oaed_replaced_amka="",
        trial_period="0",
        trial_date_to=None,
        minor_booklet_number="",
        unemployment_card_number="",
        working_card="1",
        flexible_arrival_minutes="120",
        break_within_schedule="0",
        break_minutes="15",
    ):
        """Διορθώνει την αρχική φόρμα πρόσληψης μόνο πριν από το WebE3N."""
        emp = self.proj.employments.get(employment_id)
        if emp is None or not emp.active:
            raise ValueError(f"Ανενεργή/άγνωστη απασχόληση {employment_id}")
        if self.ergani_hire_submitted(
            employment_id, "WebE3N"
        ) or self.ergani_hire_submitted(employment_id, "E3"):
            raise ValueError(
                "Η φόρμα πρόσληψης έχει ήδη υποβληθεί και είναι μόνο για προβολή"
            )
        constraints.check_service_allowances(
            service_years, frozen_service_years, extra_allowances_pct
        )
        if schedule_mode not in ("fixed", "variable"):
            raise ValueError("Άγνωστος τύπος προγράμματος (fixed/variable)")
        if schedule_mode == "fixed":
            if schedule_end_date is not None and schedule_end_date < hire_date:
                raise ValueError(
                    "Η λήξη του σταθερού εβδομαδιαίου προηγείται της πρόσληψης"
                )
            daily_schedule = {}
        else:
            schedule_weeks = int(schedule_weeks or 0)
            if not 1 <= schedule_weeks <= 10:
                raise ValueError(
                    "Για ημερήσιο πρόγραμμα δηλώστε πλήθος εβδομάδων 1 έως 10"
                )
            daily_schedule = {
                d.isoformat(): hours
                for d, hours in self._daily_from_template(
                    # μεταφορά: από την ανάληψη, όπως στην αρχική πρόσληψη
                    max(emp.payroll_from or hire_date, hire_date),
                    weekly_pattern or {}, schedule_weeks,
                ).items()
            }
        person = emp.person
        others = [
            self.proj.employments[eid]
            for eid in person.employment_ids
            if eid != employment_id and eid in self.proj.employments
        ]
        constraints.check_rehire(person, others, hire_date)
        self._check_open_period(emp.hire_date, "διόρθωση πρόσληψης")
        self._check_open_period(hire_date, "διόρθωση πρόσληψης")
        constraints.check_contract(pay_type, monthly_gross, daily_wage, weekly_hours)
        weekly_system = constraints.check_weekly_system(weekly_system) or 5
        employment_relationship, fixed_term_from, fixed_term_to = (
            constraints.check_employment_relationship(
                employment_relationship,
                fixed_term_from,
                fixed_term_to,
                hire_date=hire_date,
                payroll_from=emp.payroll_from,
            )
        )
        (
            oaed_placement,
            oaed_program,
            oaed_replacement,
            oaed_replaced_afm,
            oaed_replaced_amka,
            trial_period,
            trial_date_to,
        ) = constraints.check_oaed_hire_fields(
            oaed_placement,
            oaed_program,
            oaed_replacement,
            oaed_replaced_afm,
            oaed_replaced_amka,
            trial_period,
            trial_date_to,
            hire_date,
        )
        person = self.proj.persons.get(emp.person_id)
        minor_booklet_number, unemployment_card_number = (
            constraints.check_hire_status_fields(
                minor_booklet_number, unemployment_card_number,
                minor=(constraints.age_on(getattr(person, "birth_date", None),
                                          hire_date) or 99) < 18,
                required=True,
            )
        )
        (
            working_card,
            flexible_arrival_minutes,
            break_within_schedule,
            break_minutes,
        ) = constraints.check_digital_card_fields(
            working_card,
            flexible_arrival_minutes,
            break_within_schedule,
            break_minutes,
        )
        if not weekly_pattern:
            raise ValueError("Απαιτείται πρόγραμμα εργασίας")
        constraints.check_schedule(weekly_pattern, weekly_hours)
        wage_on, wage_service = self.wage_check_basis(
            hire_date,
            emp.payroll_from,
            service_years,
            frozen_service_years=frozen_service_years,
        )
        self.check_minimum(
            pay_type,
            monthly_gross,
            daily_wage,
            weekly_hours,
            wage_on,
            service_years=wage_service,
            married=married,
            extra_pct=extra_allowances_pct,
        )
        self.store.append(
            employment_id,
            EmploymentHireDetailsCorrected(
                employment_id=employment_id,
                hire_date=hire_date,
                pay_type=pay_type,
                monthly_gross=monthly_gross,
                daily_wage=daily_wage,
                weekly_hours=weekly_hours,
                weekly_system=weekly_system,
                service_years=float(service_years),
                frozen_service_years=float(frozen_service_years),
                married=bool(married),
                extra_allowances_pct=float(extra_allowances_pct),
                weekly_pattern=dict(weekly_pattern),
                schedule_mode=schedule_mode,
                schedule_end_date=(
                    schedule_end_date if schedule_mode == "fixed" else None
                ),
                daily_schedule=daily_schedule,
                employment_relationship=employment_relationship,
                fixed_term_from=fixed_term_from,
                fixed_term_to=fixed_term_to,
                oaed_placement=oaed_placement,
                oaed_program=oaed_program,
                oaed_replacement=oaed_replacement,
                oaed_replaced_afm=oaed_replaced_afm,
                oaed_replaced_amka=oaed_replaced_amka,
                trial_period=trial_period,
                trial_date_to=trial_date_to,
                minor_booklet_number=minor_booklet_number,
                unemployment_card_number=unemployment_card_number,
                working_card=working_card,
                flexible_arrival_minutes=flexible_arrival_minutes,
                break_within_schedule=break_within_schedule,
                break_minutes=break_minutes,
            ),
        )

    @staticmethod
    def _daily_from_template(hire_date, weekly_pattern: dict, weeks: int) -> dict:
        """Παράγει ημερήσιο πρόγραμμα {date: ωράριο} από το εβδομαδιαίο
        template, από την `hire_date` για `weeks` ΠΛΗΡΕΙΣ εβδομάδες (Δευ–Κυρ).
        Αν η πρόσληψη ΔΕΝ είναι Δευτέρα, καλύπτεται επιπλέον το υπόλοιπο της
        τρέχουσας (μερικής) εβδομάδας — δηλ. η μέτρηση των πλήρων εβδομάδων
        ξεκινά από την επόμενη Δευτέρα. Κενό ωράριο = ρεπό (δηλώνεται ρητά)."""
        first_monday = hire_date - timedelta(days=hire_date.weekday())
        full_start = (
            hire_date if hire_date.weekday() == 0 else first_monday + timedelta(days=7)
        )
        last_day = full_start + timedelta(days=weeks * 7 - 1)  # Κυριακή τελ. εβδ.
        days, d = {}, hire_date
        while d <= last_day:
            days[d] = weekly_pattern.get(constraints.DAY_CODES[d.weekday()], "")
            d += timedelta(days=1)
        return days

    # ------------------------------------------- Δήλωση προγράμματος στο ΕΡΓΑΝΗ
    def _declare_standing_weekly(
        self, employment_id, effective_from=None, end_date=None, pattern=None
    ):
        """Ένα σταθερό εβδομαδιαίο (βλ. `_declare_weekly`)."""
        return self._declare_weekly([(employment_id, effective_from, end_date, pattern)])

    def _declare_weekly(self, items):
        """Υποβάλλει σταθερά εβδομαδιαία στο ΕΡΓΑΝΗ σε ΜΙΑ κλήση (WTOWeek,
        `ergani.merge_wto`) και τα καταγράφει ως κάλυψη. `items` =
        [(employment_id, ισχύς από, λήξη, μοτίβο)] — None = της απασχόλησης·
        `end_date` κενό = χωρίς λήξη. Χωρίς στοιχεία εταιρείας δεν
        υποβάλλεται. Ένα event υποβολής για όλους: `employee_id` ο εργαζόμενος
        αν είναι ΕΝΑΣ, αλλιώς κενό (η κάλυψη ζει στα ScheduleDeclaredToErgani)."""
        todo = []
        for eid, start, end, pattern in items:
            emp = self.proj.employments[eid]
            pattern = dict(pattern if pattern is not None else emp.schedule)
            if pattern:
                todo.append((eid, emp, start or emp.payroll_start, end, pattern))
        if not self.proj.company or not todo:
            return None
        res = self._ergani_submit(
            "WTO",
            todo[0][0] if len({t[0] for t in todo}) == 1 else "",
            ergani_mod.merge_wto([
                ergani_mod.build_wto(self.proj.company, emp, pattern, start, end)
                for _eid, emp, start, end, pattern in todo
            ]),
            endpoint="WTOWeek",
        )
        # Κάλυψη ΜΟΝΟ αν το ΕΡΓΑΝΗ τη δέχτηκε — απορριφθείσα ΟΧΕ δεν καλύπτει
        # ΚΑΝΕΝΑΝ (μένει καταγεγραμμένη ως failed και ξαναστέλνεται).
        if res["status"] != "failed":
            for eid, _emp, start, end, pattern in todo:
                self.store.append(eid, ScheduleDeclaredToErgani(eid, pattern, start, end))
        return res

    # -------------------------------------------------- Υποβολές πρόσληψης ΕΡΓΑΝΗ
    def hire_announced(self, employment_id: str) -> bool:
        """Έχει γίνει (ή μεταφερθεί) η αναγγελία πρόσληψης; Χωρίς αυτήν το
        ΕΡΓΑΝΗ απορρίπτει την ΟΧΕ («Δεν υπάρχει ενεργή σχέση εργασίας»)."""
        return (self.ergani_hire_submitted(employment_id, "WebE3N")
                or self.ergani_hire_submitted(employment_id, "E3"))

    def _require_hire_announced(self, employment_id: str):
        if not self.hire_announced(employment_id):
            name = self.proj.employments[employment_id].full_name
            raise ValueError(
                f"{name}: υποβάλετε πρώτα την Αναγγελία Πρόσληψης (WebE3N) — "
                "το ΕΡΓΑΝΗ δέχεται ΟΧΕ μόνο για ενεργή σχέση εργασίας")

    def ergani_hire_submitted(self, employment_id: str, doc_type: str) -> bool:
        """Έχει ήδη υποβληθεί (επιτυχώς ή εικονικά) το έντυπο για αυτή την
        απασχόληση; Αποτυχημένη υποβολή (failed) επιτρέπεται να ξαναγίνει."""
        return any(
            s.employee_id == employment_id
            and s.doc_type == doc_type
            and s.status != "failed"
            for s in self.proj.ergani_submissions
        )

    def build_hire_web_e3n(self, employment_id):
        """Παράγει το ακριβές JSON WebE3N χωρίς να πραγματοποιεί υποβολή."""
        emp = self.proj.employments.get(employment_id)
        if emp is None:
            raise ValueError(f"Άγνωστη απασχόληση {employment_id}")
        if self.proj.company is None:
            raise ValueError("Δεν έχουν καταχωρηθεί στοιχεία εταιρείας")
        if emp.insurance_profile is None:
            raise ValueError(
                f"Λείπει ασφαλιστικό προφίλ για την απασχόληση {employment_id}"
            )
        branch_no = emp.insurance_profile.branch_no
        branch = self.get_branch(branch_no)
        return ergani_mod.build_web_e3n(
            self.proj.company,
            emp,
            self.ergani_profile(emp.insurance_profile),
            emp.schedule,
            branch=branch,
            files={kind: doc["content"]
                   for kind in self.hire_document_kinds(employment_id)
                   if (doc := self.person_document(emp.person_id, kind))},
        )

    def hire_document_kinds(self, employment_id) -> list[str]:
        """Ποια έγγραφα του προσώπου ΑΦΟΡΟΥΝ την αναγγελία πρόσληψης:
        νομιμοποιητικά για υπηκοότητα εκτός ΕΕ, βιβλιάριο για ηλικία < 18
        την ημέρα πρόσληψης."""
        emp = self.proj.employments[employment_id]
        p = self.proj.persons.get(emp.person_id)
        if p is None:
            return []
        kinds = []
        if p.nationality_code and not ergani_parameters.is_eu_nationality(
                p.nationality_code):
            kinds.append("foreign_file")
        if (constraints.age_on(p.birth_date, emp.hire_date) or 99) < 18:
            kinds.append("young_file")
        return kinds

    def missing_hire_documents(self, employment_id) -> list[str]:
        """Ετικέτες εγγράφων που αφορούν την πρόσληψη αλλά ΔΕΝ έχουν ανέβει."""
        emp = self.proj.employments[employment_id]
        return [PERSON_DOCUMENT_KINDS[k] for k in self.hire_document_kinds(employment_id)
                if not self.person_document(emp.person_id, k)]

    def submit_hire_e3(self, employment_id):
        """WebE3N — Αναγγελία Πρόσληψης (χειροκίνητη ενεργοποίηση)."""
        if self.ergani_hire_submitted(
            employment_id, "WebE3N"
        ) or self.ergani_hire_submitted(employment_id, "E3"):
            raise ValueError("Η αναγγελία πρόσληψης έχει ήδη υποβληθεί")
        res = self._ergani_submit(
            "WebE3N",
            employment_id,
            self.build_hire_web_e3n(employment_id),
        )
        return res

    def submit_hire_wto(self, employment_id):
        """ΟΧΕ — δήλωση ΣΤΑΘΕΡΟΥ εβδομαδιαίου (χωρίς λήξη) στο ΕΡΓΑΝΗ, με
        χειροκίνητη ενεργοποίηση. Ισχύει ΜΟΝΟ για fixed πρόγραμμα — το μεταβλητό
        δηλώνεται ρητά ανά εβδομάδα από το «Πρόγραμμα». Παράγει και coverage
        event ώστε να μετράει στην κάλυψη των παρουσιών."""
        emp = self.proj.employments.get(employment_id)
        if emp is None:
            raise ValueError(f"Άγνωστη απασχόληση {employment_id}")
        if self.proj.company is None:
            raise ValueError("Δεν έχουν καταχωρηθεί στοιχεία εταιρείας")
        if emp.schedule_mode == "variable":
            raise ValueError(
                "Το μεταβλητό πρόγραμμα δηλώνεται ανά εβδομάδα από το «Πρόγραμμα»"
            )
        if not emp.schedule:
            raise ValueError("Δεν έχει οριστεί εβδομαδιαίο πρόγραμμα εργασίας")
        # Η ΚΑΛΥΨΗ (όχι το employee_id της υποβολής: μια κοινή κλήση για
        # πολλούς έχει κενό) — ό,τι δηλώθηκε επιτυχώς γράφει standing_history
        if emp.standing_history:
            raise ValueError("Η ΟΧΕ πρόσληψης έχει ήδη υποβληθεί")
        self._require_hire_announced(employment_id)
        self._declare_standing_weekly(employment_id)

    def submit_hire_wto_variable(self, employment_id):
        """ΟΧΕ μεταβλητού προγράμματος από την καρτέλα: δηλώνει στο ΕΡΓΑΝΗ τις
        ΑΔΗΛΩΤΕΣ (pending) ημέρες του ΣΧΕΔΙΑΣΜΕΝΟΥ ημερήσιου προγράμματος
        (`daily_schedule`) — αυτές που παρήχθησαν στην πρόσληψη «ημερήσιο για N
        εβδομάδες» αλλά δεν προλάβαν να δηλωθούν (π.χ. χωρίς στοιχεία εταιρείας
        τότε). Οι υπόλοιπες εβδομάδες δηλώνονται ρητά από το «Πρόγραμμα»."""
        emp = self.proj.employments.get(employment_id)
        if emp is None:
            raise ValueError(f"Άγνωστη απασχόληση {employment_id}")
        if emp.schedule_mode != "variable":
            raise ValueError("Μόνο για μεταβλητό (ημερήσιο) πρόγραμμα")
        days = [
            d
            for d in sorted(emp.daily_schedule)
            if emp.active_on(d) and emp.day_status(d) == "pending"
        ]
        if not days:
            raise ValueError(
                "Δεν υπάρχουν αδήλωτες ημέρες σχεδιασμένου προγράμματος — "
                "δήλωσε το πρόγραμμα ανά εβδομάδα από το «Πρόγραμμα»"
            )
        return self.submit_schedule_days({employment_id: days})
