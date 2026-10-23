"""
Domain Events — τα «γεγονότα» του συστήματος.

Κάθε event είναι αμετάβλητο (frozen dataclass) και καταγράφει κάτι που
ΣΥΝΕΒΗ. Η κατάσταση του συστήματος προκύπτει αποκλειστικά από το replay
των events (event sourcing).
"""

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from enum import Enum

from misevent.rules import config


class LeaveType(str, Enum):
    ANNUAL = "kanoniki_adeia"  # κανονική άδεια
    SICK = "astheneia"  # ασθένεια
    UNPAID = "adeia_aneu_apodoxon"  # άνευ αποδοχών
    MATERNITY = "mitrotita"
    PARENTAL = "goniki"
    # Ισόχρονη άδεια φροντίδας τέκνου (μειωμένο ωράριο σε συνεχόμενες ημέρες):
    # πληρώνεται από τον εργοδότη, ΔΕΝ αφαιρείται από την κανονική άδεια.
    CHILDCARE = "frontida_teknou"


@dataclass(frozen=True)
class DomainEvent:
    """Βάση όλων των events."""

    def to_record(self, aggregate_id: str) -> dict:
        payload = asdict(self)
        return {
            "event_id": str(uuid.uuid4()),
            "aggregate_id": aggregate_id,
            "event_type": type(self).__name__,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": json.dumps(payload, default=str, ensure_ascii=False),
        }


# ------------------------------------------------------------ Πρόσωπο
@dataclass(frozen=True)
class PersonRegistered(DomainEvent):
    """Φυσικό πρόσωπο — σταθερή ταυτότητα, ανεξάρτητη από το αν
    εργάζεται τώρα. Κλειδώνει σε ΑΦΜ/ΑΜΚΑ/ΑΜΑ. Δημιουργείται μία φορά·
    η επαναπρόσληψη είναι νέο Employment στο ίδιο person_id."""

    person_id: str
    last_name: str  # Επώνυμο
    first_name: str  # Όνομα
    father_name: str  # Πατρώνυμο
    mother_name: str  # Μητρώνυμο
    birth_date: date
    afm: str  # ΑΦΜ (9ψήφιο)
    amka: str  # ΑΜΚΑ (11ψήφιο)
    ama: str = ""  # Αριθμός Μητρώου Ασφαλισμένου (9ψήφιο)
    children: int = 0  # τέκνα (για φορολογική κλίμακα/επιδόματα)
    email: str = ""  # email επικοινωνίας (προαιρετικό)
    # Προαιρετικά στοιχεία ταυτοποίησης WebE3N· απαιτούνται κατά την υποβολή.
    sex: str = ""  # 0=Άνδρας, 1=Γυναίκα
    nationality_code: str = ""  # τριψήφιος κωδικός
    identity_type: str = ""  # 1–10 κεφαλαίοι χαρακτήρες
    identity_number: str = ""  # έως 20 χαρακτήρες
    identity_issuer: str = ""  # έως 50 χαρακτήρες
    identity_issue_date: date | None = None
    identity_expiry_date: date | None = None
    marital_status: str = "0"  # 0 άγαμος, 1 έγγαμος, 2 διαζευγμένος, 3 χήρος
    # Άδειες/θεώρηση εισόδου πολιτών τρίτων χωρών (WebE3N f_res_permit_*).
    res_permit_inst: str = ""
    res_permit_inst_type: str = ""
    res_permit_inst_number: str = ""
    res_permit_inst_expiry_date: date | None = None
    res_permit_ap: str = ""
    res_permit_ap_type: str = ""
    res_permit_ap_number: str = ""
    res_permit_ap_expiry_date: date | None = None
    res_permit_visa: str = ""
    res_permit_visa_number: str = ""
    res_permit_visa_from: date | None = None
    res_permit_visa_to: date | None = None
    education_level: str = ""  # WebE3N f_epipedo_morfosis
    # Επικοινωνία & τόπος γέννησης (π.χ. από τη Βεβαίωση ΑΜΚΑ) — προαιρετικά
    address: str = ""  # Οδός / αριθμός
    postal_code: str = ""  # Τ.Κ.
    municipality: str = ""  # Δήμος διεύθυνσης
    prefecture: str = ""  # Νομός
    phone: str = ""
    birth_place: str = ""  # Δήμος, Νομός, Χώρα γέννησης


@dataclass(frozen=True)
class PersonDetailsCorrected(DomainEvent):
    """Διόρθωση/ενημέρωση στοιχείων ταυτότητας προσώπου (CRUD update).
    Επιτρεπόμενα πεδία: βλ. constraints.PERSON_FIELDS."""

    person_id: str
    fields: dict


@dataclass(frozen=True)
class PersonDetailsChanged(DomainEvent):
    """ΜΕΤΑΒΟΛΗ στοιχείων προσώπου που αλλάζουν στη ζωή του (τέκνα, οικογ.
    κατάσταση, ταυτότητα, μόρφωση…) ΑΠΟ μια ημερομηνία — σε αντίθεση με τη
    διόρθωση λάθους (`PersonDetailsCorrected`), που ισχύει «από πάντα».
    Επιτρεπόμενα πεδία: βλ. constraints.PERSON_CHANGEABLE_FIELDS."""

    person_id: str
    effective_from: date
    fields: dict


# ------------------------------------------------------ Εργασιακή σχέση
@dataclass(frozen=True)
class EmploymentStarted(DomainEvent):
    """Έναρξη μιας περιόδου απασχόλησης ενός Person (πρόσληψη ή
    επαναπρόσληψη). Το employment_id είναι το aggregate των employment-
    scoped events (σύμβαση, ασφάλιση, μισθοδοσία, άδειες)."""

    employment_id: str
    person_id: str
    hire_date: date
    # ΜΕΤΑΦΟΡΑ ΑΠΟ ΑΛΛΟ ΠΡΟΓΡΑΜΜΑ: από ΠΟΤΕ αναλαμβάνει ΑΥΤΟ το σύστημα τη
    # μισθοδοσία. Η `hire_date` παραμένει η ΠΡΑΓΜΑΤΙΚΗ πρόσληψη (τριετίες,
    # Δώρα, δικαίωμα αδείας, αποζημίωση απόλυσης μετρούν από εκεί)· απλώς οι
    # περίοδοι/υποχρεώσεις ξεκινούν από εδώ. None = από την πρόσληψη (κανονική
    # περίπτωση). Πάντα 1η του μήνα — η μισθοδοσία είναι μηνιαία.
    payroll_from: date | None = None
    pay_type: str = "monthly"  # "monthly" | "daily"
    monthly_gross: float = 0.0  # μικτός μηνιαίος μισθός (μισθωτοί)
    daily_wage: float = 0.0  # μικτό ημερομίσθιο (ημερομίσθιοι)
    weekly_hours: float = 40.0  # <40 = σύμβαση μερικής απασχόλησης
    # ΣΥΣΤΗΜΑ ΕΒΔΟΜΑΔΙΑΙΑΣ ΕΡΓΑΣΙΑΣ (5 = πενθήμερο, 6 = εξαήμερο).
    weekly_system: int = 5
    # Προσαυξήσεις κατώτατου ορίου — δηλώνονται στην πρόσληψη (config.py):
    service_years: float = 0.0  # ΠΡΟΫΠΗΡΕΣΙΑ που ΜΕΤΡΑ στις τριετίες
    # ΕΠΙΠΛΕΟΝ παγωμένη προϋπηρεσία 14/02/2012–31/12/2023: εκτός τριετιών,
    # μέσα στον συνολικό χρόνο εργασίας. Σύνολο = service + frozen.
    frozen_service_years: float = 0.0
    married: bool = False  # επίδομα γάμου 10% (προαιρετικό)
    extra_allowances_pct: float = 0.0  # λοιπές (πτυχίο κ.λπ.), % π.χ. 10 = 10%
    draft: bool = False  # UI draft μέχρι την υποβολή WebE3N
    # Σχέση απασχόλησης WebE3N: 0=αορίστου, 1=ορισμένου χρόνου.
    employment_relationship: str = "0"
    fixed_term_from: date | None = None
    fixed_term_to: date | None = None
    oaed_placement: str = "0"
    oaed_program: str = ""
    oaed_replacement: str = ""
    oaed_replaced_afm: str = ""
    oaed_replaced_amka: str = ""
    trial_period: str = "0"
    trial_date_to: date | None = None
    working_card: str = "1"
    flexible_arrival_minutes: str = "120"
    break_within_schedule: str = "0"
    break_minutes: str = "15"


