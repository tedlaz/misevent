"""
Παραγωγή αρχείου **JL10** — Δήλωση απόδοσης παρακρατούμενου ΦΜΥ (ΑΑΔΕ).

Γραμμογράφηση: `data/JL10a.md` (πεδία & θέσεις), `data/JL10b.md` (πίνακας
τιμών «Είδος αποδοχών», πεδίο 4.9). Όνομα αρχείου: **JL10**.

Εγγραφές — **ΟΛΕΣ 148 χαρακτήρες**, με αυτή τη σειρά:

    0  Κεφαλίδα αρχείου
    1  Στοιχεία δηλούντος (εργοδότη)
    2  Σύνολα αποδοχών & λοιπών στοιχείων
    3  Αναλυτικές αμοιβές — ΜΙΑ ανά εργαζόμενο/είδος αποδοχών

Κοινοί κανόνες μορφοποίησης (ίδιοι με την ΑΠΔ — γι' αυτό οι formatters
`alpha`/`amount`/`num` επαναχρησιμοποιούνται από το `apd_files`, ΜΙΑ πηγή
αλήθειας για κεφαλαία/τόνους/zero-padding):
- Αριθμητικά: zero-padded αριστερά. Το `V` των μορφών `9(n)V99` ΔΕΝ γράφεται
  (1.234,56 -> `000000123456` σε πεδίο 9(10)V99).
- Αλφαριθμητικά: κεφαλαία χωρίς τόνους, space-padded δεξιά.
- Ημερομηνίες: **ΕΕΕΕΜΜΗΗ** (ΠΡΟΣΟΧΗ: ανάποδα από την ΑΠΔ, που θέλει ΗΗΜΜΕΕΕΕ).
- FILLER: αριθμητικό -> μηδενικά, αλφαριθμητικό -> κενά.

**ENCODING = cp1253**, επιβεβαιωμένο από πραγματικό δείγμα αρχείου ΑΑΔΕ (ΔΕΝ
τηρείται στο repo — περιείχε προσωπικά δεδομένα): τα bytes των ελληνικών του
(193=Α, 196=Δ, 201=Ι, 211=Σ …) ήταν ακριβώς Windows-1253 — ίδιο με την ΑΠΔ.
Η προδιαγραφή αναφέρει «437», αλλά το `cp437` κωδικοποιεί μόνο **5 από τα 24**
ελληνικά κεφαλαία (Γ Θ Σ Φ Ω), δηλαδή δεν θα μπορούσε καν να γράψει την
επωνυμία του δείγματος. Είναι ΜΙΑ σταθερά και παράμετρος
της `write()`. Ό,τι δεν κωδικοποιείται ΣΚΑΕΙ με σαφές μήνυμα (ποιος
χαρακτήρας, ποιο codepage) αντί να γραφτεί χαλασμένο αρχείο.
"""

import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from io import BytesIO

from misevent.integrations.apd_files import alpha, amount, num

CRLF = "\r\n"
ENCODING = "cp1253"  # βλ. docstring — επιβεβαιωμένο από δείγμα ΑΑΔΕ
FILE_NAME = "JL10"  # το όνομα ΜΕΣΑ στο zip — έτσι το περιμένει η ΑΑΔΕ


def zip_name(year: int, month: int) -> str:
    """Όνομα του αρχείου αποθήκευσης: `FMY_<έτος>_<μήνας>.zip`."""
    return f"FMY_{year}_{month:02d}.zip"

# Πεδίο 4.9 «Είδος αποδοχών» — πλήρης πίνακας στο data/JL10b.md.
# Εδώ μόνο όσα παράγει η μισθοδοσία· νέο είδος = μία εγγραφή.
EARNINGS_KIND_REGULAR = 1  # Τακτικές αποδοχές
EARNINGS_KIND_EXTRA = 2  # Πρόσθετες αποδοχές (επιδόματα, υπερωρίες, μπόνους)


class FMYFormatError(ValueError):
    pass


def dt_ymd(d: date | None) -> str:
    """Ημερομηνία ΕΕΕΕΜΜΗΗ (πεδίο 1.3)."""
    if d is None:
        return "0" * 8
    return f"{d.year:04d}{d.month:02d}{d.day:02d}"


def digits(text: str, length: int) -> str:
    """Αριθμητικό πεδίο δοσμένο ως κείμενο (ΑΦΜ/ΤΚ/ΑΜΚΑ): κρατά μόνο ψηφία."""
    d = "".join(c for c in str(text or "") if c.isdigit())
    if len(d) > length:
        raise FMYFormatError(f"«{text}» δεν χωρά σε {length} ψηφία")
    return d.zfill(length)


# ------------------------------------------------------------- Δεδομένα
@dataclass(frozen=True)
class DeclarantInfo:
    """Στοιχεία δηλούντος — εγγραφή τύπου 1."""

    afm: str
    legal_name: str  # Επωνυμία ή Επώνυμο (πεδίο 2.3α)
    first_name: str = ""  # μόνο για φυσικά πρόσωπα (2.3β)
    father_name: str = ""  # μόνο για φυσικά πρόσωπα (2.3γ)
    is_person: bool = False  # 2.4: επωνυμία=0, ονοματεπώνυμο=1
    # TODO: Να βάλουμε αντικείμενο δραστηριότητας
    activity: str = ""  # 2.6 Αντικείμενο δραστηριότητας
    city: str = ""
    street: str = ""
    street_no: str = ""
    postal_code: str = "00000"


