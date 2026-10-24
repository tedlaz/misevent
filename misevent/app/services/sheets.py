"""
Συγκεντρωτικό παρουσιών: προσχέδιο, ανάλυση ωρών, έγκριση

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

from datetime import date

import misevent.domain.payroll_engine as pe
import misevent.domain.work_hours as work_hours
import misevent.rules.config as config
from misevent.domain.events import AttendanceSheetApproved, LeaveType, RetroScheduleSubmitted
from misevent.integrations import card_punches as cp
from misevent.rules import constraints
from misevent.rules.calendar_gr import last_working_day_of_month


def _physical_segments(day_text: str) -> list:
    """Τα τμήματα ΦΥΣΙΚΗΣ παρουσίας ενός ωραρίου ημέρας (η τηλεεργασία δεν
    κατατάσσεται — §5.10)."""
    try:
        segs = constraints.parse_day_schedule(day_text)
    except ValueError:
        return []
    return [seg["text"] for seg in segs if not seg["telework"]]


class SheetsMixin:
    # ------------------------------------ Συγκεντρωτικό παρουσιών (μηνιαίο)
    def attendance_draft(self, year: int, month: int) -> dict:
        """Προσχέδιο του μηνιαίου συγκεντρωτικού — ΔΕΝ γράφει event.

        Ανά απασχόληση: ημέρες εργασίας, άδεια άνευ αποδοχών, λοιπές άδειες.
        Πηγή παρουσιών: οι ΚΑΤΑΓΕΓΡΑΜΜΕΝΕΣ παρουσίες αν υπάρχουν, αλλιώς
        ΤΕΚΜΑΙΡΟΝΤΑΙ από το πρόγραμμα (εβδομαδιαίο/ημερήσιο).

        Περιλαμβάνει και τις ΩΡΕΣ ΠΡΟΣΑΥΞΗΣΕΩΝ (νύχτα/αργία/πρόσθετη/
        υπερεργασία/υπερωρία/παράνομη — work_hours.SHEET_HOUR_FIELDS) από την
        κατάταξη των βαρδιών της κάρτας. Χωρίς βάρδιες: μηδενικές (και
        διορθώνονται χειροκίνητα στην έγκριση, όπως όλα τα πεδία)."""
        end = last_working_day_of_month(year, month)
        hour_stats = self.hours_analysis(year, month)
        out = {}
        for emp in self.proj.employments.values():
            if not emp.hire_submitted:
                continue
            # Πριν την ΑΝΑΛΗΨΗ της μισθοδοσίας δεν υπάρχει συγκεντρωτικό εδώ.
            if emp.payroll_start is None or emp.payroll_start > end:
                continue
            if emp.termination_date and emp.termination_date < date(year, month, 1):
                continue
            out[emp.employment_id] = self.attendance_draft_line(
                emp, year, month, hour_stats
            )
        return out

    def attendance_draft_line(self, emp, year: int, month: int, hour_stats=None):
        """Η γραμμή του προσχεδίου για ΕΝΑΝ εργαζόμενο — ΜΙΑ πηγή αλήθειας για
        το συγκεντρωτικό ΟΛΩΝ (`attendance_draft`) και για τη μισθοδοσία της
        ΑΠΟΧΩΡΗΣΗΣ (που τη ζητά για έναν, με υποθετική ημ. λήξης)."""
        if hour_stats is None:
            hour_stats = self.hours_analysis(year, month)
        planned = emp.scheduled_days_in_month(year, month)
        # Ασθένεια: οι ημέρες πάνω από το ετήσιο όριο είναι ΑΝΕΥ ΑΠΟΔΟΧΩΝ,
        # άρα προσμετρώνται στις unpaid (κόβουν και ημέρες ασφάλισης).
        sick = emp.sick_days_in_month(year, month)
        unpaid_leave = len(emp.leave_days_in_month(year, month, LeaveType.UNPAID))
        unpaid = unpaid_leave + sick["over"]
        on_leave = len(emp.leave_days_in_month(year, month)) - unpaid_leave - sick["over"]

        # Ημέρα προγράμματος ΧΩΡΙΣ χτύπημα και χωρίς άδεια ΜΕΤΡΑ ΜΕ ΤΟ
        # ΠΡΟΓΡΑΜΜΑ (`emp.pay_source`): τηλεεργασία (η κάρτα καταγράφει μόνο
        # φυσική παρουσία), βλάβη/ξεχασμένη κάρτα. Πραγματική απουσία
        # δηλώνεται ρητά (άδεια) ή διορθώνεται στο φύλλο.
        recorded = emp.attendance_days_in_month(year, month)
        leave_days = set(emp.leave_days_in_month(year, month))
        unpunched = [d for d in planned if d not in emp.attendance and d not in leave_days]
        worked = recorded + len(unpunched)
        source = "recorded" if recorded else "schedule"
        # Σε μήνα με κάρτα, οι ημέρες χωρίς χτύπημα (εκτός της αναμενόμενης
        # τηλεεργασίας) δείχνονται πριν την έγκριση
        no_punch = (
            [d for d in unpunched if not emp.is_telework_only(d)] if recorded else []
        )

        # Ημέρες προγράμματος ΟΛΟΥ του μήνα — παρονομαστής της αναλογίας
        # των 25 (πρόσληψη/αποχώρηση στη μέση του μήνα)
        month_planned = len(emp.scheduled_days_in_month(year, month, whole_month=True))
        return {
            "name": emp.full_name,
            "pay_type": emp.pay_type,
            "planned": len(planned),
            "month_planned": month_planned,
            "worked": worked,
            "unpaid": unpaid,
            "leave": on_leave,
            # Ανάλυση ασθένειας — ΠΕΡΙΚΟΠΤΕΙ τα μικτά της μισθοδοσίας
            "sick_half": sick["half"],  # τριήμερο: μισό ημερομίσθιο
            "sick_full": sick["full"],  # κανονικά, μείον επίδομα ΕΦΚΑ
            "sick_benefit": sick["benefit"],
            # Ώρες προσαυξήσεων — ΠΡΟΣΤΙΘΕΝΤΑΙ στα μικτά της μισθοδοσίας
            **{
                f: hour_stats.get(emp.employment_id, {}).get(f, 0.0)
                for f in work_hours.SHEET_HOUR_FIELDS
            },
            # ΠΡΟΒΛΕΠΟΜΕΝΕΣ ημέρες ΕΦΚΑ — αυτές οδηγούν τη μισθοδοσία
            "efka_days": self.expected_efka_days(
                emp, worked, on_leave, unpaid, month_planned
            ),
            "source": source,
            "no_punch_days": no_punch,
            # ημέρες που ΘΕΛΟΥΝ κάρτα (φυσική παρουσία, χωρίς άδεια) — 0 =
            # τίποτα να καλυφθεί (π.χ. μόνο τηλεεργασία/άδεια όλο τον μήνα)
            "card_needed": sum(1 for d in planned if d not in leave_days
                               and not emp.is_telework_only(d)),
            # ημέρες όπου η κάρτα ΑΠΟΚΛΙΝΕΙ πέρα από την ανοχή — ίδια
            # ταξινόμηση με την κατάταξη ωρών και την απολογιστική πρόταση
            "card_days": self.card_pay_days(emp, year, month),
        }

    def hours_analysis(self, year: int, month: int) -> dict:
        """Κατάταξη ωρών & προσαυξήσεις της περιόδου, ανά απασχόληση.

        Πηγή ανά ΗΜΕΡΑ (ARCH_REF §5.11 «Συμφωνία προγράμματος ↔ κάρτας»):
        - ημέρα με κάρτα που ΑΠΟΚΛΙΝΕΙ (`card_pay_days`) -> οι βάρδιες της
          κάρτας (στο προαναγγελτικό μόνο αν `pre_major_by_card`)·
        - ημέρα με κάρτα εντός ανοχής -> το ΔΗΛΩΜΕΝΟ πρόγραμμα·
        - ημέρα χωρίς κάρτα -> οι βάρδιες ΤΕΚΜΑΙΡΟΝΤΑΙ από το πρόγραμμα
          (`scheduled_shifts_in_month`), ώστε π.χ. μια βάρδια 15:00-23:00 να
          προτείνει τη νυχτερινή προσαύξηση και χωρίς Κάρτα Εργασίας.
        Η κατάταξη τρέχει σε ΟΛΟ το έτος ως το τέλος της περιόδου, ώστε το
        ετήσιο όριο των 150 ωρών υπερωρίας να μετρήσει σωστά, και μετά
        φιλτράρεται στον μήνα.

        Read-only: τροφοδοτεί τις ΠΡΟΤΕΙΝΟΜΕΝΕΣ ώρες του συγκεντρωτικού
        (`attendance_draft`). Στη μισθοδοσία φτάνουν μόνο μέσω του
        ΕΓΚΕΚΡΙΜΕΝΟΥ φύλλου, αφού τυχόν διορθωθούν χειροκίνητα."""
        rules = config.work_hour_rules_for(date(year, month, 1))
        out = {}
        for emp in self.proj.employments.values():
            if not emp.hire_submitted:
                continue
            month_buckets = self._month_buckets(emp, year, month)
            if not month_buckets:
                continue
            rate = work_hours.hourly_rate(
                emp.pay_type, emp.monthly_gross, emp.daily_wage, emp.weekly_hours
            )
            fields = work_hours.sheet_fields_from_buckets(month_buckets)
            out[emp.employment_id] = {
                "name": emp.full_name,
                "hourly_rate": rate,
                # τα 20 πεδία (κλάση x ημέρα/νύχτα x εργάσιμη/αργία), σε ώρες
                **fields,
                "supplement": work_hours.pay_supplement(rate, fields, rules)["total"],
            }
        return out

    def _month_buckets(self, emp, year: int, month: int) -> list:
        """Η κατάταξη ωρών (`HourBucket`) του μήνα για ΜΙΑ απασχόληση — η
        πηγή του `hours_analysis` (βλ. εκεί ποια ημέρα μετρά με τι)."""
        year_shifts = {}
        for m in range(1, month + 1):
            year_shifts.update(emp.scheduled_shifts_in_month(year, m))
            # Στρογγυλοποίηση (`card_rounding_minutes`, 0 = καμία) ΜΟΝΟ
            # εδώ: οι ώρες είναι ΠΡΟΤΑΣΗ, εγκρίνονται στο φύλλο.
            step = config.work_hour_rules_for(date(year, m, 1)).card_rounding_minutes
            major = set(self.card_pay_days(emp, year, m))
            # ημέρες με κάρτα — και ΜΟΝΟ ορφανό χτύπημα (συμπληρώνεται από
            # το πρόγραμμα στο `actual_shifts`)
            for d in set(emp.attendance_shifts) | set(emp.card_punches):
                if d.year != year or d.month != m:
                    continue
                s = emp.actual_shifts(d)
                if not s:
                    continue
                if d in major and self._major_by_card(d, emp):
                    year_shifts[d] = cp.rounded_texts(s, step)
                else:
                    year_shifts[d] = _physical_segments(emp.declared_on(d))
        year_shifts = {d: s for d, s in year_shifts.items() if s}
        if not year_shifts:
            return []
        system = emp.weekly_system  # όρος σύμβασης
        buckets = work_hours.classify_shifts(year_shifts, emp.weekly_hours, system)
        return [b for b in buckets if b.day.month == month]

    def month_premiums(self, emp, year: int, month: int) -> dict:
        """Οι ΠΡΟΣΑΥΞΗΣΕΙΣ του μήνα (αργία, νύχτα, υπερεργασία, υπερωρία…)
        ανά ημέρα + σύνολο, με ποσά (`pay_supplement`) — για τη σύγκριση
        προγράμματος ↔ κάρτας. Ίδια κατάταξη με το `hours_analysis`, άρα
        ακολουθεί τον τρόπο υπολογισμού του μήνα. Read-only."""
        rules = config.work_hour_rules_for(date(year, month, 1))
        rate = work_hours.hourly_rate(
            emp.pay_type, emp.monthly_gross, emp.daily_wage, emp.weekly_hours
        )
        buckets = self._month_buckets(emp, year, month)
        by_day = {}
        for b in buckets:
            by_day.setdefault(b.day, []).append(b)
        days = []
        for d in sorted(by_day):
            fields = work_hours.sheet_fields_from_buckets(by_day[d])
            br = work_hours.pay_supplement(rate, fields, rules)
            if br["lines"]:
                days.append({"day": d, "lines": list(br["lines"].values()),
                             "total": br["total"], "premium": br["premium"]})
        br = work_hours.pay_supplement(
            rate, work_hours.sheet_fields_from_buckets(buckets), rules)
        return {"rate": rate, "days": days, "lines": list(br["lines"].values()),
                "total": br["total"], "premium": br["premium"]}

    def card_pay_days(self, emp, year: int, month: int) -> list:
        """Οι ημέρες του μήνα όπου η κάρτα ΑΠΟΚΛΙΝΕΙ από το δηλωμένο πέρα από
        την ευέλικτη προσέλευση + `card_tolerance_minutes`
        (`emp.pay_source` == "card"), χρονολογικά."""
        return sorted(
            d for d in set(emp.attendance_shifts) | set(emp.card_punches)
            if d.year == year and d.month == month
            and emp.pay_source(d, config.work_hour_rules_for(d).card_tolerance_minutes,
                               config.work_hour_rules_for(d).card_rounding_minutes)
            == "card"
        )

    def _major_by_card(self, d: date, emp=None) -> bool:
        """Πληρώνεται με την ΚΑΡΤΑ μια ημέρα απόκλισης; Πρώτα η ρητή επιλογή
        του μήνα του `emp` (`MonthPayBasisChosen`, σύγκριση στο ΗΜΕΡΟΛΟΓΙΟ —
        χωρίς `emp` = ο κανόνας του συστήματος)· αλλιώς
        απολογιστικό: πάντα (δηλώνεται εκ των υστέρων)· προαναγγελτικό: κατά
        την προτίμηση «Διάφορα → pre_major_by_card»."""
        basis = emp.pay_basis.get((d.year, d.month), ("auto", True))[0] if emp else "auto"
        if basis != "auto":
            return basis == "card"
        return self.proj.schedule_system_on(d) == "retro" or bool(
            self.proj.misc_settings.get("pre_major_by_card", True)
        )

    def month_schedule_pending(self, emp, year: int, month: int) -> bool:
        """Έχει ο μήνας ΕΝΕΡΓΗ ημέρα ΑΔΗΛΩΤΗ (pending) στο ΕΡΓΑΝΗ;

        Ελέγχονται ΜΟΝΟ οι ημέρες εντός του μήνα — μια αλλαγή (modified) δεν
        μπλοκάρει (το πρόγραμμα ΕΧΕΙ δηλωθεί)· μόνο η παντελής έλλειψη δήλωσης."""
        from calendar import monthrange

        last = monthrange(year, month)[1]
        for day in range(1, last + 1):
            d = date(year, month, day)
            if emp.active_on(d) and emp.day_status(d) == "pending":
                return True
        return False

    def month_schedule_incomplete(self, year: int, month: int) -> bool:
        """True αν ΕΣΤΩ μία ενεργή απασχόληση του μήνα έχει ημέρα χωρίς
        δηλωμένο πρόγραμμα στο ΕΡΓΑΝΗ. Η «Έγκριση Παρουσιών» του μήνα ΔΕΝ
        ενεργοποιείται όσο ισχύει αυτό (προαπαιτούμενο — βλ. queries.blocked_reason)."""
        return any(
            self.month_schedule_pending(emp, year, month)
            for emp in self.proj.employments.values()
            if emp.hire_submitted
        )

    def attendance_ergani_pending(self, year: int, month: int) -> list[dict]:
        """Ποιες απασχολήσεις της περιόδου δεν έχουν ολοκληρώσει τις υποβολές
        ΕΡΓΑΝΗ πρόσληψης (Ε3) και τη ΔΗΛΩΣΗ ΠΡΟΓΡΑΜΜΑΤΟΣ — προαπαιτούμενο των
        παρουσιών.

        Το «Πρόγραμμα» θεωρείται καλυμμένο όταν κάθε εβδομάδα του μήνα είναι
        δηλωμένη στο ΕΡΓΑΝΗ: για fixed αρκεί το standing εβδομαδιαίο (εφόσον
        δεν υπάρχουν αδήλωτες ημερήσιες αποκλίσεις)· για variable κάθε εβδομάδα
        δηλώνεται ρητά. Επιστρέφει [{"eid", "name", "missing": [...]}] μόνο για
        όσες λείπει κάτι. Read-only — το χρησιμοποιεί και το UI για προειδοποίηση."""
        out = []
        for eid, d in self.attendance_draft(year, month).items():
            emp = self.proj.employments[eid]
            missing = []
            if not (
                self.ergani_hire_submitted(eid, "WebE3N")
                or self.ergani_hire_submitted(eid, "E3")
            ):
                missing.append("Ε3")
            if self.month_schedule_pending(emp, year, month):
                missing.append("Πρόγραμμα")
            if missing:
                out.append({"eid": eid, "name": d["name"], "missing": missing})
        return out

    @staticmethod
    def expected_efka_days(emp, worked, leave, unpaid, month_planned) -> int:
        """Οι ΠΡΟΒΛΕΠΟΜΕΝΕΣ ημέρες ασφάλισης (ΕΦΚΑ) της περιόδου.

        Μισθωτός: 25 ημέρες/μήνα σε πλήρη απασχόληση (**παραδοχή** — όποιες
        κι αν είναι οι εργάσιμες)· σε μερική οι ημέρες προγράμματος του μήνα
        (`pe.monthly_insurance_days`). Οι μη πληρωμένες ημέρες αφαιρούνται
        από τη βάση ΑΝΑΛΟΓΙΚΑ προς τον μήνα: ο στόχος είναι να τηρούνται
        μισθός + ημέρες ασφάλισης, και κάθε υπόλειμμα να τεκμηριώνεται
        (άδεια με ή χωρίς αποδοχές).
        Ημερομίσθιος: οι πραγματικές ημέρες εργασίας."""
        if emp.pay_type == "daily":
            return int(worked)
        if not month_planned:
            return 0
        paid = min(worked + leave, max(month_planned - unpaid, 0))
        base = pe.monthly_insurance_days(emp.weekly_hours, month_planned)
        return min(round(base * paid / month_planned), base)

    def approve_attendance(self, year: int, month: int, lines: dict | None = None):
        """Εγκρίνει το συγκεντρωτικό της περιόδου. ΑΝΟΙΓΕΙ τη μισθοδοσία.

        Το πεδίο που ΟΔΗΓΕΙ τη μισθοδοσία είναι οι **ημέρες ΕΦΚΑ**
        (`efka_days`) — δίνονται από το σύστημα (`expected_efka_days`) και
        μπορεί ο χρήστης να τις αλλάξει. Οι ημέρες εργασίας/αδειών μένουν ως
        τεκμηρίωση του πώς προέκυψαν."""
        # Κλεισμένη μισθοδοσία = κλειδωμένες παρουσίες: νέα έγκριση θα άλλαζε
        # ημέρες ΕΦΚΑ/ασθένειας που έχουν ήδη πληρωθεί και υποβληθεί (ΑΠΔ/ΦΜΥ).
        if self.is_period_closed(year, month):
            self._check_open_period(date(year, month, 1), "έγκριση παρουσιών")
        draft = self.attendance_draft(year, month)
        if not draft:
            raise ValueError(f"Καμία απασχόληση στην περίοδο {month}/{year}")

        # ΠΡΟΑΠΑΙΤΟΥΜΕΝΟ: κάθε απασχόληση της περιόδου πρέπει να έχει υποβάλει
        # Ε3 (Αναγγελία Πρόσληψης) ΚΑΙ ΟΧΕ στο ΕΡΓΑΝΗ πριν μετρηθούν παρουσίες.
        pending = self.attendance_ergani_pending(year, month)
        if pending:
            names = "· ".join(
                f"{p['name']} ({', '.join(p['missing'])})" for p in pending
            )
            raise ValueError(
                "Εκκρεμούν υποβολές ΕΡΓΑΝΗ πρόσληψης πριν την έγκριση "
                f"παρουσιών: {names}"
            )
        # «Διάφορα → card_required»: η κάρτα ελέγχεται στο ΗΜΕΡΟΛΟΓΙΟ πριν
        card = [d["name"] for eid, d in draft.items()
                if self.card_month_pending(self.proj.employments[eid], year, month)]
        if card:
            raise ValueError(
                "Εκκρεμεί ο έλεγχος κάρτας (ΗΜΕΡΟΛΟΓΙΟ εργαζομένου) για: "
                + "· ".join(card)
            )

        final = {}
        for eid, d in draft.items():
            over = (lines or {}).get(eid) or {}
            worked = int(over.get("worked", d["worked"]))
            unpaid = int(over.get("unpaid", d["unpaid"]))
            leave = int(over.get("leave", d["leave"]))
            efka = int(over.get("efka_days", d["efka_days"]))
            s_half = int(over.get("sick_half", d["sick_half"]))
            s_full = int(over.get("sick_full", d["sick_full"]))
            s_ben = round(float(over.get("sick_benefit", d["sick_benefit"])), 2)
            hours = {
                f: round(float(over.get(f, d[f])), 2)
                for f in work_hours.SHEET_HOUR_FIELDS
            }
            if min(hours.values()) < 0:
                raise ValueError(f"Αρνητικές ώρες προσαυξήσεων για {d['name']}")
            if min(worked, unpaid, leave, efka, s_half, s_full, s_ben) < 0:
                raise ValueError(f"Αρνητικές ημέρες για {d['name']}")
            if s_half + s_full > leave:
                raise ValueError(
                    f"Ημέρες ασθενείας ({s_half + s_full}) πάνω από τις ημέρες "
                    f"αδείας ({leave}) για {d['name']}"
                )
            if worked > 31 or worked + unpaid + leave > 31:
                raise ValueError(f"Πάνω από 31 ημέρες για {d['name']}")
            if efka > 25:
                raise ValueError(
                    f"Ημέρες ΕΦΚΑ > 25 για {d['name']} — ο μήνας δεν έχει "
                    "περισσότερες από 25 ημέρες ασφάλισης"
                )
            manual = (worked, unpaid, leave, efka, s_half, s_full, s_ben) != (
                d["worked"],
                d["unpaid"],
                d["leave"],
                d["efka_days"],
                d["sick_half"],
                d["sick_full"],
                d["sick_benefit"],
            ) or any(hours[f] != d[f] for f in work_hours.SHEET_HOUR_FIELDS)
            final[eid] = {
                "planned": d["planned"],
                "month_planned": d["month_planned"],
                "worked": worked,
                "unpaid": unpaid,
                "leave": leave,
                "efka_days": efka,  # ← αυτό υπολογίζει τις ΗΜΕΡΕΣ
                # ← αυτά ΠΕΡΙΚΟΠΤΟΥΝ τα μικτά (τριήμερο + επίδομα ΕΦΚΑ)
                "sick_half": s_half,
                "sick_full": s_full,
                "sick_benefit": s_ben,
                # ← αυτά ΠΡΟΣΑΥΞΑΝΟΥΝ τα μικτά (ώρες x ωρομίσθιο x ποσοστό)
                **hours,
                "source": "manual" if manual else d["source"],
            }
        self.store.append("COMPANY", AttendanceSheetApproved(year, month, final))
        # Απολογιστικό: η έγκριση ΕΤΟΙΜΑΖΕΙ αμέσως την απολογιστική δήλωση
        # (οι ημέρες απόκλισης -> «τροποποιημένες»)· η RETRO_SCHEDULE που μόλις
        # γέννησε ο scheduler μένει ανοιχτή για την ΥΠΟΒΟΛΗ στο ΕΡΓΑΝΗ — ή
        # κλείνει αμέσως αν δεν υπάρχει τίποτα προς δήλωση.
        if self.proj.schedule_system_on(date(year, month, 1)) == "retro":
            if not self._prepare_retro_schedule(year, month):
                self.store.append("COMPANY", RetroScheduleSubmitted(year, month))
        return final