@dataclass(frozen=True)
class EmploymentHireDetailsCorrected(DomainEvent):
    """Διόρθωση της αρχικής φόρμας πρόσληψης πριν από την υποβολή WebE3N."""

    employment_id: str
    hire_date: date
    pay_type: str
    monthly_gross: float = 0.0
    daily_wage: float = 0.0
    weekly_hours: float = 40.0
    weekly_system: int = 5
    service_years: float = 0.0
    frozen_service_years: float = 0.0
    married: bool = False
    extra_allowances_pct: float = 0.0
    weekly_pattern: dict = field(default_factory=dict)
    schedule_mode: str = "fixed"
    schedule_end_date: date | None = None
    daily_schedule: dict = field(default_factory=dict)
    employment_relationship: str = "0"
    fixed_term_from: date | None = None
    fixed_term_to: date | None = None
    oaed_placement: str = "0"
    oaed_program: str = ""
    oaed_replacement: str = ""
    oaed_replaced_afm: str = ""
    oaed_replaced_amka: str = ""
    trial_period: str = "0"
    trial_date_to: date | None = None
    working_card: str = "1"
    flexible_arrival_minutes: str = "120"
    break_within_schedule: str = "0"
    break_minutes: str = "15"


@dataclass(frozen=True)
class ContractChanged(DomainEvent):
    """Αλλαγή σύμβασης εργασίας: αποδοχές, τύπος αμοιβής, ωράριο
    (πλήρης <-> μερική απασχόληση)."""

    employee_id: str
    effective_from: date
    pay_type: str  # "monthly" | "daily"
    monthly_gross: float = 0.0
    daily_wage: float = 0.0
    weekly_hours: float = 40.0
    # Σύστημα εβδομαδιαίας εργασίας (5/6) — όρος σύμβασης, βλ. EmploymentStarted.
    # None = αμετάβλητο (η μεταβολή δεν το άγγιξε).
    weekly_system: int | None = None
    # ΑΙΤΙΑ της μεταβολής (κωδικός, βλ. config.CONTRACT_CHANGE_REASONS): κενό =
    # χειροκίνητη μεταβολή (η αιτιολογία περιγράφεται από τη διαφορά των όρων).
    reason: str = ""
    # Ημερομηνία που τεκμηριώνει την αιτία (π.χ. ισχύς της νέας ΓΣΣΕ)
    reason_date: date | None = None
    # Στην πράξη: εδώ θα γινόταν και η υποβολή Ε3 στο ΕΡΓΑΝΗ πριν την ανάληψη


@dataclass(frozen=True)
class EmploymentDeleted(DomainEvent):
    """ΑΝΤΙΣΤΑΘΜΙΣΤΙΚΟ event: η περίοδος απασχόλησης καταχωρήθηκε ΕΚ ΠΑΡΑΔΡΟΜΗΣ
    και διαγράφεται ολόκληρη.

    ΔΕΝ είναι αποχώρηση (`EmployeeTerminated`) — εκεί η σχέση υπήρξε και
    κρατάει δικαιώματα. Εδώ η σχέση δεν υπήρξε ποτέ: τα projections και το read
    model σβήνουν την απασχόληση, ενώ το event log μένει άθικτο (κανόνας #2).
    Επιτρέπεται ΜΟΝΟ όσο δεν έχει «πατήσει» πουθενά (βλ.
    `services.delete_employment`)."""

    employee_id: str
    reason: str = ""


@dataclass(frozen=True)
class EmployeeTerminated(DomainEvent):
    employee_id: str
    termination_date: date
    reason: str = ""  # απόλυση / οικειοθελής αποχώρηση (Ε5/Ε6/Ε7)


# ------------------------------------------------------ Πρόγραμμα / Παρουσίες
@dataclass(frozen=True)
class WorkScheduleAssigned(DomainEvent):
    """Εβδομαδιαίο ωράριο (αντιστοιχεί σε Ε4/Ψηφιακή Οργάνωση Χρόνου Εργασίας)."""

    employee_id: str
    effective_from: date
    # π.χ. {"MON": ["09:00-17:00"], ...}
    weekly_pattern: dict = field(default_factory=dict)
    # Ημερομηνία λήξης του σταθερού εβδομαδιαίου (κενή = δεν λήγει ποτέ).
    # Μετά τη λήξη δεν ισχύει πρόγραμμα, εκτός αν ακολουθεί νεότερο εβδομαδιαίο.
    end_date: date | None = None


@dataclass(frozen=True)
class DailyScheduleAssigned(DomainEvent):
    """ΗΜΕΡΗΣΙΟ πρόγραμμα — καθαρός ΣΧΕΔΙΑΣΜΟΣ στο grid, ΔΕΝ υποβάλλεται πλέον
    αυτόματα στο ΕΡΓΑΝΗ.

    `days`: {"2026-07-14": "09:00-17:00", ...}. ΥΠΕΡΙΣΧΥΕΙ του εβδομαδιαίου
    για τις ημέρες που ορίζει. Νέα υποβολή για την ίδια ημέρα αντικαθιστά την
    προηγούμενη (κενή τιμή "" = ρεπό/άρση της ημέρας). Η δήλωση των αλλαγών
    στο ΕΡΓΑΝΗ γίνεται ρητά, ανά εβδομάδα (`ScheduleDeclaredToErgani`)."""

    employee_id: str
    days: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EmploymentScheduleModeSet(DomainEvent):
    """Τύπος προγράμματος της απασχόλησης — δηλώνεται στην πρόσληψη.

    `mode`:
      - "fixed"    — ΣΤΑΘΕΡΟ εβδομαδιαίο: υποβάλλεται standing weekly (χωρίς
                     λήξη) στο ΕΡΓΑΝΗ και όλες οι ημέρες θεωρούνται δηλωμένες.
                     Μεταγενέστερες αποκλίσεις δηλώνονται ανά εβδομάδα.
      - "variable" — ΜΕΤΑΒΛΗΤΟ: το grid προσυμπληρώνεται από το template αλλά
                     όλες οι εβδομάδες είναι αδήλωτες· δηλώνονται ρητά ανά
                     εβδομάδα, χωρίς να μένουν κενά.
    """

    employee_id: str
    mode: str


@dataclass(frozen=True)
class ScheduleDeclaredToErgani(DomainEvent):
    """Δήλωση STANDING εβδομαδιαίου (χωρίς λήξη, τύπος fixed) στο ΕΡΓΑΝΗ.

    `days` = το εβδομαδιαίο μοτίβο ανά DAY_CODE, baseline των ημερών
    [effective_from, end_date] (None = χωρίς όριο· παλιά events).
    Κάθε υποβολή είναι Ή εβδομαδιαία (αυτό) Ή ημερήσια (ScheduleDaysSubmitted)·
    για κάθε ημέρα ισχύει η ΤΕΛΕΥΤΑΙΑ που την καλύπτει."""

    employee_id: str
    days: dict
    effective_from: date | None = None
    end_date: date | None = None


@dataclass(frozen=True)
class ScheduleDaysSubmitted(DomainEvent):
    """Υποβολή ΗΜΕΡΗΣΙΩΝ προγραμμάτων στο ΕΡΓΑΝΗ, ανά εργαζόμενο, μέσα σε ΕΝΑ
    αριθμημένο batch (`batch_no` — καθολική αύξουσα αρίθμηση εταιρείας).

    `days` = {ISO: ωράριο} — οι ημέρες που δηλώθηκαν σε ΑΥΤΟ το batch για αυτόν
    τον εργαζόμενο (κενό ωράριο = ρεπό). Κάθε ημέρα = ένα «ημερήσιο πρόγραμμα».
    Το batch μπορεί να καλύπτει πολλούς εργαζομένους (ένα event ανά εργαζόμενο,
    ίδιο `batch_no`). Ενημερώνει το `declared_days` (baseline υποβεβλημένων)."""

    employee_id: str
    batch_no: int = 0
    days: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ScheduleSystemDeclared(DomainEvent):
    """Σύστημα καταχώρισης αλλαγών ωραρίου/οργάνωσης χρόνου/υπερωριών της
    ΕΤΑΙΡΕΙΑΣ από τον μήνα `effective_from` (1η του μήνα) και μετά, έως νέα
    δήλωση (ΥΑ 32689/2025, άρθρο 2Α):

    "pre"   — προαναγγελτικό: οι αλλαγές δηλώνονται ΠΡΙΝ γίνουν·
    "retro" — απολογιστικό (μόνο με ψηφιακή κάρτα): δηλώνονται έως το τέλος
              του ΕΠΟΜΕΝΟΥ μήνα, από τα χτυπήματα της κάρτας.
    Υποβάλλεται έως την τελευταία ημέρα του προηγούμενου μήνα."""

    effective_from: date
    system: str


