"""
Παραγωγή αρχείων ΑΠΔ Κοινών Επιχειρήσεων:

1. ΑΠΔ e-ΕΦΚΑ (νέο ΟΠΣ e-ΕΦΚΑ)
   Βάσει Γενικού Εγγράφου e-ΕΦΚΑ 2120927/16-12-2025 (ΠΑΡΑΡΤΗΜΑ 1,
   «Αναλυτική Περιοδική Δήλωση Κοινών Επιχειρήσεων - Ηλεκτρονική
   υποβολή με αρχείο»). Όνομα αρχείου: CSL01.
   Εγγραφές: τύπος 1 (363), τύπος 2 (178), τύπος 3 (162),
             τύπος 4 (46), EOF (3).

2. ΑΠΔ ΤΕΚΑ Κοινών Επιχειρήσεων & Δημόσιου Τομέα
   Βάσει «Τεχνικές Προδιαγραφές δημιουργίας αρχείου ΑΠΔ ΤΕΚΑ»
   (ανακοίνωση ΤΕΚΑ 10/03/2026, αναβάθμιση για συμβατότητα με το
   νέο ΟΠΣ e-ΕΦΚΑ). Όνομα αρχείου: CSL01.
   Εγγραφές: τύπος 1 (363), τύπος 2 (178), τύπος 3 (148),
             τύπος 4 (46), EOF (3).
   Η ΤΕΚΑ τύπου 3 = ΕΦΚΑ τύπου 3 ΧΩΡΙΣ τα πεδία:
   «Γραμμάτιο προείσπραξης έμμισθων δικηγόρων»,
   «Φορέας Επικουρικής Ασφάλισης εκτός eΕΦΚΑ»,
   «Φορέας Ασθένειας εκτός eΕΦΚΑ».

Κοινοί κανόνες μορφοποίησης (και στις δύο προδιαγραφές):
- Αριθμητικά πεδία: zero-padded αριστερά. Ο χαρακτήρας V δεν γράφεται
  (2.340,30€ -> 0000234030 σε πεδίο 99999999V99).
- Αλφαβητικά/αλφαριθμητικά: κεφαλαία (Α-Ω, A-Z), space-padded δεξιά.
- Ημερομηνίες: ΗΗΜΜΕΕΕΕ.
- Κάθε εγγραφή τελειώνει σε CR LF (0D 0A).
- Ταξινόμηση: ασφαλισμένοι κατά ΑΜΑ, εγγραφές ασφάλισης κατά τύπο αποδοχών.
- Μέγιστο μέγεθος αρχείου 20MB.
"""

import unicodedata
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO
from typing import ClassVar

CRLF = "\r\n"
ENCODING = "cp1253"  # Ελληνικά Windows-1253 (τα κεφαλαία ελληνικά δεν είναι ASCII)
FILE_NAME = "CSL01"  # Το όνομα που περιμένει ο e-ΕΦΚΑ/ΤΕΚΑ ΜΕΣΑ στο ZIP
MAX_BYTES = 20 * 1024 * 1024


def zip_name(fund: str, year: int, month: int) -> str:
    """Όνομα του ZIP λήψης: `APD_<ΤΑΜΕΙΟ>_<έτος>_<μήνας>.zip`.

    Το ΑΡΧΕΙΟ ΔΕΝ αποθηκεύεται ποτέ στον δίσκο — παράγεται on the fly από
    τα events· αυτό είναι απλώς το όνομα με το οποίο κατεβαίνει.
    """
    return f"APD_{fund.upper()}_{year}_{month:02d}.zip"


# ------------------------------------------------------------ Μορφοποίηση
class APDFormatError(ValueError):
    pass


