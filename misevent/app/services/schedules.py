"""
Πρόγραμμα εργασίας (εβδομαδιαίο/ημερήσιο) και δήλωσή του στο ΕΡΓΑΝΗ

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

from calendar import monthrange
from datetime import date, timedelta

import misevent.integrations.ergani as ergani_mod
import misevent.rules.config as config
import misevent.rules.constraints as constraints
from misevent.domain.events import (
    ChildcareReductionSet,
    DailyScheduleAssigned,
    RetroAbsenceReasonSet,
    RetroScheduleSubmitted,
    ScheduleDaysSubmitted,
    ScheduleSystemDeclared,
    WorkScheduleAssigned,
)
from misevent.integrations import card_punches as cp
from misevent.integrations import ergani_parameters

SCHEDULE_SYSTEMS = {"pre": "Προαναγγελτικό", "retro": "Απολογιστικό"}


class SchedulesMixin:
    def set_childcare_reduction(self, employee_id, date_from, option=""):
        """Μειωμένο ωράριο φροντίδας τέκνου. Η σύμβαση ΔΕΝ αλλάζει· επιτρέπεται
        απλώς μικρότερο πρόγραμμα, που δηλώνεται στο ΕΡΓΑΝΗ όπως κάθε αλλαγή
        προγράμματος. Νέα δήλωση αντικαθιστά την προηγούμενη· "" = κατάργηση."""
        emp = self.proj.employments.get(employee_id)
        if emp is None:
            raise ValueError(f"Άγνωστος εργαζόμενος {employee_id}")
        if option and option not in config.CHILDCARE_REDUCTION_OPTIONS:
            raise ValueError(f"Άγνωστη επιλογή μειωμένου ωραρίου «{option}»")
        if option and not emp.active_on(date_from):
            raise ValueError("Η έναρξη είναι εκτός της περιόδου απασχόλησης")
        self.store.append(
            employee_id, ChildcareReductionSet(employee_id, date_from, option)
        )

    def set_schedule(
        self, employee_id, effective_from, weekly_pattern: dict, end_date=None
    ):
        emp = self.proj.employments.get(employee_id)
        if emp is None:
            raise ValueError(f"Άγνωστος εργαζόμενος {employee_id}")
        self._check_open_period(effective_from, "αλλαγή προγράμματος")
        # Οι ώρες του προγράμματος πρέπει να συμφωνούν με τη σύμβαση (40 = πλήρης)
        constraints.check_schedule(
            weekly_pattern, emp.weekly_hours, emp.childcare_hours_on(effective_from)
        )
        if end_date is not None and end_date < effective_from:
            raise ValueError(
                "Η ημερομηνία λήξης του εβδομαδιαίου δεν μπορεί να είναι "
                "πριν την ημερομηνία έναρξης"
            )
        # Όρια με τις ΓΕΙΤΟΝΙΚΕΣ ημέρες (11ωρη ανάπαυση). Μέσα στο διάστημα
        # ισχύος το νέο εβδομαδιαίο ΣΒΗΝΕΙ τις ημερήσιες αλλαγές (latest-wins),
        # άρα ελέγχεται το ωράριο του ΜΟΤΙΒΟΥ — όχι το ημερήσιο που θα χαθεί.
        # (Μέσα στο μοτίβο, και Κυρ→Δευ, ελέγχει ήδη το check_schedule.)
        def pattern_on(d):
            return weekly_pattern.get(constraints.DAY_CODES[d.weekday()], "")

        previous_day = effective_from - timedelta(days=1)
        constraints.check_daily_rest(
            emp.day_schedule(previous_day),
            pattern_on(effective_from),
            previous_day.strftime("%d/%m/%Y"),
            effective_from.strftime("%d/%m/%Y"),
        )
        if end_date is not None:
            # μετά τη λήξη μένουν μόνο τυχόν ημερήσιες αλλαγές (καμία εβδομάδα)
            after = end_date + timedelta(days=1)
            constraints.check_daily_rest(
                pattern_on(end_date),
                emp.daily_schedule.get(after, ""),
                end_date.strftime("%d/%m/%Y"),
                after.strftime("%d/%m/%Y"),
            )
        self.store.append(
            employee_id,
            WorkScheduleAssigned(employee_id, effective_from, weekly_pattern, end_date),
        )
        # ΚΑΜΙΑ αυτόματη υποβολή: το ΣΤΑΘΕΡΟ εβδομαδιαίο μένει «εκκρεμές»
        # (`emp.pending_weekly`) και δηλώνεται ΜΟΝΟ από το «Πρόγραμμα → ΕΡΓΑΝΗ»
        # (`submit_weekly_schedules`). ΜΕΤΑΒΛΗΤΟ: μόνο template του grid.

    def submit_weekly_schedules(self, selection: dict) -> int:
        """Υποβολή ΕΚΚΡΕΜΩΝ εβδομαδιαίων (standing ΟΧΕ) στο ΕΡΓΑΝΗ:
        `selection` = {employment_id: [ισχύς από, …]}. Με τη σειρά ισχύος,
        ώστε το νεότερο να είναι το τελευταίο δηλωμένο. Επιστρέφει πλήθος."""
        if self.proj.company is None:
            raise ValueError("Δεν έχουν καταχωρηθεί στοιχεία εταιρείας")
        todo = []
        for eid, starts in selection.items():
            emp = self.proj.employments.get(eid)
            if emp is None:
                raise ValueError(f"Άγνωστη απασχόληση {eid}")
            self._require_hire_announced(eid)
            wanted = {s if isinstance(s, date) else date.fromisoformat(str(s))
                      for s in starts}
            todo += [(eid, p) for p in emp.pending_weekly if p[0] in wanted]
        if not todo:
            raise ValueError("Δεν επιλέχθηκε εκκρεμές εβδομαδιαίο πρόγραμμα")
        rejected = []
        for eid, (start, pattern, end) in sorted(todo, key=lambda t: t[1][0]):
            self._declare_standing_weekly(eid, start, end, pattern)
            if any(p[0] == start for p in self.proj.employments[eid].pending_weekly):
                rejected.append(self.proj.employments[eid].full_name)
        if rejected:
            raise ValueError("ΑΠΕΡΡΙΨΕ το ΕΡΓΑΝΗ το εβδομαδιαίο: " + ", ".join(rejected))
        return len(todo)

    def validate_daily_schedule(self, employee_id, days: dict) -> dict:
        """Ελέγχει ημερήσιες μεταβολές χωρίς να γράψει event."""
        if employee_id not in self.proj.employments:
            raise ValueError(f"Άγνωστος εργαζόμενος {employee_id}")
        if not days:
            raise ValueError("Κενό ημερήσιο πρόγραμμα")
        emp = self.proj.employments[employee_id]
        normalized = {
            (d if isinstance(d, date) else date.fromisoformat(str(d))): (h or "")
            for d, h in days.items()
        }
        # Κλεισμένη περίοδος = παγωμένο πρόγραμμα. Ο έλεγχος ζει ΕΔΩ (και όχι
        # στη set_daily_schedule) ώστε να τον περνά και ο προέλεγχος του UI.
        self._check_open_days(normalized, "αλλαγή προγράμματος")
        # Ίδιοι κανόνες μορφής με το εβδομαδιαίο: σπαστό (2 τμήματα, κενό
        # >= 3 ώρες), τηλεεργασία με πρόθεμα «Τ». Κενό ωράριο = ρεπό.
        for h in normalized.values():
            if h:
                constraints.parse_day_schedule(h)

        def effective_schedule(day):
            if not emp.active_on(day):
                return ""
            return normalized.get(day, emp.day_schedule(day))

        boundaries = set()
        for day in normalized:
            boundaries.add((day - timedelta(days=1), day))
            boundaries.add((day, day + timedelta(days=1)))
        for previous_day, following_day in sorted(boundaries):
            constraints.check_daily_rest(
                effective_schedule(previous_day),
                effective_schedule(following_day),
                previous_day.strftime("%d/%m/%Y"),
                following_day.strftime("%d/%m/%Y"),
            )
        return normalized

    def set_daily_schedule(self, employee_id, days: dict):
        """ΗΜΕΡΗΣΙΟ πρόγραμμα: {date: "09:00-17:00"} (κενό ωράριο = ρεπό).

        Υπερισχύει του εβδομαδιαίου για τις ημέρες που ορίζει. Καθαρός
        ΣΧΕΔΙΑΣΜΟΣ στο grid — **ΔΕΝ υποβάλλεται στο ΕΡΓΑΝΗ**. Η δήλωση γίνεται
        ρητά, σε αριθμημένα batches (`submit_schedule_days`)."""
        normalized = self.validate_daily_schedule(employee_id, days)
        payload = {day.isoformat(): hours for day, hours in normalized.items()}
        self.store.append(employee_id, DailyScheduleAssigned(employee_id, payload))

    # ------------------- Δήλωση ημερήσιου προγράμματος στο ΕΡΓΑΝΗ (ΜΕΤΑΒΛΗΤΟ)
    @staticmethod
    def _week_monday(d: date) -> date:
        return d - timedelta(days=d.weekday())

    def next_schedule_batch_no(self) -> int:
        """Ο επόμενος καθολικός αριθμός batch υποβολής ημερήσιου προγράμματος."""
        return (max(self.proj.schedule_batches, default=0)) + 1

    def week_submittable_days(self, employment_id, monday: date) -> list:
        """Οι ΕΝΕΡΓΕΣ ημέρες της εβδομάδας που εκκρεμούν δήλωση/επαναδήλωση
        (κατάσταση pending ή modified) — αυτές θα σταλούν στο ΕΡΓΑΝΗ."""
        emp = self.proj.employments.get(employment_id)
        if emp is None:
            return []
        return [
            d
            for d in emp.active_days_in_week(monday)
            if emp.day_status(d) in ("pending", "modified")
        ]

    def submit_schedule_days(self, selection: dict, absences: dict | None = None):
        """Υποβάλλει ΗΜΕΡΗΣΙΑ προγράμματα στο ΕΡΓΑΝΗ σε ΕΝΑ αριθμημένο batch
        (καθολική αρίθμηση εταιρείας). `selection` = {employment_id: [date, …]}.
        `absences` = {employment_id: {date: [(κωδικός, από, έως)]}} — ΑΠΟΥΣΙΕΣ
        μέσα στο ωράριο (απολογιστικό, `retro_absences`): στέλνονται ως
        αναλυτικές εγγραφές της ημέρας και μετρούν στο εβδομαδιαίο σύνολο.

        Έλεγχοι πριν γραφτεί event:
          - στοιχεία εταιρείας,
          - κάθε ημέρα είναι εντός περιόδου απασχόλησης,
          - ΣΥΝΕΧΕΙΑ ανά εργαζόμενο: δεν αφήνεται προγενέστερη ΑΔΗΛΩΤΗ ημέρα
            (pending) πριν την πρώτη υποβαλλόμενη — καμία τρύπα.
        Σε κάθε πλήρη εβδομάδα απασχόλησης, το άθροισμα δεν μπορεί να είναι
        μικρότερο από τις συμβατικές ώρες. Μπορεί να είναι μεγαλύτερο λόγω
        πρόσθετης εργασίας, υπερεργασίας ή υπερωρίας.
        Επιστρέφει {"batch_no", "count" (σύνολο ημερήσιων), "employees"}."""
        if self.proj.company is None:
            raise ValueError("Δεν έχουν καταχωρηθεί στοιχεία εταιρείας")
        absences = absences or {}
        grouped = {}
        for eid, dates in selection.items():
            emp = self.proj.employments.get(eid)
            if emp is None:
                raise ValueError(f"Άγνωστη απασχόληση {eid}")
            absent = absences.get(eid, {})
            self._require_hire_announced(eid)  # ΟΧΕ μόνο για ενεργή σχέση εργασίας
            days = sorted(
                {
                    d if isinstance(d, date) else date.fromisoformat(str(d))
                    for d in dates
                }
            )
            if not days:
                continue
            for d in days:
                if not emp.active_on(d):
                    raise ValueError(
                        f"{emp.full_name}: η {d.strftime('%d/%m/%Y')} είναι εκτός "
                        "περιόδου απασχόλησης"
                    )
            # ΣΥΝΕΧΕΙΑ: καμία αδήλωτη ημέρα πριν την πρώτη υποβαλλόμενη.
            earlier = self._earliest_pending_before(emp, days[0])
            if earlier is not None:
                raise ValueError(
                    f"{emp.full_name}: υπάρχει προγενέστερη αδήλωτη ημέρα "
                    f"({earlier.strftime('%d/%m/%Y')}) — υπόβαλε πρώτα τις "
                    "προηγούμενες ώστε να μη μένουν κενά"
                )
            # Κάθε επηρεαζόμενη ΠΛΗΡΗΣ εβδομάδα ελέγχεται, είτε είναι πρώτη
            # δήλωση είτε μεταβολή ήδη δηλωμένης ημέρας. Οι μερικές εβδομάδες
            # πρόσληψης/αποχώρησης εξαιρούνται επειδή δεν καλύπτουν 7 ημέρες
            # της περιόδου απασχόλησης.
            for monday in sorted({self._week_monday(d) for d in days}):
                week_days = [monday + timedelta(days=i) for i in range(7)]
                if not all(emp.active_on(d) for d in week_days):
                    continue
                # η δηλωμένη ΑΠΟΥΣΙΑ (λόγος) καλύπτει τις ώρες που λείπουν
                total_hours = round(
                    sum(
                        constraints.day_schedule_minutes(emp.day_schedule(d))
                        + sum(e - s for _c, s, e in absent.get(d, ()))
                        for d in week_days
                    )
                    / 60,
                    2,
                )
                sunday = monday + timedelta(days=6)
                label = f"{monday.strftime('%d/%m')}–{sunday.strftime('%d/%m/%Y')}"
                # Μειωμένο ωράριο φροντίδας τέκνου: λιγότερες ώρες ανά ημέρα εργασίας
                reduction = sum(
                    emp.childcare_hours_on(d)
                    for d in week_days
                    if constraints.day_schedule_minutes(emp.day_schedule(d))
                )
                constraints.check_week_hours_minimum(
                    total_hours, emp.weekly_hours - reduction, label, emp.full_name
                )
            grouped[eid] = days
        if not grouped:
            raise ValueError("Δεν επιλέχθηκαν ημέρες προς υποβολή")

        batch_no = self.next_schedule_batch_no()
        total = 0
        rejected = []
        for eid, days in grouped.items():
            emp = self.proj.employments[eid]
            payload = {d.isoformat(): emp.day_schedule(d) for d in days}
            res = self._ergani_submit(
                "WTO",
                eid,
                ergani_mod.build_wto_variable(
                    self.proj.company, emp, payload, self._week_monday(days[0]),
                    {d.isoformat(): rows for d, rows in absences.get(eid, {}).items()},
                ),
                endpoint="WTODaily",
            )
            # Απορριφθείσες ημέρες ΔΕΝ σημειώνονται δηλωμένες — μένουν pending.
            if res["status"] == "failed":
                rejected.append(f"{emp.full_name}: {res['reference']}")
                continue
            self.store.append(eid, ScheduleDaysSubmitted(eid, batch_no, payload))
            total += len(payload)
        if rejected:
            raise ValueError(
                (f"Δηλώθηκαν {total} ημερήσια προγράμματα· " if total else "")
                + "ΑΠΕΡΡΙΨΕ το ΕΡΓΑΝΗ — " + " · ".join(rejected))
        return {"batch_no": batch_no, "count": total, "employees": len(grouped)}

    def _earliest_pending_before(self, emp, limit: date):
        """Η πρώτη ΑΔΗΛΩΤΗ (pending) ενεργή ημέρα πριν την `limit`, ή None."""
        if emp.payroll_start is None:
            return None
        d = emp.payroll_start
        while d < limit:
            if emp.active_on(d) and emp.day_status(d) == "pending":
                return d
            d += timedelta(days=1)
        return None

    def revert_days(self, selection: dict) -> int:
        """Ακυρώνει ΜΗ ΥΠΟΒΕΒΛΗΜΕΝΕΣ αλλαγές: επαναφέρει κάθε ημέρα στη δηλωμένη
        στο ΕΡΓΑΝΗ τιμή (baseline). Ημέρες χωρίς baseline (αδήλωτες) αγνοούνται.
        `selection` = {employment_id: [date, …]}. Επιστρέφει πλήθος επαναφορών."""
        reverted = 0
        for eid, dates in selection.items():
            emp = self.proj.employments.get(eid)
            if emp is None:
                raise ValueError(f"Άγνωστη απασχόληση {eid}")
            restore = {}
            for d in dates:
                d = d if isinstance(d, date) else date.fromisoformat(str(d))
                base = emp.day_baseline(d)
                if base is not None and emp.day_schedule(d) != base:
                    restore[d] = base
            if restore:
                self.set_daily_schedule(eid, restore)
                reverted += len(restore)
        return reverted

    # ------------------------- Σύστημα δήλωσης & απολογιστική δήλωση ωραρίου
    def declare_schedule_system(self, month_start: date, system: str, as_of: date):
        """Σύστημα καταχώρισης αλλαγών ωραρίου της ΕΤΑΙΡΕΙΑΣ από τον μήνα
        `month_start` (ΥΑ 32689/2025, 2Α Δ): δηλώνεται έως την τελευταία ημέρα
        του ΠΡΟΗΓΟΥΜΕΝΟΥ μήνα (`as_of` = σήμερα) και ισχύει έως νέα δήλωση.
        Το απολογιστικό επιτρέπεται μόνο με ψηφιακή κάρτα εργασίας."""
        if system not in SCHEDULE_SYSTEMS:
            raise ValueError("Άγνωστο σύστημα δήλωσης — προαναγγελτικό ή απολογιστικό")
        month_start = month_start.replace(day=1)
        if month_start <= as_of:
            raise ValueError(
                "Το σύστημα δηλώνεται έως την τελευταία ημέρα του προηγούμενου "
                f"μήνα — νωρίτερος δυνατός μήνας: "
                f"{(as_of.replace(day=1) + timedelta(days=32)).strftime('%m/%Y')}"
            )
        if system == "retro" and not any(
            e.hire_submitted and str(e.working_card) == "1"
            for e in self.proj.employments.values()
        ):
            raise ValueError(
                "Το απολογιστικό σύστημα επιτρέπεται μόνο σε όσους εφαρμόζουν "
                "ψηφιακή κάρτα εργασίας (κανείς εργαζόμενος με κάρτα)"
            )
        self.store.append("COMPANY", ScheduleSystemDeclared(month_start, system))

    def retro_schedule_proposal(self, year: int, month: int) -> dict:
        """ΠΡΟΤΑΣΗ απολογιστικής δήλωσης: {eid: {date: (δηλωμένο, πραγματικό)}}
        για τις ημέρες του μήνα που ΠΛΗΡΩΝΟΝΤΑΙ ΜΕ ΤΗΝ ΚΑΡΤΑ
        (`emp.pay_source` == "card": απόκλιση πέρα από την ευέλικτη προσέλευση
        και την ανοχή `card_tolerance_minutes`) — ό,τι πληρώνεται με την κάρτα
        δηλώνεται, ό,τι πληρώνεται με το πρόγραμμα όχι.

        Ημέρα ΧΩΡΙΣ χτύπημα μετρά με το πρόγραμμα, άρα δεν προτείνεται (μια
        πραγματική απουσία δηλώνεται ρητά). Άδεια/τηλεεργασία εξαιρούνται.
        Ημέρα με ΟΡΦΑΝΟ χτύπημα προτείνεται ΠΑΝΤΑ (συμπληρωμένη από το
        πρόγραμμα) όταν ισχύει η παράμετρος `retro_declare_orphans`.
        Read-only — η δήλωση γίνεται με `submit_retro_schedule`."""
        out = {}
        for emp in self.proj.employments.values():
            # ρητή εξαίρεση του μήνα (σύγκριση στο ΗΜΕΡΟΛΟΓΙΟ)
            if not emp.hire_submitted or not emp.pay_basis.get(
                    (year, month), ("auto", True))[1]:
                continue
            leave_days = {
                d for lv in emp.active_leaves()
                for d in (lv.date_from + timedelta(days=i)
                          for i in range((lv.date_to - lv.date_from).days + 1))
            }
            days = {}
            for day in range(1, monthrange(year, month)[1] + 1):
                d = date(year, month, day)
                if not emp.active_on(d) or d in leave_days or emp.is_telework_only(d):
                    continue
                rules = config.work_hour_rules_for(d)
                # κάρτα για τη φυσική παρουσία + η τηλεεργασία του
                # δηλωμένου αυτούσια (δεν χτυπά κάρτα)
                declared = emp.declared_on(d)
                actual = cp.with_telework(
                    cp.schedule_text(emp.actual_shifts(d)), declared)
                if emp.pay_source(d, rules.card_tolerance_minutes,
                                  rules.card_rounding_minutes) == "card":
                    days[d] = (declared, actual)
                # ΟΡΦΑΝΟ χτύπημα (`retro_declare_orphans`): δηλώνεται ΠΑΝΤΑ,
                # συμπληρωμένο από το πρόγραμμα — εκτός αν έχει ήδη δηλωθεί
                # ημερήσια με αυτή ακριβώς την τιμή
                elif (rules.retro_declare_orphans and actual
                      and emp.declared_days.get(d) != actual
                      and any(emp.orphan_shift(d, s) for s in emp.card_shifts(d))):
                    days[d] = (declared, actual)
            if days:
                out[emp.employment_id] = days
        return out

    def retro_absence_gaps(self, emp, d: date) -> list:
        """Τα κενά ΑΠΟΥΣΙΑΣ της `d` [(από, έως) σε λεπτά] που θέλουν ΛΟΓΟ στην
        απολογιστική δήλωση: ημέρα που δηλώνεται με την κάρτα (`pay_source`
        == "card") και η κάρτα έχει ΛΙΓΟΤΕΡΕΣ ώρες από το δηλωμένο
        (`cp.absence_gaps`). Κενό = δεν χρειάζεται λόγος."""
        rules = config.work_hour_rules_for(d)
        if emp.pay_source(d, rules.card_tolerance_minutes,
                          rules.card_rounding_minutes) != "card":
            return []
        return cp.absence_gaps(emp.declared_on(d), emp.actual_shifts(d))

    def retro_absences(self, year: int, month: int) -> dict:
        """Οι ΑΠΟΥΣΙΕΣ της απολογιστικής πρότασης του μήνα:
        {eid: {date: {"gaps": [(από, έως)], "code": λόγος ή ""}}}."""
        out = {}
        for eid, days in self.retro_schedule_proposal(year, month).items():
            emp = self.proj.employments[eid]
            found = {d: {"gaps": gaps, "code": emp.absence_reasons.get(d, "")}
                     for d in days if (gaps := self.retro_absence_gaps(emp, d))}
            if found:
                out[eid] = found
        return out

    def set_absence_reason(self, employee_id: str, day: date, code: str) -> None:
        """ΛΟΓΟΣ ΑΠΟΥΣΙΑΣ της ημέρας για την απολογιστική δήλωση (κωδικός
        WorkTimeType: «ΜΕ» ή ωροάδεια ΩΑ…). Κενό = αφαίρεση. Επιτρέπεται και
        σε κλεισμένη περίοδο: η απολογιστική γίνεται τον επόμενο μήνα και ΔΕΝ
        αλλάζει ποσά (βλ. `_prepare_retro_schedule`)."""
        if self.proj.employments.get(employee_id) is None:
            raise ValueError("Άγνωστη απασχόληση")
        code = (code or "").strip()
        if code and code not in {r["code"] for r in ergani_parameters.absence_reasons()}:
            raise ValueError(f"Άγνωστος λόγος απουσίας «{code}»")
        self.store.append(employee_id, RetroAbsenceReasonSet(employee_id, day, code))

    def _prepare_retro_schedule(self, year: int, month: int) -> dict:
        """ΠΡΟΕΤΟΙΜΑΣΙΑ απολογιστικής δήλωσης: οι ημέρες της πρότασης
        ΚΑΤΑΓΡΑΦΟΝΤΑΙ ως πρόγραμμα (DailyScheduleAssigned) και μένουν
        «τροποποιημένες» ώσπου να υποβληθούν. Idempotent (ώσπου να υποβληθούν
        παραμένουν στην πρόταση). Καλείται με την έγκριση παρουσιών.

        ⚠️ ΣΚΟΠΙΜΗ εξαίρεση από το πάγωμα κλεισμένης περιόδου και τον έλεγχο
        11ωρης ανάπαυσης (`validate_daily_schedule`): γίνεται τον ΕΠΟΜΕΝΟ
        μήνα, αφού η μισθοδοσία έχει κλείσει, και καταγράφει ό,τι ΣΥΝΕΒΗ —
        δεν αλλάζει ποσά (πληρώθηκαν από το εγκεκριμένο συγκεντρωτικό)."""
        if self.proj.schedule_system_on(date(year, month, 1)) != "retro":
            raise ValueError(f"Ο μήνας {month}/{year} δεν είναι σε απολογιστικό σύστημα")
        if (year, month) not in self.proj.attendance_sheets:
            raise ValueError(f"Εγκρίνετε πρώτα τις παρουσίες {month}/{year}")
        proposal = self.retro_schedule_proposal(year, month)
        for eid, days in proposal.items():
            self.store.append(eid, DailyScheduleAssigned(
                eid, {d.isoformat(): new for d, (_old, new) in days.items()}))
        return proposal

    def submit_retro_schedule(self, year: int, month: int) -> dict:
        """ΥΠΟΒΟΛΗ ΑΠΟΛΟΓΙΣΤΙΚΟΥ στο ΕΡΓΑΝΗ (υποχρέωση RETRO_SCHEDULE): οι
        ημέρες της πρότασης καταγράφονται (`_prepare_retro_schedule`) και
        υποβάλλονται σε ΕΝΑ batch (`submit_schedule_days`). Κλείνει την
        υποχρέωση· χωρίς ημέρες (π.χ. υποβλήθηκαν ήδη από «Πρόγραμμα →
        ΕΡΓΑΝΗ») απλώς την κλείνει.
        ⚠️ Ημέρα με ΛΙΓΟΤΕΡΕΣ ώρες κάρτας από το πρόγραμμα = ΑΠΟΥΣΙΑ: θέλει
        ΛΟΓΟ (`set_absence_reason`) — αλλιώς απορρίπτεται ΟΛΗ η υποβολή."""
        absences = self.retro_absences(year, month)
        missing = [
            f"{self.proj.employments[eid].full_name} {config.gr_date(d)}"
            for eid, days in absences.items() for d, a in days.items() if not a["code"]
        ]
        if missing:
            raise ValueError("Λείπει ο λόγος απουσίας (λιγότερες ώρες από το "
                             "πρόγραμμα) — ΗΜΕΡΟΛΟΓΙΟ → ημέρα: " + " · ".join(missing))
        proposal = self._prepare_retro_schedule(year, month)
        res = {"count": sum(len(v) for v in proposal.values()),
               "employees": len(proposal), "batch_no": 0}
        if proposal:
            res["batch_no"] = self.submit_schedule_days(
                {eid: list(days) for eid, days in proposal.items()},
                {eid: {d: [(a["code"], s, e) for s, e in a["gaps"]]
                       for d, a in days.items()}
                 for eid, days in absences.items()},
            )["batch_no"]
        self.store.append("COMPANY", RetroScheduleSubmitted(
            year, month, res["batch_no"], res["count"]))
        return res
