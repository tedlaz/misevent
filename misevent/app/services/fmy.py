"""
ΦΜΥ: αναλυτικές αμοιβές, δηλών, παραγωγή αρχείου JL10

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

import misevent.domain.payroll_engine as pe
import misevent.rules.obligation_types as ot
from misevent.domain.events import FMYFileGenerated, FMYSubmitted
from misevent.integrations.fmy_files import (
    EARNINGS_KIND_REGULAR,
    DeclarantInfo,
    EarningsRow,
    FMYFileBuilder,
)


class FmyMixin:
    def gift_fmy_period(self, gift) -> tuple[int, int]:
        """(έτος, μήνας) **ΥΠΟΛΟΓΙΣΜΟΥ** ενός Δώρου — εκεί δηλώνεται στο ΦΜΥ.

        ΠΡΟΣΟΧΗ: ΔΕΝ είναι η περίοδος ΑΠΔ του Δώρου (`_apd_rows_for_period` —
        εκεί μπαίνει στο αρχείο του ΕΠΟΜΕΝΟΥ μήνα, §5.4). Ο φόρος
        παρακρατείται όταν το Δώρο υπολογίζεται/καταβάλλεται:
        - τακτικός κύκλος: ο μήνας της προθεσμίας του τύπου
          (Χριστουγέννων → Δεκέμβριος· Πάσχα → Μ. Τετάρτη, δηλαδή Απρίλιος
          Ή ΜΑΙΟΣ — το Πάσχα κινείται· τιμά και τυχόν override του §6α),
        - **εκκαθάριση αποχώρησης**: ο μήνας της αποχώρησης, αν αυτή
          προηγείται του τακτικού μήνα (το `terminate()` υπολογίζει τότε το
          pro-rata Δώρο).
        """
        code = "CHRISTMAS_GIFT" if gift.gift_type == "christmas" else "EASTER_GIFT"
        month = ot.due_date(code, gift.year, 0).month
        emp = self.proj.employments.get(gift.employee_id)
        term = getattr(emp, "termination_date", None) if emp else None
        if term is not None and term.year == gift.year and term.month < month:
            return (gift.year, term.month)
        return (gift.year, month)

    def allowance_fmy_period(self, allowance) -> tuple[int, int]:
        """(έτος, μήνας) υπολογισμού του Επιδόματος Αδείας: ο μήνας της
        πρώτης κανονικής άδειας — αλλιώς της αποχώρησης — του εργαζόμενου
        (`EmploymentState.allowance_period_month`, ΜΙΑ πηγή αλήθειας με τον
            scheduler)."""
        emp = self.proj.employments.get(allowance.employee_id)
        m = emp.allowance_period_month(allowance.year) if emp else None
        if m is None:
            raise ValueError(
                f"Δεν έχει οριστεί μήνας επιδόματος αδείας για {allowance.employee_id}"
            )
        return (allowance.year, m)

    def fmy_rows(self, year: int, month: int):
        """Οι αναλυτικές αμοιβές (εγγραφές 3 του JL10) της περιόδου —
        **ΜΙΑ γραμμή ανά εργαζόμενο**, είδος αποδοχών **1** (τακτικές).

        ΠΗΓΕΣ — ό,τι φορολογήθηκε ΜΕΣΑ στον μήνα, ΑΘΡΟΙΣΤΙΚΑ:
        - η **μηνιαία μισθοδοσία**,
        - τα **Δώρα** και το **Επίδομα Αδείας** που υπολογίστηκαν στον μήνα
          (`gift_fmy_period` / `allowance_fmy_period`) — αποδοχές, κρατήσεις
          και φόρος **προστίθενται** στη γραμμή του μήνα· ΔΕΝ γράφονται ως
          ξεχωριστή εγγραφή.
        Το σύνολο ταυτίζεται με το `FMYSubmitted.total_fmy` (`fmy_total`).

        Οι «Καθαρές αποδοχές» (4.12) είναι οι **φορολογητέες** (ακαθάριστες −
        κρατήσεις ΕΚΤΟΣ φόρου), αφού ο φόρος έχει δικό του πεδίο (4.17) —
        αλλιώς θα αφαιρούνταν δύο φορές.
        """
        acc, order = {}, []

        def add(employee_id, gross, deductions, taxable, tax):
            if employee_id not in acc:
                if self.proj.employments.get(employee_id) is None:
                    return
                acc[employee_id] = [0.0, 0.0, 0.0, 0.0]
                order.append(employee_id)
            a = acc[employee_id]
            a[0] += gross
            a[1] += deductions
            a[2] += taxable
            a[3] += tax

        for p in self.proj.payrolls:
            if p.period_year == year and p.period_month == month:
                add(p.employee_id, p.gross, p.efka_employee, p.taxable, p.fmy)
        # Δώρα & Επίδομα Αδείας: στον μήνα που υπολογίστηκαν
        extras = [(g, self.gift_fmy_period(g)) for g in self.proj.gifts]
        extras += [(a, self.allowance_fmy_period(a)) for a in self.proj.allowances]
        for item, period in extras:
            if period == (year, month):
                add(
                    item.employee_id,
                    item.gross,
                    item.efka_employee,
                    pe.r2(item.gross - item.efka_employee),
                    item.fmy,
                )

        rows = []
        for employee_id in order:
            emp = self.proj.employments[employee_id]
            gross, deductions, taxable, tax = acc[employee_id]
            rows.append(
                EarningsRow(
                    afm=emp.afm,
                    last_name=emp.last_name,
                    first_name=emp.first_name,
                    father_name=emp.father_name,
                    amka=emp.amka,
                    children=emp.children,
                    gross=pe.r2(gross),
                    deductions=pe.r2(deductions),
                    net=pe.r2(taxable),
                    tax=pe.r2(tax),
                    earnings_kind=EARNINGS_KIND_REGULAR,
                )
            )
        return rows

    def fmy_total(self, year: int, month: int) -> float:
        """Ο συνολικός παρακρατηθείς ΦΜΥ της περιόδου — **ΜΙΑ πηγή αλήθειας**
        για δήλωση, αρχείο JL10 και προεπισκόπηση (τακτικές + Δώρα/Επίδομα
        που υπολογίστηκαν στον μήνα)."""
        return pe.r2(sum(r.tax for r in self.fmy_rows(year, month)))

    def declarant_info(self) -> DeclarantInfo:
        """Στοιχεία δηλούντος (JL10 εγγραφή 1) από την εταιρεία."""
        c = self.proj.company
        if c is None:
            raise ValueError(
                "Δεν έχουν καταχωρηθεί στοιχεία εταιρείας — "
                "καλέστε πρώτα register_company()"
            )
        branch = self.get_branch(0)
        return DeclarantInfo(
            afm=c.afm,
            legal_name=c.legal_name,
            first_name=c.first_name,
            father_name=c.father_name,
            # Φυσικό πρόσωπο = έχει όνομα (πεδίο 2.4: επωνυμία=0/ονομ/μο=1)
            is_person=bool((c.first_name or "").strip()),
            activity=c.activity,
            city=branch.city,
            street=branch.street,
            street_no=branch.street_no,
            postal_code=branch.postal_code,
        )

    def fmy_builder(self, year: int, month: int, created=None):
        """Ο builder του JL10 μιας περιόδου — ΧΩΡΙΣ δίσκο.

        ΜΙΑ πηγή αλήθειας για το περιεχόμενο του αρχείου: το ίδιο καλούν η
        καταγραφή της παραγωγής (`generate_fmy_file`) και η παραγωγή on the
        fly για προβολή/λήψη (`fmy_file_bytes`/`fmy_zip_bytes`)."""
        rows = self.fmy_rows(year, month)
        if not rows:
            raise ValueError(f"Καμία μισθοδοσία για την περίοδο {month}/{year}")
        b = FMYFileBuilder(self.declarant_info(), year, month, created=created)
        for r in rows:
            b.add(r)
        return b

    def fmy_file_bytes(self, year: int, month: int, created=None) -> bytes:
        """Το ΩΜΟ JL10 (cp1253) της περιόδου, παραγόμενο on the fly."""
        return self.fmy_builder(year, month, created=created).encoded()

    def fmy_zip_bytes(self, year: int, month: int, created=None) -> bytes:
        """Το JL10 της περιόδου μέσα σε ZIP (bytes) — για κατέβασμα."""
        return self.fmy_builder(year, month, created=created).zip_bytes()

    def generate_fmy_file(self, year: int, month: int, created=None) -> str:
        """Καταγράφει την παραγωγή του JL10 της περιόδου. Επιστρέφει το όνομα.

        ⚠️ **Τίποτα δεν γράφεται στον δίσκο** (όπως και η ΑΠΔ, §5.3α): το
        αρχείο είναι παράγωγο των events και παράγεται on the fly όποτε
        ζητηθεί (οθόνη «Αρχεία ΦΜΥ» → προβολή/λήψη). Εδώ καταγράφονται μόνο
        τα σύνολα και το **λογικό όνομα λήψης** (`fmy_files.zip_name`)."""
        b = self.fmy_builder(year, month, created=created)
        # Επικυρώνεται ΤΩΡΑ (γραμμογράφηση, codepage) — δεν θέλουμε να μάθουμε
        # για σφάλμα τη στιγμή της λήψης.
        b.encoded()
        name = b.zip_name()
        t = b.totals()
        self.store.append(
            "COMPANY",
            FMYFileGenerated(year, month, t["gross"], t["tax"], b.row_count),
        )
        return name

    def submit_fmy(self, year: int, month: int, reference=""):
        """Στην πράξη: προσωρινή δήλωση ΦΜΥ στο myAADE. Το ποσό είναι το ΙΔΙΟ
        με το αρχείο JL10 (τακτικές + Δώρα/Επίδομα του μήνα)."""
        total = self.fmy_total(year, month)
        self.store.append("COMPANY", FMYSubmitted(year, month, total, reference))
        return total
