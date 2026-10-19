"""
Πρόγραμμα εργασίας (εβδομαδιαίο/ημερήσιο) και δήλωσή του στο ΕΡΓΑΝΗ

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

from datetime import date, timedelta

import misevent.integrations.ergani as ergani_mod
import misevent.rules.constraints as constraints
from misevent.domain.events import (
    DailyScheduleAssigned,
    ScheduleDaysSubmitted,
    WorkScheduleAssigned,
)


class SchedulesMixin:
    def set_schedule(
        self, employee_id, effective_from, weekly_pattern: dict, end_date=None
    ):
        emp = self.proj.employments.get(employee_id)
        if emp is None:
            raise ValueError(f"Άγνωστος εργαζόμενος {employee_id}")
        self._check_open_period(effective_from, "αλλαγή προγράμματος")
        # Οι ώρες του προγράμματος πρέπει να συμφωνούν με τη σύμβαση (40 = πλήρης)
        constraints.check_schedule(weekly_pattern, emp.weekly_hours)
        if end_date is not None and end_date < effective_from:
            raise ValueError(
                "Η ημερομηνία λήξης του εβδομαδιαίου δεν μπορεί να είναι "
                "πριν την ημερομηνία έναρξης"
            )
        previous_day = effective_from - timedelta(days=1)
        day_code = constraints.DAY_CODES[effective_from.weekday()]
        current = emp.daily_schedule.get(
            effective_from, weekly_pattern.get(day_code, "")
        )
        constraints.check_daily_rest(
            emp.day_schedule(previous_day),
            current,
            previous_day.strftime("%d/%m/%Y"),
            effective_from.strftime("%d/%m/%Y"),
        )
        self.store.append(
            employee_id,
            WorkScheduleAssigned(employee_id, effective_from, weekly_pattern, end_date),
        )
        # ΣΤΑΘΕΡΟ πρόγραμμα: το νέο εβδομαδιαίο υποβάλλεται αμέσως ως standing
        # ΟΧΕ (με τη λήξη του, αν έχει). ΜΕΤΑΒΛΗΤΟ: το εβδομαδιαίο είναι ΜΟΝΟ
        # template του grid — η υποβολή στο ΕΡΓΑΝΗ γίνεται ρητά ανά εβδομάδα.
        if emp.schedule_mode == "fixed":
            self._declare_standing_weekly(employee_id, effective_from, end_date)

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
            if emp.hire_date and day < emp.hire_date:
                return ""
            if emp.termination_date and day > emp.termination_date:
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

    def submit_schedule_days(self, selection: dict):
        """Υποβάλλει ΗΜΕΡΗΣΙΑ προγράμματα στο ΕΡΓΑΝΗ σε ΕΝΑ αριθμημένο batch
        (καθολική αρίθμηση εταιρείας). `selection` = {employment_id: [date, …]}.

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
        grouped = {}
        for eid, dates in selection.items():
            emp = self.proj.employments.get(eid)
            if emp is None:
                raise ValueError(f"Άγνωστη απασχόληση {eid}")
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
                total_hours = round(
                    sum(
                        constraints.day_schedule_minutes(emp.day_schedule(d))
                        for d in week_days
                    )
                    / 60,
                    2,
                )
                sunday = monday + timedelta(days=6)
                label = f"{monday.strftime('%d/%m')}–{sunday.strftime('%d/%m/%Y')}"
                constraints.check_week_hours_minimum(
                    total_hours, emp.weekly_hours, label, emp.full_name
                )
            grouped[eid] = days
        if not grouped:
            raise ValueError("Δεν επιλέχθηκαν ημέρες προς υποβολή")

        batch_no = self.next_schedule_batch_no()
        total = 0
        for eid, days in grouped.items():
            emp = self.proj.employments[eid]
            payload = {d.isoformat(): emp.day_schedule(d) for d in days}
            self._ergani_submit(
                "WTO",
                eid,
                ergani_mod.build_wto_variable(
                    self.proj.company, emp, payload, self._week_monday(days[0])
                ),
            )
            self.store.append(eid, ScheduleDaysSubmitted(eid, batch_no, payload))
            total += len(payload)
        return {"batch_no": batch_no, "count": total, "employees": len(grouped)}

    def _earliest_pending_before(self, emp, limit: date):
        """Η πρώτη ΑΔΗΛΩΤΗ (pending) ενεργή ημέρα πριν την `limit`, ή None."""
        if emp.hire_date is None:
            return None
        d = emp.hire_date
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