@dataclass(frozen=True)
class EarningsRow:
    """Αναλυτική αμοιβή ενός εργαζόμενου — εγγραφή τύπου 3."""

    afm: str
    last_name: str
    first_name: str
    father_name: str
    amka: str
    children: int
    gross: float  # 4.10 Ακαθάριστες αποδοχές
    deductions: float  # 4.11 Κρατήσεις ΕΚΤΟΣ φόρου (εισφορές εργαζομένου)
    net: float  # 4.12 Καθαρές αποδοχές
    tax: float  # 4.17 Φόρος που παρακρατήθηκε
    earnings_kind: int = EARNINGS_KIND_REGULAR  # 4.9
    solidarity: float = 0.0  # 4.18 Ειδική εισφορά αλληλεγγύης άρ. 43Α
    digital_fee: float = 0.0  # 4.19 Ψηφιακό Τέλος Συναλλαγής
    foreign: bool = False  # 4.13 Ένδειξη αλλοδαπού
    country: str = ""  # 4.14 Χώρα φορ. κατοικίας (ISO-3166 alpha-2)
    tax_rate: int = 0  # 4.15 Φορολογικός συντελεστής
    reference_year: int = 0  # 4.21 Έτος αναφοράς (μόνο αναδρομικά)
    # 4.22 Διάταξη νόμου, μορφή ΧΧΧΧ/ΖΖΖΖ (άρθρο/νόμος). Το δείγμα της
    # ΑΑΔΕ γράφει «0000/0000» όταν δεν υπάρχει — ΟΧΙ κενά.
    law_provision: str = "0000/0000"


