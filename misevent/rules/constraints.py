"""
Κεντρικοί κανόνες στηλών & πινάκων — η ΜΟΝΑΔΙΚΗ πηγή αλήθειας για τη
μοναδικότητα, τη μορφή και τα όρια των βασικών πεδίων του μοντέλου.

Πού επιβάλλονται
----------------
Η επιβολή γίνεται στο **command layer** (`services.py`), ΠΡΙΝ γραφτεί το
event. ΔΕΝ υπάρχει δεύτερο δίχτυ σε επίπεδο βάσης: το παλιό SQL read model
έφυγε, και ούτως ή άλλως UNIQUE στους πίνακες θα έσπαγε το replay όταν
υπάρχει ήδη αποθηκευμένο event που την παραβιάζει (τα events δεν διαγράφονται).

Πίνακας κανόνων
---------------
PERSON (φυσικό πρόσωπο — σταθερή ταυτότητα)
  ΜΟΝΑΔΙΚΟ  ΑΦΜ    (9 ψηφία + ΨΗΦΙΟ ΕΛΕΓΧΟΥ mod 11 — `check_afm`)
  ΜΟΝΑΔΙΚΟ  ΑΜΚΑ   (11 ψηφία: ΗΗΜΜΕΕ γέννησης + Luhn — `check_amka`)
  ΜΟΝΑΔΙΚΟΣ συνδυασμός (Επώνυμο, Όνομα, Πατρώνυμο, Ημ. Γέννησης)
  ΥΠΟΧΡΕΩΤΙΚΑ Επώνυμο, Όνομα, Πατρώνυμο, Μητρώνυμο, Ημ. Γέννησης, ΑΦΜ, ΑΜΚΑ
  ΠΡΟΑΙΡΕΤΙΚΟ ΑΜΑ                                 (9 ψηφία, αν δοθεί)
  Ονόματα: κεφαλαία, χωρίς τόνους (κανονικοποίηση `norm_name`)

EMPLOYMENT (εργασιακή σχέση — περίοδος απασχόλησης ενός Person)
  pay_type ∈ {"monthly", "daily"}
  monthly_gross > 0 για μισθωτούς· daily_wage > 0 για ημερομίσθιους
  0 < weekly_hours <= 48
  Ένα Person → πολλά Employments (επαναπρόσληψη = νέο Employment).

Οι έλεγχοι πετούν `ValueError` με **ελληνικό** μήνυμα.
"""

import re
import unicodedata
from datetime import date as _date

import misevent.integrations.ergani_parameters as ergani_parameters
import misevent.rules.config as config

# Ωράριο ημέρας: "09:00-17:00"
_TIME_RANGE = re.compile(
    r"([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)"
)

# Μήκη αριθμητικών μητρώων (ψηφία)
AFM_LENGTH = 9
AMKA_LENGTH = 11
AMA_LENGTH = 9

# Πεδία ταυτότητας Person που μπορούν να διορθωθούν (PersonDetailsCorrected)
PERSON_FIELDS = (
    "last_name",
    "first_name",
    "father_name",
    "mother_name",
    "afm",
    "amka",
    "ama",
    "children",
    "email",
    "sex",
    "nationality_code",
    "identity_type",
    "identity_number",
    "identity_issuer",
    "identity_issue_date",
    "identity_expiry_date",
    "marital_status",
    "education_level",
    "res_permit_inst", "res_permit_inst_type", "res_permit_inst_number",
    "res_permit_inst_expiry_date", "res_permit_ap", "res_permit_ap_type",
    "res_permit_ap_number", "res_permit_ap_expiry_date", "res_permit_visa",
    "res_permit_visa_number", "res_permit_visa_from", "res_permit_visa_to",
)

# Κλειδιά μοναδικότητας — τα επιβάλλει το command layer πριν γραφτεί event
PERSON_UNIQUE_KEYS = (
    ("afm",),
    ("amka",),
    ("last_name", "first_name", "father_name", "mother_name"),
)


# ------------------------------------------------------ Κανονικοποίηση
def _strip_accents(s: str) -> str:
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def norm_name(s: str) -> str:
    """Κεφαλαία, χωρίς τόνους, χωρίς διπλά κενά (για ονόματα προσώπων)."""
    return " ".join(_strip_accents(str(s or "")).upper().split())


def norm_digits(s: str) -> str:
    """Κρατά μόνο τα ψηφία (ΑΦΜ/ΑΜΚΑ/ΑΜΑ γράφονται συχνά με κενά/παύλες)."""
    return "".join(c for c in str(s or "") if c.isdigit())


def norm_email(s: str) -> str:
    """Κανονικοποιεί email (χωρίς κενά, πεζά). Προαιρετικό πεδίο· αν δοθεί
    ελέγχεται στοιχειώδης μορφή (ένα «@» με κείμενο εκατέρωθεν)."""
    e = str(s or "").strip().lower()
    if not e:
        return ""
    local, sep, domain = e.partition("@")
    if not sep or not local or "." not in domain or domain.startswith("."):
        raise ValueError(f"Μη έγκυρο email: {s}")
    return e


def identity_key(last_name: str, first_name: str, father_name: str, birth_date):
    """Κανονικοποιημένο κλειδί ταυτότητας προσώπου:
    Επώνυμο / Όνομα / Πατρώνυμο / **ημερομηνία γέννησης**.

    Το μητρώνυμο παραμένει υποχρεωτικό στοιχείο (ΑΠΔ/ΕΡΓΑΝΗ) αλλά ΔΕΝ
    ταυτοποιεί — δύο αδέλφια έχουν ίδια τετράδα ονομάτων και διαφορετική
    γέννηση, ενώ συνώνυμοι με ίδιο πατέρα διακρίνονται από τη γέννηση."""
    return (
        norm_name(last_name),
        norm_name(first_name),
        norm_name(father_name),
        birth_date,
    )