@dataclass(frozen=True)
class RetroScheduleSubmitted(DomainEvent):
    """Ολοκληρώθηκε η ΑΠΟΛΟΓΙΣΤΙΚΗ δήλωση ωραρίου της περιόδου (κλείνει την
    υποχρέωση RETRO_SCHEDULE). Οι ίδιες οι ημέρες είναι `DailyScheduleAssigned`
    + `ScheduleDaysSubmitted` (batch `batch_no`· 0 = καμία απόκλιση από την
    κάρτα, τίποτα προς δήλωση)."""

    period_year: int
    period_month: int
    batch_no: int = 0
    days: int = 0


@dataclass(frozen=True)
class AttendanceRecorded(DomainEvent):
    """Πραγματική παρουσία ημέρας (π.χ. από ψηφιακή κάρτα εργασίας).

    `shifts`: οι αναλυτικές βάρδιες της ημέρας ("HH:MM-HH:MM", με σειρά) —
    απαραίτητες για την κατάταξη ωρών (νύχτα/αργία/υπερεργασία/υπερωρία,
    βλ. work_hours.py). Κενό tuple δηλώνει χειροκίνητη συνολική παρουσία."""

    employee_id: str
    work_date: date
    hours_worked: float
    overtime_hours: float = 0.0
    shifts: tuple = ()


@dataclass(frozen=True)
class CardPunchRecorded(DomainEvent):
    """ΨΗΦΙΑΚΗ ΚΑΡΤΑ ΕΡΓΑΣΙΑΣ — ΜΙΑ ΔΙΕΛΕΥΣΗ (το ωμό γεγονός).

    Η κάρτα δεν γνωρίζει «βάρδιες»: καταγράφει ποιος πέρασε, πότε και προς
    ποια κατεύθυνση. Οι βάρδιες **ΠΑΡΑΓΟΝΤΑΙ** με ζευγάρωμα εισόδων/εξόδων
    (`card_punches.pair_punches`) — γι' αυτό το event μένει ωμό: μια ξεχασμένη
    έξοδος πρέπει να φαίνεται ως ξεχασμένη, όχι να «στρογγυλεύεται».

    `punch_date`: η ημερολογιακή ημέρα της διέλευσης (ΟΧΙ της βάρδιας — μια
    έξοδος στις 06:00 της επομένης ανήκει στη δική της ημέρα και το ζευγάρωμα
    την ενώνει με την είσοδο της προηγούμενης).
    `minute`: λεπτά από τα μεσάνυχτα (0-1439).
    `kind`: "in" (είσοδος) | "out" (έξοδος).
    `source`: "manual" (χειροκίνητη καταχώρηση) | "card" | "ergani" (εισαγωγή
    αρχείου ΕΡΓΑΝΗ) | "schedule" (ταίρι ΤΥΦΛΟΥ χτυπήματος από το πρόγραμμα).
    `blind`: ΤΥΦΛΟ χτύπημα — ήρθε ΧΩΡΙΣ το ταίρι του (γραμμή ΕΡΓΑΝΗ χωρίς
    είσοδο ή έξοδο). Μένει ΜΟΝΙΜΑ στο log, ακόμη κι αφού η βάρδια
    συμπληρωθεί (από το πρόγραμμα ή χειροκίνητα)."""

    employee_id: str
    punch_date: date
    minute: int
    kind: str = "in"
    source: str = "manual"
    punch_id: str = ""
    note: str = ""
    blind: bool = False


@dataclass(frozen=True)
class CardPunchCancelled(DomainEvent):
    """ΑΚΥΡΩΣΗ διέλευσης (λάθος καταχώρηση) — ΑΝΤΙΣΤΑΘΜΙΣΤΙΚΟ event.

    Το χτύπημα ΔΕΝ διαγράφεται από το log (κανόνας #2): παύει να μετράει στα
    projections/read model, αλλά το ιστορικό δείχνει ότι καταγράφηκε και
    ακυρώθηκε."""

    employee_id: str
    punch_id: str
    reason: str = ""


@dataclass(frozen=True)
class CardPeriodChecked(DomainEvent):
    """Η ΚΑΡΤΑ ΤΗΣ ΠΕΡΙΟΔΟΥ εισήχθη και πέρασε τον έλεγχο αρτιότητας για
    ΟΛΟΥΣ τους εργαζόμενους (κλείνει την υποχρέωση CARD_IMPORT —
    προαπαιτούμενο της έγκρισης παρουσιών με «Διάφορα → digital_card»)."""

    year: int
    month: int


@dataclass(frozen=True)
class CardMonthChecked(DomainEvent):
    """Ο ΕΛΕΓΧΟΣ ΑΡΤΙΟΤΗΤΑΣ ΚΑΡΤΑΣ του μήνα ολοκληρώθηκε (ARCH_REF §5.11):
    ο χρήστης τακτοποίησε στο ΗΜΕΡΟΛΟΓΙΟ ό,τι έλειπε και αποδέχεται τις
    ημέρες χωρίς χτύπημα (μετρούν με το πρόγραμμα). Με «Διάφορα →
    card_required» είναι προαπαιτούμενο της έγκρισης παρουσιών. Κάθε νέα
    διέλευση/ακύρωση στον μήνα το ΑΝΑΙΡΕΙ (projection): ξαναελέγχεται."""

    employee_id: str
    year: int
    month: int


@dataclass(frozen=True)
class MonthPayBasisChosen(DomainEvent):
    """ΤΡΟΠΟΣ ΥΠΟΛΟΓΙΣΜΟΥ ενός μήνα του εργαζομένου, μετά τη σύγκριση
    προγράμματος ↔ κάρτας στο ΗΜΕΡΟΛΟΓΙΟ (ARCH_REF §5.11): οι ημέρες
    απόκλισης πληρώνονται με το πρόγραμμα ή με την κάρτα (`auto` = ο κανόνας
    του συστήματος, `_major_by_card`). `retro`: μπαίνει ο μήνας στην
    απολογιστική πρόταση (§5.7β). Latest-wins ανά (απασχόληση, μήνας)."""

    employee_id: str
    year: int
    month: int
    basis: str  # auto | schedule | card
    retro: bool = True


@dataclass(frozen=True)
class RetroAbsenceReasonSet(DomainEvent):
    """ΛΟΓΟΣ ΑΠΟΥΣΙΑΣ μιας ημέρας για την ΑΠΟΛΟΓΙΣΤΙΚΗ δήλωση: η κάρτα έχει
    λιγότερες ώρες από το πρόγραμμα, άρα ο εργαζόμενος απουσίασε — τα κενά
    (`cp.absence_gaps`) δηλώνονται με αυτόν τον κωδικό WorkTimeType («ΜΕ» ή
    ωροάδεια ΩΑ…, `ergani_parameters.absence_reasons`). Latest-wins ανά
    (απασχόληση, ημέρα)· `code` κενό = αφαίρεση."""

    employee_id: str
    day: date
    code: str = ""


@dataclass(frozen=True)
class AttendanceSheetApproved(DomainEvent):
    """Μηνιαίο συγκεντρωτικό παρουσιών/απουσιών — ΕΓΚΡΙΝΕΤΑΙ πριν τη μισθοδοσία.

    Παράγεται από τις καταγεγραμμένες παρουσίες ή, όπου δεν υπάρχουν, από το
    πρόγραμμα (τεκμαρτές παρουσίες), και μπορεί να διορθωθεί χειροκίνητα πριν
    την έγκριση. Η μισθοδοσία της περιόδου δουλεύει ΠΑΝΩ ΣΕ ΑΥΤΟ.

    `lines`: {employment_id: {"worked": int, "unpaid": int, "leave": int,
                              "source": "recorded"|"schedule"|"manual"}}"""

    period_year: int
    period_month: int
    lines: dict = field(default_factory=dict)


@dataclass(frozen=True)
class LeaveRecorded(DomainEvent):
    employee_id: str
    leave_type: LeaveType
    date_from: date
    date_to: date
    notes: str = ""
    leave_id: str = ""  # για ακύρωση (CRUD delete)


@dataclass(frozen=True)
class LeaveCancelled(DomainEvent):
    """Ακύρωση προγραμματισμένης άδειας."""

    employee_id: str
    leave_id: str


@dataclass(frozen=True)
class LeaveRescheduled(DomainEvent):
    """Διόρθωση υπάρχουσας άδειας χωρίς απώλεια της ταυτότητάς της.

    Το σταθερό ``leave_id`` διατηρεί τυχόν συνδεδεμένα στοιχεία (π.χ.
    επίδομα ασθενείας), ενώ το event log παραμένει append-only.
    """

    employee_id: str
    leave_id: str
    leave_type: LeaveType
    date_from: date
    date_to: date
    notes: str = ""


