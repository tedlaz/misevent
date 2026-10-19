"""
Παρουσίες και ΨΗΦΙΑΚΗ ΚΑΡΤΑ ΕΡΓΑΣΙΑΣ (§5.11)

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

import uuid
from datetime import date

import misevent.integrations.card_punches as cp
import misevent.integrations.karta_import as karta_import
import misevent.rules.config as config
from misevent.domain.events import AttendanceRecorded, CardPunchCancelled, CardPunchRecorded


class AttendanceMixin:
    def record_attendance(self, employee_id, work_date, hours, overtime=0.0, shifts=()):
        self._check_open_period(work_date, "καταγραφή παρουσίας")
        self.store.append(
            employee_id,
            AttendanceRecorded(employee_id, work_date, hours, overtime, tuple(shifts)),
        )

    # ------------------------------------------------ ΨΗΦΙΑΚΗ ΚΑΡΤΑ ΕΡΓΑΣΙΑΣ
    def record_card_punch(self, employee_id: str, punch_date: date, time_text,
                          kind: str, source: str = "manual",
                          note: str = "") -> str:
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
            ),
        )
        return punch_id

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
        η μορφή που δέχεται το `record_attendance(..., shifts=…)`.

        ➜ Γέφυρα προς τη μισθοδοσία: η κάρτα είναι σήμερα **ΑΝΕΞΑΡΤΗΤΗ** πηγή
        (φάση 1 — καταγραφή & ζωντανή προβολή). Όποιος θελήσει να τη μετατρέψει
        σε παρουσία περνά από ΕΔΩ και από το `record_attendance`, ώστε να μη
        γραφτεί ποτέ δεύτερη υλοποίηση του ζευγαρώματος."""
        emp = self.proj.employments.get(employee_id)
        if emp is None:
            raise ValueError("Άγνωστη απασχόληση")
        return cp.shift_texts(emp.card_shifts(day))

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

    def import_karta_attendance(self, data) -> dict:
        """Διαβάζει αρχείο «Κάρτας Εργασίας» ΕΡΓΑΝΗ (xlsx) και καταγράφει τις
        πραγματικές παρουσίες ως `AttendanceRecorded` (μία ανά εργαζόμενο/ημέρα,
        με αθροισμένες ώρες βαρδιών). Η αντιστοίχιση γίνεται με **ΑΦΜ**.

        Επιστρέφει σύνοψη: ημέρες που καταγράφηκαν, πλήθος εργαζομένων,
        περίοδοι που θίχτηκαν, και **άγνωστα ΑΦΜ** (χωρίς αντίστοιχη ενεργή
        απασχόληση) ώστε ο χρήστης να τα δει, αντί να χαθούν σιωπηλά."""
        punches = karta_import.parse_karta(data)
        daily = karta_import.daily_hours(punches)
        shifts = karta_import.daily_shifts(punches)
        names = {p.afm: f"{p.last_name} {p.first_name}".strip() for p in punches}
        # Προέλεγχος ΟΛΟΥ του αρχείου: αν έστω μία ημέρα ανήκει σε κλεισμένη
        # περίοδο απορρίπτεται ολόκληρη η εισαγωγή — όχι μισές καταγραφές.
        self._check_open_days({day for _, day in daily}, "εισαγωγή παρουσιών")

        recorded = 0
        employments: set[str] = set()
        periods: set[tuple[int, int]] = set()
        unmatched: dict[str, str] = {}
        for (afm, day), hours in sorted(daily.items()):
            emp = self._employment_for_afm(afm, day)
            if emp is None:
                unmatched[afm] = names.get(afm, "")
                continue
            self.record_attendance(
                emp.employment_id, day, hours, shifts=shifts.get((afm, day), ())
            )
            recorded += 1
            employments.add(emp.employment_id)
            periods.add((day.year, day.month))
        return {
            "punches": len(punches),
            "recorded": recorded,
            "employees": len(employments),
            "periods": sorted(periods),
            "unmatched": unmatched,
        }