# --------------------------------------------------------- Έλεγχοι
def afm_checksum_ok(afm: str) -> bool:
    """Αλγοριθμικός έλεγχος ΑΦΜ (ΑΑΔΕ, mod 11).

    Τα 8 πρώτα ψηφία σταθμίζονται με 2^8 … 2^1 (από αριστερά προς τα δεξιά)·
    το άθροισμα mod 11, και το αποτέλεσμα mod 10, δίνει το 9ο (ψηφίο
    ελέγχου). Το «000000000» περνά αριθμητικά αλλά ΔΕΝ είναι ΑΦΜ."""
    a = str(afm or "")
    if len(a) != AFM_LENGTH or not a.isdigit() or a == "0" * AFM_LENGTH:
        return False
    total = sum(int(d) << (8 - i) for i, d in enumerate(a[:8]))
    return total % 11 % 10 == int(a[8])


def amka_checksum_ok(amka: str) -> bool:
    """Αλγοριθμικός έλεγχος ΑΜΚΑ: ψηφίο ελέγχου κατά **Luhn** πάνω στα 11
    ψηφία. (Τα 6 πρώτα ψηφία, ΗΗΜΜΕΕ γέννησης, ελέγχονται χωριστά από την
    `amka_birth_ok` — μαζί συνθέτουν τον πλήρη έλεγχο.)"""
    a = str(amka or "")
    if len(a) != AMKA_LENGTH or not a.isdigit() or a == "0" * AMKA_LENGTH:
        return False
    # Luhn: το ψηφίο ελέγχου (θέση 10) μπαίνει ΑΥΤΟΥΣΙΟ στο άθροισμα και ΔΕΝ
    # διπλασιάζεται ΠΟΤΕ. Από τα 10 πρώτα διπλασιάζονται όσα βρίσκονται σε
    # ΠΕΡΙΤΤΗ θέση από αριστερά (1,3,5,7,9) — δηλαδή κάθε δεύτερο ψηφίο
    # ξεκινώντας δίπλα από το ψηφίο ελέγχου, όπως ορίζει το Luhn.
    total = int(a[10])
    for i, d in enumerate(a[:10]):
        n = int(d)
        if i % 2 != 0:
            n *= 2
            if n > 9:
                n -= 9  # = άθροισμα των ψηφίων του (π.χ. 14 -> 1+4 = 5)
        total += n
    return total % 10 == 0


def amka_birth_ok(amka: str) -> bool:
    """Τα 6 πρώτα ψηφία του ΑΜΚΑ είναι η ημ. γέννησης σε μορφή ΗΗΜΜΕΕ.
    Ο αιώνας δεν προκύπτει από το ΑΜΚΑ, οπότε αρκεί να είναι ΥΠΑΡΚΤΗ
    ημερομηνία σε έναν από τους δύο πιθανούς αιώνες (19xx/20xx)."""
    a = str(amka or "")
    if len(a) != AMKA_LENGTH or not a.isdigit():
        return False
    day, month, yy = int(a[0:2]), int(a[2:4]), int(a[4:6])
    for century in (1900, 2000):
        try:
            _date(century + yy, month, day)
            return True
        except ValueError:
            continue
    return False


def check_afm(afm, label="ΑΦΜ"):
    """Μήκος + ψηφίο ελέγχου ΑΦΜ. Πετά ValueError στα ελληνικά."""
    a = str(afm or "")
    if len(a) != AFM_LENGTH or not a.isdigit():
        raise ValueError(f"Το {label} πρέπει να έχει {AFM_LENGTH} ψηφία (δόθηκε «{a}»)")
    if not afm_checksum_ok(a):
        raise ValueError(f"Μη έγκυρο {label} «{a}» — αποτυγχάνει ο έλεγχος ψηφίου")
    return a


def check_amka(amka, label="ΑΜΚΑ"):
    """Μήκος + ημ. γέννησης (ΗΗΜΜΕΕ) + ψηφίο ελέγχου (Luhn) ΑΜΚΑ."""
    a = str(amka or "")
    if len(a) != AMKA_LENGTH or not a.isdigit():
        raise ValueError(
            f"Το {label} πρέπει να έχει {AMKA_LENGTH} ψηφία (δόθηκε «{a}»)"
        )
    if not amka_birth_ok(a):
        raise ValueError(
            f"Μη έγκυρο {label} «{a}» — τα 6 πρώτα ψηφία δεν είναι "
            "έγκυρη ημερομηνία γέννησης (ΗΗΜΜΕΕ)"
        )
    if not amka_checksum_ok(a):
        raise ValueError(f"Μη έγκυρο {label} «{a}» — αποτυγχάνει ο έλεγχος ψηφίου")
    return a


def check_person_identity(
    last_name, first_name, father_name, mother_name, afm, amka, ama=""
):
    """Υποχρεωτικά πεδία + μορφή ΚΑΙ ΑΛΓΟΡΙΘΜΙΚΟΣ έλεγχος των αριθμητικών
    μητρώων (ΑΦΜ mod 11, ΑΜΚΑ Luhn + ΗΗΜΜΕΕ). Δέχεται ήδη κανονικοποιημένες
    τιμές (βλ. `norm_name`/`norm_digits`)."""
    if not str(last_name).strip():
        raise ValueError("Απαιτείται Επώνυμο")
    if not str(first_name).strip():
        raise ValueError("Απαιτείται Όνομα")
    if not str(father_name).strip():
        raise ValueError("Απαιτείται Πατρώνυμο")
    if not str(mother_name).strip():
        raise ValueError("Απαιτείται Μητρώνυμο")
    check_afm(afm)
    check_amka(amka)
    if ama and len(ama) != AMA_LENGTH:
        raise ValueError(f"Το ΑΜΑ πρέπει να έχει {AMA_LENGTH} ψηφία (δόθηκε «{ama}»)")