@dataclass(frozen=True)
class SicknessBenefitRecorded(DomainEvent):
    """Επίδομα ασθενείας που ΕΝΕΚΡΙΝΕ ο ΕΦΚΑ για μια συγκεκριμένη άδεια
    ασθενείας. Καταχωρείται χειροκίνητα (το ποσό είναι γνωστό μόνο εκ των
    υστέρων) και ΑΦΑΙΡΕΙΤΑΙ από όσα οφείλει ο εργοδότης για τις ημέρες που
    πληρώνονται κανονικά — όχι από το τριήμερο, που το βαρύνει ολόκληρο.

    Νέα καταχώρηση για την ίδια άδεια ΑΝΤΙΚΑΘΙΣΤΑ την προηγούμενη (διόρθωση)."""

    employee_id: str
    leave_id: str
    amount: float  # συνολικό ποσό επιδόματος για ΟΛΗ την άδεια
    reference: str = ""  # αρ. απόφασης / πρωτοκόλλου ΕΦΚΑ


@dataclass(frozen=True)
class ChildcareReductionSet(DomainEvent):
    """Μειωμένο ωράριο φροντίδας τέκνου (μετά τη λοχεία). Λογίζεται ΠΛΗΡΗΣ
    χρόνος εργασίας: σύμβαση, μισθός, εισφορές, δώρα ΔΕΝ αλλάζουν — μόνο το
    πρόγραμμα επιτρέπεται να είναι μικρότερο (βλ. `constraints.check_schedule`).

    Νέα δήλωση ΑΝΤΙΚΑΘΙΣΤΑ την προηγούμενη· κενό `option` = κατάργηση."""

    employee_id: str
    date_from: date
    option: str  # κλειδί του `config.CHILDCARE_REDUCTION_OPTIONS` ή ""


@dataclass(frozen=True)
class ChildBirthRecorded(DomainEvent):
    """Τοκετός εργαζόμενης → μείωση 50% εισφοράς σύνταξης (ΑΠΔ κωδ. 103)
    για 12 μήνες απασχόλησης μετά τον μήνα τοκετού. Νέα δήλωση ΑΝΤΙΚΑΘΙΣΤΑ
    την προηγούμενη· None = κατάργηση (καταχώρηση εκ παραδρομής)."""

    employee_id: str
    birth_date: date | None


# ---------------------------------------------------------------- Μισθοδοσία
@dataclass(frozen=True)
class MonthlyPayrollCalculated(DomainEvent):
    employee_id: str
    period_year: int
    period_month: int
    gross: float
    efka_employee: float
    efka_employer: float
    taxable: float
    fmy: float  # παρακρατούμενος ΦΜΥ
    net: float
    unpaid_days: int = 0
    insurance_days: int = 25  # 25 μισθωτοί, πραγματικές ημέρες ημερομίσθιοι
    daily_wage: float = 0.0
    # Περικοπή μικτών λόγω ασθένειας: μισό ημερομίσθιο για κάθε ημέρα του
    # τριημέρου + το επίδομα ΕΦΚΑ που αφαιρείται από τις πληρωμένες ημέρες.
    # (Οι ημέρες πάνω από το όριο είναι άνευ αποδοχών — μπαίνουν στα unpaid_days.)
    sick_deduction: float = 0.0
    # Σύνολο προσαυξήσεων (νύχτα/αργία/πρόσθετη/υπερεργασία/υπερωρίες) που
    # ΠΡΟΣΤΙΘΕΝΤΑΙ στα μικτά — από το εγκεκριμένο συγκεντρωτικό παρουσιών.
    premiums: float = 0.0
    # ΠΛΗΡΗΣ ΑΝΑΛΥΣΗ του υπολογισμού (βάση, περικοπές, γραμμές προσαυξήσεων,
    # κρατήσεις) — ό,τι χρειάζεται η απόδειξη αποδοχών του εργαζόμενου.
    analysis: dict = field(default_factory=dict)
    # ΕΚΚΑΘΑΡΙΣΗ ΑΠΟΧΩΡΗΣΗΣ: μισθοδοσία ΜΟΝΟ αυτού που αποχωρεί (μέσα στον
    # τρέχοντα μήνα) — ΔΕΝ κλείνει την υποχρέωση της περιόδου ούτε ανοίγει
    # τον επόμενο μήνα· οι υπόλοιποι υπολογίζονται στον τακτικό κύκλο.
    exit_settlement: bool = False


@dataclass(frozen=True)
class GiftCalculated(DomainEvent):
    """Δώρο Χριστουγέννων ή Πάσχα."""

    employee_id: str
    gift_type: str  # "christmas" | "easter"
    year: int
    gross: float
    efka_employee: float
    fmy: float
    net: float
    # ΕΚΚΑΘΑΡΙΣΗ ΑΠΟΧΩΡΗΣΗΣ: ο υπολογισμός αφορά ΑΠΟΚΛΕΙΣΤΙΚΑ τον εργαζόμενο
    # που αποχωρεί, ΟΧΙ τον εταιρικό κύκλο Δώρων — άρα ΔΕΝ κλείνει την
    # υποχρέωση της εταιρείας (οι υπόλοιποι δεν έχουν υπολογιστεί ακόμη).
    exit_settlement: bool = False
    # Οι εισφορές ΕΡΓΟΔΟΤΗ του Δώρου. Παλιά events δεν τις έχουν (0.0) και
    # ανασυντίθενται από τα ποσοστά της ημέρας καταβολής — βλ. `_gift_cost`.
    # Χωρίς αυτές η ΑΠΔ δεν μπορεί να δηλώσει ό,τι ΠΡΑΓΜΑΤΙΚΑ υπολογίστηκε.
    efka_employer: float = 0.0
    # Επιδότηση εργατικής εισφοράς (ΑΠΔ κωδ. 102): το `efka_employee` είναι
    # ήδη ΜΕΙΩΜΕΝΟ κατά αυτήν (= ό,τι κρατήθηκε)· η ΑΠΔ την ξαναπροσθέτει.
    subsidy_employee: float = 0.0
    # Κωδ. ΑΠΔ της `subsidy_employee`: 102 ή 103 (μητέρα)· κενό = 102
    subsidy_code: str = ""
    # Επιδότηση εργοδοτικής εισφοράς νέων ≤25 (ΑΠΔ 107): το `efka_employer`
    # είναι ήδη ΜΕΙΩΜΕΝΟ κατά αυτήν (= ό,τι καταβάλλεται).
    subsidy_employer: float = 0.0


@dataclass(frozen=True)
class LeaveAllowanceCalculated(DomainEvent):
    """Επίδομα Αδείας."""

    employee_id: str
    year: int
    gross: float
    efka_employee: float
    fmy: float
    net: float
    # Εκκαθάριση αποχώρησης — βλ. GiftCalculated.exit_settlement
    exit_settlement: bool = False
    # Εισφορές ΕΡΓΟΔΟΤΗ — βλ. GiftCalculated.efka_employer
    efka_employer: float = 0.0
    # Επιδοτήσεις — βλ. GiftCalculated.subsidy_employee
    subsidy_employee: float = 0.0
    subsidy_code: str = ""
    subsidy_employer: float = 0.0


# ------------------------------------------------------------- Εταιρεία
@dataclass(frozen=True)
class CompanyRegistered(DomainEvent):
    """Στοιχεία εργοδότη (Μητρώο Εργοδοτών e-ΕΦΚΑ) — απαιτούνται για ΑΠΔ."""

    ame: str  # 10ψήφιος Αριθμός Μητρώου Εργοδότη
    afm: str
    legal_name: str
    first_name: str = ""
    father_name: str = ""
    local_office_code: str = "0000000000"
    local_office_name: str = ""
    # Αντικείμενο δραστηριότητας — πεδίο 2.6 του JL10 (ΦΜΥ).
    activity: str = ""


@dataclass(frozen=True)
class CompanyBranchDefined(DomainEvent):
    """Φυσικό σημείο δραστηριότητας της εταιρείας (παράρτημα ΕΡΓΑΝΗ).

    Νέα δήλωση με τον ίδιο κωδικό αντικαθιστά την προηγούμενη. Ο κωδικός
    ``0`` είναι πάντοτε το βασικό παράρτημα/έδρα.
    """

    code: int
    street: str = ""
    street_no: str = ""
    postal_code: str = "00000"
    city: str = ""
    sepe_code: str = ""
    oaed_code: str = ""
    kad_code: str = ""
    kallikratis_code: str = ""

    @property
    def display_name(self) -> str:
        address = " ".join(x for x in (self.street, self.street_no) if x).strip()
        return f"{self.code} — {address}" if address else str(self.code)

    @property
    def employment_label(self) -> str:
        """Ετικέτα επιλογής στις φόρμες εργαζομένου."""
        return "0 Κεντρικό" if self.code == 0 else self.display_name