def _strip_accents(s: str) -> str:
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def num(value: int, length: int, field: str = "") -> str:
    """Αριθμητικό πεδίο, zero-padded αριστερά.

    `field`: η ΟΝΟΜΑΣΙΑ του πεδίου — μπαίνει στο μήνυμα σφάλματος, ώστε ο
    χρήστης να ξέρει ΠΟΙΑ καταχώρηση να διορθώσει όταν έχει γραφτεί κείμενο
    σε αριθμητικό πεδίο (π.χ. ΚΠΚ/ΚΑΔ/ΑΜΕ) — αλλιώς έβγαινε το ακατανόητο
    «invalid literal for int()».
    """
    where = f" ({field})" if field else ""
    try:
        s = str(int(value))
    except (TypeError, ValueError) as exc:
        raise APDFormatError(
            f"Η τιμή «{value}»{where} δεν είναι αριθμός — η ΑΠΔ δέχεται μόνο "
            "ψηφία στο πεδίο αυτό. Διόρθωσε την καταχώρηση."
        ) from exc
    if len(s) > length:
        raise APDFormatError(f"Αριθμός {value}{where} δεν χωρά σε {length} ψηφία")
    return s.zfill(length)


def amount(value: float, length: int) -> str:
    """Ποσό σε πεδίο μορφής 9...9V99 συνολικού μήκους `length`
    (τα 2 τελευταία ψηφία = δεκαδικά, το V δεν γράφεται)."""
    cents = int(
        Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100
    )
    if cents < 0:
        raise APDFormatError("Δεν επιτρέπονται αρνητικά ποσά στην ΑΠΔ")
    return num(cents, length)


def pct(value: float) -> str:
    """Ποσοστό σε πεδίο 999V999 (6 ψηφία, 3 δεκαδικά)."""
    mils = int(
        Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP) * 1000
    )
    return num(mils, 6)


def alpha(text: str, length: int) -> str:
    """Κεφαλαία χωρίς τόνους, space-padded δεξιά."""
    t = _strip_accents((text or "").upper())
    t = t.replace("ς", "Σ")
    if len(t) > length:
        t = t[:length]
    return t.ljust(length)


def dt(d: date | None, blank_zero: bool = False) -> str:
    if d is None:
        return "00000000" if blank_zero else " " * 8
    return f"{d.day:02d}{d.month:02d}{d.year:04d}"


# ------------------------------------------------------------- Δεδομένα
@dataclass(frozen=True)
class EmployerInfo:
    ame: str  # 10ψήφιος Αριθμός Μητρώου Εργοδότη
    afm: str  # 9ψήφιο ΑΦΜ
    legal_name: str  # Επωνυμία / Επώνυμο
    first_name: str = ""  # Όνομα (φυσικά πρόσωπα)
    father_name: str = ""
    street: str = ""
    street_no: str = ""
    postal_code: str = "00000"
    city: str = ""
    local_office_code: str = "0000000000"  # Κωδικός Τοπικής Δ/νσης e-ΕΦΚΑ
    local_office_name: str = ""


@dataclass(frozen=True)
class InsuredInfo:
    ama: str  # 9ψήφιος Αριθμός Μητρώου Ασφαλισμένου
    amka: str  # 11ψήφιο ΑΜΚΑ
    last_name: str
    first_name: str
    father_name: str
    mother_name: str
    birth_date: date
    afm: str


