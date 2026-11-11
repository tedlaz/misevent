"""
ΜΗΤΡΩΟ ΕΙΔΩΝ ΑΜΟΙΒΗΣ — μία εγγραφή ανά είδος που ΚΑΤΑΒΑΛΛΕΤΑΙ στον εργαζόμενο.

Κάθε είδος έχει 5–7 «ονόματα» σε διαφορετικά σημεία (είδος καθολικού,
`PaymentMade.payment_type`, υποχρέωση υπολογισμού/καταβολής, σήμα αρχείου…).
Πριν από αυτό το αρχείο οι μεταφράσεις μεταξύ τους ήταν χειρόγραφα dicts σε
~10 σημεία, και είχαν ήδη αρχίσει να αποκλίνουν (λατινικό «EA» στα PDF). Τώρα
ΖΟΥΝ ΕΔΩ· τα `scheduler.PAYMENT_TYPES`, `queries.PAYMENT_*`/`LEDGER_KINDS`,
`views/payroll.GIFT_*`, `pdf_reports.GIFT_*` ΠΑΡΑΓΟΝΤΑΙ.

Ό,τι είναι ΣΥΜΠΕΡΙΦΟΡΑ (τύποι `calc_*`, ΑΠΔ/ΦΜΥ περίοδοι, χρονοπρογραμματισμός
του Επιδόματος Αδείας) μένει κώδικας — βλ. ARCH_REF §6α.Ε.

Η ΣΕΙΡΑ των εγγραφών είναι η σειρά εμφάνισης (καθολικό, tooltip πληρωμών).
"""

from dataclasses import dataclass

from misevent.rules import obligation_types as ot


@dataclass(frozen=True)
class PaymentKind:
    kind: str  # είδος καθολικού· για Δώρα = `GiftCalculated.gift_type`
    payment_type: str  # `PaymentMade.payment_type`
    label: str
    ledger_label: str  # σύντομη ετικέτα στήλης καθολικού
    pay_obligation: str  # υποχρέωση από την οποία βγαίνει η ημ. καταβολής
    closes: bool = True  # η πληρωμή ΚΛΕΙΝΕΙ την `pay_obligation`
    calc_obligation: str = ""  # υπολογισμός (Δώρα/Επίδομα)· "" = κανένας
    short: str = ""  # σήμα ονόματος αρχείου (ΖΙΡ/PNG/PDF)
    filename: str = ""  # stem αρχείου εξαγωγής
    own_icon: str = ""  # μόνο για είδη ΧΩΡΙΣ υποχρέωση υπολογισμού

    @property
    def icon(self) -> str:
        """Το εικονίδιο της υποχρέωσης υπολογισμού (ΜΙΑ πηγή: ot.TYPES)."""
        return ot.ICONS.get(self.calc_obligation) or self.own_icon

    @property
    def is_bonus(self) -> bool:
        """Δώρο/Επίδομα: έχει δικό του υπολογισμό, εκτός μισθοδοσίας."""
        return bool(self.calc_obligation)


KINDS: tuple[PaymentKind, ...] = (
    PaymentKind("payroll", "salary", "Μισθοδοσία", "Περιόδου", "SALARY_PAYMENT"),
    PaymentKind("easter", "easter_gift", "Δώρο Πάσχα", "Δώρο Πάσχα",
                "EASTER_GIFT_PAYMENT", calc_obligation="EASTER_GIFT",
                short="ΔΠ", filename="doro_pasxa"),
    PaymentKind("christmas", "christmas_gift", "Δώρο Χριστουγέννων", "Δώρο Χριστ.",
                "CHRISTMAS_GIFT_PAYMENT", calc_obligation="CHRISTMAS_GIFT",
                short="ΔΧ", filename="doro_christougennon"),
    PaymentKind("allowance", "leave_allowance", "Επίδομα Αδείας", "Επίδομα Αδείας",
                "LEAVE_ALLOWANCE_PAYMENT", calc_obligation="LEAVE_ALLOWANCE",
                short="ΕΑ", filename="epidoma_adeias"),
    # Αποζημιώσεις αποχώρησης: εξοφλούνται ΜΑΖΙ με τους μισθούς — δεν κλείνουν
    # δική τους υποχρέωση (το SALARY_PAYMENT το κλείνει η πληρωμή μισθών).
    PaymentKind("severance", "severance", "Αποζημίωση Απόλυσης",
                "Αποζημίωση Απόλυσης", "SALARY_PAYMENT", closes=False, own_icon="📤"),
    PaymentKind("leave", "leave_compensation", "Αποζημίωση Αδείας",
                "Αποζημίωση Αδείας", "SALARY_PAYMENT", closes=False, own_icon="🗓️"),
)

BY_KIND = {k.kind: k for k in KINDS}
BY_PAYMENT = {k.payment_type: k for k in KINDS}
BONUSES = tuple(k for k in KINDS if k.is_bonus)
GIFTS = tuple(k for k in BONUSES if k.kind != "allowance")  # ετήσια Δώρα

# Πληρωμές ΤΑΜΕΙΩΝ (όχι αμοιβές): payment_type -> υποχρέωση καταβολής
FUND_PAYMENTS = {"efka": "EFKA_PAYMENT", "teka": "TEKA_PAYMENT", "fmy": "FMY_PAYMENT"}


def exit_gift_kind(month: int) -> str:
    """Ποιο Δώρο «τρέχει» σε αποχώρηση τον μήνα `month`: Πάσχα (1/1–30/4)
    ή Χριστουγέννων (1/5–31/12)."""
    return "easter" if month <= 4 else "christmas"


def validate() -> None:
    """Στο import: μοναδικά κλειδιά, υπαρκτές υποχρεώσεις (ot.TYPES)."""
    for attr in ("kind", "payment_type"):
        vals = [getattr(k, attr) for k in KINDS]
        dup = {v for v in vals if vals.count(v) > 1}
        if dup:
            raise ValueError(f"payment_kinds: διπλό {attr}: {', '.join(sorted(dup))}")
    for k in KINDS:
        for code in (k.pay_obligation, k.calc_obligation):
            if code and code not in ot.TYPES:
                raise ValueError(f"payment_kinds.{k.kind}: άγνωστη υποχρέωση «{code}»")
        if k.is_bonus and not k.short:
            raise ValueError(f"payment_kinds.{k.kind}: λείπει το σήμα αρχείου (`short`)")
    for code in FUND_PAYMENTS.values():
        if code not in ot.TYPES:
            raise ValueError(f"payment_kinds: άγνωστη υποχρέωση «{code}»")


validate()