@dataclass(frozen=True)
class CompanySpecialtyDefined(DomainEvent):
    """Ειδικότητα (θέση εργασίας) της εταιρείας — ΑΝΕΞΑΡΤΗΤΗ από τον κωδικό
    ειδικότητας ΕΦΚΑ. Δηλώνει τον ΑΣΦΑΛΙΣΤΙΚΟ ΣΥΝΔΥΑΣΜΟ σε τρεις βαθμίδες:

      ① Κύρια — πάντα ΕΦΚΑ, δεδομένη (δεν είναι επιλογή).
      ② Επικουρική — ΕΦΚΑ ή ΤΕΚΑ (`epik_carriers`: ποιους επιτρέπει αυτή η
        ειδικότητα, τουλάχιστον έναν). ΕΦΚΑ -> ΕΝΑ πακέτο κύριας+επικουρικής
        (ρόλος MAIN_EPIK). ΤΕΚΑ -> ΔΥΟ πακέτα (MAIN + TEKA).
      ③ Πρόνοια — προαιρετική ανά ειδικότητα (`pron`: enabled/name/mandatory/
        differentiated). Διαφοροποιημένη -> PRON_NEW/PRON_OLD, ενιαία ->
        PRON_UNIFORM.

    Κάθε κάλυψη είναι ΠΑΚΕΤΟ ΕΝΟΣ ΡΟΛΟΥ (config.SPECIALTY_ROLES). Η συνθήκη
    επιλογής (`when`) ΠΑΡΑΓΕΤΑΙ από τον ρόλο — δεν γράφεται στο χέρι. Τα
    ΚΑΔ/ειδικότητα ζουν ΕΔΩ, για ΚΑΘΕ ρόλο: η τριάδα της ΑΠΔ (No 31/35/37)
    εξαρτάται από το επάγγελμα, όχι από το πακέτο κάλυψης. Το ΚΠΚ δεν
    κουβαλά κωδικούς.

    Στην πρόσληψη επιλέγεται ειδικότητα + φορέας επικουρικής + πρόνοια.
    Νέα δήλωση με το ίδιο όνομα αντικαθιστά την προηγούμενη (συμψηφιστικά)."""

    name: str  # ονομασία θέσης εργασίας, π.χ. "ΣΕΡΒΙΤΟΡΟΣ"
    # ({"role","fund","label","kad","specialty_code","kpk","when"}, ...) —
    # μετά από replay μπορεί να είναι list (JSON)· παντού ως sequence.
    coverages: tuple = ()
    # Ξεχωριστή ειδικότητα ΕΡΓΑΝΗ για WebE3N (όχι ο κωδικός ΟΣΥΚ/ΑΠΔ).
    ergani_specialty_code: str = ""
    ergani_specialty_description: str = ""
    # Επιτρεπτοί φορείς επικουρικής: ("efka",) / ("teka",) / και τα δύο.
    epik_carriers: tuple = ()
    # Ρυθμίσεις προνοιακής ασφάλισης (config.PRON_DEFAULTS).
    pron: dict = field(default_factory=dict)

    def coverage(self, fund: str):
        """Η κάλυψη του ταμείου `fund` (πρώτη), ή None."""
        for c in self.coverages:
            if c.get("fund") == fund:
                return c
        return None

    def package(self, role: str):
        """Το πακέτο ενός ΡΟΛΟΥ (config.SPECIALTY_ROLES), ή None."""
        for c in self.coverages:
            if c.get("role") == role:
                return c
        return None

    @property
    def pron_settings(self) -> dict:
        """Πλήρεις ρυθμίσεις πρόνοιας (με τις προεπιλογές συμπληρωμένες)."""
        return config.normalize_pron(self.pron)

    def coverages_for(self, fund: str):
        """Όλες οι εναλλακτικές καλύψεις ενός fund (με διαφορετικό `when`)."""
        return [c for c in self.coverages if c.get("fund") == fund]

    @property
    def has_teka(self) -> bool:
        return self.coverage("teka") is not None

    @property
    def efka_main(self):
        return self.coverage("efka_main")


@dataclass(frozen=True)
class WageAdjustmentApplied(DomainEvent):
    """Ολοκληρώθηκε ο έλεγχος/προσαρμογή συμβάσεων στον κατώτατο (ΓΣΣΕ/ΚΥΑ)
    για την περίοδο. Οι ίδιες οι προσαρμογές είναι ΞΕΧΩΡΙΣΤΑ ContractChanged
    events (+ Δήλωση Μεταβολής ΕΡΓΑΝΗ) — εδώ μένει η σύνοψη, που κλείνει
    και την υποχρέωση WAGE_ADJUSTMENT."""

    period_year: int
    period_month: int
    # ({"employment_id","name","old","new"}, ...) — κενό = κανείς κάτω
    # από το όριο (ο έλεγχος έγινε, δεν χρειάστηκε προσαρμογή)
    adjusted: tuple = ()


@dataclass(frozen=True)
class KPKDefined(DomainEvent):
    """ΤΑΥΤΟΤΗΤΑ ενός ΚΠΚ — ό,τι ΔΕΝ αλλάζει με τον χρόνο (σε αντίθεση με τα
    ποσοστά, που είναι χρονοσειρά: `KPKRatesDefined`).

    - `carrier`: ΠΟΙΟΣ το εισπράττει (ΕΦΚΑ / ΤΕΚΑ).
    - `tier`   : ΒΑΘΜΙΔΑ ασφάλισης (config.TIERS) — καθορίζει σε ποιον ρόλο
                 πακέτου ειδικότητας επιτρέπεται να μπει.
    - `fund`   : ΠΑΡΑΓΩΓΟ (config.fund_for) — το ταμείο δρομολόγησης προς την
                 ΑΠΔ. Γράφεται στο event ώστε τα projections να μη
                 ξαναϋπολογίζουν.
    ⚠️ ΚΑΜΙΑ ΑΝΑΦΟΡΑ ΣΕ ΚΑΔ/ΕΙΔΙΚΟΤΗΤΑ. Το ΚΠΚ είναι το ΠΑΚΕΤΟ ΚΑΛΥΨΗΣ
    (ποιος εισπράττει, τι βαθμίδα, τι ποσοστά)· η τριάδα ΚΑΔ/ειδικότητα/ΚΠΚ
    της ΑΠΔ συμπληρώνεται στην ΕΙΔΙΚΟΤΗΤΑ, που ξέρει το επάγγελμα και τη
    δραστηριότητα — για ΚΑΘΕ ρόλο, χωρίς εξαιρέσεις.

    Ίδιο (ταμείο, ΚΠΚ) = αντικατάσταση της προηγούμενης δήλωσης."""

    fund: str  # κλειδί του config.FUNDS (παράγωγο carrier + tier)
    kpk: str
    name: str  # ονομασία, π.χ. "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ"
    tier: str  # κλειδί του config.TIERS
    carrier: str = "efka"  # κλειδί του config.CARRIERS


@dataclass(frozen=True)
class KPKRatesDefined(DomainEvent):
    """Ποσοστά κρατήσεων ενός ΚΠΚ (πακέτου κάλυψης) ανά ταμείο, με ημερομηνία
    ισχύος — διαχειρίζεται από το UI (σελίδα Παράμετροι) και ΥΠΕΡΙΣΧΥΕΙ των
    προεπιλογών του config.KPK_HISTORY. Νέα δήλωση με ίδιο (ταμείο, ΚΠΚ,
    ισχύς από) αντικαθιστά την προηγούμενη (αντισταθμιστικό event).

    ΚΟΙΝΟ ΓΙΑ ΟΛΕΣ ΤΙΣ ΕΤΑΙΡΕΙΕΣ (`SHARED_EVENT_TYPES`)."""

    fund: str  # κλειδί του config.FUNDS
    kpk: str
    valid_from: date
    employee: float  # ποσοστό εργαζομένου (κλάσμα, π.χ. 0.1337)
    employer: float  # ποσοστό εργοδότη
    # Πλήρης ανάλυση του πακέτου. Κάθε στοιχείο είναι
    # {"branch", "employee", "employer"}.
    # Τα συνολικά employee/employer ελέγχονται ότι ισούνται με τα αθροίσματα.
    components: tuple