def check_web_e3n_identity(
    sex="",
    nationality_code="",
    identity_type="",
    identity_number="",
    identity_issuer="",
    identity_issue_date=None,
    identity_expiry_date=None,
):
    """Έλεγχοι τύπων των πεδίων ταυτότητας κατά ``WebE3N.md``.

    Αν δεν έχει ξεκινήσει ακόμη συμπλήρωση των στοιχείων, επιτρέπονται όλα
    κενά. Μόλις δοθεί ένα πεδίο, η ομάδα ελέγχεται ως πλήρες σύνολο.
    """
    values = (
        sex,
        nationality_code,
        identity_type,
        identity_number,
        identity_issuer,
        identity_issue_date,
        identity_expiry_date,
    )
    if not any(v not in (None, "") for v in values):
        return
    if str(sex) not in ("0", "1"):
        raise ValueError("Το Φύλο πρέπει να είναι 0 (Άνδρας) ή 1 (Γυναίκα)")
    nationality = str(nationality_code or "")
    if not re.fullmatch(r"[0-9]{3}", nationality):
        raise ValueError("Η Υπηκοότητα πρέπει να είναι τριψήφιος κωδικός")
    if not ergani_parameters.contains("nationalities", nationality):
        raise ValueError(
            f"Ο κωδικός Υπηκοότητας «{nationality}» δεν υπάρχει "
            "στο μητρώο ΕΡΓΑΝΗ"
        )
    id_type = str(identity_type or "").upper()
    if not re.fullmatch(r"[A-ZΑ-Ω]{1,10}", id_type):
        raise ValueError(
            "Ο Τύπος Ταυτότητας πρέπει να έχει 1–10 κεφαλαίους χαρακτήρες"
        )
    if not ergani_parameters.contains("identity_types", id_type):
        raise ValueError(
            f"Ο Τύπος Ταυτότητας «{id_type}» δεν υπάρχει στο μητρώο ΕΡΓΑΝΗ"
        )
    number = str(identity_number or "").strip()
    if not 1 <= len(number) <= 20:
        raise ValueError("Ο Αριθμός Ταυτότητας πρέπει να έχει 1–20 χαρακτήρες")
    if len(str(identity_issuer or "")) > 50:
        raise ValueError("Η Εκδούσα Αρχή μπορεί να έχει έως 50 χαρακτήρες")
    if (
        identity_issue_date
        and identity_expiry_date
        and identity_expiry_date < identity_issue_date
    ):
        raise ValueError(
            "Η ημερομηνία λήξης ταυτότητας δεν μπορεί να προηγείται της έκδοσης"
        )


def check_web_e3n_person_details(
    marital_status="0", nationality_code="",
    res_permit_inst="", res_permit_inst_type="", res_permit_inst_number="",
    res_permit_inst_expiry_date=None,
    res_permit_ap="", res_permit_ap_type="", res_permit_ap_number="",
    res_permit_ap_expiry_date=None,
    res_permit_visa="", res_permit_visa_number="",
    res_permit_visa_from=None, res_permit_visa_to=None,
    education_level="",
):
    """Περιορισμοί WebE3N για οικογενειακή κατάσταση και άδειες τρίτων χωρών."""
    if str(marital_status) not in ("0", "1", "2", "3"):
        raise ValueError("Μη έγκυρη Οικογενειακή Κατάσταση")
    education = str(education_level or "").strip()
    if education:
        if not re.fullmatch(r"[0-9]{1,10}", education):
            raise ValueError("Το Επίπεδο Μόρφωσης πρέπει να είναι αριθμητικός κωδικός")
        if not ergani_parameters.contains("education_levels", education):
            raise ValueError(
                f"Ο κωδικός Επιπέδου Μόρφωσης «{education}» δεν υπάρχει "
                "στο μητρώο ΕΡΓΑΝΗ"
            )
    permit_values = (
        res_permit_inst, res_permit_inst_type, res_permit_inst_number,
        res_permit_inst_expiry_date, res_permit_ap, res_permit_ap_type,
        res_permit_ap_number, res_permit_ap_expiry_date, res_permit_visa,
        res_permit_visa_number, res_permit_visa_from, res_permit_visa_to,
    )
    has_permit_details = any(value not in (None, "", "0") for value in permit_values)
    if ergani_parameters.is_eu_nationality(nationality_code) and has_permit_details:
        raise ValueError(
            "Τα στοιχεία πολιτών τρίτων χωρών δεν επιτρέπονται για υπηκοότητα ΕΕ"
        )
    for label, flag in (
        ("άδεια άμεσης πρόσβασης", res_permit_inst),
        ("άδεια με πρόσθετη έγκριση", res_permit_ap),
        ("θεώρηση εισόδου", res_permit_visa),
    ):
        if flag not in (None, "", "0", "1"):
            raise ValueError(f"Το πεδίο «{label}» δέχεται μόνο 0 ή 1")
    for label, value in (
        ("Είδος άδειας άμεσης πρόσβασης", res_permit_inst_type),
        ("Είδος άδειας με πρόσθετη έγκριση", res_permit_ap_type),
    ):
        value = str(value or "")
        if value and (not value.isdigit() or len(value) > 5):
            raise ValueError(f"{label}: έως 5 ψηφία")
    for label, value in (
        ("Αριθμός άδειας άμεσης πρόσβασης", res_permit_inst_number),
        ("Αριθμός άδειας με πρόσθετη έγκριση", res_permit_ap_number),
        ("Αριθμός θεώρησης εισόδου", res_permit_visa_number),
    ):
        if len(str(value or "")) > 20:
            raise ValueError(f"{label}: έως 20 χαρακτήρες")
    if res_permit_visa_from and res_permit_visa_to and res_permit_visa_to < res_permit_visa_from:
        raise ValueError("Η λήξη θεώρησης εισόδου προηγείται της έναρξης")