@dataclass(frozen=True)
class InsuranceRow:
    """Μία εγγραφή τύπου 3 «Στοιχεία Ασφάλισης» (ανά τύπο αποδοχών)."""

    branch_no: int  # Αρ. Παραρτήματος (0 = έδρα)
    kad: str  # 4ψήφιος ΚΑΔ (κατά ΟΣΥΚ)
    full_schedule: bool  # Πλήρες ωράριο
    all_working_days: bool  # Όλες εργάσιμες
    sundays: int  # Κυριακές/Αργίες
    specialty_code: str  # 6ψήφιος κωδ. ειδικότητας (ΟΣΥΚ)
    special_case: str  # Ειδική περίπτωση ασφάλισης ("00" αν καμία)
    kpk: str  # 4ψήφιος Κωδικός Πακέτου Κάλυψης
    period_month: int
    period_year: int
    date_from: date | None
    date_to: date | None
    earnings_type: str  # 3ψήφιος τύπος αποδοχών (001 Τακτικές, 003 ΔΧ,
    # 004 ΔΠ, 005 Επ. Αδείας, 008 Αποδοχές ασθενείας, ...)
    # ➜ Το 008 έχει ΔΙΚΟ ΤΟΥ διάστημα (No 40/41): έναρξη/λήξη της ασθένειας,
    #   όχι ο πλήρης μήνας — βλ. services.sickness_apd_lines.
    insurance_days: int
    daily_wage: float
    gross: float
    contrib_employee: float
    contrib_employer: float
    subsidy_employee: float = 0.0
    subsidy_employer: float = 0.0
    lawyer_advance: float = 0.0  # ΕΦΚΑ μόνο: Γραμμάτιο προείσπραξης
    aux_fund_code: str = ""  # ΕΦΚΑ μόνο: Φορέας Επικουρικής εκτός
    # e-ΕΦΚΑ ("01"=ΤΕΚΑ, κενό αν e-ΕΦΚΑ)
    sickness_fund_code: str = ""  # ΕΦΚΑ μόνο: Φορέας Ασθένειας εκτός
    is_daily_paid: bool = False  # Ημερομίσθιος
    weekly_system: int = 5  # 5=πενθήμερη, 6=εξαήμερη
    art39_par9: str = "00"  # Ιδιότητα αντισυμβαλλόμενου αρ.39§9
    # ΔΕΥΤΕΡΗ ΚΑΛΥΨΗ ΠΑΝΩ ΣΤΙΣ ΙΔΙΕΣ ΑΠΟΔΟΧΕΣ: η εγγραφή αφορά τις ΙΔΙΕΣ
    # αποδοχές/ημέρες με άλλη εγγραφή του ίδιου ασφαλισμένου στο ΙΔΙΟ αρχείο
    # (π.χ. επικουρική ή πρόνοια ΕΦΚΑ δίπλα στην κύρια, με δικό της ΚΠΚ).
    # Τα No 45/43 της γράφονται κανονικά — αλλά στα ΣΥΝΟΛΑ της κεφαλίδας οι
    # αποδοχές (No 17) και οι ημέρες (No 16) μετρούν ΜΙΑ φορά. Οι εισφορές
    # (No 18) αθροίζονται ΠΑΝΤΑ, γιατί κάθε κάλυψη οφείλει τις δικές της.
    repeats_earnings: bool = False

    @property
    def total_contrib(self) -> float:
        return round(self.contrib_employee + self.contrib_employer, 2)

    @property
    def total_subsidy(self) -> float:
        return round(self.subsidy_employee + self.subsidy_employer, 2)

    @property
    def payable(self) -> float:
        return round(self.total_contrib - self.total_subsidy - self.lawyer_advance, 2)


@dataclass(frozen=True)
class SubsidyRow:
    """Εγγραφή τύπου 4 «Στοιχεία Επιδοτήσεων» (προαιρετική)."""

    subsidy_type: str  # 3ψήφιος κωδικός (π.χ. 102, 103, 107)
    pct_employee: float
    amount_employee: float
    pct_employer: float
    amount_employer: float

    @property
    def total(self) -> float:
        return round(self.amount_employee + self.amount_employer, 2)