# ----------------------------------------------------- Ασφαλιστικό προφίλ
@dataclass(frozen=True)
class InsuranceProfileAssigned(DomainEvent):
    """Επιλυμένες ασφαλιστικές καλύψεις μιας απασχόλησης."""

    employee_id: str
    branch_no: int = 0  # Παράρτημα (0 = έδρα)
    specialty_name: str = ""
    coverages: tuple = ()
    auxiliary_scheme: str = "none"  # none | efka | teka
    welfare_scheme: str = "none"  # none | tapit | άλλο δηλωμένο selector
    insurance_class: str = "new"  # old | new
    ergani_specialty_code: str = ""
    ergani_specialty_description: str = ""
    # ΕΞΑΙΡΕΣΗ από την επιδότηση εργατικής εισφοράς (ΑΠΔ κωδ. 102):
    # συνταξιούχος, οικοδόμος, τεκμαρτό ημερομίσθιο, ασφαλιστικές κλάσεις,
    # αμοιβή από κλαδική ΣΣΕ — δεν προκύπτουν από τα δεδομένα, τα δηλώνει ο χρήστης.
    labor_subsidy_excluded: bool = False

    @property
    def teka_member(self) -> bool:
        return self.auxiliary_scheme == "teka"

    @property
    def main_coverage(self):
        return next(
            (c for c in self.coverages if c.get("fund") == "efka_main"),
            self.coverages[0] if self.coverages else {},
        )

    @property
    def kad(self) -> str:
        return self.main_coverage.get("kad", "")

    @property
    def specialty_code(self) -> str:
        return self.main_coverage.get("specialty_code", "")

    @property
    def kpk(self) -> str:
        return self.main_coverage.get("kpk", "")

    @property
    def teka_kpk(self) -> str:
        coverage = next((c for c in self.coverages if c.get("fund") == "teka"), {})
        return coverage.get("kpk", "")


@dataclass(frozen=True)
class APDFileGenerated(DomainEvent):
    """Παρήχθη αρχείο ΑΠΔ (CSL01) προς υποβολή."""

    fund: str  # "efka" | "teka"
    period_year: int
    period_month: int
    total_gross: float
    total_contributions: float
    insured_count: int


# ----------------------------------------------------- Υποβολές / Πληρωμές
@dataclass(frozen=True)
class APDSubmitted(DomainEvent):
    """Υποβολή ΑΠΔ (e-ΕΦΚΑ ή ΤΕΚΑ) για περίοδο."""

    fund: str
    period_year: int
    period_month: int
    total_contributions: float
    reference: str = ""


@dataclass(frozen=True)
class FMYFileGenerated(DomainEvent):
    """Παρήχθη αρχείο JL10 (Δήλωση απόδοσης ΦΜΥ) προς υποβολή στην ΑΑΔΕ."""

    period_year: int
    period_month: int
    total_gross: float
    total_tax: float
    employee_count: int


@dataclass(frozen=True)
class FMYSubmitted(DomainEvent):
    """Δήλωση απόδοσης παρακρατούμενου ΦΜΥ στην ΑΑΔΕ."""

    period_year: int
    period_month: int
    total_fmy: float
    reference: str = ""


@dataclass(frozen=True)
class PaymentMade(DomainEvent):
    """Πληρωμή: μισθός εργαζομένου / εισφορές ΕΦΚΑ / ΦΜΥ."""

    payment_type: str  # "salary" | "efka" | "fmy"
    period_year: int
    period_month: int
    amount: float
    beneficiary: str = ""  # employee_id ή "EFKA" ή "AADE"
    reference: str = ""


# --------------------------------------------------------------- ΕΡΓΑΝΗ
@dataclass(frozen=True)
class ErganiSubmissionRecorded(DomainEvent):
    """Υποβολή εντύπου στο ΠΣ ΕΡΓΑΝΗ ΙΙ μέσω Web API (REST)."""

    doc_type: str  # WebE3N | WTO | E4_METAVOLI | E5_LIXI
    employee_id: str
    payload: str  # JSON του υποβληθέντος εντύπου
    status: str  # submitted | simulated | failed
    reference: str = ""  # αρ. πρωτοκόλλου ΕΡΓΑΝΗ
    environment: str = "trial"  # trial | production


# ------------------------------------------------- Παράμετροι εφαρμογής
# Όλες οι «νομοθετικές» παράμετροι (ΓΣΣΕ, ΕΦΚΑ, ΤΕΚΑ, φορολογική κλίμακα,
# ασθένεια, προσαυξήσεις, όρια ωρών) καταχωρούνται ως events — μία εγγραφή
# ανά (οικογένεια, ημερομηνία ισχύος). Ίδιο valid_from = αντικατάσταση της
# προηγούμενης δήλωσης (αντισταθμιστικό event). Οι τιμές του config.py είναι
# ΜΟΝΟ προτεινόμενες αρχικές τιμές για το wizard πρώτου τρεξίματος.


@dataclass(frozen=True)
class MinimumWageDefined(DomainEvent):
    """Κατώτατος μισθός/ημερομίσθιο ΓΣΣΕ/ΚΥΑ με ημερομηνία ισχύος.
    Η καταχώρηση νέας ΓΣΣΕ γεννά αντιδραστικά υποχρέωση WAGE_ADJUSTMENT
    στην περίοδο ισχύος (scheduler)."""

    valid_from: date
    monthly: float  # κατώτατος μισθός υπαλλήλων (πλήρης απασχόληση)
    daily: float  # κατώτατο ημερομίσθιο εργατοτεχνιτών
    # ΚΑΤΑΡΓΗΣΗ: εγγραφή που σημαίνει «από αυτή την ημερομηνία ΔΕΝ υπάρχει
    # κανόνας». Δεν είναι διαγραφή (κανόνας #2) — είναι κανονική εγγραφή με
    # valid_from. Το config._pick σκάει αντί να υποχωρήσει σιωπηλά στην
    # προηγούμενη γραμμή (θα ήταν μισθοδοσία με καταργημένο κανόνα).
    repealed: bool = False


@dataclass(frozen=True)
class EfkaRatesDefined(DomainEvent):
    """Καθολικές παράμετροι ΕΦΚΑ και πλαφόν ασφαλιστέων αποδοχών."""

    valid_from: date
    employee: float  # κλάσμα, π.χ. 0.1337
    employer: float
    monthly_cap: float  # πλαφόν ασφαλιστέων αποδοχών / μήνα
    # ΚΑΤΑΡΓΗΣΗ: εγγραφή που σημαίνει «από αυτή την ημερομηνία ΔΕΝ υπάρχει
    # κανόνας». Δεν είναι διαγραφή (κανόνας #2) — είναι κανονική εγγραφή με
    # valid_from. Το config._pick σκάει αντί να υποχωρήσει σιωπηλά στην
    # προηγούμενη γραμμή (θα ήταν μισθοδοσία με καταργημένο κανόνα).
    repealed: bool = False


@dataclass(frozen=True)
# ⚠️ ΙΣΤΟΡΙΚΟ EVENT — ΔΕΝ ΓΡΑΦΕΤΑΙ ΠΙΑ. Η οικογένεια παραμέτρων «teka»
# καταργήθηκε (τα ποσοστά επικουρικής έρχονται πάντα από το ΚΠΚ της
# κάλυψης). Μένει εδώ ΜΟΝΟ για να αποκωδικοποιούνται παλιά logs — το
# projection δεν το αντιστοιχεί σε καμία οικογένεια, άρα το αγνοεί.
class TekaRatesDefined(DomainEvent):
    """Καθολικές παράμετροι ΤΕΚΑ."""

    valid_from: date
    employee: float
    employer: float
    # ΚΑΤΑΡΓΗΣΗ: εγγραφή που σημαίνει «από αυτή την ημερομηνία ΔΕΝ υπάρχει
    # κανόνας». Δεν είναι διαγραφή (κανόνας #2) — είναι κανονική εγγραφή με
    # valid_from. Το config._pick σκάει αντί να υποχωρήσει σιωπηλά στην
    # προηγούμενη γραμμή (θα ήταν μισθοδοσία με καταργημένο κανόνα).
    repealed: bool = False


@dataclass(frozen=True)
class TaxScaleDefined(DomainEvent):
    """Φορολογική κλίμακα ΦΜΥ με ημερομηνία ισχύος.

    `brackets`: ({"up_to": 10000, "rate": 0.09}, ..., {"up_to": None, ...})
    — up_to None = τελευταίο (ανοιχτό) κλιμάκιο.
    `model`: το ΚΑΘΕΣΤΩΣ υπολογισμού (config.TAX_MODELS) — η λογική κάθε
    καθεστώτος είναι κώδικας· εδώ επιλέγεται ποιο ισχύει και με τι
    παραμέτρους (`model_params`, π.χ. συντελεστές ανά ηλικία/τέκνα του
    καθεστώτος 2026)."""

    valid_from: date
    brackets: tuple
    reductions: tuple = (777.0, 900.0, 1120.0, 1340.0, 1580.0)
    extra_child_reduction: float = 220.0
    phaseout_threshold: float = 12000.0
    phaseout_per_1000: float = 20.0
    model: str = "standard"  # κλειδί του config.TAX_MODELS
    model_params: dict = field(default_factory=dict)
    # ΚΑΤΑΡΓΗΣΗ: εγγραφή που σημαίνει «από αυτή την ημερομηνία ΔΕΝ υπάρχει
    # κανόνας». Δεν είναι διαγραφή (κανόνας #2) — είναι κανονική εγγραφή με
    # valid_from. Το config._pick σκάει αντί να υποχωρήσει σιωπηλά στην
    # προηγούμενη γραμμή (θα ήταν μισθοδοσία με καταργημένο κανόνα).
    repealed: bool = False