def check_weekly_system(value, required=False):
    """ΣΥΣΤΗΜΑ ΕΒΔΟΜΑΔΙΑΙΑΣ ΕΡΓΑΣΙΑΣ — όρος της σύμβασης: 5 (πενθήμερο) ή
    6 (εξαήμερο). Επιστρέφει int ή None.

    Γράφεται ΩΣ ΕΝΑ ΨΗΦΙΟ στην ΑΠΔ (No 55) και καθορίζει το ημερήσιο νόμιμο
    όριο στην κατάταξη ωρών (9ω πενθήμερο / 8ω εξαήμερο) — γι' αυτό
    ελέγχεται στην πηγή. Κενό/None = «δεν δηλώθηκε» (None), εκτός αν
    `required`."""
    if value in (None, ""):
        if required:
            raise ValueError("Δηλώστε σύστημα εργασίας (5 = πενθήμερο, 6 = εξαήμερο)")
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = None
    if v not in (5, 6):
        raise ValueError(
            "Το σύστημα εργασίας είναι 5 (πενθήμερο) ή 6 (εξαήμερο) "
            f"— δόθηκε «{value}»"
        )
    return v


def check_employment_relationship(value="0", fixed_term_from=None,
                                  fixed_term_to=None, hire_date=None,
                                  payroll_from=None):
    """Σχέση απασχόλησης WebE3N και υποχρεωτικό διάστημα ορισμένου χρόνου.

    Σε **παλαιά πρόσληψη** (ανάληψη από άλλο πρόγραμμα, `payroll_from`) η λήξη
    της σύμβασης πρέπει να είναι ΜΕΤΑΓΕΝΕΣΤΕΡΗ της ανάληψης — αλλιώς η σύμβαση
    έχει ήδη λήξει πριν καν αρχίσει να υπολογίζει αυτό το πρόγραμμα."""
    relationship = str(value or "0").strip()
    if relationship not in {"0", "1"}:
        raise ValueError(
            "Η σχέση απασχόλησης είναι 0 (αορίστου) ή 1 (ορισμένου χρόνου)"
        )
    if relationship == "0":
        return relationship, None, None
    # Στη WebE3N η έναρξη της ορισμένου χρόνου σύμβασης είναι η ίδια
    # ημερομηνία με την πρόσληψη. Η ημερομηνία πρόσληψης είναι η μοναδική
    # πηγή αλήθειας, ανεξάρτητα από τυχόν τιμή που έστειλε ο browser.
    if hire_date is not None:
        fixed_term_from = hire_date
    if fixed_term_from is None or fixed_term_to is None:
        raise ValueError(
            "Για σύμβαση ορισμένου χρόνου απαιτούνται οι ημερομηνίες από και έως"
        )
    if fixed_term_to < fixed_term_from:
        raise ValueError(
            "Η λήξη της σύμβασης ορισμένου χρόνου προηγείται της έναρξης"
        )
    if payroll_from is not None and fixed_term_to <= payroll_from:
        raise ValueError(
            "Η λήξη της σύμβασης ορισμένου χρόνου "
            f"({fixed_term_to.strftime('%d/%m/%Y')}) πρέπει να είναι "
            "μεταγενέστερη της έναρξης μισθοδοσίας στο σύστημα "
            f"({payroll_from.strftime('%d/%m/%Y')})"
        )
    return relationship, fixed_term_from, fixed_term_to


def check_oaed_hire_fields(
    oaed_placement="0", oaed_program="", oaed_replacement="0",
    oaed_replaced_afm="", oaed_replaced_amka="", trial_period="0",
    trial_date_to=None, hire_date=None,
):
    """Πεδία ΟΑΕΔ/ΔΥΠΑ και δοκιμαστικής περιόδου του WebE3N."""
    placement = str(oaed_placement or "0").strip()
    replacement = str(oaed_replacement or "0").strip()
    trial = str(trial_period or "0").strip()
    if placement not in {"0", "1"}:
        raise ValueError("Η τοποθέτηση με πρόγραμμα ΔΥΠΑ είναι 0 ή 1")
    if trial not in {"0", "1"}:
        raise ValueError("Η δοκιμαστική περίοδος είναι 0 ή 1")

    program = str(oaed_program or "").strip()
    replaced_afm = norm_digits(oaed_replaced_afm)
    replaced_amka = norm_digits(oaed_replaced_amka)
    if placement == "0":
        program, replacement, replaced_afm, replaced_amka = "", "", "", ""
    else:
        if not re.fullmatch(r"[0-9-]{1,10}", program):
            raise ValueError(
                "Για τοποθέτηση μέσω ΔΥΠΑ απαιτείται κωδικός προγράμματος "
                "έως 10 χαρακτήρες (ψηφία ή παύλα)"
            )
        if replacement not in {"0", "1"}:
            raise ValueError("Η αντικατάσταση ωφελούμενου είναι 0 ή 1")
        if replacement == "0":
            replaced_afm, replaced_amka = "", ""
        else:
            if replaced_afm and not re.fullmatch(r"[0-9]{9}", replaced_afm):
                raise ValueError("Το ΑΦΜ του αντικατασταθέντος πρέπει να έχει 9 ψηφία")
            if replaced_amka and not re.fullmatch(r"[0-9]{1,20}", replaced_amka):
                raise ValueError("Το ΑΜΚΑ του αντικατασταθέντος μπορεί να έχει έως 20 ψηφία")

    if trial == "0":
        trial_date_to = None
    else:
        if trial_date_to is None:
            raise ValueError("Για δοκιμαστική περίοδο απαιτείται ημερομηνία λήξης")
        if hire_date is not None and trial_date_to < hire_date:
            raise ValueError("Η λήξη της δοκιμαστικής περιόδου προηγείται της πρόσληψης")
    return (
        placement, program, replacement, replaced_afm, replaced_amka,
        trial, trial_date_to,
    )


def check_digital_card_fields(
    working_card="0", flexible_arrival_minutes="0",
    break_within_schedule="0", break_minutes="0",
):
    """Ψηφιακή κάρτα και διάλειμμα του WebE3N."""
    card = str(working_card or "0").strip()
    within = str(break_within_schedule or "0").strip()
    if card not in {"0", "1"}:
        raise ValueError("Η κάρτα εργασίας είναι 0 ή 1")
    if within not in {"0", "1"}:
        raise ValueError("Το διάλειμμα εντός ωραρίου είναι 0 ή 1")

    def minutes(value, label):
        raw = str(value if value not in (None, "") else "0").strip()
        if not re.fullmatch(r"[0-9]{1,3}", raw):
            raise ValueError(f"{label}: απαιτείται αριθμός από 0 έως 999 λεπτά")
        return str(int(raw))

    return (
        card,
        minutes(flexible_arrival_minutes, "Ευέλικτη προσέλευση"),
        within,
        minutes(break_minutes, "Διάλειμμα"),
    )


