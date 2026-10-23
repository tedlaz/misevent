"""
Παρουσίες και ΨΗΦΙΑΚΗ ΚΑΡΤΑ ΕΡΓΑΣΙΑΣ (§5.11)

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

import uuid
from calendar import monthrange
from datetime import date, timedelta

import misevent.integrations.card_punches as cp
import misevent.integrations.karta_import as karta_import
import misevent.rules.config as config
import misevent.rules.constraints as constraints
from misevent.domain.events import (
    CardMonthChecked,
    CardPeriodChecked,
    CardPunchCancelled,
    CardPunchRecorded,
    MonthPayBasisChosen,
)

PAY_BASES = ("auto", "schedule", "card")


def _shift_minutes(text: str) -> int:
    """«ΩΩ:ΛΛ-ΩΩ:ΛΛ» -> λεπτά (λήξη <= έναρξης = πέρασμα μεσανύχτων)."""
    a, _, b = text.partition("-")
    s, e = cp.parse_time(a), cp.parse_time(b)
    return e - s if e > s else e + 24 * 60 - s


class AttendanceMixin:
    def record_attendance(self, employee_id, work_date, shifts, source="manual") -> list:
        """Καταγραφή των βαρδιών ΜΙΑΣ ημέρας («ΩΩ:ΛΛ-ΩΩ:ΛΛ», έξοδος < είσοδο =
        επόμενη ημέρα) — ως ΧΤΥΠΗΜΑΤΑ ΚΑΡΤΑΣ, γιατί η κάρτα είναι η ΜΟΝΗ πηγή
        παρουσιών (§5.11). ΑΝΤΙΚΑΘΙΣΤΑ τις βάρδιες που ξεκινούν την ημέρα: τα
        προηγούμενα χτυπήματά τους ακυρώνονται (αντισταθμιστικά).
        Ελλιπής βάρδια «ΩΩ:ΛΛ-» / «-ΩΩ:ΛΛ» = ΜΟΝΟ είσοδος / ΜΟΝΟ έξοδος: γράφεται
        ως ΤΥΦΛΟ χτύπημα (`blind`). Επιστρέφει τα `punch_id` των τυφλών."""
        emp = self.proj.employments.get(employee_id)
        if emp is None:
            raise ValueError("Άγνωστη απασχόληση")
        self._check_open_period(work_date, "καταγραφή παρουσίας")
        # Προέλεγχος ΟΛΩΝ πριν γραφτεί οτιδήποτε — όχι μισή ημέρα.
        pairs = []
        for text in shifts:
            t_in, sep, t_out = str(text).partition("-")
            if not sep or not (t_in.strip() or t_out.strip()):
                raise ValueError(f"Μη έγκυρη βάρδια: {text!r} (μορφή ΩΩ:ΛΛ-ΩΩ:ΛΛ)")
            a = cp.parse_time(t_in) if t_in.strip() else None
            b = cp.parse_time(t_out) if t_out.strip() else None
            if a == b:
                raise ValueError(f"{text}: η είσοδος ταυτίζεται με την έξοδο")
            pairs.append((a, b))
        if not pairs:
            raise ValueError("Η παρουσία θέλει τουλάχιστον μία βάρδια")
        for s in emp.card_shifts(work_date):
            for pid in (s["in_id"], s["out_id"]):
                if pid:
                    self.cancel_card_punch(pid, "αντικατάσταση παρουσίας ημέρας")
        blind = []
        for a, b in pairs:
            lone = a is None or b is None
            if a is not None:
                pid = self.record_card_punch(employee_id, work_date, a, cp.IN,
                                             source, blind=lone)
                blind += [pid] if lone else []
            if b is not None:
                out_day = (work_date + timedelta(days=1)
                           if a is not None and b < a else work_date)
                pid = self.record_card_punch(employee_id, out_day, b, cp.OUT,
                                             source, blind=lone)
                blind += [pid] if lone else []
        return blind

    # ------------------------------------------------ ΨΗΦΙΑΚΗ ΚΑΡΤΑ ΕΡΓΑΣΙΑΣ
    def record_card_punch(self, employee_id: str, punch_date: date, time_text,
                          kind: str, source: str = "manual",
                          note: str = "", blind: bool = False) -> str:
        """Καταγραφή ΜΙΑΣ διέλευσης (είσοδος/έξοδος) της ψηφιακής κάρτας.

        ⚠️ **ΩΜΟ ΓΕΓΟΝΟΣ — ΚΑΜΙΑ ΛΟΓΙΚΗ ΒΑΡΔΙΑΣ ΕΔΩ.** Δεν απαιτείται να
        προηγείται είσοδος για να καταγραφεί έξοδος, ούτε να «κλείνει» η
        ημέρα: η κάρτα καταγράφει ό,τι συνέβη και το ζευγάρωμα
        (`card_punches.pair_punches`) δείχνει τα κενά ως κενά. Ελέγχονται
        μόνο όσα κάνουν την εγγραφή **αδύνατη**: άγνωστη απασχόληση, μη
        ενεργή ημέρα, λάθος ώρα/τύπος, κλεισμένη περίοδος και ΔΙΠΛΗ
        καταγραφή (ίδιος εργαζόμενος, ίδια στιγμή, ίδιος τύπος).

        `time_text`: «ΩΩ:ΛΛ» ή λεπτά από τα μεσάνυχτα. Επιστρέφει το
        `punch_id` (για ακύρωση)."""
        emp = self.proj.employments.get(employee_id)
        if emp is None:
            raise ValueError("Άγνωστη απασχόληση")
        if kind not in cp.KINDS:
            raise ValueError("Ο τύπος διέλευσης πρέπει να είναι είσοδος ή έξοδος")
        minute = (int(time_text) if isinstance(time_text, int)
                  else cp.parse_time(time_text))
        if not 0 <= minute < 24 * 60:
            raise ValueError("Μη έγκυρη ώρα διέλευσης")
        if not emp.active_on(punch_date):
            raise ValueError(
                f"Ο/Η {emp.full_name} δεν είναι σε ενεργή απασχόληση στις "
                f"{config.gr_date(punch_date)}"
            )
        self._check_open_period(punch_date, "καταγραφή διέλευσης κάρτας")
        if any(p["m"] == minute and p["kind"] == kind
               for p in emp.punches_on(punch_date)):
            raise ValueError(
                f"Υπάρχει ήδη {'είσοδος' if kind == cp.IN else 'έξοδος'} "
                f"{cp.hhmm(minute)} στις {config.gr_date(punch_date)}"
            )
        punch_id = "K" + uuid.uuid4().hex[:10]
        self.store.append(
            employee_id,
            CardPunchRecorded(
                employee_id=employee_id,
                punch_date=punch_date,
                minute=minute,
                kind=kind,
                source=source,
                punch_id=punch_id,
                note=note,
                blind=blind,
            ),
        )
        return punch_id

    def complete_punch_from_schedule(self, punch_id: str) -> str:
        """ΟΡΦΑΝΟ χτύπημα (είσοδος χωρίς έξοδο / έξοδος χωρίς είσοδο): το
        ταίρι που λείπει παίρνεται από το ΠΡΟΓΡΑΜΜΑ της ημέρας — η λήξη ή η
        αρχή του πλησιέστερου τμήματος ωραρίου. Το ΕΡΓΑΝΗ το δέχεται έως
        `orphan_month_cap` φορές ανά εργαζόμενο τον μήνα· πέρα από αυτό
        απορρίπτεται. Γράφεται ως κανονική διέλευση με `source="schedule"`
        (ακυρώνεται όπως κάθε άλλη). Επιστρέφει μήνυμα για το UI."""
        from misevent.rules import constraints

        for emp in self.proj.employments.values():
            for day, punches in emp.card_punches.items():
                if any(p["id"] == punch_id for p in punches):
                    break
            else:
                continue
            break
        else:
            raise ValueError("Άγνωστη διέλευση κάρτας")
        shift = next((s for s in emp.card_shifts(day)
                      if punch_id in (s["in_id"], s["out_id"])), None)
        if shift is None or not (shift["open"] or shift["orphan"]):
            raise ValueError("Η διέλευση ανήκει ήδη σε πλήρη βάρδια")
        cap = config.work_hour_rules_for(day).orphan_month_cap
        used = emp.schedule_completions(day.year, day.month)
        if used >= cap:
            raise ValueError(
                f"Εξαντλήθηκαν τα {cap} ορφανά χτυπήματα του μήνα για "
                f"{emp.full_name} — καταχωρήστε την ώρα χειροκίνητα"
            )
        segs = [s for s in constraints.parse_day_schedule(emp.day_schedule(day))
                if not s["telework"]]
        if not segs:
            raise ValueError(
                f"Δεν υπάρχει πρόγραμμα εργασίας στις {config.gr_date(day)} "
                "για να συμπληρωθεί το χτύπημα"
            )
        kind = cp.OUT if shift["open"] else cp.IN
        minute = cp.completion_minute(shift, segs)
        if minute is None:
            raise ValueError(
                "Η είσοδος είναι μετά τη λήξη του προγράμματος" if shift["open"]
                else "Η έξοδος είναι πριν την έναρξη του προγράμματος")
        punch_day = day + timedelta(days=minute // (24 * 60))
        self.record_card_punch(emp.employment_id, punch_day, minute % (24 * 60),
                               kind, "schedule", "συμπλήρωση από πρόγραμμα")
        return (f"{'Έξοδος' if kind == cp.OUT else 'Είσοδος'} {cp.hhmm(minute)} "
                f"από το πρόγραμμα ({used + 1}/{cap} ορφανά του μήνα)")

    def cancel_card_punch(self, punch_id: str, reason: str = "") -> None:
        """ΑΚΥΡΩΣΗ λανθασμένης διέλευσης — αντισταθμιστικό event, ΠΟΤΕ
        διαγραφή (κανόνας #2). Η καταγραφή μένει στο log."""
        for emp in self.proj.employments.values():
            for day, punches in emp.card_punches.items():
                if not any(p["id"] == punch_id for p in punches):
                    continue
                self._check_open_period(day, "ακύρωση διέλευσης κάρτας")
                self.store.append(
                    emp.employment_id,
                    CardPunchCancelled(
                        employee_id=emp.employment_id,
                        punch_id=punch_id,
                        reason=reason,
                    ),
                )
                return
        raise ValueError("Άγνωστη διέλευση κάρτας")

    def card_shift_texts(self, employee_id: str, day: date) -> tuple:
        """Οι ΚΛΕΙΣΤΕΣ βάρδιες της ημέρας από την κάρτα, ως «ΩΩ:ΛΛ-ΩΩ:ΛΛ» —
        η μορφή που δέχεται το `record_attendance` (= `emp.attendance_shifts`)."""
        emp = self.proj.employments.get(employee_id)
        if emp is None:
            raise ValueError("Άγνωστη απασχόληση")
        return cp.shift_texts(emp.card_shifts(day))

    # ------------------------------- Έλεγχος αρτιότητας κάρτας (ανά μήνα)
    def card_issues(self, emp, year: int, month: int) -> list[dict]:
        """Οι εκκρεμότητες της κάρτας του μήνα: [{day, kind, text, blocking}].

        Μπλοκάρουν την ολοκλήρωση του ελέγχου (`blocking`):
        - "empty": ΚΑΝΕΝΑ χτύπημα στον μήνα ενώ υπάρχει φυσική παρουσία στο
          πρόγραμμα — δεν έχει εισαχθεί το αρχείο της κάρτας (`day` = None)·
        - "leave": χτύπημα σε ημέρα άδειας.
        Δεν μπλοκάρουν (μετρούν με το πρόγραμμα, §5.11): "no_punch" — ημέρα
        προγράμματος χωρίς χτύπημα· "open" — ορφανό χτύπημα (το ταίρι
        συμπληρώνεται από το πρόγραμμα, `emp.actual_shifts`).
        Η τηλεεργασία δεν χτυπά κάρτα: δεν ελέγχεται."""
        leave = set(emp.leave_days_in_month(year, month))
        physical = {d for d in emp.scheduled_days_in_month(year, month)
                    if d not in leave and not emp.is_telework_only(d)}
        if physical and not any((d.year, d.month) == (year, month)
                                for d in emp.card_punches):
            return [{"day": None, "kind": "empty", "blocking": True,
                     "text": "Κανένα χτύπημα κάρτας στον μήνα — εισαγωγή "
                             "αρχείου κάρτας (Παρουσίες)"}]
        out = []
        for n in range(1, monthrange(year, month)[1] + 1):
            d = date(year, month, n)
            shifts = emp.card_shifts(d)
            if any(s["open"] or s["orphan"] for s in shifts):
                out.append({"day": d, "kind": "open", "blocking": False,
                            "text": "Χτύπημα χωρίς ταίρι — μετρά με το πρόγραμμα"})
            if d in leave and emp.punches_on(d):
                out.append({"day": d, "kind": "leave", "blocking": True,
                            "text": "Χτύπημα σε ημέρα άδειας"})
            if d in physical and not shifts:
                out.append({"day": d, "kind": "no_punch", "blocking": False,
                            "text": "Χωρίς χτύπημα — μετρά με το πρόγραμμα"})
        return out

    def card_month_pending(self, emp, year: int, month: int) -> bool:
        """Εμποδίζει η κάρτα τη μισθοδοσία του μήνα; Μόνο με «Διάφορα →
        card_required», όταν υπάρχουν εκκρεμότητες και ο έλεγχος δεν έχει
        ολοκληρωθεί (μήνας χωρίς καμία εκκρεμότητα είναι έτοιμος)."""
        return (
            bool(self.proj.misc_settings.get("card_required"))
            and (year, month) not in emp.card_checked
            and bool(self.card_issues(emp, year, month))
        )

    def confirm_card_month(self, employee_id: str, year: int, month: int) -> None:
        """«Ολοκλήρωση ελέγχου κάρτας» του μήνα (από το ΗΜΕΡΟΛΟΓΙΟ). Απορρίπτεται
        όσο υπάρχουν εκκρεμότητες που μπλοκάρουν (`card_issues`)."""
        emp = self.proj.employments.get(employee_id)
        if emp is None:
            raise ValueError("Άγνωστη απασχόληση")
        self._check_open_period(date(year, month, 1), "έλεγχος κάρτας")
        blocking = [i for i in self.card_issues(emp, year, month) if i["blocking"]]
        if blocking:
            raise ValueError(
                f"Εκκρεμούν {len(blocking)} προβλήματα κάρτας: " + "· ".join(
                    (f"{config.gr_date(i['day'])} " if i["day"] else "") + i["text"]
                    for i in blocking
                )
            )
        self.store.append(employee_id, CardMonthChecked(employee_id, year, month))

    def card_period_issues(self, year: int, month: int) -> list[dict]:
        """Οι εκκρεμότητες κάρτας ΟΛΩΝ των εργαζομένων της περιόδου (όσων
        μετρούν στις παρουσίες): [{eid, name, day, kind, text, blocking}]."""
        return [
            {"eid": eid, "name": d["name"], **i}
            for eid, d in self.attendance_draft(year, month).items()
            for i in self.card_issues(self.proj.employments[eid], year, month)
        ]

    def check_card_period(self, year: int, month: int) -> dict:
        """ΕΙΣΑΓΩΓΗ & ΕΛΕΓΧΟΣ ΚΑΡΤΑΣ ΠΕΡΙΟΔΟΥ (υποχρέωση CARD_IMPORT, «Διάφορα
        → digital_card»): απορρίπτεται όσο κάποιος εργαζόμενος έχει
        εκκρεμότητα που μπλοκάρει (`card_issues`: κανένα χτύπημα, χτύπημα σε
        άδεια) — διορθώνονται στο ΗΜΕΡΟΛΟΓΙΟ. Οι ημέρες χωρίς χτύπημα και τα
        ορφανά ΔΕΝ μπλοκάρουν (μετρούν με το πρόγραμμα)."""
        self._check_open_period(date(year, month, 1), "έλεγχος κάρτας")
        issues = self.card_period_issues(year, month)
        blocking = [i for i in issues if i["blocking"]]
        if blocking:
            raise ValueError(
                f"Εκκρεμούν {len(blocking)} προβλήματα κάρτας: " + "· ".join(
                    f"{i['name']} " + (f"{config.gr_date(i['day'])} " if i["day"] else "")
                    + i["text"] for i in blocking
                )
            )
        self.store.append("COMPANY", CardPeriodChecked(year, month))
        return {"no_punch": len(issues)}

    def card_comparison(self, emp, year: int, month: int) -> dict:
        """ΣΥΓΚΡΙΣΗ προγράμματος ↔ κάρτας του μήνα (modal του ΗΜΕΡΟΛΟΓΙΟΥ).
        Ανά ημέρα: δηλωμένο, κάρτα, διαφορά λεπτών και η ταξινόμηση
        `emp.pay_source` — η ΙΔΙΑ με μισθοδοσία/απολογιστική (§5.11), όχι
        δεύτερη σύγκριση. Read-only."""
        leave = set(emp.leave_days_in_month(year, month))
        days = []
        for n in range(1, monthrange(year, month)[1] + 1):
            d = date(year, month, n)
            declared, actual = emp.declared_on(d), emp.actual_shifts(d)
            if not emp.active_on(d) or d in leave or not (declared or actual):
                continue
            physical, _tele = cp.split_telework(declared)
            try:
                plan = constraints.day_schedule_minutes(physical)
            except ValueError:
                plan = 0
            rules = config.work_hour_rules_for(d)
            # η κάρτα ΣΤΡΟΓΓΥΛΕΜΕΝΗ (`card_rounding_minutes`) — όπως πληρώνεται
            exact = sum(_shift_minutes(t) for t in actual)
            card = cp.round_total(exact, rules.card_rounding_minutes)
            source = emp.pay_source(d, rules.card_tolerance_minutes,
                                    rules.card_rounding_minutes)
            row = {"day": d, "declared": declared, "card": " + ".join(actual),
                   "plan_min": plan, "card_min": card, "card_exact": exact,
                   "diff": card - plan if actual else 0,
                   "state": "nopunch" if not actual else
                            ("ok" if source == "schedule" else "diff")}
            # η απολογιστική δήλωση της ημέρας (ίδια με retro_schedule_proposal)
            if row["state"] == "diff":
                row["retro"] = cp.with_telework(cp.schedule_text(actual), declared)
            # «ok» με απόκλιση = ΤΑΚΤΟΠΟΙΗΘΗΚΕ από κανόνα· ποιος, με τη σειρά
            # του day_matches (ανοχή → ευέλικτη → και τα δύο → στρογγυλοποίηση)
            elif actual and cp.schedule_text(actual) != physical:
                act, flex = cp.schedule_text(actual), int(emp.flexible_arrival_minutes or 0)
                tol = rules.card_tolerance_minutes
                row["rule"] = (
                    "ανοχή" if cp.day_matches(physical, act, 0, tol)
                    else "ευέλικτη προσέλευση" if cp.day_matches(physical, act, flex)
                    else "ευέλικτη προσέλευση + ανοχή" if cp.day_matches(physical, act, flex, tol)
                    else "στρογγυλοποίηση")
            days.append(row)
        basis, retro = emp.pay_basis.get((year, month), ("auto", True))
        first = date(year, month, 1)
        diff = [r for r in days if r["state"] == "diff"]
        return {
            "days": days,
            "diff_days": len(diff),
            "nopunch_days": sum(r["state"] == "nopunch" for r in days),
            "settled_days": sum("rule" in r for r in days),
            "plan_min": sum(r["plan_min"] for r in days),
            "card_min": sum(r["card_min"] or r["plan_min"] for r in days),
            "basis": basis, "retro": retro,
            "step": config.work_hour_rules_for(first).card_rounding_minutes,
            "auto_card": self._major_by_card(first),  # τι θα έκανε το «αυτόματο»
            "system": self.proj.schedule_system_on(first),
            "retro_needed": self.proj.schedule_system_on(first) == "retro" and bool(diff),
            "closed": self.is_period_closed(year, month),
        }

    def choose_month_pay_basis(self, employee_id: str, year: int, month: int,
                               basis: str, retro: bool = True) -> None:
        """Τρόπος υπολογισμού του μήνα (σύγκριση στο ΗΜΕΡΟΛΟΓΙΟ): οι ημέρες
        απόκλισης πληρώνονται με το πρόγραμμα ή την κάρτα (`auto` = κανόνας
        συστήματος) · `retro`: ο μήνας μπαίνει στην απολογιστική πρόταση."""
        if self.proj.employments.get(employee_id) is None:
            raise ValueError("Άγνωστη απασχόληση")
        if basis not in PAY_BASES:
            raise ValueError(f"Άγνωστος τρόπος υπολογισμού: {basis!r}")
        self._check_open_period(date(year, month, 1), "τρόπος υπολογισμού")
        self.store.append(employee_id, MonthPayBasisChosen(
            employee_id, year, month, basis, bool(retro)))

    # ---------------------------------------- Εισαγωγή καρτών ΕΡΓΑΝΗ (xlsx)
    def _employment_for_afm(self, afm: str, day: date):
        """Η απασχόληση του προσώπου με αυτό το ΑΦΜ που ήταν ΕΝΕΡΓΗ την `day`
        (ή None). Ένα πρόσωπο έχει πολλές περιόδους στον χρόνο — διαλέγουμε
        αυτήν που καλύπτει την ημέρα της παρουσίας."""
        for p in self.proj.persons.values():
            if p.afm != afm:
                continue
            for eid in p.employment_ids:
                emp = self.proj.employments.get(eid)
                if (
                    emp
                    and emp.hire_submitted
                    and emp.hire_date
                    and emp.hire_date <= day
                    and (emp.termination_date is None or day <= emp.termination_date)
                ):
                    return emp
        return None

    def import_karta_attendance(self, data, year: int, month: int) -> dict:
        """Διαβάζει αρχείο «Κάρτας Εργασίας» ΕΡΓΑΝΗ (xlsx) της ΠΕΡΙΟΔΟΥ
        `month`/`year` και καταγράφει τις πραγματικές παρουσίες ως ΧΤΥΠΗΜΑΤΑ
        ΚΑΡΤΑΣ (`record_attendance` ανά εργαζόμενο/ημέρα — επανεισαγωγή
        αντικαθιστά). Αντιστοίχιση με **ΑΦΜ**.

        ➜ **ΜΟΝΟ Ο ΜΗΝΑΣ**: οι εγγραφές του αρχείου που αφορούν άλλον μήνα
        ΔΕΝ καταγράφονται ποτέ — παραλείπονται και επιστρέφονται (`skipped`).

        ➜ **ΤΥΦΛΑ ΧΤΥΠΗΜΑΤΑ** (γραμμή χωρίς είσοδο ή έξοδο): γράφονται με
        `blind=True` (μόνιμη πληροφορία) και διορθώνονται ΕΔΩ από το
        πρόγραμμα (`complete_punch_from_schedule`), μέσα στο μηνιαίο όριο·
        όσα δεν διορθώθηκαν επιστρέφονται με τον λόγο.

        Επιστρέφει σύνοψη: ημέρες που καταγράφηκαν, πλήθος εργαζομένων,
        περίοδοι που θίχτηκαν, τυφλά χτυπήματα (`blind`), χτυπήματα σε ημέρες
        ΑΔΕΙΑΣ (`on_leave` — προειδοποίηση) και **άγνωστα ΑΦΜ**
        (χωρίς αντίστοιχη ενεργή απασχόληση) ώστε ο χρήστης να τα δει, αντί
        να χαθούν σιωπηλά."""
        punches = karta_import.parse_karta(data)
        shifts = karta_import.daily_shifts(punches)
        names = {p.afm: f"{p.last_name} {p.first_name}".strip() for p in punches}
        skipped = sorted({d for _, d in shifts if (d.year, d.month) != (year, month)})
        shifts = {k: v for k, v in shifts.items() if (k[1].year, k[1].month) == (year, month)}
        # Προέλεγχος ΟΛΟΥ του αρχείου: αν έστω μία ημέρα ανήκει σε κλεισμένη
        # περίοδο απορρίπτεται ολόκληρη η εισαγωγή — όχι μισές καταγραφές.
        self._check_open_days({day for _, day in shifts}, "εισαγωγή παρουσιών")

        recorded = 0
        employments: set[str] = set()
        periods: set[tuple[int, int]] = set()
        unmatched: dict[str, str] = {}
        blind: list[dict] = []
        on_leave: list[dict] = []
        for (afm, day), day_shifts in sorted(shifts.items()):
            emp = self._employment_for_afm(afm, day)
            if emp is None:
                unmatched[afm] = names.get(afm, "")
                continue
            # Χτύπημα σε ημέρα ΑΔΕΙΑΣ: καταγράφεται (η κάρτα δεν κρίνει), αλλά
            # ΠΡΟΕΙΔΟΠΟΙΕΙ — ή η άδεια διακόπηκε ή η άδεια είναι λάθος.
            if day in emp.leave_days_in_month(day.year, day.month):
                on_leave.append({"name": emp.full_name, "day": day})
            for pid in self.record_attendance(emp.employment_id, day,
                                              day_shifts, "ergani"):
                try:
                    fix, fixed = self.complete_punch_from_schedule(pid), True
                except ValueError as exc:
                    fix, fixed = str(exc), False
                blind.append({"name": emp.full_name, "day": day,
                              "fixed": fixed, "fix": fix})
            recorded += 1
            employments.add(emp.employment_id)
            periods.add((day.year, day.month))
        return {
            "punches": len(punches),
            "recorded": recorded,
            "employees": len(employments),
            "periods": sorted(periods),
            "unmatched": unmatched,
            "blind": blind,
            "on_leave": on_leave,
            "skipped": skipped,  # ημέρες ΑΛΛΟΥ μήνα — δεν καταγράφηκαν
        }