@dataclass(frozen=True)
class SickLeaveRulesDefined(DomainEvent):
    """Κανόνες ασθενείας: τριήμερο εργοδότη + ετήσιο όριο 13/26."""

    valid_from: date
    half_wage_days: int
    limit_under_year: int
    limit_over_year: int
    # ΚΑΤΑΡΓΗΣΗ: εγγραφή που σημαίνει «από αυτή την ημερομηνία ΔΕΝ υπάρχει
    # κανόνας». Δεν είναι διαγραφή (κανόνας #2) — είναι κανονική εγγραφή με
    # valid_from. Το config._pick σκάει αντί να υποχωρήσει σιωπηλά στην
    # προηγούμενη γραμμή (θα ήταν μισθοδοσία με καταργημένο κανόνα).
    repealed: bool = False


@dataclass(frozen=True)
class AllowanceRulesDefined(DomainEvent):
    """Προσαυξήσεις κατώτατου: τριετίες (μισθωτοί/ημερομίσθιοι) + γάμος."""

    valid_from: date
    monthly_triennium_pct: float = 0.10
    monthly_max_triennia: int = 3
    daily_triennium_pct: float = 0.05
    daily_max_triennia: int = 6
    marriage_pct: float = 0.10
    # ΚΑΤΑΡΓΗΣΗ: εγγραφή που σημαίνει «από αυτή την ημερομηνία ΔΕΝ υπάρχει
    # κανόνας». Δεν είναι διαγραφή (κανόνας #2) — είναι κανονική εγγραφή με
    # valid_from. Το config._pick σκάει αντί να υποχωρήσει σιωπηλά στην
    # προηγούμενη γραμμή (θα ήταν μισθοδοσία με καταργημένο κανόνα).
    repealed: bool = False


@dataclass(frozen=True)
class LaborSubsidyDefined(DomainEvent):
    """Επιδότηση εργατικής εισφοράς (αρ. 14 ν. 2837/2000) — ποσοστό."""

    valid_from: date
    pct: float = 0.0667
    repealed: bool = False


@dataclass(frozen=True)
class MotherSubsidyDefined(DomainEvent):
    """Μείωση εισφοράς μητέρας (αρ. 141§2 ν. 3655/2008) — ποσοστό."""

    valid_from: date
    pct: float = 0.03335
    repealed: bool = False


@dataclass(frozen=True)
class YouthSubsidyDefined(DomainEvent):
    """Επιδότηση εργοδοτικής εισφοράς νέων έως 25 (αρ. 86 ν. 4583/2018)."""

    valid_from: date
    pct: float = 0.0666
    repealed: bool = False


@dataclass(frozen=True)
class PremiumExemptionDefined(DomainEvent):
    """Απαλλαγή εισφορών προσαυξήσεων (αρ. 41 ν. 5184/2025) — μερίδιο."""

    valid_from: date
    share: float = 1.0
    repealed: bool = False


@dataclass(frozen=True)
class SeveranceRulesDefined(DomainEvent):
    """Αποζημίωση απόλυσης: πίνακας μηνών ανά έτη υπηρεσίας, προσαύξηση 1/6
    και κλίμακα αυτοτελούς φόρου — με ημερομηνία ισχύος.

    `months`: ((έτη, μήνες), …) — αύξοντα κατώφλια.
    `tax_brackets`: ((άνω όριο, συντελεστής), …) — το τελευταίο ανοιχτό
    (`None` = χωρίς άνω όριο, όπως στα κλιμάκια ΦΜΥ)."""

    valid_from: date
    months: tuple = ()
    gift_factor: float = 7.0 / 6.0
    tax_brackets: tuple = ()
    repealed: bool = False


@dataclass(frozen=True)
class WorkHourRulesDefined(DomainEvent):
    """Κατάταξη ωρών & προσαυξήσεις: νύχτα/αργία/υπερεργασία/υπερωρίες."""

    valid_from: date
    night_start: int = 22
    night_end: int = 6
    night_premium: float = 0.25
    holiday_premium: float = 0.75
    extra_premium: float = 0.12
    overwork_premium: float = 0.20
    overtime_premium: float = 0.40
    illegal_premium: float = 0.80
    weekly_legal_5: int = 45
    weekly_legal_6: int = 48
    daily_legal_5: int = 9
    daily_legal_6: int = 8
    daily_overwork_start: int = 8
    overtime_day_cap: int = 3
    overtime_year_cap: int = 150
    orphan_month_cap: int = 3
    retro_declare_orphans: int = 1
    card_rounding_minutes: int = 0
    card_tolerance_minutes: int = 0
    # ΚΑΤΑΡΓΗΣΗ: εγγραφή που σημαίνει «από αυτή την ημερομηνία ΔΕΝ υπάρχει
    # κανόνας». Δεν είναι διαγραφή (κανόνας #2) — είναι κανονική εγγραφή με
    # valid_from. Το config._pick σκάει αντί να υποχωρήσει σιωπηλά στην
    # προηγούμενη γραμμή (θα ήταν μισθοδοσία με καταργημένο κανόνα).
    repealed: bool = False


# ------------------------------------------------------------- Υποχρεώσεις
@dataclass(frozen=True)
class ObligationTypeConfigured(DomainEvent):
    """Παραμετροποίηση ΤΥΠΟΥ υποχρέωσης — υπερκαλύπτει τα ΔΕΔΟΜΕΝΑ της
    εγγραφής του μητρώου (obligation_types.py): κανόνα προθεσμίας (`due`)
    και πολιτική παράτασης (`extension`). Η ΣΥΜΠΕΡΙΦΟΡΑ (εντολή, spawn,
    εξαρτήσεις) μένει κώδικας. Κενό πεδίο = χωρίς αλλαγή. Νέα δήλωση για
    τον ίδιο τύπο αντικαθιστά την προηγούμενη· ΔΕΝ είναι αναδρομική: οι
    ήδη προγραμματισμένες υποχρεώσεις κρατούν την προθεσμία του event τους."""

    code: str
    due: tuple = ()  # π.χ. ("eom", 1) | ("fixed", 12, 21) | ("easter_minus", 3)
    extension: dict = field(default_factory=dict)  # {"allowed": bool, "max_days": N}


@dataclass(frozen=True)
class ObligationExtended(DomainEvent):
    """Παράταση προθεσμίας ΣΥΓΚΕΚΡΙΜΕΝΗΣ υποχρέωσης (επιτρέπεται μόνο αν ο
    τύπος της δηλώνει extension.allowed, έως extension.max_days ημέρες)."""

    obligation_id: str
    new_due_date: date
    reason: str = ""


@dataclass(frozen=True)
class ObligationScheduled(DomainEvent):
    """Μελλοντική υποχρέωση που δημιουργεί ο scheduler."""

    obligation_id: str
    obligation_type: str
    due_date: date
    employee_id: str = ""  # κενό για υποχρεώσεις επιπέδου εταιρείας
    period_year: int = 0
    period_month: int = 0


@dataclass(frozen=True)
class LeaveCompensationCalculated(DomainEvent):
    """Αποζημίωση ΜΗ ΛΗΦΘΕΙΣΑΣ κανονικής άδειας κατά την αποχώρηση. Είναι
    ξεχωριστή «μισθοδοσία» με ΜΟΝΟ φορολογική κράτηση (ΦΜΥ) — καμία
    ασφαλιστική εισφορά. Οι εργάσιμες ημέρες αδείας ανάγονται πενθήμερο→
    εξαήμερο (×1,2· εξαήμερο ×1)."""

    employee_id: str
    termination_date: date
    year: int
    leave_days: float  # υπόλοιπο εργάσιμων ημερών κανονικής άδειας
    comp_days: float  # ημέρες αποζημίωσης (× 1,2 στο πενθήμερο)
    gross: float
    fmy: float
    net: float


@dataclass(frozen=True)
class SeveranceCalculated(DomainEvent):
    """Αποζημίωση απόλυσης (μόνο σε καταγγελία σύμβασης). Καμία ασφαλιστική
    κράτηση — αυτοτελής φορολόγηση. Ποσό = μήνες (πίνακας ανά προϋπηρεσία,
    ΜΕ προειδοποίηση = μισό) × μηνιαίες αποδοχές × 7/6."""

    employee_id: str
    termination_date: date
    service_years: float
    months: float
    with_notice: bool
    monthly_salary: float
    gross: float
    fmy: float
    net: float


@dataclass(frozen=True)
class MiscSettingsChanged(DomainEvent):
    """Λειτουργικές προτιμήσεις εφαρμογής — ΟΧΙ νομοθετικές παράμετροι/
    υπολογισμοί (π.χ. αποθήκευση ZIP στον φάκελο, εμφάνιση κόστους εργοδότη
    στην απόδειξη). Latest-wins: κάθε event ενημερώνει όσα κλειδιά φέρει."""

    fields: dict