def check_employer_name(legal_name, first_name="", father_name=""):
    """Επωνυμία/Ονοματεπώνυμο εργοδότη (ΑΠΔ No 5/6/7, JL10 2.3α/β/γ).

    ΕΤΑΙΡΙΑ: μόνο επωνυμία. ΑΤΟΜΙΚΗ: επώνυμο + όνομα + πατρώνυμο του
    φυσικού προσώπου. Η ΠΑΡΟΥΣΙΑ ονόματος είναι που κάνει τον δηλούντα
    φυσικό πρόσωπο (JL10 πεδίο 2.4 = 1), οπότε πατρώνυμο χωρίς όνομα θα
    έγραφε πατρώνυμο σε δήλωση εταιρίας — ασυνεπές αρχείο."""
    if not str(legal_name).strip():
        raise ValueError("Απαιτείται Επωνυμία (ή Επώνυμο για ατομική επιχείρηση)")
    if str(father_name).strip() and not str(first_name).strip():
        raise ValueError(
            "Το Πατρώνυμο συμπληρώνεται μόνο σε ατομική επιχείρηση, "
            "μαζί με το Όνομα του φυσικού προσώπου"
        )


def check_numeric_code(value, label, length, required=True, exact=False):
    """Αριθμητικός κωδικός αρχείου ΑΠΔ (ΑΜΕ, ΤΚ, ΚΑΔ, ΚΠΚ, …).

    Τα πεδία αυτά γράφονται ΩΣ ΑΡΙΘΜΟΙ στην ΑΠΔ (`apd_files.num`), οπότε
    κείμενο μέσα τους σκάει τη ΓΕΝΝΗΣΗ ΤΟΥ ΑΡΧΕΙΟΥ — δηλαδή μήνες αργότερα,
    μακριά από την καταχώρηση. Ελέγχονται εδώ, στην πηγή."""
    v = str(value or "").strip()
    if not v:
        if required:
            raise ValueError(f"Απαιτείται {label}")
        return v
    if not v.isdigit():
        raise ValueError(f"{label}: μόνο ψηφία (δόθηκε «{v}»)")
    if exact and len(v) != length:
        raise ValueError(f"{label}: απαιτούνται {length} ψηφία (δόθηκε «{v}»)")
    if len(v) > length:
        raise ValueError(f"{label}: το πολύ {length} ψηφία (δόθηκε «{v}»)")
    return v


def check_employer_codes(ame, afm, postal_code="", local_office_code=""):
    """Οι ΑΡΙΘΜΗΤΙΚΟΙ κωδικοί του εργοδότη (ΑΠΔ No 3/8/9/12)."""
    check_numeric_code(ame, "ΑΜΕ εργοδότη (ΑΠΔ No 8)", 10)
    check_numeric_code(afm, "ΑΦΜ εργοδότη (ΑΠΔ No 9)", AFM_LENGTH, exact=True)
    check_afm(afm, "ΑΦΜ εργοδότη (ΑΠΔ No 9)")
    check_numeric_code(postal_code, "ΤΚ εργοδότη (ΑΠΔ No 12)", 5, required=False)
    check_numeric_code(
        local_office_code,
        "Κωδ. Τοπικής Δ/νσης e-ΕΦΚΑ (ΑΠΔ No 3)",
        10,
        required=False,
    )


def check_coverage_codes(kad, specialty_code, kpk, label=""):
    """Η τριάδα κάθε κάλυψης ειδικότητας (ΑΠΔ No 31/35/37)."""
    where = f"Κάλυψη «{label}» — " if label else ""
    check_numeric_code(kad, f"{where}ΚΑΔ (ΑΠΔ No 31)", 4)
    check_numeric_code(specialty_code, f"{where}Κωδ. ειδικότητας (ΑΠΔ No 35)", 6)
    check_numeric_code(kpk, f"{where}ΚΠΚ (ΑΠΔ No 37)", 4)


def check_person_unique(persons, afm, amka, key, exclude_person_id=None):
    """Ελέγχει μοναδικότητα ΑΦΜ, ΑΜΚΑ και του κλειδιού ταυτότητας
    (Επώνυμο/Όνομα/Πατρώνυμο/ημ. γέννησης) έναντι των ήδη καταχωρημένων
    προσώπων. ΚΑΝΕΝΑ από τα τρία δεν επιτρέπεται να επαναληφθεί.

    `persons`: iterable αντικειμένων με `.person_id`, `.afm`, `.amka` και
    μέθοδο `.identity_key()`. `exclude_person_id`: αγνοείται (για διορθώσεις
    του ίδιου προσώπου)."""
    for p in persons:
        if exclude_person_id is not None and p.person_id == exclude_person_id:
            continue
        if afm and p.afm == afm:
            raise ValueError(
                f"Υπάρχει ήδη πρόσωπο με ΑΦΜ {afm} ({p.full_name})"
            )
        if amka and p.amka == amka:
            raise ValueError(
                f"Υπάρχει ήδη πρόσωπο με ΑΜΚΑ {amka} ({p.full_name})"
            )
        if p.identity_key() == key:
            raise ValueError(
                "Υπάρχει ήδη πρόσωπο με ίδιο Επώνυμο/Όνομα/Πατρώνυμο και "
                f"ημερομηνία γέννησης ({p.full_name}, πατρ. {key[2]}, "
                f"γενν. {key[3].strftime('%d/%m/%Y')})"
            )


