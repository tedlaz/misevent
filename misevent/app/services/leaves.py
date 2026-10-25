"""
Άδειες και ασθένεια (καταχώρηση/ακύρωση/επιδόματα ΕΦΚΑ)

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

import uuid
from datetime import timedelta

import misevent.integrations.ergani as ergani_mod
from misevent.domain.events import (
    LeaveCancelled,
    LeaveDeclarationSubmitted,
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
        self._check_leave_not_declared(emp, lv, "ακυρώνεται")
        self.store.append(employee_id, LeaveCancelled(employee_id, leave_id))

    def leave_declared_to_ergani(self, employee_id, leave_id) -> bool:
        """Έχει δηλωθεί στο ΕΡΓΑΝΗ έστω μία ημέρα της άδειας (= κλειδωμένη);"""
        emp = self.proj.employments.get(employee_id)
        lv = emp and next((x for x in emp.active_leaves() if x.leave_id == leave_id), None)
        if not lv:
            return False
        days = (lv.date_from + timedelta(days=i)
                for i in range((lv.date_to - lv.date_from).days + 1))
        return any(d in emp.leave_declared for d in days)

    def _check_leave_not_declared(self, emp, lv, verb: str):
        """Η άδεια δηλώνεται στο ΕΡΓΑΝΗ ΑΦΟΥ ληφθεί (τον επόμενο μήνα): μετά
        τη δήλωση δεν αλλάζει τίποτα πάνω της — ούτε ακύρωση ούτε διόρθωση."""
        if self.leave_declared_to_ergani(emp.employment_id, lv.leave_id):
            raise ValueError(f"Η άδεια έχει δηλωθεί στο ΕΡΓΑΝΗ — δεν {verb}")

    # ------------------------------------------- Δήλωση αδειών (WTOLeave)
    def leave_declaration_proposal(self, year: int, month: int) -> dict:
        """Οι ΑΔΗΛΩΤΕΣ ημέρες άδειας του μήνα: {eid: {date: (κωδικός, έτος,
        δικαιούμενες)}}. Μόνο ημέρες ΠΡΟΓΡΑΜΜΑΤΟΣ (τα ρεπό μένουν ΑΝ) και μόνο
        απασχολήσεις με αναγγελία πρόσληψης. Έτος/δικαιούμενες: κανονική άδεια."""
        out = {}
        for eid, emp in self.proj.employments.items():
            if not emp.hire_submitted:
                continue
            leaves = emp.active_leaves()
            days = {}
            for d in emp.scheduled_days_in_month(year, month):
                lv = next((x for x in leaves if x.date_from <= d <= x.date_to), None)
                if lv is None or d in emp.leave_declared:
                    continue
                annual = lv.leave_type == LeaveType.ANNUAL
                days[d] = (ergani_mod.LEAVE_CODES[lv.leave_type.value],
                           d.year if annual else None,
                           self.annual_leave_entitlement(eid, d.year) if annual else None)
            if days:
                out[eid] = days
        return out

    def submit_leave_declaration(self, year: int, month: int) -> dict:
        """ΥΠΟΒΟΛΗ της δήλωσης αδειών του μήνα (υποχρέωση LEAVE_DECLARATION):
        ΟΛΟΙ οι εργαζόμενοι σε ΜΙΑ κλήση WTOLeave. Κλείνει την υποχρέωση·
        χωρίς τίποτα αδήλωτο απλώς την κλείνει. Απόρριψη = καμία κάλυψη."""
        proposal = self.leave_declaration_proposal(year, month)
        if not proposal:
            self._ergani_nothing_to_send("WTOLeave")
        res = {"count": sum(map(len, proposal.values())),
               "employees": len(proposal), "reference": ""}
        if proposal:
            if self.proj.company is None:
                raise ValueError("Δεν έχουν καταχωρηθεί στοιχεία εταιρείας")
            sent = self._ergani_submit(
                "WTOLeave",
                next(iter(proposal)) if len(proposal) == 1 else "",
                ergani_mod.merge_wto([
                    ergani_mod.build_wto_leave(self.proj.employments[eid], days)
                    for eid, days in proposal.items()
                ]),
            )
            if sent["status"] == "failed":
                raise ValueError("ΑΠΕΡΡΙΨΕ το ΕΡΓΑΝΗ — " + sent["reference"])
            res["reference"] = sent["reference"]
        self.store.append("COMPANY", LeaveDeclarationSubmitted(
            year, month,
            {eid: {d.isoformat(): v[0] for d, v in days.items()}
             for eid, days in proposal.items()},
            res["reference"],
        ))
        return res

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
        self._check_leave_not_declared(emp, old, "τροποποιείται")
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