@dataclass(frozen=True)
class ObligationFulfilled(DomainEvent):
    obligation_id: str
    fulfilled_by_event: str  # event_id που την εκπλήρωσε


@dataclass(frozen=True)
class PayrollPeriodReopened(DomainEvent):
    """ΑΝΤΙΣΤΑΘΜΙΣΤΙΚΟ event: η υπολογισμένη μισθοδοσία μιας περιόδου ανοίγει
    ξανά για ΔΙΟΡΘΩΣΗ.

    Το «κλείσιμο περιόδου» δεν είναι αποθηκευμένη κατάσταση αλλά ΠΑΡΑΓΩΓΟ των
    εκπληρωμένων υποχρεώσεων (`payroll._latest_closed_payroll`), οπότε αυτό το
    event ξεκλειδώνει ταυτόχρονα παρουσίες, άδειες, πρόγραμμα, πρόσληψη/
    αποχώρηση και την επανέγκριση του συγκεντρωτικού για τον μήνα.

    Επιτρέπεται ΜΟΝΟ όσο δεν στέκει καμία υποβολή της περιόδου — αλλιώς
    προηγείται δήλωση λανθασμένης υποβολής (`SubmissionWithdrawn`)."""

    period_year: int
    period_month: int
    reason: str = ""


@dataclass(frozen=True)
class SubmissionWithdrawn(DomainEvent):
    """ΔΗΛΩΣΗ ΛΑΝΘΑΣΜΕΝΗΣ ΥΠΟΒΟΛΗΣ (ΑΠΔ ΕΦΚΑ/ΤΕΚΑ ή ΦΜΥ).

    Το αρχείο ανεβαίνει ΧΕΙΡΟΚΙΝΗΤΑ στον φορέα, άρα η «υποβολή» του συστήματος
    είναι δήλωση του χρήστη — και ανακαλείται όταν ο φορέας την απορρίψει ή
    σταλεί λάθος αρχείο. Η υποχρέωση ξαναγίνεται εκκρεμής και η υποβολή παύει
    να «στέκει»· το ίδιο το `APDSubmitted`/`FMYSubmitted` μένει στο log ως
    ιστορικό (append-only).

    ⚠️ ΔΕΝ είναι συμπληρωματική/τροποποιητική δήλωση: λέει ότι η προηγούμενη
    υποβολή ΔΕΝ ίσχυσε."""

    obligation_type: str  # APD_SUBMISSION | APD_TEKA_SUBMISSION | FMY_SUBMISSION
    period_year: int
    period_month: int
    reason: str
    fund: str = ""  # "efka" | "teka" — κενό για ΦΜΥ


EVENT_TYPES = {
    cls.__name__: cls
    for cls in [
        PersonRegistered,
        PersonDetailsCorrected,
        PersonDetailsChanged,
        EmploymentStarted,
        EmploymentHireDetailsCorrected,
        EmploymentDeleted,
        EmployeeTerminated,
        ContractChanged,
        LeaveCancelled,
        LeaveRescheduled,
        SicknessBenefitRecorded,
        ChildcareReductionSet,
        ChildBirthRecorded,
        ErganiSubmissionRecorded,
        CompanyRegistered,
        CompanyBranchDefined,
        CompanySpecialtyDefined,
        KPKDefined,
        KPKRatesDefined,
        WageAdjustmentApplied,
        InsuranceProfileAssigned,
        APDFileGenerated,
        WorkScheduleAssigned,
        DailyScheduleAssigned,
        EmploymentScheduleModeSet,
        ScheduleDeclaredToErgani,
        ScheduleDaysSubmitted,
        ScheduleSystemDeclared,
        RetroScheduleSubmitted,
        AttendanceRecorded,
        CardPunchRecorded,
        CardPunchCancelled,
        CardMonthChecked,
        CardPeriodChecked,
        MonthPayBasisChosen,
        RetroAbsenceReasonSet,
        AttendanceSheetApproved,
        LeaveRecorded,
        MonthlyPayrollCalculated,
        GiftCalculated,
        LeaveAllowanceCalculated,
        APDSubmitted,
        FMYFileGenerated,
        FMYSubmitted,
        PaymentMade,
        MinimumWageDefined,
        EfkaRatesDefined,
        TekaRatesDefined,
        TaxScaleDefined,
        SickLeaveRulesDefined,
        AllowanceRulesDefined,
        LaborSubsidyDefined,
        MotherSubsidyDefined,
        YouthSubsidyDefined,
        PremiumExemptionDefined,
        SeveranceRulesDefined,
        WorkHourRulesDefined,
        ObligationTypeConfigured,
        ObligationExtended,
        ObligationScheduled,
        ObligationFulfilled,
        PayrollPeriodReopened,
        SubmissionWithdrawn,
        MiscSettingsChanged,
        LeaveCompensationCalculated,
        SeveranceCalculated,
    ]
}

# ---------------------------------------------------------------- ΠΟΛΛΕΣ ΕΤΑΙΡΕΙΕΣ
# ΤΑ ΚΟΙΝΑ EVENTS ΟΛΩΝ ΤΩΝ ΕΤΑΙΡΕΙΩΝ. Κάθε εταιρεία έχει ΔΙΚΟ ΤΗΣ event log
# (ένα αρχείο ανά εταιρεία), αλλά οι «νομοθετικές» παράμετροι είναι ΜΙΑ φορά
# για όλες: μια νέα ΓΣΣΕ ή φορολογική κλίμακα αφορά ολόκληρο το σύστημα.
# Γι' αυτό αυτοί οι τύποι γράφονται στο ΚΟΙΝΟ log (`EventStore.shared`) και
# ξαναπαίζονται σε ΚΑΘΕ εταιρεία (βλ. `EventStore.load`).
#
# ⚠️ Ο λόγος δεν είναι μόνο σημασιολογικός: το παράγωγό τους ζει σε
# ΚΑΘΟΛΙΚΟ state του process (`config.set_overrides`, `obligation_types.
# set_override`), οπότε δύο εταιρείες με ΔΙΑΦΟΡΕΤΙΚΕΣ τιμές θα πατούσε η μία
# την άλλη. Ό,τι καταλήγει σε module-global override ΠΡΕΠΕΙ να είναι εδώ.
SHARED_EVENT_TYPES = frozenset(
    {
        "MinimumWageDefined",
        "EfkaRatesDefined",
        "TekaRatesDefined",
        "TaxScaleDefined",
        "SickLeaveRulesDefined",
        "AllowanceRulesDefined",
        "LaborSubsidyDefined",
        "MotherSubsidyDefined",
        "YouthSubsidyDefined",
        "PremiumExemptionDefined",
        "SeveranceRulesDefined",
        "WorkHourRulesDefined",
        "ObligationTypeConfigured",
        # Το ΜΗΤΡΩΟ ΚΠΚ είναι θεσμικό, όχι εταιρικό: οι κωδικοί πακέτων
        # κάλυψης και τα ποσοστά κρατήσεων ορίζονται από ΕΦΚΑ/ΤΕΚΑ και
        # ισχύουν για ΟΛΟΥΣ τους εργοδότες. Ζει στη σελίδα «Παράμετροι».
        "KPKDefined",
        "KPKRatesDefined",
    }
)


def _validate_event_registry() -> None:
    """⚠️ ΤΡΕΧΕΙ ΣΤΟ IMPORT. Κάθε event αυτού του module ΠΡΕΠΕΙ να είναι στο
    `EVENT_TYPES`.

    ➜ ΓΙΑΤΙ ΔΕΝ ΑΡΚΕΙ ΝΑ ΤΟ ΠΡΟΣΕΞΕΙΣ: η παράλειψη **δεν σκάει όταν την
    κάνεις**. Το `EventStore.append` δεν κοιτά καθόλου το μητρώο — το event
    γράφεται κανονικά και οι ζωντανοί subscribers το βλέπουν, οπότε όλα
    δουλεύουν. Σκάει με `KeyError` στο ΕΠΟΜΕΝΟ replay (`_load_own`), δηλαδή
    στο επόμενο restart — με τη βάση ήδη γεμάτη events που δεν διαβάζονται.
    Εδώ σκάει αμέσως, στο import."""
    missing = sorted(
        cls.__name__
        for cls in _all_subclasses(DomainEvent)
        if cls.__module__ == __name__ and cls.__name__ not in EVENT_TYPES
    )
    if missing:
        raise ValueError(
            "Events εκτός EVENT_TYPES (δεν θα διαβάζονταν σε replay): "
            + ", ".join(missing)
        )


def _all_subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _all_subclasses(sub)


_validate_event_registry()