def check_rehire(person, employments, hire_date):
    """Κανόνες νέας περιόδου απασχόλησης για υπάρχον πρόσωπο.

    `employments`: οι υπάρχουσες περίοδοι του προσώπου (EmploymentState).

    1. ΔΕΝ επιτρέπεται νέα πρόσληψη όσο υπάρχει ΕΝΕΡΓΗ απασχόληση — πρέπει
       πρώτα να καταχωρηθεί η αποχώρηση (Λήξη στο ΕΡΓΑΝΗ).
    2. Η επαναπρόσληψη πρέπει να είναι ΜΕΤΑ τη λήξη της τελευταίας
       περιόδου — δεν επικαλύπτονται περίοδοι."""
    for e in employments:
        if e.active:
            raise ValueError(
                f"Ο/Η {person.full_name} έχει ήδη ΕΝΕΡΓΗ απασχόληση "
                f"(από {e.hire_date.strftime('%d/%m/%Y')}). Για νέα περίοδο "
                "πρέπει πρώτα να καταχωρηθεί η αποχώρηση."
            )
    for e in employments:
        if e.termination_date and hire_date <= e.termination_date:
            raise ValueError(
                "Η ημερομηνία πρόσληψης πρέπει να είναι μετά τη λήξη της "
                f"προηγούμενης περιόδου ({e.termination_date.strftime('%d/%m/%Y')})"
            )


def check_payroll_from(payroll_from, hire_date):
    """Έναρξη μισθοδοσίας ΣΤΟ ΣΥΣΤΗΜΑ (μεταφορά από άλλο πρόγραμμα).

    Επιτρέπεται ΜΟΝΟ 1η του μήνα — η μισθοδοσία είναι μηνιαία, οπότε μια
    ενδιάμεση ημερομηνία θα σήμαινε μισό μήνα ευθύνης χωρίς κανόνα επιμερισμού
    (η αναλογία της πρόσληψης δεν ισχύει: ο εργαζόμενος δούλευε ΟΛΟΚΛΗΡΟ τον
    μήνα, απλώς τον πλήρωνε άλλο πρόγραμμα).

    Επιστρέφει None όταν ταυτίζεται με την πρόσληψη (κανονική περίπτωση —
    τίποτα να θυμόμαστε).

    ⚠️ Ανάληψη ΠΡΙΝ την πρόσληψη ΑΠΟΡΡΙΠΤΕΤΑΙ — δεν αγνοείται σιωπηλά. Η
    σιωπηλή αγνόηση έκανε μια ανάποδη καταχώρηση (π.χ. πρόσληψη 2028, ανάληψη
    2026) να περνά ως ΚΑΝΟΝΙΚΗ πρόσληψη του 2028: ο εργαζόμενος φαινόταν
    νεοπροσληφθείς και έχανε αποζημίωση απόλυσης, Δώρα και τριετίες."""
    if payroll_from is None:
        return None
    if hire_date is not None and payroll_from == hire_date:
        return None  # δεν προσθέτει τίποτα — κανονική πρόσληψη
    if hire_date is not None and payroll_from < hire_date:
        raise ValueError(
            "Η έναρξη μισθοδοσίας στο σύστημα "
            f"({payroll_from.strftime('%d/%m/%Y')}) δεν μπορεί να είναι πριν "
            f"την ημερομηνία πρόσληψης ({hire_date.strftime('%d/%m/%Y')}) — "
            "ελέγξτε τις δύο ημερομηνίες"
        )
    if payroll_from.day != 1:
        raise ValueError(
            "Η έναρξη μισθοδοσίας στο σύστημα πρέπει να είναι η 1η του μήνα "
            f"(δόθηκε {payroll_from.strftime('%d/%m/%Y')})"
        )
    return payroll_from


DAY_CODES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")

# Σπαστό ωράριο: ελάχιστη απόσταση αποχώρησης 1ου τμήματος από την
# προσέλευση του 2ου (ώρες)
MIN_SPLIT_GAP_HOURS = 3

# Ελάχιστη συνεχόμενη ανάπαυση ανάμεσα σε δύο διαδοχικές ημερολογιακές ημέρες.
MIN_DAILY_REST_HOURS = 11

# Ανοχή (ώρες) στη σύγκριση προγραμματισμένων ωρών με τη σύμβαση — οι ώρες
# ΔΕΝ επιτρέπεται να ΥΠΟΛΕΙΠΟΝΤΑΙ της σύμβασης· περισσότερες επιτρέπονται
# (πρόσθετη εργασία / υπερεργασία / υπερωρία, βλ. work_hours.py).
# Μόνο αριθμητική ανοχή στρογγυλοποίησης, όχι λειτουργική απόκλιση.
HOURS_TOLERANCE = 0.01

# Πρόθεμα τμήματος ΤΗΛΕΕΡΓΑΣΙΑΣ (δεκτό ελληνικό «Τ» ή λατινικό «T»)
_TELE_PREFIXES = ("Τ", "T", "τ", "t")