# -------------------------------------------------------------- Builder
class FMYFileBuilder:
    """Παράγει το αρχείο JL10 μιας μισθολογικής περιόδου."""

    REC_LEN = 148

    def __init__(
        self,
        declarant: DeclarantInfo,
        period_year: int,
        period_month: int,
        created: date | None = None,
        run_cycle: int | None = None,
    ):
        self.declarant = declarant
        self.year = period_year
        self.month = period_month
        self.created = created or datetime.now(UTC).date()
        # 1.4 «Αρ. κύκλου τρεξίματος: 20ΧΧ για τη χρήση 20ΧΧ»
        self.run_cycle = run_cycle if run_cycle is not None else period_year
        self._rows: list[EarningsRow] = []

    def add(self, row: EarningsRow):
        self._rows.append(row)

    @property
    def row_count(self) -> int:
        """Πλήθος αναλυτικών αμοιβών (εγγραφές τύπου 3) — μία ανά εργαζόμενο."""
        return len(self._rows)

    # ------------------------------------------------------ Εγγραφές
    def _rec0(self) -> str:
        rec = (
            "0"  # 1.1 Τύπος εγγραφής
            + alpha(FILE_NAME, 8)  # 1.2 Όνομα αρχείου
            + dt_ymd(self.created)  # 1.3 Ημερομηνία δημιουργίας
            + num(self.run_cycle, 4)  # 1.4 Αρ. κύκλου τρεξίματος
            + " " * 127  # 1.5 FILLER
        )
        return self._check(rec, "κεφαλίδα")

    def _rec1(self) -> str:
        d = self.declarant
        rec = (
            "1"  # 2.1 Τύπος εγγραφής
            + num(self.year, 4)  # 2.2 Έτος
            + alpha(d.legal_name, 18)  # 2.3α Επώνυμο/Επωνυμία
            + alpha(d.first_name, 9)  # 2.3β Όνομα
            + alpha(d.father_name, 3)  # 2.3γ Πατρώνυμο
            + ("1" if d.is_person else "0")  # 2.4 Ένδειξη
            + digits(d.afm, 9)  # 2.5 ΑΦΜ
            + alpha(d.activity, 16)  # 2.6 Αντικείμενο δραστηριότητας
            + alpha(d.city, 10)  # 2.7 Πόλη
            + alpha(d.street, 16)  # 2.8 Οδός
            + alpha(d.street_no, 5)  # 2.9 Αριθμός
            + digits(d.postal_code, 5)  # 2.10 ΤΚ
            + num(self.month, 2)  # 2.11 Μήνας
            + " " * 49  # 2.12 FILLER
        )
        return self._check(rec, "στοιχεία δηλούντος")

    def _rec2(self) -> str:
        t = self.totals()
        rec = (
            "2"  # 3.1 Τύπος εγγραφής
            + amount(t["gross"], 16)  # 3.2 Σύνολο ακαθαρίστων
            + amount(t["deductions"], 16)  # 3.3 Σύνολο κρατήσεων
            + amount(t["net"], 16)  # 3.4 Σύνολο καθαρών
            + "0" * 15  # 3.5 FILLER
            + amount(t["tax"], 15)  # 3.6 Σύνολο παρακρατηθέντος φόρου
            + amount(t["solidarity"], 15)  # 3.7 Σύνολο ειδικής εισφοράς
            + amount(t["digital_fee"], 14)  # 3.8 Σύνολο ψηφιακού τέλους
            + "0" * 13  # 3.9 FILLER
            + " " * 27  # 3.10 FILLER
        )
        return self._check(rec, "σύνολα")

    def _rec3(self, r: EarningsRow) -> str:
        rec = (
            "3"  # 4.1 Τύπος εγγραφής
            + digits(r.afm, 9)  # 4.2 ΑΦΜ
            + " "  # 4.3 FILLER
            + alpha(r.last_name, 18)  # 4.4 Επώνυμο
            + alpha(r.first_name, 9)  # 4.5 Όνομα
            + alpha(r.father_name, 3)  # 4.6 Πατρώνυμο
            + digits(r.amka, 11)  # 4.7 ΑΜΚΑ
            + num(r.children, 2)  # 4.8 Αριθμός τέκνων
            + num(r.earnings_kind, 2)  # 4.9 Είδος αποδοχών
            + amount(r.gross, 11)  # 4.10 Ακαθάριστες αποδοχές
            + amount(r.deductions, 10)  # 4.11 Κρατήσεις (εκτός φόρου)
            + amount(r.net, 11)  # 4.12 Καθαρές αποδοχές
            + ("1" if r.foreign else "0")  # 4.13 Ένδειξη αλλοδαπού
            + alpha(r.country, 2)  # 4.14 Χώρα φορολογικής κατοικίας
            + num(r.tax_rate, 2)  # 4.15 Φορολογικός συντελεστής
            + "0" * 5  # 4.16 FILLER
            + amount(r.tax, 10)  # 4.17 Φόρος που παρακρατήθηκε
            + amount(r.solidarity, 10)  # 4.18 Ειδική εισφορά αλληλεγγύης
            + amount(r.digital_fee, 9)  # 4.19 Ψηφιακό τέλος συναλλαγής
            + "0" * 8  # 4.20 FILLER
            + num(r.reference_year, 4)  # 4.21 Έτος αναφοράς
            + alpha(r.law_provision, 9)  # 4.22 Διάταξη νόμου
        )
        return self._check(rec, f"αναλυτική αμοιβή {r.afm}")

    def _check(self, rec: str, what: str) -> str:
        if len(rec) != self.REC_LEN:
            raise FMYFormatError(
                f"Εγγραφή «{what}»: {len(rec)} χαρακτήρες αντί για {self.REC_LEN}"
            )
        return rec

    # -------------------------------------------------------- Σύνολα
    def totals(self) -> dict:
        """Τα σύνολα της εγγραφής τύπου 2 — άθροισμα των αναλυτικών."""
        return {
            k: round(sum(getattr(r, k) for r in self._rows), 2)
            for k in ("gross", "deductions", "net", "tax", "solidarity", "digital_fee")
        }

    def render(self) -> str:
        if not self._rows:
            raise FMYFormatError("Καμία αναλυτική αμοιβή για την περίοδο")
        # Ταξινόμηση κατά ΑΦΜ, ώστε το αρχείο να είναι αναπαραγώγιμο
        rows = sorted(self._rows, key=lambda r: (digits(r.afm, 9), r.earnings_kind))
        lines = [self._rec0(), self._rec1(), self._rec2()]
        lines += [self._rec3(r) for r in rows]
        return CRLF.join(lines) + CRLF

    def encoded(self, encoding: str = ENCODING) -> bytes:
        """Το περιεχόμενο του JL10 σε bytes, στο codepage του αρχείου."""
        content = self.render()
        try:
            return content.encode(encoding)
        except UnicodeEncodeError as exc:
            ch = content[exc.start : exc.end]
            raise FMYFormatError(
                f"Ο χαρακτήρας {ch!r} δεν κωδικοποιείται σε {encoding}. "
                "Το JL10 γράφεται σε cp1253 (Windows Greek). "
                "Άλλαξε το fmy_files.ENCODING."
            ) from exc

    def zip_bytes(self, encoding: str = ENCODING) -> bytes:
        """Το JL10 πακεταρισμένο σε ZIP (bytes) — ΧΩΡΙΣ δίσκο.

        Μέσα στο ZIP το αρχείο έχει το κανονικό του όνομα (`JL10`), όπως το
        περιμένει η ΑΑΔΕ. Το timestamp του entry είναι η **ημ. δημιουργίας**,
        όχι η ώρα εκτέλεσης: ίδια δεδομένα -> ίδιο ZIP byte-for-byte
        (αναπαραγώγιμο, όπως και το ίδιο το JL10)."""
        info = zipfile.ZipInfo(
            FILE_NAME, date_time=(*self.created.timetuple()[:3], 0, 0, 0)
        )
        info.compress_type = zipfile.ZIP_DEFLATED
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(info, self.encoded(encoding))
        return buf.getvalue()

    def zip_name(self) -> str:
        return zip_name(self.year, self.month)