# ------------------------------------------------------------- Builder
class APDFileBuilder:
    """Παράγει αρχείο ΑΠΔ Κοινών Επιχειρήσεων για fund='efka' ή 'teka'.

    Και στις δύο περιπτώσεις το όνομα αρχείου είναι CSL01·
    διαφέρει μόνο η εγγραφή τύπου 3 (ΕΦΚΑ 162 bytes, ΤΕΚΑ 148 bytes).
    """

    REC1_LEN = 363
    REC2_LEN = 178
    REC4_LEN = 46
    REC3_LEN: ClassVar[Mapping[str, int]] = {"efka": 162, "teka": 148}

    def __init__(
        self,
        fund: str,
        employer: EmployerInfo,
        period_month: int,
        period_year: int,
        declaration_type: str = "01",  # 01 Κανονική, 04 Συμπληρωματική
        submission_date: date | None = None,
    ):
        if fund not in ("efka", "teka"):
            raise ValueError("fund πρέπει να είναι 'efka' ή 'teka'")
        self.fund = fund
        self.employer = employer
        self.month = period_month
        self.year = period_year
        self.decl_type = declaration_type
        self.submission_date = submission_date or datetime.now(UTC).date()
        # [(InsuredInfo, [ (InsuranceRow, [SubsidyRow,...]) ... ]), ...]
        self._insured: list = []

    @staticmethod
    def _insured_key(info: InsuredInfo) -> tuple:
        """Ταυτότητα ασφαλισμένου στο αρχείο (ΑΜΑ/ΑΜΚΑ/ΑΦΜ)."""
        return (info.ama, info.amka, info.afm)

    def add_insured(
        self,
        info: InsuredInfo,
        rows: list[InsuranceRow],
        subsidies_per_row: dict[int, list[SubsidyRow]] | None = None,
    ):
        """Προσθέτει εγγραφές ασφάλισης (τύπου 3) για έναν ασφαλισμένο.

        ΜΙΑ και μόνο εγγραφή τύπου 2 ανά ασφαλισμένο, με ΟΛΕΣ τις δικές του
        εγγραφές τύπου 3 από κάτω: αν ο ίδιος ασφαλισμένος προστεθεί ξανά
        (π.χ. ο καλών στέλνει μία γραμμή τη φορά — τακτικές, Δώρα, δεύτερη
        κάλυψη ΕΦΚΑ, δεύτερη περίοδο απασχόλησης στον ίδιο μήνα), οι γραμμές
        ΣΥΓΧΩΝΕΥΟΝΤΑΙ στην υπάρχουσα εγγραφή του."""
        subs = subsidies_per_row or {}
        packed = [(r, subs.get(i, [])) for i, r in enumerate(rows)]
        key = self._insured_key(info)
        for existing_info, existing_packed in self._insured:
            if self._insured_key(existing_info) == key:
                existing_packed.extend(packed)
                return
        self._insured.append((info, packed))

    # ------------------------------------------------------ Εγγραφές
    def _rec1(self, total_days: int, total_gross: float, total_payable: float) -> str:
        e = self.employer
        rec = (
            "1"  # 1  Τύπος εγγραφής
            + self.decl_type  # 2  Τύπος δήλωσης (01/04)
            + num(e.local_office_code, 10, "No 3 Κωδ. Τοπικής Δ/νσης")  # 3
            + alpha(e.local_office_name, 50)  # 4  Ονομασία Τοπ. Δ/νσης
            + alpha(e.legal_name, 80)  # 5  Επωνυμία/Επώνυμο
            + alpha(e.first_name, 30)  # 6  Όνομα
            + alpha(e.father_name, 30)  # 7  Όνομα Πατρός
            + num(e.ame, 10, "No 8 ΑΜΕ εργοδότη")  # 8  ΑΜΕ
            + num(e.afm, 9, "No 9 ΑΦΜ εργοδότη")  # 9  ΑΦΜ
            + alpha(e.street, 50)  # 10 Οδός
            + alpha(e.street_no, 10)  # 11 Αριθμός
            + num(e.postal_code, 5, "No 12 ΤΚ εργοδότη")  # 12 ΤΚ
            + alpha(e.city, 30)  # 13 Πόλη
            + num(self.month, 2)  # 14 Μήνας Αναφοράς
            + num(self.year, 4)  # 15 Έτος Αναφοράς
            + num(total_days, 8)  # 16 Σύνολο Ημερών
            + amount(total_gross, 12)  # 17 Σύνολο Αποδοχών
            + amount(total_payable, 12)  # 18 Σύνολο Καταβλητέων
            + dt(self.submission_date)  # 19 Ημ/νία υποβολής
        )
        assert len(rec) == self.REC1_LEN, len(rec)
        return rec

    def _rec2(self, p: InsuredInfo) -> str:
        rec = (
            "2"
            + num(p.ama, 9, f"No 21 ΑΜΑ — {p.last_name} {p.first_name}")  # 21
            + num(p.amka, 11, f"No 22 ΑΜΚΑ — {p.last_name} {p.first_name}")  # 22
            + alpha(p.last_name, 50)  # 23 Επώνυμο
            + alpha(p.first_name, 30)  # 24 Όνομα
            + alpha(p.father_name, 30)  # 25 Όνομα Πατρός
            + alpha(p.mother_name, 30)  # 26 Όνομα Μητρός
            + dt(p.birth_date)  # 27 Ημ. Γέννησης
            + num(p.afm, 9, f"No 28 ΑΦΜ — {p.last_name} {p.first_name}")  # 28
        )
        assert len(rec) == self.REC2_LEN, len(rec)
        return rec

    def _rec3(self, r: InsuranceRow) -> str:
        common_a = (
            "3"
            + num(r.branch_no, 4)  # 30 Αρ. Παραρτήματος
            + num(r.kad, 4, "No 31 ΚΑΔ ειδικότητας")  # 31 ΚΑΔ
            + ("1" if r.full_schedule else "0")  # 32 Πλήρες Ωράριο
            + ("1" if r.all_working_days else "0")  # 33 Όλες εργάσιμες
            + num(r.sundays, 1)  # 34 Κυριακές/Αργίες
            + num(r.specialty_code, 6, "No 35 Κωδ. Ειδικότητας")  # 35
            + (r.special_case or "00").zfill(2)  # 36 Ειδική περίπτωση
            + num(r.kpk, 4, "No 37 ΚΠΚ κάλυψης")  # 37 ΚΠΚ
            + num(r.period_month, 2)  # 38 Μισθ. περίοδος - μήνας
            + num(r.period_year, 4)  # 39 Μισθ. περίοδος - έτος
            + dt(r.date_from, blank_zero=True)  # 40 Από (κενό/0 για δώρα)
            + dt(r.date_to, blank_zero=True)  # 41 Έως
            + (r.earnings_type or "001").zfill(3)  # 42 Τύπος Αποδοχών
            + num(r.insurance_days, 3)  # 43 Ημέρες Ασφάλισης
            + amount(r.daily_wage, 10)  # 44 Ημερομίσθιο
            + amount(r.gross, 10)  # 45 Αποδοχές
            + amount(r.contrib_employee, 10)  # 46 Εισφορές Ασφαλισμένου
            + amount(r.contrib_employer, 10)  # 47 Εισφορές Εργοδότη
            + amount(r.total_contrib, 11)  # 48 Συνολικές Εισφορές
            + amount(r.subsidy_employee, 10)  # 49 Συν. Επιδ. Ασφαλισμένου
            + amount(r.subsidy_employer, 10)  # 50 Συν. Επιδ. Εργοδότη
            + amount(r.payable, 11)  # 51 Καταβλητέες εισφορές
            + amount(r.total_subsidy, 10)  # 52 Συν. Ποσό Επιδοτήσεων
        )
        if self.fund == "efka":
            middle = (
                amount(r.lawyer_advance, 10)  # 53 Γραμμάτιο προείσπραξης
                + (
                    r.aux_fund_code.zfill(2)  # 54 Φορέας Επικ. εκτός eΕΦΚΑ
                    if r.aux_fund_code
                    else "  "
                )  #    (κενό αν e-ΕΦΚΑ)
                + (
                    r.sickness_fund_code.zfill(2)  # 55 Φορέας Ασθένειας εκτός
                    if r.sickness_fund_code
                    else "  "
                )
            )
        else:
            middle = ""  # ΤΕΚΑ: τα 3 πεδία δεν υπάρχουν
        rec = (
            common_a
            + middle
            + ("1" if r.is_daily_paid else "0")  # Ημερομίσθιος
            + num(r.weekly_system, 1)  # Σύστημα Εβδ. Εργασίας 5/6
            + (r.art39_par9 or "00").zfill(2)  # Ιδιότητα αρ.39 §9
        )
        assert len(rec) == self.REC3_LEN[self.fund], len(rec)
        return rec

    def _rec4(self, s: SubsidyRow) -> str:
        rec = (
            "4"
            + s.subsidy_type.zfill(3)  # Κωδ. Τύπου επιδότησης
            + pct(s.pct_employee)  # Ποσοστό Ασφαλισμένου
            + amount(s.amount_employee, 10)  # Ποσό Ασφαλισμένου
            + pct(s.pct_employer)  # Ποσοστό Εργοδότη
            + amount(s.amount_employer, 10)  # Ποσό Εργοδότη
            + amount(s.total, 10)  # Συνολικό Ποσό Επιδότησης
        )
        assert len(rec) == self.REC4_LEN, len(rec)
        return rec

    # ------------------------------------------------------ Παραγωγή
    def render(self) -> str:
        # Ταξινόμηση: ασφαλισμένοι κατά ΑΜΑ, εγγραφές κατά τύπο αποδοχών
        # Ταξινόμηση κατά ΑΜΑ. Ανεκτική στο μη αριθμητικό ΑΜΑ, ώστε το σφάλμα
        # να το βγάλει η _rec2 — με το ΟΝΟΜΑ του πεδίου και του ασφαλισμένου.
        def _ama_key(t):
            ama = str(t[0].ama or "")
            return (0, int(ama)) if ama.isdigit() else (1, 0)

        insured = sorted(self._insured, key=_ama_key)
        total_days = total_gross = total_payable = 0
        body = []
        for info, packed in insured:
            body.append(self._rec2(info))
            for row, subs in sorted(packed, key=lambda t: t[0].earnings_type):
                body.append(self._rec3(row))
                for s in subs:
                    body.append(self._rec4(s))
                # Οι αποδοχές/ημέρες μιας δεύτερης κάλυψης πάνω στις ΙΔΙΕΣ
                # αποδοχές δεν ξαναμετρούν στην κεφαλίδα (βλ. repeats_earnings).
                if not row.repeats_earnings:
                    total_days += row.insurance_days
                    total_gross = round(total_gross + row.gross, 2)
                total_payable = round(total_payable + row.payable, 2)
        header = self._rec1(total_days, total_gross, total_payable)
        return CRLF.join([header, *body, "EOF"]) + CRLF

    def encoded(self, encoding: str = ENCODING) -> bytes:
        """Το περιεχόμενο του CSL01 σε bytes (cp1253) — ΧΩΡΙΣ δίσκο."""
        data = self.render().encode(encoding)
        if len(data) > MAX_BYTES:
            raise APDFormatError(
                "Το αρχείο ΑΠΔ υπερβαίνει τα 20MB — "
                "απαιτείται διάσπαση σε περισσότερα αρχεία"
            )
        return data

    def zip_bytes(self, encoding: str = ENCODING) -> bytes:
        """Το CSL01 πακεταρισμένο σε ZIP (bytes), για κατέβασμα.

        Το timestamp του entry είναι η ημ. υποβολής (όχι η ώρα εκτέλεσης):
        ίδια δεδομένα -> ίδιο ZIP byte-for-byte, όπως και στο JL10.
        """
        info = zipfile.ZipInfo(
            FILE_NAME, date_time=(*self.submission_date.timetuple()[:3], 0, 0, 0)
        )
        info.compress_type = zipfile.ZIP_DEFLATED
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(info, self.encoded(encoding))
        return buf.getvalue()

    def zip_name(self) -> str:
        return zip_name(self.fund, self.year, self.month)