def parse_day_schedule(s) -> list:
    """Αναλύει το ωράριο ΜΙΑΣ ημέρας σε τμήματα.

    Μορφή: ένα ή δύο τμήματα «ΩΩ:ΛΛ-ΩΩ:ΛΛ» χωρισμένα με «+» (ΣΠΑΣΤΟ
    ωράριο)· τμήμα ΤΗΛΕΕΡΓΑΣΙΑΣ με πρόθεμα «Τ», π.χ.:
        09:00-17:00                 απλό
        09:00-13:00+16:00-20:00     σπαστό (κενό >= 3 ώρες)
        Τ09:00-17:00                τηλεεργασία
        09:00-13:00+Τ16:00-20:00    μικτό
    Επιστρέφει λίστα dicts {"start","end" (λεπτά), "telework", "text"},
    ταξινομημένη κατά έναρξη. Κενό/None -> []. ValueError σε λάθος μορφή.

    Η λήξη μπορεί να είναι έως τις 09:00 της επόμενης ημέρας· τότε τα λεπτά
    λήξης επιστρέφονται με +24ω (π.χ. 22:00-06:00 -> 1320, 1800).

    Κανόνες: έως 2 τμήματα· στο σπαστό, η προσέλευση του δεύτερου πρέπει να
    απέχει τουλάχιστον MIN_SPLIT_GAP_HOURS ώρες από την αποχώρηση του πρώτου."""
    parts = [p.strip() for p in (s or "").replace(",", "+").split("+")
             if p.strip()]
    if not parts:
        return []
    if len(parts) > 2:
        raise ValueError(
            f"Έως 2 τμήματα ωραρίου ανά ημέρα (σπαστό) — δόθηκαν {len(parts)}"
        )
    segs = []
    for p in parts:
        tele = p[:1] in _TELE_PREFIXES
        core = p[1:].strip() if tele else p
        m = _TIME_RANGE.fullmatch(core)
        if not m:
            raise ValueError(
                f"Μη έγκυρο τμήμα ωραρίου «{p}» — μορφή ΩΩ:ΛΛ-ΩΩ:ΛΛ, σπαστό "
                "με «+», πρόθεμα Τ = τηλεεργασία (π.χ. 09:00-13:00+Τ16:00-20:00)"
            )
        a_h, a_m, b_h, b_m = (int(x) for x in m.groups())
        start, end = a_h * 60 + a_m, b_h * 60 + b_m
        if end == start:
            raise ValueError(f"Η ώρα λήξης είναι ίδια με την έναρξη ({p})")
        if end < start:
            if end > 9 * 60:
                raise ValueError(
                    f"Η ώρα λήξης της επόμενης ημέρας πρέπει να είναι έως 09:00 ({p})"
                )
            end += 24 * 60
        segs.append({"start": start, "end": end, "telework": tele,
                     "text": core})
    segs.sort(key=lambda x: x["start"])
    if len(segs) == 2:
        if any(seg["end"] > 24 * 60 for seg in segs):
            raise ValueError(
                "Σπαστό ωράριο που περνά τα μεσάνυχτα δεν υποστηρίζεται"
            )
        gap = segs[1]["start"] - segs[0]["end"]
        if gap < MIN_SPLIT_GAP_HOURS * 60:
            raise ValueError(
                "Σπαστό ωράριο: η προσέλευση του δεύτερου τμήματος πρέπει να "
                f"απέχει τουλάχιστον {MIN_SPLIT_GAP_HOURS} ώρες από την "
                "αποχώρηση του πρώτου"
            )
    total_minutes = sum(seg["end"] - seg["start"] for seg in segs)
    if total_minutes > 13 * 60:
        raise ValueError(
            "Η ημερήσια εργασία δεν μπορεί να υπερβαίνει τις 13 ώρες"
        )
    return segs


def day_schedule_minutes(s, telework=None) -> int:
    """Συνολικά λεπτά του ωραρίου μιας ημέρας. `telework`: None = όλα,
    True = μόνο τηλεεργασία, False = μόνο εργασία με φυσική παρουσία.
    Μη έγκυρο ωράριο εγείρει ``ValueError``."""
    segs = parse_day_schedule(s)
    return sum(
        seg["end"] - seg["start"] for seg in segs
        if telework is None or seg["telework"] == telework
    )


def schedule_hours(weekly_pattern) -> float:
    """Σύνολο ωρών/εβδομάδα που προκύπτει από το πρόγραμμα (εργασία +
    τηλεεργασία — και οι δύο είναι χρόνος απασχόλησης της σύμβασης)."""
    total = sum(day_schedule_minutes(h) for h in weekly_pattern.values())
    return round(total / 60, 2)


def check_daily_rest(previous, following, previous_label="προηγούμενη ημέρα",
                     following_label="επόμενη ημέρα"):
    """Ελέγχει την ελάχιστη 11ωρη ανάπαυση μεταξύ διαδοχικών ημερών.

    Το `previous` και το `following` είναι ωράρια ημέρας. Κενό ωράριο σημαίνει
    ρεπό και συνεπώς δεν χρειάζεται έλεγχος. Βάρδια της προηγούμενης ημέρας που
    περνά τα μεσάνυχτα έχει `end > 1440` από το `parse_day_schedule`."""
    previous_segments = parse_day_schedule(previous)
    following_segments = parse_day_schedule(following)
    if not previous_segments or not following_segments:
        return
    previous_end = max(segment["end"] for segment in previous_segments)
    following_start = min(segment["start"] for segment in following_segments)
    rest_minutes = 24 * 60 + following_start - previous_end
    minimum = MIN_DAILY_REST_HOURS * 60
    if rest_minutes < minimum:
        found = max(0, rest_minutes)
        hours, minutes = divmod(found, 60)
        raise ValueError(
            f"Απαιτούνται τουλάχιστον {MIN_DAILY_REST_HOURS} ώρες ανάπαυσης "
            f"μεταξύ {previous_label} και {following_label} "
            f"(βρέθηκαν {hours}:{minutes:02d})"
        )


