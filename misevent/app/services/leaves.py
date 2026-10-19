"""
Άδειες και ασθένεια (καταχώρηση/ακύρωση/επιδόματα ΕΦΚΑ)

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

import uuid

from misevent.domain.events import (
    LeaveCancelled,
    LeaveRecorded,
    LeaveRescheduled,
    LeaveType,
    SicknessBenefitRecorded,
)


class LeavesMixin:
    def record_leave(
        self, employee_id, leave_type: LeaveType, date_from, date_to, notes=""
    ):
        if date_to < date_from:
            raise ValueError("Η ημερομηνία λήξης προηγείται της έναρξης")
        self._check_open_period(date_from, "καταχώρηση άδειας")
        self._check_leave_employment_period(employee_id, date_from, date_to)
        self._check_leave_overlap(employee_id, date_from, date_to)
        lid = str(uuid.uuid4())[:8]
        self.store.append(
            employee_id,
            LeaveRecorded(employee_id, leave_type, date_from, date_to, notes, lid),
        )
        return lid

    def _check_leave_employment_period(self, employee_id, date_from, date_to):
        """Η άδεια πρέπει να βρίσκεται μέσα στην περίοδο απασχόλησης."""
        emp = self.proj.employments.get(employee_id)
        if emp is None or emp.hire_date is None:
            raise ValueError(f"Άγνωστος εργαζόμενος {employee_id}")
        if date_from < emp.hire_date:
            raise ValueError("Η άδεια προηγείται της πρόσληψης")
        if emp.termination_date and date_to > emp.termination_date:
            raise ValueError("Η άδεια έπεται της αποχώρησης")

    def _check_leave_overlap(
        self, employee_id, date_from, date_to, exclude_leave_id=None
    ):
        """Δεν επιτρέπονται δύο ενεργές άδειες την ίδια ημέρα εργαζομένου."""
        emp = self.proj.employments.get(employee_id)
        if emp is None:
            raise ValueError(f"Άγνωστος εργαζόμενος {employee_id}")
        conflict = next(
            (
                leave
                for leave in emp.active_leaves()
                if leave.leave_id != exclude_leave_id
                and date_from <= leave.date_to
                and date_to >= leave.date_from
            ),
            None,
        )
        if conflict:
            raise ValueError(
                "Υπάρχει ήδη άδεια για τον εργαζόμενο στο διάστημα "
                f"{conflict.date_from.strftime('%d/%m/%Y')} – "
                f"{conflict.date_to.strftime('%d/%m/%Y')}"
            )

    def cancel_leave(self, employee_id, leave_id):
        """CRUD delete: ακύρωση προγραμματισμένης άδειας."""
        emp = self.proj.employments.get(employee_id)
        if emp is None:
            raise ValueError(f"Άγνωστος εργαζόμενος {employee_id}")
        lv = next((x for x in emp.active_leaves() if x.leave_id == leave_id), None)
        if lv is None:
            raise ValueError("Δεν βρέθηκε ενεργή άδεια με αυτό το id")
        # Άδεια που ξεκινά σε κλεισμένη περίοδο έχει ήδη μετρήσει σε
        # μισθοδοσία/ημέρες ΕΦΚΑ — δεν ακυρώνεται αναδρομικά.
        self._check_open_period(lv.date_from, "ακύρωση άδειας")
        self.store.append(employee_id, LeaveCancelled(employee_id, leave_id))

    def reschedule_leave(
        self, employee_id, leave_id, leave_type: LeaveType, date_from, date_to, notes=""
    ):
        """Μετακινεί/αλλάζει μια άδεια, κρατώντας σταθερό το ``leave_id``.

        Για κανονική άδεια ο έλεγχος δικαιώματος αγνοεί την παλιά περίοδο της
        ίδιας εγγραφής και ελέγχει χωριστά κάθε ημερολογιακό έτος.
        """
        from datetime import date as _date

        from misevent.rules.calendar_gr import working_days_in_range

        emp = self.proj.employments.get(employee_id)
        if emp is None or emp.hire_date is None:
            raise ValueError(f"Άγνωστος εργαζόμενος {employee_id}")
        old = next((lv for lv in emp.active_leaves() if lv.leave_id == leave_id), None)
        if old is None:
            raise ValueError("Δεν βρέθηκε ενεργή άδεια με αυτό το id")
        if date_to < date_from:
            raise ValueError("Η ημερομηνία λήξης προηγείται της έναρξης")
        # Κλειδωμένη και η ΠΑΛΙΑ και η ΝΕΑ θέση της άδειας: ούτε να φύγει από
        # κλεισμένη περίοδο, ούτε να μπει σε αυτήν.
        self._check_open_days({old.date_from, date_from}, "μεταβολή άδειας")
        self._check_leave_employment_period(employee_id, date_from, date_to)
        self._check_leave_overlap(
            employee_id, date_from, date_to, exclude_leave_id=leave_id
        )

        if leave_type == LeaveType.ANNUAL:
            for year in range(date_from.year, date_to.year + 1):
                start = max(date_from, _date(year, 1, 1))
                end = min(date_to, _date(year, 12, 31))
                requested = working_days_in_range(start, end)
                if requested == 0:
                    continue
                taken = self.annual_leave_taken(employee_id, year)
                if old.leave_type == LeaveType.ANNUAL:
                    old_start = max(old.date_from, _date(year, 1, 1))
                    old_end = min(old.date_to, _date(year, 12, 31))
                    if old_start <= old_end:
                        taken -= working_days_in_range(old_start, old_end)
                entitlement = self.annual_leave_entitlement(employee_id, year)
                if taken + requested > entitlement:
                    raise ValueError(
                        f"Υπέρβαση δικαιώματος αδείας: ζητούνται {requested} "
                        f"ημέρες, υπόλοιπο {entitlement - taken} από "
                        f"{entitlement} για το {year}"
                    )

        self.store.append(
            employee_id,
            LeaveRescheduled(
                employee_id, leave_id, leave_type, date_from, date_to, notes
            ),
        )

    def record_sickness_benefit(
        self, employee_id, leave_id, amount: float, reference=""
    ):
        """Καταχωρεί το επίδομα ασθενείας που ΕΝΕΚΡΙΝΕ ο ΕΦΚΑ για μια άδεια.

        Το ποσό γίνεται γνωστό εκ των υστέρων, γι' αυτό δίνεται χειροκίνητα.
        Αφαιρείται από όσα οφείλει ο εργοδότης για τις ημέρες που πληρώνονται
        κανονικά — όχι από το τριήμερο, που το βαρύνει ολόκληρο."""
        emp = self.proj.employments.get(employee_id)
        if emp is None:
            raise ValueError(f"Άγνωστος εργαζόμενος {employee_id}")
        lv = next((x for x in emp.active_leaves() if x.leave_id == leave_id), None)
        if lv is None:
            raise ValueError("Δεν βρέθηκε ενεργή άδεια με αυτό το id")
        if lv.leave_type != LeaveType.SICK:
            raise ValueError("Επίδομα ΕΦΚΑ καταχωρείται μόνο σε άδεια ασθενείας")
        if amount < 0:
            raise ValueError("Αρνητικό ποσό επιδόματος")
        self.store.append(
            employee_id,
            SicknessBenefitRecorded(employee_id, leave_id, round(amount, 2), reference),
        )