def check_schedule(weekly_pattern, weekly_hours=None, childcare_hours=0):
    """Το ΠΡΟΓΡΑΜΜΑ ΕΡΓΑΣΙΑΣ είναι ΥΠΟΧΡΕΩΤΙΚΟ στην πρόσληψη — όπως η σύμβαση.

    Χωρίς αυτό δεν μπορούν να τεκμαρθούν παρουσίες/απουσίες (συγκεντρωτικό
    πριν τη μισθοδοσία) ούτε να υποβληθεί η ΟΧΕ στο ΕΡΓΑΝΗ.
    Μορφή: {"MON": "09:00-17:00", ...} — τουλάχιστον μία ημέρα. Κάθε ημέρα
    επιτρέπει σπαστό ωράριο (2 τμήματα με «+», κενό >= 3 ώρες) και τμήματα
    τηλεεργασίας (πρόθεμα «Τ») — βλ. parse_day_schedule().

    Το σύνολο του template πρέπει να είναι ΙΣΟ με τις εβδομαδιαίες ώρες της
    σύμβασης. Πρόσθετη εργασία/υπερεργασία/υπερωρία προκύπτει από πραγματική
    απασχόληση ή μεταγενέστερη δήλωση και δεν ενσωματώνεται στο συμβατικό
    template της πρόσληψης.

    `childcare_hours`: μειωμένο ωράριο φροντίδας τέκνου — το σύνολο μπορεί να
    είναι έως τόσες ώρες ανά ημέρα εργασίας ΚΑΤΩ από τη σύμβαση, που μένει
    αμετάβλητη (η μείωση λογίζεται πλήρης χρόνος εργασίας)."""
    if not weekly_pattern:
        raise ValueError(
            "Απαιτείται πρόγραμμα εργασίας (τουλάχιστον μία ημέρα με ωράριο) — "
            "υποβάλλεται ως ΟΧΕ στο ΕΡΓΑΝΗ"
        )
    for code, hours in weekly_pattern.items():
        if code not in DAY_CODES:
            raise ValueError(f"Άγνωστη ημέρα «{code}» στο πρόγραμμα εργασίας")
        try:
            segs = parse_day_schedule(hours)
        except ValueError as exc:
            raise ValueError(f"{code}: {exc}")
        if not segs:
            raise ValueError(
                f"Μη έγκυρο ωράριο «{hours}» ({code}) — μορφή ΩΩ:ΛΛ-ΩΩ:ΛΛ, "
                "π.χ. 09:00-17:00"
            )

    # Το εβδομαδιαίο μοτίβο επαναλαμβάνεται, άρα ελέγχεται και η μετάβαση
    # Κυριακής -> Δευτέρας. Ημέρα που λείπει από το dict είναι ρεπό.
    for index, code in enumerate(DAY_CODES):
        next_code = DAY_CODES[(index + 1) % len(DAY_CODES)]
        check_daily_rest(
            weekly_pattern.get(code, ""), weekly_pattern.get(next_code, ""),
            code, next_code,
        )

    if weekly_hours is None:
        return
    total = schedule_hours(weekly_pattern)
    contract_hours = float(weekly_hours)
    work_days = sum(1 for h in weekly_pattern.values() if parse_day_schedule(h))
    low = contract_hours - childcare_hours * work_days
    if not low - HOURS_TOLERANCE <= total <= contract_hours + HOURS_TOLERANCE:
        if childcare_hours:
            raise ValueError(
                f"Το πρόγραμμα δίνει {total:g} ώρες/εβδομάδα. Με μειωμένο "
                f"ωράριο φροντίδας τέκνου ({childcare_hours:g} ώρ./ημέρα) "
                f"πρέπει να είναι από {low:g} έως {contract_hours:g}."
            )
        raise ValueError(
            f"Το πρόγραμμα δίνει {total:g} ώρες/εβδομάδα, αλλά η σύμβαση "
            f"ορίζει {contract_hours:g}. Το σύνολο των ωρών του προγράμματος "
            "πρέπει να είναι ίσο με τις ώρες ανά εβδομάδα."
        )


def check_week_hours_minimum(total_hours, weekly_hours, week_label,
                             employee_label=""):
    """Ημερήσιο πρόγραμμα πλήρους εβδομάδας: όχι κάτω από τη σύμβαση.

    Η υπέρβαση επιτρέπεται επειδή μπορεί να αποτελεί πρόσθετη εργασία,
    υπερεργασία ή υπερωρία.
    """
    if weekly_hours is None:
        return
    contract_hours = float(weekly_hours)
    if float(total_hours) + HOURS_TOLERANCE < contract_hours:
        who = f"{employee_label}: " if employee_label else ""
        raise ValueError(
            f"{who}η εβδομάδα {week_label} έχει {total_hours:g} ώρες, "
            f"λιγότερες από τις {contract_hours:g} ώρες/εβδομάδα της "
            "σύμβασης. Η ημερήσια δήλωση μπορεί να έχει πρόσθετες, αλλά "
            "όχι λιγότερες ώρες."
        )


def check_contract(pay_type, monthly_gross, daily_wage, weekly_hours):
    """Κανόνες σύμβασης απασχόλησης (κοινοί σε πρόσληψη & μεταβολή)."""
    if pay_type not in ("monthly", "daily"):
        raise ValueError("pay_type: 'monthly' ή 'daily'")
    if not (0 < weekly_hours <= 48):
        raise ValueError("Οι εβδομαδιαίες ώρες πρέπει να είναι 1-48")
    if pay_type == "daily" and daily_wage <= 0:
        raise ValueError("Για ημερομίσθιο απαιτείται daily_wage > 0")
    if pay_type == "monthly" and monthly_gross <= 0:
        raise ValueError("Για μισθωτό απαιτείται monthly_gross > 0")


def check_service_allowances(
    service_years, frozen_service_years=0.0, extra_allowances_pct=0.0
):
    """Προϋπηρεσία & προσαυξήσεις κατώτατου — ΜΙΑ πηγή αλήθειας.

    ΔΥΟ ΑΝΕΞΑΡΤΗΤΑ μεγέθη, ΟΧΙ μέρος-όλον:
      - `service_years`         = η προϋπηρεσία που ΜΕΤΡΑ ΣΤΙΣ ΤΡΙΕΤΙΕΣ,
      - `frozen_service_years`  = ο ΠΑΓΩΜΕΝΟΣ χρόνος 14/02/2012–31/12/2023,
        που δεν διαμορφώνει τριετίες αλλά μετρά στον συνολικό χρόνο εργασίας
        (ημέρες άδειας κ.λπ.).
    Σύνολο προϋπηρεσίας = το ΑΘΡΟΙΣΜΑ τους, άρα ο παγωμένος χρόνος ΔΕΝ
    περιορίζεται από τον χρόνο των τριετιών — μόνο από το ΜΗΚΟΣ του ίδιου
    του παγώματος."""
    if service_years < 0 or frozen_service_years < 0 or extra_allowances_pct < 0:
        raise ValueError("Αρνητική προϋπηρεσία ή προσαύξηση")
    span = config.freeze_span_years()
    if frozen_service_years > span + 1e-9:
        y, m = int(span), int((span - int(span)) * 12)  # προς τα κάτω
        raise ValueError(
            "Ο παγωμένος χρόνος δεν μπορεί να υπερβαίνει το διάστημα "
            f"14/02/2012–31/12/2023 ({y} έτη {m} μήνες)"
        )
