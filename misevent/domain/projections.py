"""
Projections (read models) — η «τρέχουσα κατάσταση» που προκύπτει
από την αναπαραγωγή των events. Δεν αποθηκεύεται αυθαίρετα state·
ό,τι βλέπεις εδώ μπορεί πάντα να ξαναχτιστεί από το event store.

Μοντέλο: **Person** (φυσικό πρόσωπο, σταθερή ταυτότητα) ΚΑΙ **Employment**
(περίοδος απασχόλησης). Ένα Person έχει πολλά Employments στον χρόνο· η
επαναπρόσληψη είναι νέο Employment στο ίδιο Person.

"""

import calendar
from dataclasses import dataclass, field
from dataclasses import fields as dc_fields
from datetime import date, timedelta
from typing import NamedTuple

import misevent.rules.obligation_types as ot
from misevent.domain.events import (
    EVENT_TYPES,
    SHARED_EVENT_TYPES,
    APDFileGenerated,
    APDSubmitted,
    AttendanceRecorded,
    AttendanceSheetApproved,
    CardMonthChecked,
    CardPunchCancelled,
    CardPunchRecorded,
    ChildBirthRecorded,
    ChildcareReductionSet,
    CompanyBranchDefined,
    CompanyRegistered,
    CompanySpecialtyDefined,
    ContractChanged,
    DailyScheduleAssigned,
    EmployeeTerminated,
    EmploymentDeleted,
    EmploymentHireDetailsCorrected,
    EmploymentScheduleModeSet,
    EmploymentStarted,
    ErganiSubmissionRecorded,
    FMYFileGenerated,
    FMYSubmitted,
    GiftCalculated,
    InsuranceProfileAssigned,
    KPKDefined,
    KPKRatesDefined,
    LeaveAllowanceCalculated,
    LeaveCancelled,
    LeaveCompensationCalculated,
    LeaveDeclarationSubmitted,
    LeaveRecorded,
    LeaveRescheduled,
    LeaveType,
    MiscSettingsChanged,
    MonthlyPayrollCalculated,
    MonthPayBasisChosen,
    ObligationExtended,
    ObligationFulfilled,
    ObligationScheduled,
    ObligationTypeConfigured,
    PaymentMade,
    PayrollPeriodReopened,
    PersonDetailsChanged,
    PersonDetailsCorrected,
    PersonRegistered,
    RetroAbsenceReasonSet,
    ScheduleDaysSubmitted,
    ScheduleDeclaredToErgani,
    ScheduleSystemDeclared,
    SeveranceCalculated,
    SeveranceRulesDefined,
    SicknessBenefitRecorded,
    SubmissionWithdrawn,
    TaxScaleDefined,
    WorkScheduleAssigned,
)
from misevent.rules import config
from misevent.rules.config import sick_leave_rules_for


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


# Οικογένεια παραμέτρων ανά event — ΠΑΡΑΓΩΓΟ του μητρώου `config.FAMILIES`.
# Το config αναφέρει το event με το ΟΝΟΜΑ του (είναι φύλλο του γράφου και δεν
# κάνει import το events.py)· εδώ είναι το μόνο σημείο που βλέπει και τα δύο,
# οπότε εδώ γίνεται η ανάλυση ονόματος→κλάση ΚΑΙ ο έλεγχος συνέπειας.
PARAM_EVENT_FAMILY = {EVENT_TYPES[f.event]: k for k, f in config.FAMILIES.items()}


def _validate_param_registry() -> None:
    """⚠️ ΤΡΕΧΕΙ ΣΤΟ IMPORT. Χωρίς αυτό, μια νέα οικογένεια παραμέτρων μπορεί
    να ξεχαστεί από το `SHARED_EVENT_TYPES` και να γίνει **σιωπηλά ανά
    εταιρεία** — ενώ το παράγωγό της ζει σε ΚΑΘΟΛΙΚΟ state του process
    (`config.set_overrides`). Δύο εταιρείες θα πατούσε η μία την άλλη, με
    λάθος συντελεστές και κανένα μήνυμα (ARCH_REF.md §1α)."""
    from dataclasses import fields as _dc_fields

    for key, spec in config.FAMILIES.items():
        if spec.event not in EVENT_TYPES:
            raise ValueError(
                f"{key}: το event «{spec.event}» δεν είναι στο EVENT_TYPES"
            )
        if spec.event not in SHARED_EVENT_TYPES:
            raise ValueError(
                f"{key}: το event «{spec.event}» ΠΡΕΠΕΙ να είναι στο "
                "SHARED_EVENT_TYPES — οι παράμετροι είναι ΚΟΙΝΕΣ σε όλες τις "
                "εταιρείες και το παράγωγό τους ζει σε καθολικό state"
            )
        ev_names = {f.name for f in _dc_fields(EVENT_TYPES[spec.event])}
        missing = {f.name for f in spec.fields} - ev_names
        if missing:
            raise ValueError(
                f"{key}: πεδία του μητρώου που λείπουν από το «{spec.event}»: "
                + ", ".join(sorted(missing))
            )

        # ⚠️ Η ΚΛΑΣΗ ΤΗΣ ΠΑΡΑΜΕΤΡΟΥ ΚΑΙ ΤΟ EVENT ΕΙΝΑΙ ΔΥΟ ΑΝΤΙΓΡΑΦΑ ΤΩΝ ΙΔΙΩΝ
        # ΠΕΔΙΩΝ. Το `param_entry` χτίζει την κλάση με `getattr(event, …)` για
        # ΚΑΘΕ πεδίο της: ένα πεδίο που υπάρχει στην κλάση αλλά όχι στο event
        # δεν σκάει εδώ — σκάει με `AttributeError` στο REPLAY, δηλαδή αφού
        # έχουν ήδη γραφτεί events.
        cls_names = {f.name for f in _dc_fields(spec.cls)}
        absent = cls_names - ev_names - {"repealed"}
        if absent:
            raise ValueError(
                f"{key}: πεδία της «{spec.cls.__name__}» που λείπουν από το "
                f"«{spec.event}» (θα έσκαγαν στο replay): " + ", ".join(sorted(absent))
            )

        # Χωρίς `repealed` και στα δύο, η κατάργηση κανόνα (`_pick`) δεν
        # μπορεί να δουλέψει — και η αστοχία της είναι ΛΑΘΟΣ ΠΟΣΟ, όχι σφάλμα.
        for owner, names in ((spec.cls.__name__, cls_names), (spec.event, ev_names)):
            if "repealed" not in names:
                raise ValueError(f"{key}: λείπει το πεδίο `repealed` από το «{owner}»")


_validate_param_registry()

# Λειτουργικές προτιμήσεις («Διάφορα») + οι προεπιλογές τους. Δεν είναι
# νομοθετικές παράμετροι — μόνο η ΜΟΡΦΗ/τιμή τους ζει εδώ (bool). Το event
# MiscSettingsChanged τις υπερκαλύπτει (latest-wins).
MISC_DEFAULTS = {
    "save_zip": False,  # αποθήκευση ZIP αποδείξεων στον φάκελο apodeijeis
    "show_employer_cost": True,  # εμφάνιση κόστους εργοδότη στην απόδειξη
    # Προσαρμογή μισθού (τριετία/ΓΣΣΕ) όταν ο μισθός είναι πάνω από το όριο:
    # False = συμψηφισμός (η διαφορά απορροφά την αύξηση), True = διατήρηση
    # (η αύξηση περνά στον μισθό, η διαφορά μένει πάνω από το όριο).
    "keep_wage_excess": False,
    # ΠΡΟΑΝΑΓΓΕΛΤΙΚΟ σύστημα: ημέρα όπου η κάρτα αποκλίνει πέρα από την ανοχή
    # (`EmploymentState.pay_source` == "card") — True = πληρώνεται με την
    # ΚΑΡΤΑ (οι ώρες οφείλονται, η απόκλιση μένει αδήλωτη: προειδοποίηση),
    # False = με το ΠΡΟΓΡΑΜΜΑ. Στο απολογιστικό πάντα με την κάρτα.
    "pre_major_by_card": True,
    # Η ΚΑΡΤΑ ΕΙΝΑΙ ΠΡΟΑΠΑΙΤΟΥΜΕΝΟ της μισθοδοσίας: η έγκριση παρουσιών θέλει,
    # για κάθε εργαζόμενο με εκκρεμότητες κάρτας (`svc.card_issues`),
    # ολοκληρωμένο έλεγχο στο ΗΜΕΡΟΛΟΓΙΟ (`CardMonthChecked`).
    "card_required": False,
    # Η εταιρεία είναι ΕΝΤΑΓΜΕΝΗ στην ψηφιακή κάρτα εργασίας: κάθε περίοδος
    # έχει υποχρέωση CARD_IMPORT (εισαγωγή + έλεγχος αρχείου κάρτας) πριν την
    # έγκριση παρουσιών. Αλλαγή -> scheduler (γέννηση/κλείσιμο εκκρεμών).
    "digital_card": True,
    # Αυτόματη εξόφληση πληρωμών: οι υποχρεώσεις στήλης «Πληρωμές» δεν
    # εμφανίζονται στο χρονολόγιο και εξοφλούνται μόλις ξεκλειδώσουν
    # (webapp/actions.auto_settle_payments).
    "auto_payments": True,
    # Χρονολόγιο «Εκκρεμείς»: οι ΜΕΛΛΟΝΤΙΚΕΣ υποχρεώσεις εμφανίζονται έως και
    # τόσους μήνες μετά τον τρέχοντα (3/2026 + 1 -> έως 30/4/2026). Ακέραιος.
    "obligations_horizon_months": 1,
}


def param_entry(event):
    """Μετατρέπει event παραμέτρων στην αντίστοιχη εγγραφή του config
    (π.χ. MinimumWageDefined -> config.MinimumWage). Για την κλίμακα ΦΜΥ
    διαλέγεται η κλάση του ΚΑΘΕΣΤΩΤΟΣ (config.TAX_MODELS[model])."""
    if isinstance(event, TaxScaleDefined):
        cls = config.TAX_MODELS.get(event.model, config.TaxScale)
        brackets = tuple(
            config.TaxBracket(
                float("inf") if b.get("up_to") in (None, "") else float(b["up_to"]),
                float(b["rate"]),
            )
            for b in event.brackets
        )
        kwargs = {
            "valid_from": event.valid_from,
            "brackets": brackets,
            "reductions": tuple(event.reductions),
            "extra_child_reduction": event.extra_child_reduction,
            "phaseout_threshold": event.phaseout_threshold,
            "phaseout_per_1000": event.phaseout_per_1000,
            # ⚠️ Η κλίμακα ΦΜΥ χτίζεται πεδίο-πεδίο (δεν περνά από το γενικό
            # μονοπάτι παρακάτω) — το `repealed` πρέπει να μεταφερθεί ΡΗΤΑ,
            # αλλιώς η κατάργηση γράφεται στο log αλλά δεν ισχύει ποτέ.
            "repealed": getattr(event, "repealed", False),
        }
        allowed = {f.name for f in dc_fields(cls)}
        for k, v in (event.model_params or {}).items():
            if k in allowed:
                kwargs[k] = tuple(v) if isinstance(v, list) else v
        return cls(**kwargs)
    if isinstance(event, SeveranceRulesDefined):
        # ⚠️ ΠΙΝΑΚΕΣ ΑΠΟ JSON: το replay τους δίνει ως λίστες από λίστες, και
        # το ανοιχτό κλιμάκιο ως `None` (το JSON δεν έχει άπειρο). Εδώ
        # ξαναγίνονται tuples και `inf`, όπως τα περιμένει το config.
        return config.SeveranceRules(
            valid_from=event.valid_from,
            months=tuple((int(y), int(m)) for y, m in event.months),
            gift_factor=event.gift_factor,
            tax_brackets=tuple(
                (float("inf") if up in (None, "") else float(up), float(rate))
                for up, rate in event.tax_brackets
            ),
            repealed=getattr(event, "repealed", False),
        )
    family = PARAM_EVENT_FAMILY[type(event)]
    cls = config.FAMILY_CLASSES[family]
    return cls(**{f.name: getattr(event, f.name) for f in dc_fields(cls)})


@dataclass
class PersonState:
    """Φυσικό πρόσωπο — σταθερή ταυτότητα, κοινή σε όλα τα Employments."""

    person_id: str = ""
    last_name: str = ""
    first_name: str = ""
    father_name: str = ""
    mother_name: str = ""
    birth_date: date | None = None
    afm: str = ""
    amka: str = ""
    ama: str = ""
    children: int = 0
    email: str = ""
    sex: str = ""
    nationality_code: str = ""
    identity_type: str = ""
    identity_number: str = ""
    identity_issuer: str = ""
    identity_issue_date: date | None = None
    identity_expiry_date: date | None = None
    marital_status: str = "0"
    education_level: str = ""
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
    address: str = ""
    postal_code: str = ""
    municipality: str = ""
    prefecture: str = ""
    phone: str = ""
    birth_place: str = ""
    employment_ids: list = field(default_factory=list)
    # Τέκνα ΑΝΑ ΗΜΕΡΟΜΗΝΙΑ ΙΣΧΥΟΣ: [(None, αρχικά), (από, πλήθος), …] —
    # ο ΦΜΥ κάθε περιόδου παίρνει όσα ίσχυαν τότε (βλ. children_on).
    children_history: list = field(default_factory=list)
    # Ιστορικό μεταβολών (PersonDetailsChanged) για το UI:
    # [{"effective_from": date, "fields": {πεδίο: (παλιό, νέο)}}]
    changes: list = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.last_name} {self.first_name}".strip()

    def children_on(self, d: date | None) -> int:
        """Τέκνα που ίσχυαν στην `d` (None = τρέχοντα)."""
        if d is None or not self.children_history:
            return self.children
        active = [n for eff, n in self.children_history if eff is None or eff <= d]
        return active[-1] if active else self.children_history[0][1]

    def identity_key(self):
        """Κλειδί ταυτότητας — Επώνυμο/Όνομα/Πατρώνυμο + ημ. γέννησης
        (κλειδί μοναδικότητας, βλ. constraints.identity_key)."""
        return (self.last_name, self.first_name, self.father_name, self.birth_date)


# Ελεύθερα κείμενα επικοινωνίας/γέννησης (ίδιο όνομα σε event, state, φόρμα)
PERSON_TEXT_FIELDS = (
    "address", "postal_code", "municipality", "prefecture", "phone", "birth_place",
)


def _apply_person_fields(p: "PersonState", fields: dict) -> None:
    """Γράφει πεδία προσώπου στο state (διόρθωση ή μεταβολή) — τα dates
    γυρίζουν ως ISO strings από το JSON του event log."""
    for k, v in fields.items():
        if k == "children":
            p.children = int(v)
        elif k in (
            "identity_issue_date",
            "identity_expiry_date",
            "res_permit_inst_expiry_date",
            "res_permit_ap_expiry_date",
            "res_permit_visa_from",
            "res_permit_visa_to",
        ):
            setattr(p, k, date.fromisoformat(v) if isinstance(v, str) else v)
        elif k in (
            "last_name",
            "first_name",
            "father_name",
            "mother_name",
            "afm",
            "amka",
            "ama",
            "email",
            "sex",
            "nationality_code",
            "identity_type",
            "identity_number",
            "identity_issuer",
            "marital_status",
            "education_level",
            "res_permit_inst",
            "res_permit_inst_type",
            "res_permit_inst_number",
            "res_permit_ap",
            "res_permit_ap_type",
            "res_permit_ap_number",
            "res_permit_visa",
            "res_permit_visa_number",
            *PERSON_TEXT_FIELDS,
        ):
            setattr(p, k, v)


class ContractTerms(NamedTuple):
    """Οι όροι αμοιβής μιας έκδοσης της σύμβασης (βλ. `contract_history`)."""

    effective_from: date | None
    pay_type: str
    monthly_gross: float
    daily_wage: float
    weekly_hours: float

    @property
    def wage(self) -> float:
        """Η αμοιβή που «μετράει» για τον τύπο αμοιβής (μισθός ή ημερομίσθιο)."""
        return self.daily_wage if self.pay_type == "daily" else self.monthly_gross


def _terms_of(event, effective_from: date | None) -> ContractTerms:
    """Οι όροι αμοιβής ενός event σύμβασης (πρόσληψη/διόρθωση/μεταβολή)."""
    return ContractTerms(
        effective_from,
        event.pay_type,
        event.monthly_gross,
        event.daily_wage,
        event.weekly_hours,
    )


@dataclass
class EmploymentState:
    """Μια περίοδος απασχόλησης. Τα στοιχεία ταυτότητας (ονόματα, ΑΦΜ,
    ΑΜΚΑ, ΑΜΑ, τέκνα, γέννηση) αντλούνται μέσω delegation από το
    συνδεδεμένο `person`, ώστε οι υπάρχοντες consumers (`emp.full_name`,
    `emp.afm`, …) να δουλεύουν αμετάβλητοι."""

    employee_id: str = ""  # == employment_id (aggregate των employment events)
    person_id: str = ""
    hire_date: date | None = None
    # Μεταφορά από άλλο πρόγραμμα: από πότε αναλαμβάνει ΑΥΤΟ το σύστημα τη
    # μισθοδοσία (None = από την πρόσληψη). Βλ. `payroll_start`.
    payroll_from: date | None = None
    termination_date: date | None = None
    termination_reason: str = ""  # ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ/ΚΑΤΑΓΓΕΛΙΑ_ΣΥΜΒΑΣΗΣ/…
    monthly_gross: float = 0.0
    pay_type: str = "monthly"
    daily_wage: float = 0.0
    weekly_hours: float = 40.0
    # Προσαυξήσεις κατώτατου ορίου (δηλώνονται στην πρόσληψη — config.py)
    service_years: float = 0.0  # προϋπηρεσία που ΜΕΤΡΑ στις τριετίες
    frozen_service_years: float = 0.0  # ΕΠΙΠΛΕΟΝ παγωμένη (2012-2023)
    married: bool = False
    extra_allowances_pct: float = 0.0  # λοιπές (πτυχίο κ.λπ.), σε %
    employment_relationship: str = "0"  # 0=αορίστου, 1=ορισμένου χρόνου
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
    # ΟΛΕΣ οι εκδόσεις των όρων αμοιβής, με σειρά ισχύος (η πρώτη είναι η
    # αρχική σύμβαση της πρόσληψης). Απαιτείται για υπολογισμούς σε
    # ΠΑΡΕΛΘΟΝΤΑ χρόνο — π.χ. το Δώρο Πάσχα υπολογίζεται στον μισθό που
    # ίσχυε 15 ημέρες πριν την καταβολή (βλ. `contract_on`).
    contract_history: list[ContractTerms] = field(default_factory=list)
    # Οι ΜΕΤΑΒΟΛΕΣ σύμβασης με σειρά καταχώρησης: (όροι, αναγγελμένη_τότε).
    # Κάθε μία θέλει τη δική της Δήλωση Μεταβολής ΕΡΓΑΝΗ (`subject` "E4:<n>")·
    # όσες έγιναν ΠΡΙΝ την αναγγελία πρόσληψης τις καλύπτει το ίδιο το WebE3N.
    contract_changes: list[tuple[ContractTerms, bool]] = field(default_factory=list)
    schedule: dict = field(default_factory=dict)  # εβδομαδιαίο: "MON" -> ωράριο
    schedule_history: list[tuple[date, dict, date | None]] = field(default_factory=list)
    # (ισχύς από, εβδομαδιαίο μοτίβο, ημ. λήξης ή None) — απαιτείται για
    # ιστορική/ετήσια προβολή· η λήξη κόβει το πρόγραμμα (βλ. weekly_schedule_on)
    daily_schedule: dict = field(default_factory=dict)  # date -> ωράριο (υπερισχύει)
    # Τύπος προγράμματος & κάλυψη ΕΡΓΑΝΗ (βλ. EmploymentScheduleModeSet /
    # ScheduleDeclaredToErgani / ScheduleDaysSubmitted).
    schedule_mode: str = ""  # "fixed" | "variable"
    # δηλωμένα standing εβδομαδιαία (fixed), με σειρά υποβολής:
    # (ισχύς από | None, λήξη | None, μοτίβο) — βλ. day_baseline
    standing_history: list = field(default_factory=list)
    # εβδομαδιαία (fixed) που ΚΑΤΑΓΡΑΦΗΚΑΝ αλλά δεν δηλώθηκαν ακόμη στο ΕΡΓΑΝΗ:
    # (ισχύς από, μοτίβο, λήξη) — η υποβολή γίνεται από το «Πρόγραμμα → ΕΡΓΑΝΗ»
    pending_weekly: list = field(default_factory=list)
    # baseline ΥΠΟΒΕΒΛΗΜΕΝΩΝ ημερών: date -> το ωράριο που στάλθηκε στο ΕΡΓΑΝΗ.
    # Μια ημέρα με τιμή ίδια με το τρέχον πρόγραμμα = «υποβεβλημένη»· διαφορετική
    # = «τροποποιημένη» (εκκρεμεί νέα υποβολή).
    declared_days: dict = field(default_factory=dict)  # date -> ωράριο
    # Σύστημα εβδομαδιαίας εργασίας ΑΠΟ ΤΗ ΣΥΜΒΑΣΗ (5/6).
    contract_weekly_system: int = 5
    insurance_profile: InsuranceProfileAssigned | None = None
    # ΠΑΡΟΥΣΙΕΣ = ΠΑΡΑΓΩΓΟ ΤΗΣ ΚΑΡΤΑΣ ΚΑΙ ΜΟΝΟ (§5.11): ξαναϋπολογίζονται σε
    # κάθε διέλευση (`_sync_attendance`) από τις ΚΛΕΙΣΤΕΣ βάρδιες — καμία
    # άλλη πηγή δεν τις γράφει.
    attendance: dict = field(default_factory=dict)  # date -> hours
    # date -> ["HH:MM-HH:MM", ...] — οι κλειστές βάρδιες της κάρτας,
    # απαραίτητες για την κατάταξη ωρών (work_hours.py).
    attendance_shifts: dict = field(default_factory=dict)
    # ΨΗΦΙΑΚΗ ΚΑΡΤΑ — ΩΜΕΣ ΔΙΕΛΕΥΣΕΙΣ: date -> [{"m", "kind", "id", "source",
    # "note"}, …] κατά ώρα. Οι ΒΑΡΔΙΕΣ δεν αποθηκεύονται· παράγονται με
    # ζευγάρωμα (`card_shifts`), ώστε μια ξεχασμένη έξοδος να μένει ορατή.
    card_punches: dict = field(default_factory=dict)
    # μήνες (έτος, μήνας) με ΟΛΟΚΛΗΡΩΜΕΝΟ έλεγχο κάρτας (`CardMonthChecked`)·
    # κάθε διέλευση/ακύρωση του μήνα τον αναιρεί
    card_checked: set = field(default_factory=set)
    # (έτος, μήνας) -> (τρόπος, απολογιστική) — `MonthPayBasisChosen`
    pay_basis: dict = field(default_factory=dict)
    # ημέρα -> κωδικός WorkTimeType της ΑΠΟΥΣΙΑΣ (`RetroAbsenceReasonSet`)
    absence_reasons: dict = field(default_factory=dict)
    leaves: list = field(default_factory=list)  # LeaveRecorded events
    cancelled_leaves: set = field(default_factory=set)
    # ημέρα -> κωδικός ΕΡΓΑΝΗ της ΔΗΛΩΜΕΝΗΣ άδειας (`LeaveDeclarationSubmitted`)·
    # άδεια με δηλωμένη ημέρα είναι κλειδωμένη (svc.leave_locked)
    leave_declared: dict = field(default_factory=dict)
    # έτος -> μήνας-περίοδος του Επιδόματος Αδείας, ΠΑΓΩΜΕΝΟΣ με τον υπολογισμό
    # (`allowance_period_month`): ΕΝΑ επίδομα ανά απασχόληση και έτος
    allowance_months: dict = field(default_factory=dict)
    sickness_benefits: dict = field(default_factory=dict)  # leave_id -> ποσό ΕΦΚΑ
    # Μειωμένο ωράριο φροντίδας τέκνου: [(από, έως, ώρες/ημέρα)]
    childcare_reduction: list = field(default_factory=list)
    # Τοκετός → μείωση εισφοράς μητέρας (ΑΠΔ 103), βλ. svc.mother_subsidy_on
    child_birth_date: date | None = None
    person: PersonState | None = None  # σύνδεσμος με το φυσικό πρόσωπο
    # ΕΧΕΙ ΥΠΟΒΛΗΘΕΙ Η ΑΝΑΓΓΕΛΙΑ ΠΡΟΣΛΗΨΗΣ (Ε3/WebE3N ή «external»);
    # ΜΟΝΗ πηγή: `ErganiSubmissionRecorded`. Δεν τεκμαίρεται ΠΟΤΕ από τον
    # τρόπο καταχώρησης — μια απασχόληση χωρίς αναγγελία ΔΕΝ είναι πρόσληψη
    # που «συνέβη», όσο συμπληρωμένη κι αν είναι η φόρμα της.
    hire_submitted: bool = False

    @property
    def employment_id(self) -> str:
        return self.employee_id

    # -- delegation στην ταυτότητα του προσώπου ---------------------------
    @property
    def last_name(self) -> str:
        return self.person.last_name if self.person else ""

    @property
    def first_name(self) -> str:
        return self.person.first_name if self.person else ""

    @property
    def father_name(self) -> str:
        return self.person.father_name if self.person else ""

    @property
    def mother_name(self) -> str:
        return self.person.mother_name if self.person else ""

    @property
    def birth_date(self) -> date | None:
        return self.person.birth_date if self.person else None

    @property
    def afm(self) -> str:
        return self.person.afm if self.person else ""

    @property
    def amka(self) -> str:
        return self.person.amka if self.person else ""

    @property
    def ama(self) -> str:
        return self.person.ama if self.person else ""

    @property
    def children(self) -> int:
        return self.person.children if self.person else 0

    def children_on(self, d: date | None) -> int:
        return self.person.children_on(d) if self.person else 0

    @property
    def sex(self) -> str:
        return self.person.sex if self.person else ""

    @property
    def nationality_code(self) -> str:
        return self.person.nationality_code if self.person else ""

    @property
    def identity_type(self) -> str:
        return self.person.identity_type if self.person else ""

    @property
    def identity_number(self) -> str:
        return self.person.identity_number if self.person else ""

    @property
    def identity_issuer(self) -> str:
        return self.person.identity_issuer if self.person else ""

    @property
    def identity_issue_date(self) -> date | None:
        return self.person.identity_issue_date if self.person else None

    @property
    def identity_expiry_date(self) -> date | None:
        return self.person.identity_expiry_date if self.person else None

    @property
    def marital_status(self) -> str:
        return self.person.marital_status if self.person else "0"

    @property
    def education_level(self) -> str:
        return self.person.education_level if self.person else ""

    def _person_value(self, name, default=""):
        return getattr(self.person, name, default) if self.person else default

    @property
    def res_permit_inst(self):
        return self._person_value("res_permit_inst")

    @property
    def res_permit_inst_type(self):
        return self._person_value("res_permit_inst_type")

    @property
    def res_permit_inst_number(self):
        return self._person_value("res_permit_inst_number")

    @property
    def res_permit_inst_expiry_date(self):
        return self._person_value("res_permit_inst_expiry_date", None)

    @property
    def res_permit_ap(self):
        return self._person_value("res_permit_ap")

    @property
    def res_permit_ap_type(self):
        return self._person_value("res_permit_ap_type")

    @property
    def res_permit_ap_number(self):
        return self._person_value("res_permit_ap_number")

    @property
    def res_permit_ap_expiry_date(self):
        return self._person_value("res_permit_ap_expiry_date", None)

    @property
    def res_permit_visa(self):
        return self._person_value("res_permit_visa")

    @property
    def res_permit_visa_number(self):
        return self._person_value("res_permit_visa_number")

    @property
    def res_permit_visa_from(self):
        return self._person_value("res_permit_visa_from", None)

    @property
    def res_permit_visa_to(self):
        return self._person_value("res_permit_visa_to", None)

    @property
    def full_name(self) -> str:
        return self.person.full_name if self.person else ""

    # -- κατάσταση απασχόλησης -------------------------------------------
    @property
    def part_time(self) -> bool:
        return self.weekly_hours < 40

    def total_service_years(self, on: date) -> float:
        """Προϋπηρεσία που ΜΕΤΡΑ ΣΤΙΣ ΤΡΙΕΤΙΕΣ την ημερομηνία `on`: η
        ΔΗΛΩΘΕΙΣΑ στην πρόσληψη (χωρίς τον παγωμένο χρόνο, που δηλώνεται
        χωριστά) + ο μετρήσιμος χρόνος στον τρέχοντα εργοδότη εκτός του
        παγώματος 14/02/2012–31/12/2023."""
        return config.triennia_service_years(
            self.service_years,
            self.hire_date,
            on,
        )

    def full_service_years(self, on: date) -> float:
        """ΣΥΝΟΛΙΚΟΣ χρόνος προϋπηρεσίας την `on` — ΟΛΟΣ ο χρόνος εργασίας,
        μαζί με τον παγωμένο: δηλωθείσα (τριετίες) + παγωμένη + ΟΛΟΣ ο χρόνος
        στον τρέχοντα εργοδότη. Είναι η βάση για δικαιώματα που ΔΕΝ παγώνουν
        (ημέρες άδειας κ.λπ.), σε αντίθεση με το `total_service_years`."""
        total = max(0.0, float(self.service_years or 0.0))
        total += max(0.0, float(self.frozen_service_years or 0.0))
        if self.hire_date is not None and on > self.hire_date:
            total += (on - self.hire_date).days / 365.25
        return total

    def active_leaves(self):
        return [lv for lv in self.leaves if lv.leave_id not in self.cancelled_leaves]

    def first_annual_leave(self, year: int):
        """Ημερομηνία της ΠΡΩΤΗΣ κανονικής άδειας του έτους (ή None).

        Ορίζει την περίοδο στην οποία γεννιέται το Επίδομα Αδείας αυτού του
        εργαζόμενου — καταβάλλεται όταν πάρει την άδειά του."""
        starts = [
            lv.date_from
            for lv in self.active_leaves()
            if lv.leave_type == LeaveType.ANNUAL and lv.date_from.year == year
        ]
        return min(starts) if starts else None

    def allowance_period_month(self, year: int) -> int | None:
        """Ο μήνας-περίοδος του Επιδόματος Αδείας του εργαζόμενου μέσα στο
        έτος: κανονικά ο μήνας της ΠΡΩΤΗΣ κανονικής άδειας· αν δεν πήρε άδεια
        αλλά ΑΠΟΧΩΡΗΣΕ μέσα στο έτος, ο μήνας της αποχώρησης (το επίδομα
        εκκαθαρίζεται pro-rata στην αποχώρηση). Αλλιώς, αν απασχολήθηκε μέσα
        στο έτος, ο **Δεκέμβριος**: το επίδομα ΠΡΕΠΕΙ να καταβληθεί μέσα στο
        έτος, άρα όποιος δεν πήρε άδεια το παίρνει υποχρεωτικά τον τελευταίο
        μήνα. None μόνο για όποιον δεν απασχολήθηκε καθόλου μέσα στο έτος.

        ⚠️ Μόλις υπολογιστεί το επίδομα, ο μήνας ΠΑΓΩΝΕΙ (`allowance_months`):
        μια μεταγενέστερη άδεια σε προγενέστερο μήνα δεν το μεταφέρει — αλλιώς
        θα γεννούσε δεύτερο επίδομα για το ίδιο έτος."""
        if year in self.allowance_months:
            return self.allowance_months[year]
        first = self.first_annual_leave(year)
        if first is not None:
            return first.month
        if self.termination_date is not None and self.termination_date.year == year:
            return self.termination_date.month
        if (self.hire_date is not None and self.hire_date.year <= year
                and (self.termination_date is None or self.termination_date.year > year)):
            return 12
        return None

    @property
    def active(self) -> bool:
        return self.hire_date is not None and self.termination_date is None

    @property
    def lifecycle(self) -> str:
        """ΚΥΚΛΟΣ ΖΩΗΣ της απασχόλησης — ΜΙΑ πηγή αλήθειας για κάθε ένδειξη
        κατάστασης (καρτέλα, λίστα εργαζομένων, grids Προγράμματος/Αδειών):

        - `pending`    καταχωρημένη αλλά **ΕΚΚΡΕΜΗΣ**: δεν έχει υποβληθεί η
          αναγγελία πρόσληψης (Ε3) — η σχέση δεν έχει δηλωθεί πουθενά, οπότε
          δεν είναι «Ενεργή» και ΔΕΝ επιδέχεται αποχώρηση (§5.7α)
        - `active`     ολοκληρωμένη πρόσληψη, σε ισχύ
        - `terminated` έληξε

        ⚠️ Το `active` (πρόσληψη χωρίς λήξη) ΔΕΝ αρκεί ως ένδειξη: μια
        εκκρεμής (επανα)πρόσληψη περνά κι αυτή το κριτήριο."""
        if self.hire_date is None or self.termination_date is not None:
            return "terminated" if self.termination_date is not None else "pending"
        return "active" if self.hire_submitted else "pending"

    @property
    def payroll_start(self) -> date | None:
        """ΑΠΟ ΠΟΤΕ υπολογίζει ΑΥΤΟ το σύστημα — ΜΙΑ πηγή αλήθειας.

        Κανονική πρόσληψη: η ίδια η `hire_date`. Μεταφορά από άλλο πρόγραμμα:
        η `payroll_from` (π.χ. πρόσληψη 15/5/2024, ανάληψη 1/1/2026). Ο
        διαχωρισμός είναι σκόπιμος — τριετίες, Δώρα, δικαίωμα αδείας και
        αποζημίωση απόλυσης μετρούν από την ΠΡΑΓΜΑΤΙΚΗ πρόσληψη."""
        if self.hire_date is None:
            return None
        if self.payroll_from is None:
            return self.hire_date
        return max(self.payroll_from, self.hire_date)

    @property
    def is_rehire(self) -> bool:
        """True όταν αυτή ΔΕΝ είναι η πρώτη περίοδος απασχόλησης του προσώπου
        (§2: επαναπρόσληψη = νέο Employment στο ΙΔΙΟ Person).

        ΜΙΑ πηγή αλήθειας για τις φόρμες: η αρχική (επανα)πρόσληψη και η
        μεταγενέστερη «Επεξεργασία φόρμας πρόσληψης» πρέπει να δείχνουν το ΙΔΙΟ
        πράγμα — αλλιώς η διόρθωση άνοιγε σε «mode πρόσληψης».

        Κριτήριο: δεν είναι η ΠΡΩΤΗ απασχόληση του προσώπου (η λίστα
        `person.employment_ids` είναι με τη σειρά που δημιουργήθηκαν)."""
        ids = self.person.employment_ids if self.person else []
        return bool(ids) and ids[0] != self.employment_id

    @property
    def migrated(self) -> bool:
        """True όταν η μισθοδοσία ξεκινά ΜΕΤΑ την πρόσληψη (μεταφερμένος)."""
        return (
            self.hire_date is not None
            and self.payroll_start is not None
            and self.payroll_start > self.hire_date
        )

    def payroll_active_in_month(self, year: int, month: int) -> bool:
        """Ανήκει ο μήνας στην ευθύνη ΑΥΤΟΥ του συστήματος για τη συγκεκριμένη
        απασχόληση; (ενεργή ΚΑΙ όχι πριν την ανάληψη)."""
        start = self.payroll_start
        if start is None:
            return False
        from calendar import monthrange

        end = date(year, month, monthrange(year, month)[1])
        if start > end:
            return False
        return self.termination_date is None or (
            self.termination_date >= date(year, month, 1)
        )

    def attendance_days_in_month(self, year: int, month: int) -> int:
        """Ημέρες με καταγεγραμμένη παρουσία εντός του μήνα (ημερομίσθιοι)."""
        return sum(1 for d in self.attendance if d.year == year and d.month == month)

    def unpaid_leave_days_in_month(self, year: int, month: int) -> int:
        """Ημερολογιακές ημέρες άδειας άνευ αποδοχών εντός του μήνα."""
        days = 0
        for lv in self.active_leaves():
            if lv.leave_type != LeaveType.UNPAID:
                continue
            d = lv.date_from
            while d <= lv.date_to:
                if d.year == year and d.month == month:
                    days += 1
                d = d.replace(day=d.day + 1) if d.day < 28 else _next_day(d)
        return days

    # ---------------------------------------------- Πρόγραμμα & παρουσίες
    def weekly_schedule_on(self, d: date) -> dict:
        """Το εβδομαδιαίο μοτίβο που ίσχυε τη συγκεκριμένη ημερομηνία.

        Ισχύει το ΝΕΟΤΕΡΟ εβδομαδιαίο με `effective_from <= d` (το νέο σταθερό
        εβδομαδιαίο ακυρώνει πλήρως το προηγούμενο από την έναρξή του). Αν αυτό
        έχει ημ. λήξης και η `d` την ξεπερνά, δεν ισχύει πρόγραμμα (κενό) — δεν
        «ξαναζωντανεύει» προηγούμενο, αφού είχε ήδη ακυρωθεί."""
        if not self.schedule_history:
            return self.schedule
        applicable = [
            (effective, index, pattern, end)
            for index, (effective, pattern, end) in enumerate(self.schedule_history)
            if effective <= d
        ]
        if not applicable:
            return {}
        _effective, _index, pattern, end = max(
            applicable, key=lambda item: (item[0], item[1])
        )
        if end is not None and d > end:
            return {}
        return pattern

    def day_schedule(self, d: date) -> str:
        """Το ωράριο που ισχύει την ημέρα `d` (το ημερήσιο υπερισχύει του
        εβδομαδιαίου)· κενό = ρεπό. Μπορεί να είναι σπαστό ή/και τηλεεργασία
        (βλ. constraints.parse_day_schedule)."""
        if d in self.daily_schedule:
            return self.daily_schedule[d] or ""
        code = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")[d.weekday()]
        return self.weekly_schedule_on(d).get(code, "") or ""

    def is_telework_only(self, d: date) -> bool:
        """Προβλέπει το πρόγραμμα ΜΟΝΟ τηλεεργασία την ημέρα `d`;

        Οι ημέρες αυτές δεν καταγράφονται από το σύστημα παρουσιών (κάρτα) —
        η τηλεεργασία λογίζεται ως ΔΕΔΟΜΕΝΗ (ότι πραγματοποιήθηκε)."""
        from misevent.rules import constraints

        try:
            segs = constraints.parse_day_schedule(self.day_schedule(d))
        except ValueError:
            return False
        return bool(segs) and all(seg["telework"] for seg in segs)

    # ------------------------------------------------- ψηφιακή κάρτα εργασίας
    def punches_on(self, d: date) -> list:
        """Οι ΩΜΕΣ διελεύσεις της ημέρας `d`, κατά ώρα (κοινή λίστα — μόνο
        ανάγνωση)."""
        return self.card_punches.get(d, [])

    def card_shifts(self, d: date) -> list:
        """Οι ΒΑΡΔΙΕΣ που ξεκινούν την ημέρα `d`, από το ζευγάρωμα των
        διελεύσεων — σε λεπτά από τα ΜΕΣΑΝΥΧΤΑ ΤΗΣ `d`.

        ⚠️ Το ζευγάρωμα γίνεται πάνω σε **ΤΡΕΙΣ ΗΜΕΡΕΣ** (χθες·σήμερα·αύριο,
        με μετατόπιση ∓1440΄) και μετά κρατιούνται όσες βάρδιες ΞΕΚΙΝΟΥΝ μέσα
        στην `d`:
          - η νυχτερινή 22:00→06:00 είναι ΕΝΑ διάστημα που περνά τα μεσάνυχτα
            (ανήκει στην ημέρα που ΑΡΧΙΣΕ)·
          - η έξοδος των 06:00 ΔΕΝ ξαναεμφανίζεται την επομένη ως «ορφανή» —
            γι' αυτό χρειάζεται και η ΠΡΟΗΓΟΥΜΕΝΗ ημέρα στο ζευγάρωμα.
        Ό,τι μένει πραγματικά ελλιπές (ανοιχτή βάρδια, έξοδος χωρίς είσοδο)
        επιστρέφεται σημαδεμένο: είναι λάθος που πρέπει να φαίνεται."""
        from misevent.integrations import card_punches as cp

        prev = self.card_punches.get(d - timedelta(days=1), [])
        nxt = self.card_punches.get(d + timedelta(days=1), [])
        if not (self.punches_on(d) or prev or nxt):
            return []
        stream = [{**p, "m": p["m"] - 24 * 60} for p in prev]
        stream += [dict(p) for p in self.punches_on(d)]
        stream += [{**p, "m": p["m"] + 24 * 60} for p in nxt]
        out = []
        for s in cp.pair_punches(stream):
            anchor = s["s"] if s["s"] is not None else s["e"]
            if not 0 <= anchor < 24 * 60:  # ανήκει σε άλλη ημέρα
                continue
            out.append(s)
        return out

    def orphan_shift(self, d: date, s: dict) -> bool:
        """Είναι ΟΡΦΑΝΗ η βάρδια `s` (του `card_shifts(d)`); Ανοιχτή / έξοδος
        χωρίς είσοδο, Ή με τυφλό χτύπημα / ταίρι από το πρόγραμμα (η
        εισαγωγή ΕΡΓΑΝΗ τα συμπληρώνει — η ένδειξη δεν χάνεται)."""
        if s["open"] or s["orphan"]:
            return True
        ids = {s["in_id"], s["out_id"]}
        return any(p["id"] in ids and (p.get("blind") or p.get("source") == "schedule")
                   for x in (d - timedelta(days=1), d, d + timedelta(days=1))
                   for p in self.punches_on(x))

    def schedule_completions(self, year: int, month: int) -> int:
        """Πόσα χτυπήματα του μήνα συμπληρώθηκαν ΑΠΟ ΤΟ ΠΡΟΓΡΑΜΜΑ (ορφανά —
        μετρούν στο μηνιαίο όριο `orphan_month_cap`)."""
        return sum(
            1
            for d, punches in self.card_punches.items()
            if d.year == year and d.month == month
            for p in punches
            if p["source"] == "schedule"
        )

    def actual_shifts(self, d: date) -> list:
        """Οι ΠΡΑΓΜΑΤΙΚΕΣ βάρδιες της `d` ως «ΩΩ:ΛΛ-ΩΩ:ΛΛ», από τις διελεύσεις
        της κάρτας. Πηγή της μισθοδοσίας (`pay_source`) και της απολογιστικής
        δήλωσης.

        ΟΡΦΑΝΟ χτύπημα (ανοιχτή είσοδος / έξοδος χωρίς είσοδο): επίσημα
        λαμβάνεται υπόψη το ΠΡΟΓΡΑΜΜΑ ως κανονική παρουσία — το ταίρι
        συμπληρώνεται εδώ από το πρόγραμμα της ημέρας (`cp.completion_minute`)
        ώστε να πληρωθεί και, στο απολογιστικό, να δηλωθεί. Υπολογίζεται ΖΩΝΤΑΝΑ
        (όχι στο `attendance_shifts`): οι διελεύσεις μένουν όπως χτυπήθηκαν."""
        from misevent.integrations import card_punches as cp
        from misevent.rules import constraints

        shifts = self.card_shifts(d)
        if not any(s["open"] or s["orphan"] for s in shifts):
            return list(self.attendance_shifts.get(d, []))
        try:
            segs = [s for s in constraints.parse_day_schedule(self.day_schedule(d))
                    if not s["telework"]]
        except ValueError:
            segs = []
        done = []
        for s in shifts:
            if s["open"] or s["orphan"]:
                m = cp.completion_minute(s, segs)
                if m is None:
                    continue
                s = {**s, "e": m} if s["open"] else {**s, "s": m}
            done.append(s)
        return list(cp.shift_texts(sorted(done, key=lambda s: s["s"])))

    def declared_on(self, d: date) -> str:
        """Το ΔΗΛΩΜΕΝΟ ωράριο της `d` (baseline ΕΡΓΑΝΗ), αλλιώς το τρέχον
        πρόγραμμα — το μέτρο σύγκρισης της κάρτας."""
        base = self.day_baseline(d)
        return self.day_schedule(d) if base is None else base

    def pay_source(self, d: date, tol: int = 0, step: int = 0):
        """Με ΤΙ μετρά η ημέρα `d` στη μισθοδοσία — ΜΙΑ πηγή και για την
        απολογιστική πρόταση (ARCH_REF §5.11 «Συμφωνία προγράμματος ↔ κάρτας»):

        "card"     — η κάρτα αποκλίνει από το δηλωμένο πέρα από την ευέλικτη
                     προσέλευση + την ανοχή `tol` (`card_tolerance_minutes`)
                     και τη στρογγυλοποίηση `step` (`card_rounding_minutes`)
        "schedule" — η κάρτα συμφωνεί, Ή η ημέρα έχει πρόγραμμα αλλά ΚΑΝΕΝΑ
                     χτύπημα (τηλεεργασία, βλάβη/ξεχασμένη κάρτα): μετρά το
                     πρόγραμμα· πραγματική απουσία δηλώνεται ρητά.
                     Τα τμήματα ΤΗΛΕΕΡΓΑΣΙΑΣ δεν χτυπούν κάρτα: ισχύει ό,τι
                     λέει το πρόγραμμα και ΔΕΝ μπαίνουν στη σύγκριση
        None       — ούτε πρόγραμμα ούτε κάρτα."""
        from misevent.integrations import card_punches as cp

        actual = self.actual_shifts(d)
        if not actual:
            return "schedule" if self.day_schedule(d) else None
        try:
            flex = int(self.flexible_arrival_minutes or 0)
        except ValueError:
            flex = 0
        # ΜΟΝΟ η φυσική παρουσία συγκρίνεται: η τηλεεργασία δεν χτυπά κάρτα
        physical, _tele = cp.split_telework(self.declared_on(d))
        ok = cp.day_matches(physical, cp.schedule_text(actual), flex, tol, step)
        return "schedule" if ok else "card"

    def _sync_attendance(self, d: date) -> None:
        """Ξαναϋπολογίζει την παρουσία των ημερών που αγγίζει μια διέλευση
        της `d` (χθες·σήμερα·αύριο — η νυχτερινή βάρδια περνά τα μεσάνυχτα).
        ΑΚΡΙΒΕΙΣ ώρες (Εγκ. 37271/2024: η σήμανση = η πραγματική έναρξη/λήξη)
        — η στρογγυλοποίηση αφορά ΜΟΝΟ τις προτάσεις του φύλλου
        (sheets.hours_analysis)."""
        from misevent.integrations import card_punches as cp

        for x in (d - timedelta(days=1), d, d + timedelta(days=1)):
            shifts = self.card_shifts(x)
            texts = cp.shift_texts(shifts)
            if texts:
                self.attendance_shifts[x] = list(texts)
                self.attendance[x] = round(cp.worked_minutes(shifts) / 60, 2)
            else:
                self.attendance_shifts.pop(x, None)
                self.attendance.pop(x, None)

    @property
    def weekly_system(self) -> int:
        """Σύστημα εβδομαδιαίας εργασίας της σύμβασης (5 ή 6)."""
        return self.contract_weekly_system

    @property
    def current_terms(self) -> ContractTerms:
        """Οι όροι αμοιβής που ισχύουν ΤΩΡΑ (τελευταία έκδοση σύμβασης)."""
        return ContractTerms(
            self.hire_date,
            self.pay_type,
            self.monthly_gross,
            self.daily_wage,
            self.weekly_hours,
        )

    def contract_on(self, d: date | None) -> ContractTerms:
        """Οι όροι αμοιβής που ΙΣΧΥΑΝ την ημερομηνία `d`: η ΤΕΛΕΥΤΑΙΑ έκδοση
        της σύμβασης με `effective_from <= d`.

        Το χρειάζεται κάθε υπολογισμός που δεν αποτιμάται «σήμερα» — π.χ. το
        Δώρο Πάσχα, που υπολογίζεται στον μισθό 15 ημέρες πριν την καταβολή
        (`payroll_engine.easter_gift_wage_date`).

        Πριν από την πρώτη έκδοση (ή χωρίς ιστορικό, π.χ. παλιά events)
        επιστρέφονται οι όροι της ΠΡΟΣΛΗΨΗΣ / οι τρέχοντες αντίστοιχα — ποτέ
        κενή τιμή, ώστε κανένας υπολογισμός να μη μένει χωρίς αμοιβή."""
        if not self.contract_history:
            return self.current_terms
        if d is None:
            return self.contract_history[-1]
        active = [
            t
            for t in self.contract_history
            if t.effective_from is None or t.effective_from <= d
        ]
        return active[-1] if active else self.contract_history[0]

    def childcare_hours_on(self, d: date) -> int:
        """Ώρες/ημέρα μειωμένου ωραρίου φροντίδας τέκνου που ισχύουν στην `d`."""
        return next((h for a, b, h in self.childcare_reduction if a <= d <= b), 0)

    def scheduled_days_per_week(self) -> int:
        """Πόσες ημέρες την εβδομάδα προβλέπει το εβδομαδιαίο πρόγραμμα.

        Χρειάζεται για το ημερομίσθιο μερικής απασχόλησης (ωρομίσθιο x μέσες
        ώρες ημέρας = ωρομίσθιο x εβδομαδιαίες ώρες / ημέρες εβδομάδας).
        Χωρίς πρόγραμμα χρησιμοποιείται πενθήμερη βάση."""
        days = sum(
            1
            for c in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
            if self.schedule.get(c)
        )
        return days or 5

    def is_scheduled(self, d: date, as_of: date | None = None) -> bool:
        """Προβλέπει το πρόγραμμα εργασία την ημέρα `d`;

        ΤΟ ΗΜΕΡΗΣΙΟ ΠΡΟΓΡΑΜΜΑ ΥΠΕΡΙΣΧΥΕΙ του εβδομαδιαίου: αν η ημέρα έχει
        ρητή εγγραφή, μετράει αυτή (κενό ωράριο = ρεπό), ακόμη κι αν το
        εβδομαδιαίο λέει άλλο.

        `as_of`: ΠΟΙΟ εβδομαδιαίο μοτίβο να εφαρμοστεί (η ημέρα της εβδομάδας
        μένει πάντα της `d`). Χρησιμεύει ΜΟΝΟ για τον υποθετικό «πλήρη μήνα»
        του παρονομαστή των ημερών ασφάλισης (βλ. `scheduled_days_in_month`):
        πριν την πρόσληψη δεν ισχύει κανένα μοτίβο, οπότε ρωτάμε αυτό της
        πρόσληψης."""
        if d in self.daily_schedule:
            return bool(self.daily_schedule[d])
        code = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")[d.weekday()]
        return bool(self.weekly_schedule_on(as_of or d).get(code))

    def _pattern_ref(self, d: date) -> date:
        """Η ημερομηνία της οποίας το εβδομαδιαίο μοτίβο «δανειζόμαστε» για
        ημέρες ΕΚΤΟΣ της περιόδου απασχόλησης (πριν την πρόσληψη → η ημέρα
        πρόσληψης, μετά την αποχώρηση → η ημέρα αποχώρησης)."""
        if self.hire_date is not None and d < self.hire_date:
            return self.hire_date
        if self.termination_date is not None and d > self.termination_date:
            return self.termination_date
        return d

    def scheduled_days_in_month(
        self, year: int, month: int, whole_month: bool = False
    ) -> list:
        """Οι ημέρες που ΠΡΟΒΛΕΠΕΙ το πρόγραμμα μέσα στον μήνα. Αν δεν έχει
        οριστεί πρόγραμμα, οι εργάσιμες (πενθήμερο, εκτός ελληνικών αργιών).

        `whole_month=False`: μόνο εντός της περιόδου απασχόλησης (πρόσληψη/
        αποχώρηση μέσα στον μήνα).
        `whole_month=True`: όλος ο μήνας — χρειάζεται ως ΠΑΡΟΝΟΜΑΣΤΗΣ για την
        αναλογία των 25 ημερών ασφάλισης.

        ⚠️ Στον ΠΡΩΤΟ μήνα το εβδομαδιαίο πρόγραμμα ισχύει από την **ημέρα
        πρόσληψης** και μετά, οπότε οι προηγούμενες ημέρες δεν θα είχαν
        μοτίβο: ο «πλήρης μήνας» θα έβγαινε ίσος με τις ημέρες απασχόλησης
        και η αναλογία 1 — δηλαδή 25 ημέρες ΕΦΚΑ σε όποιον προσλήφθηκε στις
        16 του μήνα. Γι' αυτό, όταν `whole_month=True`, οι ημέρες εκτός της
        περιόδου κρίνονται με το μοτίβο της πρόσληψης/αποχώρησης
        (`_pattern_ref`) — υποθετικός μήνας «σαν να δούλευε από την 1η»."""
        from misevent.rules.calendar_gr import is_working_day

        has_plan = bool(self.schedule) or any(
            d.year == year and d.month == month for d in self.daily_schedule
        )
        out = []
        d = date(year, month, 1)
        while d.month == month:
            in_period = whole_month or (
                (self.hire_date is None or d >= self.hire_date)
                and (self.termination_date is None or d <= self.termination_date)
            )
            ref = self._pattern_ref(d) if whole_month else d
            scheduled = (
                self.is_scheduled(d, as_of=ref) if has_plan else is_working_day(d)
            )
            if in_period and scheduled:
                out.append(d)
            d = _next_day(d)
        return out

    # ------------------------------------------- Κάλυψη προγράμματος στο ΕΡΓΑΝΗ
    def active_on(self, d: date) -> bool:
        """Ενεργή απασχόληση την ημέρα `d` ΓΙΑ ΑΥΤΟ το σύστημα (ανάληψη →
        αποχώρηση). Σε μεταφορά οι ημέρες πριν την `payroll_start` ανήκουν στο
        προηγούμενο πρόγραμμα: χωρίς πρόγραμμα εργασίας, παρουσίες ή ΕΡΓΑΝΗ."""
        start = self.payroll_start
        return (start is None or d >= start) and (
            self.termination_date is None or d <= self.termination_date
        )

    def active_days_in_week(self, monday: date) -> list:
        """Οι ΕΝΕΡΓΕΣ ημέρες (Δευ–Κυρ) της εβδομάδας που ξεκινά τη `monday`."""
        return [
            monday + timedelta(days=i)
            for i in range(7)
            if self.active_on(monday + timedelta(days=i))
        ]

    def week_resolved(self, monday: date) -> dict:
        """{ISO: ωράριο} για τις ΕΝΕΡΓΕΣ ημέρες της εβδομάδας (resolved plan:
        ημερήσιο override αλλιώς εβδομαδιαίο)."""
        return {
            d.isoformat(): self.day_schedule(d)
            for d in self.active_days_in_week(monday)
        }

    def day_baseline(self, d: date):
        """Το ΥΠΟΒΕΒΛΗΜΕΝΟ ωράριο (baseline) της ημέρας `d`, ή None αν δεν έχει
        δηλωθεί ποτέ στο ΕΡΓΑΝΗ. Ισχύει η ΤΕΛΕΥΤΑΙΑ υποβολή που καλύπτει τη
        `d`: η εβδομαδιαία σβήνει τις παλαιότερες ημερήσιες του διαστήματός
        της (βλ. ScheduleDeclaredToErgani), οπότε όσες έμειναν στο
        `declared_days` είναι νεότερες από κάθε εβδομαδιαία."""
        if d in self.declared_days:
            return self.declared_days[d]
        if self.schedule_mode == "fixed":
            code = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")[d.weekday()]
            for start, end, pattern in reversed(self.standing_history):
                if _within(d, start, end):
                    return pattern.get(code, "") or ""
        return None

    def day_status(self, d: date) -> str:
        """Κατάσταση δήλωσης της ημέρας στο ΕΡΓΑΝΗ:

        "off"       — εκτός περιόδου απασχόλησης
        "pending"   — δεν έχει δηλωθεί ποτέ (αδήλωτο)
        "submitted" — το τρέχον πρόγραμμα ταυτίζεται με το δηλωμένο
        "modified"  — έχει δηλωθεί αλλά το τρέχον πρόγραμμα διαφέρει (εκκρεμεί
                      νέα υποβολή της αλλαγής)
        """
        if not self.active_on(d):
            return "off"
        base = self.day_baseline(d)
        if base is None:
            return "pending"
        return "submitted" if self.day_schedule(d) == base else "modified"

    def week_status(self, monday: date) -> str:
        """Συγκεντρωτική κατάσταση της εβδομάδας από τις ημέρες της:
        "off" | "pending" (έστω μία αδήλωτη) | "modified" (έστω μία αλλαγή) |
        "covered" (όλες υποβεβλημένες)."""
        active = self.active_days_in_week(monday)
        if not active:
            return "off"
        statuses = {self.day_status(d) for d in active}
        if "pending" in statuses:
            return "pending"
        if "modified" in statuses:
            return "modified"
        return "covered"

    def leave_days_in_month(self, year: int, month: int, ltype=None) -> list:
        """Ημέρες ΠΡΟΓΡΑΜΜΑΤΟΣ που καλύπτονται από άδεια (όλων ή ενός τύπου)."""
        out = []
        for d in self.scheduled_days_in_month(year, month):
            for lv in self.active_leaves():
                if ltype is not None and lv.leave_type != ltype:
                    continue
                if lv.date_from <= d <= lv.date_to:
                    out.append(d)
                    break
        return out

    def holiday_off(self, d: date) -> bool:
        """Καθημερινή ΑΡΓΙΑ όπου η εργασία ΔΕΝ τεκμαίρεται (η επιχείρηση
        κλείνει, ο μισθός πληρώνεται) — θέλει ρητή δήλωση παρουσίας. Η
        Κυριακή εξαιρείται (το πρόγραμμα τη δηλώνει), όπως και η ημέρα
        πρόσληψης (θεωρείται ότι εργάστηκε)."""
        from misevent.rules.calendar_gr import public_holidays

        return (d.weekday() != 6 and d in public_holidays(d.year)
                and d != self.hire_date)

    def scheduled_shifts_in_month(self, year: int, month: int) -> dict:
        """Τεκμαρτές ΒΑΡΔΙΕΣ από το πρόγραμμα για τις προγραμματισμένες ημέρες
        του μήνα ΧΩΡΙΣ αναλυτική καταγραφή κάρτας και χωρίς άδεια.

        Επιστρέφει {date: ["ΩΩ:ΛΛ-ΩΩ:ΛΛ", ...]} με ΜΟΝΟ τα τμήματα φυσικής
        παρουσίας — η τηλεεργασία ΔΕΝ κατατάσσεται (§5.10). Χρησιμεύει για να
        προτείνει προσαυξήσεις (νύχτα/Κυριακή/υπερωρίες) όταν η μισθοδοσία
        τεκμαίρεται από το πρόγραμμα, χωρίς Κάρτα Εργασίας.

        ➜ **Κυριακές**: όταν το ΠΡΟΓΡΑΜΜΑ τις ορίζει, τεκμαίρονται ως εργασία
        (η κατάταξη τους δίνει την προσαύξηση Κυριακής/αργίας +75%) — το ίδιο
        το πρόγραμμα είναι η δήλωση πρόθεσης εργασίας.
        ➜ **Αργίες (καθημερινές)**: ΔΕΝ τεκμαίρονται — η εργασία σε αργία θέλει
        ρητή δήλωση στις παρουσίες (κάρτα/χειροκίνητα). ΕΞΑΙΡΕΣΗ: αν η ίδια η
        **ημέρα πρόσληψης** πέφτει σε αργία, θεωρείται ότι εργάστηκε."""
        from misevent.rules import constraints

        leave_days = set(self.leave_days_in_month(year, month))
        out = {}
        for d in self.scheduled_days_in_month(year, month):
            if d in self.attendance or d in leave_days:
                continue
            if self.holiday_off(d):
                continue
            try:
                segs = constraints.parse_day_schedule(self.day_schedule(d))
            except ValueError:
                continue
            shifts = [seg["text"] for seg in segs if not seg["telework"]]
            if shifts:
                out[d] = shifts
        return out

    # ------------------------------------------------------------ Ασθένεια
    def sick_day_map(self) -> dict:
        """Κάθε ημέρα ασθενείας -> (κατηγορία, leave_id), σε χρονολογική σειρά.

        Κατηγορίες:
          `half` — τριήμερο εργοδότη: πληρώνεται ΜΙΣΟ ημερομίσθιο.
          `full` — πληρώνεται κανονικά, μείον το επίδομα ΕΦΚΑ της άδειας.
          `over` — πάνω από το ετήσιο όριο: ΑΝΕΥ ΑΠΟΔΟΧΩΝ.

        Το «έτος» είναι ΚΥΛΙΟΜΕΝΟ: ανοίγει με την πρώτη ημέρα ασθενείας και
        διαρκεί 365 ημέρες. Μέσα σε αυτό το παράθυρο το τριήμερο δίνεται ΜΙΑ
        φορά (δεν ξαναρχίζει σε επόμενη ασθένεια) και το όριο (13 ή 26 ημέρες,
        ανάλογα με την προϋπηρεσία) εξαντλείται συνολικά. Μετράνε μόνο οι
        ημέρες που ΠΡΟΒΛΕΠΕΙ το πρόγραμμα — μια ασθένεια που πέφτει σε ρεπό
        δεν κοστίζει ούτε στον εργοδότη ούτε στο όριο."""
        days = []
        for lv in self.active_leaves():
            if lv.leave_type != LeaveType.SICK:
                continue
            d = lv.date_from
            while d <= lv.date_to:
                in_period = (self.hire_date is None or d >= self.hire_date) and (
                    self.termination_date is None or d <= self.termination_date
                )
                if in_period and self.is_scheduled(d):
                    days.append((d, lv.leave_id))
                d = _next_day(d)

        out = {}
        window_start = None
        half_used = used = limit = 0
        for d, lid in sorted(days):
            if window_start is None or (d - window_start).days >= 365:
                window_start = d
                half_used = used = 0
                limit = self._sick_limit(window_start)
            rules = sick_leave_rules_for(d)
            if used >= limit:
                out[d] = ("over", lid)
                continue
            used += 1
            if half_used < rules.half_wage_days:
                half_used += 1
                out[d] = ("half", lid)
            else:
                out[d] = ("full", lid)
        return out

    def _sick_limit(self, on: date) -> int:
        """Ετήσιο όριο πληρωμένων ημερών ασθενείας — εξαρτάται από την
        προϋπηρεσία ΣΤΟΝ ΙΔΙΟ ΕΡΓΟΔΟΤΗ κατά το άνοιγμα του παραθύρου."""
        rules = sick_leave_rules_for(on)
        if self.hire_date is None or (on - self.hire_date).days < 365:
            return rules.limit_under_year
        return rules.limit_over_year

    def sick_days_in_month(self, year: int, month: int) -> dict:
        """Ανάλυση των ημερών ασθενείας του μήνα + το επίδομα ΕΦΚΑ που
        αναλογεί σε αυτόν.

        Το επίδομα καταχωρείται συνολικά ανά άδεια· εδώ επιμερίζεται στους
        μήνες ΑΝΑΛΟΓΙΚΑ προς τις ημέρες `full` της άδειας που πέφτουν σε κάθε
        μήνα (μόνο αυτές πληρώνονται κανονικά, άρα μόνο από αυτές αφαιρείται)."""
        half = full = over = 0
        per_leave = {}  # leave_id -> [σύνολο ημερών full, ημέρες full του μήνα]
        for d, (cls, lid) in self.sick_day_map().items():
            this_month = d.year == year and d.month == month
            if cls == "full":
                t = per_leave.setdefault(lid, [0, 0])
                t[0] += 1
                if this_month:
                    t[1] += 1
            if not this_month:
                continue
            if cls == "half":
                half += 1
            elif cls == "full":
                full += 1
            else:
                over += 1

        benefit = 0.0
        for lid, (total, in_month) in per_leave.items():
            if in_month:
                benefit += self.sickness_benefits.get(lid, 0.0) * in_month / total
        return {
            "half": half,
            "full": full,
            "over": over,
            "benefit": round(benefit + 1e-9, 2),
        }


def leave_state(leave, ref: date) -> str:
    """Κατάσταση άδειας ΩΣ ΠΡΟΣ την ημερομηνία αναφοράς — ΠΑΡΑΓΩΓΗ, όχι event:
    "planned" (δεν ξεκίνησε) | "ongoing" (σε εξέλιξη) | "taken" (πέρασε χωρίς
    αλλαγή = ληφθείσα· από εδώ και πέρα αλλάζει μόνο ως διόρθωση)."""
    if leave.date_from > ref:
        return "planned"
    return "ongoing" if leave.date_to >= ref else "taken"


def _within(d: date, start: date | None, end: date | None) -> bool:
    """d ∈ [start, end] — None = χωρίς όριο."""
    return (start is None or start <= d) and (end is None or d <= end)


def _next_day(d: date):
    from datetime import timedelta

    return d + timedelta(days=1)


@dataclass
class Obligation:
    obligation_id: str
    obligation_type: str
    due_date: date
    employee_id: str
    period_year: int
    period_month: int
    fulfilled: bool = False
    # Παράταση (ObligationExtended): η due_date ενημερώνεται, η αρχική
    # προθεσμία μένει εδώ — από αυτήν μετρά το όριο max_days του τύπου.
    original_due: date | None = None
    extended: bool = False


def profile_coverages(prof) -> list[dict]:
    """Οι επιλυμένες ασφαλιστικές καλύψεις ενός προφίλ."""
    if prof is None:
        return []
    return [dict(c) for c in prof.coverages]


class Projections:
    """Συνδέεται ως subscriber στο event store και ενημερώνεται live,
    αλλά μπορεί και να ξαναχτιστεί πλήρως με rebuild()."""

    def __init__(self):
        # Μητρώο παραμέτρων από events: family -> {valid_from: event}.
        # Παράγωγό του είναι τα overrides του config (set_overrides) — τα
        # καθαρίζουμε εδώ ώστε το rebuild μετά από rollback να μην αφήνει
        # «ορφανές» τιμές.
        self.parameters: dict[str, dict[date, object]] = {}
        config.clear_overrides()
        # Παραμετροποίηση τύπων υποχρεώσεων: code -> τελευταία δήλωση
        self.obligation_config: dict[str, ObligationTypeConfigured] = {}
        ot.clear_overrides()
        self.company: CompanyRegistered | None = None
        # code -> τελευταία δήλωση παραρτήματος.
        self.branches: dict[int, CompanyBranchDefined] = {}
        # Ειδικότητες (θέσεις εργασίας) εταιρείας: name -> τελευταία δήλωση
        self.specialties: dict[str, CompanySpecialtyDefined] = {}
        # Ταυτότητες ΚΠΚ: (fund, kpk) -> τελευταία δήλωση (ονομασία, βαθμίδα,
        # και ΚΑΔ/ειδικότητα για τις βαθμίδες με σταθερούς κωδικούς)
        self.kpk_identity: dict[tuple, KPKDefined] = {}
        # Ποσοστά ΚΠΚ από το UI: (fund, kpk, valid_from) -> τελευταία δήλωση
        # (χρονοσειρά — μία γραμμή ανά ημερομηνία ισχύος)
        self.kpk_rates: dict[tuple, KPKRatesDefined] = {}
        self.persons: dict[str, PersonState] = {}
        self.employments: dict[str, EmploymentState] = {}
        self.obligations: dict[str, Obligation] = {}
        # Υπολογισμοί μισθοδοσίας/Δώρων/Επιδόματος: κρατιέται ΜΟΝΟ ο
        # ΤΕΛΕΥΤΑΙΟΣ ανά φυσικό κλειδί — ο επανυπολογισμός είναι
        # αντισταθμιστικό event που ΑΝΤΙΚΑΘΙΣΤΑ τον παλιό (κανόνας #2),
        # αλλιώς ο ίδιος εργαζόμενος θα εμφανιζόταν δύο φορές στην περίοδο.
        self._payrolls: dict[tuple, MonthlyPayrollCalculated] = {}
        self._gifts: dict[tuple, GiftCalculated] = {}
        self._allowances: dict[tuple, LeaveAllowanceCalculatedType] = {}
        # Αποζημίωση μη ληφθείσας αδείας ανά απασχόληση (η αποχώρηση είναι μία)
        self._leave_compensations: dict[str, LeaveCompensationCalculated] = {}
        # Αποζημίωση απόλυσης ανά απασχόληση
        self._severances: dict[str, SeveranceCalculated] = {}
        self.apd_files: list[APDFileGenerated] = []
        self.fmy_files: list[FMYFileGenerated] = []
        self.apd_submissions: list[APDSubmitted] = []
        self.fmy_submissions: list[FMYSubmitted] = []
        self.payments: list[PaymentMade] = []
        self.ergani_submissions: list = []
        # batch_no -> {"count": πλήθος ημερήσιων, "employees": set} — αριθμημένες
        # υποβολές ημερήσιου προγράμματος στο ΕΡΓΑΝΗ (ScheduleDaysSubmitted)
        self.schedule_batches: dict[int, dict] = {}
        # (year, month) -> AttendanceSheetApproved (εγκεκριμένο συγκεντρωτικό)
        self.attendance_sheets: dict[tuple[int, int], AttendanceSheetApproved] = {}
        # Λειτουργικές προτιμήσεις («Διάφορα») — latest-wins από events
        self.misc_settings: dict = dict(MISC_DEFAULTS)
        # Σύστημα δήλωσης αλλαγών ωραρίου ΑΝΑ ΜΗΝΑ (1η του μήνα -> "pre"|"retro"):
        # νέα δήλωση για τον ίδιο μήνα αντικαθιστά (βλ. schedule_system_on)
        self.schedule_systems: dict = {}
        # ΨΗΦΙΑΚΗ ΚΑΡΤΑ: μετρητής διελεύσεων (κάθε καταγραφή/ακύρωση τον
        # ανεβάζει). Είναι το «σφυγμός» που ρωτά η οθόνη «Ζωντανά» για να
        # ανανεωθεί ΜΟΝΟ όταν όντως άλλαξε κάτι — δεν είναι δεδομένο, είναι
        # παράγωγο του log (το replay δίνει το ίδιο πλήθος).
        self.card_pulse: int = 0

    # -- Τελευταίοι υπολογισμοί (ο επανυπολογισμός αντικαθιστά τον παλιό) --
    @property
    def payrolls(self) -> list:
        """Ο ΤΕΛΕΥΤΑΙΟΣ υπολογισμός ανά (απασχόληση, περίοδος)."""
        return list(self._payrolls.values())

    @property
    def gifts(self) -> list:
        """Το ΤΕΛΕΥΤΑΙΟ Δώρο ανά (απασχόληση, τύπος, έτος)."""
        return list(self._gifts.values())

    @property
    def allowances(self) -> list:
        """Το ΤΕΛΕΥΤΑΙΟ Επίδομα Αδείας ανά (απασχόληση, έτος)."""
        return list(self._allowances.values())

    @property
    def leave_compensations(self) -> list:
        """Η ΤΕΛΕΥΤΑΙΑ αποζημίωση μη ληφθείσας αδείας ανά απασχόληση."""
        return list(self._leave_compensations.values())

    @property
    def severances(self) -> list:
        """Η ΤΕΛΕΥΤΑΙΑ αποζημίωση απόλυσης ανά απασχόληση."""
        return list(self._severances.values())

    def person_by_afm(self, afm: str) -> PersonState | None:
        norm = "".join(c for c in str(afm or "") if c.isdigit())
        for p in self.persons.values():
            if p.afm == norm:
                return p
        return None

    # -- pub/sub entrypoint ------------------------------------------------
    def handle(self, aggregate_id: str, event):
        if isinstance(event, tuple(PARAM_EVENT_FAMILY)):
            # Παράμετροι εφαρμογής: ίδιο (οικογένεια, valid_from) =
            # αντικατάσταση. Το config ενημερώνεται ως ΠΑΡΑΓΩΓΟ state.
            family = PARAM_EVENT_FAMILY[type(event)]
            self.parameters.setdefault(family, {})[event.valid_from] = event
            config.set_overrides(
                family,
                [param_entry(e) for e in self.parameters[family].values()],
            )

        elif isinstance(event, ObligationTypeConfigured):
            self.obligation_config[event.code] = event
            ot.set_override(
                event.code,
                {"due": tuple(event.due), "extension": dict(event.extension)},
            )

        elif isinstance(event, ObligationExtended):
            ob = self.obligations.get(event.obligation_id)
            if ob:
                ob.due_date = event.new_due_date
                ob.extended = True

        elif isinstance(event, MiscSettingsChanged):
            # ενημερώνει μόνο τα κλειδιά που φέρει το event (latest-wins)
            for k, v in event.fields.items():
                if k in MISC_DEFAULTS:  # ίδιος τύπος με το default (bool/int)
                    self.misc_settings[k] = type(MISC_DEFAULTS[k])(v)

        elif isinstance(event, CompanyRegistered):
            self.company = event

        elif isinstance(event, CompanyBranchDefined):
            self.branches[event.code] = event

        elif isinstance(event, CompanySpecialtyDefined):
            self.specialties[event.name] = event  # ίδιο όνομα = αντικατάσταση

        elif isinstance(event, KPKDefined):
            # ίδιο (ταμείο, ΚΠΚ) = αντικατάσταση της ταυτότητας
            self.kpk_identity[(event.fund, event.kpk)] = event

        elif isinstance(event, KPKRatesDefined):
            # ίδιο (ταμείο, ΚΠΚ, ισχύς από) = αντικατάσταση
            self.kpk_rates[(event.fund, event.kpk, event.valid_from)] = event

        elif isinstance(event, PersonRegistered):
            p = self.persons.setdefault(event.person_id, PersonState())
            p.person_id = event.person_id
            p.last_name = event.last_name
            p.first_name = event.first_name
            p.father_name = event.father_name
            p.mother_name = event.mother_name
            p.birth_date = event.birth_date
            p.afm, p.amka, p.ama = event.afm, event.amka, event.ama
            p.children = event.children
            p.children_history = [(None, event.children)]
            p.email = event.email
            p.sex = event.sex
            p.nationality_code = event.nationality_code
            p.identity_type = event.identity_type
            p.identity_number = event.identity_number
            p.identity_issuer = event.identity_issuer
            p.identity_issue_date = event.identity_issue_date
            p.identity_expiry_date = event.identity_expiry_date
            for k in (
                "marital_status",
                "education_level",
                "res_permit_inst",
                "res_permit_inst_type",
                "res_permit_inst_number",
                "res_permit_inst_expiry_date",
                "res_permit_ap",
                "res_permit_ap_type",
                "res_permit_ap_number",
                "res_permit_ap_expiry_date",
                "res_permit_visa",
                "res_permit_visa_number",
                "res_permit_visa_from",
                "res_permit_visa_to",
                *PERSON_TEXT_FIELDS,
            ):
                setattr(p, k, getattr(event, k))

        elif isinstance(event, PersonDetailsCorrected):
            p = self.persons[event.person_id]
            _apply_person_fields(p, event.fields)
            if "children" in event.fields:
                # Διόρθωση = λάθος στην ΤΡΕΧΟΥΣΑ τιμή -> τελευταία εγγραφή
                eff, _ = p.children_history[-1]
                p.children_history[-1] = (eff, p.children)

        elif isinstance(event, PersonDetailsChanged):
            p = self.persons[event.person_id]
            eff = event.effective_from
            # Ανά πεδίο: η μεταβολή γίνεται «τρέχουσα» τιμή ΜΟΝΟ αν καμία
            # νεότερη (σε ισχύ) μεταβολή δεν έχει ήδη αλλάξει το ίδιο πεδίο —
            # μια αναδρομική καταχώρηση δεν πατά νεότερη.
            current = {
                k: v for k, v in event.fields.items()
                if not any(eff < c["effective_from"] and k in c["fields"]
                           for c in p.changes)
            }
            p.changes.append({
                "effective_from": eff,
                "fields": {k: (getattr(p, k, None), v)
                           for k, v in event.fields.items()},
            })
            p.changes.sort(key=lambda c: c["effective_from"])
            _apply_person_fields(p, current)
            if "children" in event.fields:
                p.children_history.append((eff, int(event.fields["children"])))
                p.children_history.sort(key=lambda h: h[0] or date.min)

        elif isinstance(event, EmploymentStarted):
            emp = self.employments.setdefault(event.employment_id, EmploymentState())
            emp.employee_id = event.employment_id
            emp.person_id = event.person_id
            emp.person = self.persons.get(event.person_id)
            emp.hire_date = event.hire_date
            emp.payroll_from = event.payroll_from
            emp.pay_type = event.pay_type
            emp.monthly_gross = event.monthly_gross
            emp.daily_wage = event.daily_wage
            emp.weekly_hours = event.weekly_hours
            emp.contract_weekly_system = event.weekly_system
            emp.service_years = event.service_years
            emp.frozen_service_years = event.frozen_service_years
            emp.married = event.married
            emp.extra_allowances_pct = event.extra_allowances_pct
            emp.employment_relationship = event.employment_relationship
            emp.fixed_term_from = event.fixed_term_from
            emp.fixed_term_to = event.fixed_term_to
            for field_name in (
                "oaed_placement",
                "oaed_program",
                "oaed_replacement",
                "oaed_replaced_afm",
                "oaed_replaced_amka",
                "trial_period",
                "trial_date_to",
                "working_card",
                "flexible_arrival_minutes",
                "break_within_schedule",
                "break_minutes",
            ):
                setattr(emp, field_name, getattr(event, field_name))
            # Η πρόσληψη ΔΕΝ θεωρείται ολοκληρωμένη με την καταχώρηση: γίνεται
            # ολοκληρωμένη ΜΟΝΟ με την αναγγελία στο ΕΡΓΑΝΗ (Ε3/WebE3N ή
            # καταγραφή «external» στη μεταφορά). Το `draft` του event μένει ως
            # ιστορικό του πώς καταχωρήθηκε, ΔΕΝ δηλώνει υποβολή.
            emp.hire_submitted = False
            emp.contract_history = [_terms_of(event, event.hire_date)]
            if emp.person and event.employment_id not in emp.person.employment_ids:
                emp.person.employment_ids.append(event.employment_id)

        elif isinstance(event, EmploymentHireDetailsCorrected):
            emp = self.employments[event.employment_id]
            emp.hire_date = event.hire_date
            emp.pay_type = event.pay_type
            emp.monthly_gross = event.monthly_gross
            emp.daily_wage = event.daily_wage
            emp.weekly_hours = event.weekly_hours
            emp.contract_weekly_system = event.weekly_system
            emp.service_years = event.service_years
            emp.frozen_service_years = event.frozen_service_years
            emp.married = event.married
            emp.extra_allowances_pct = event.extra_allowances_pct
            emp.employment_relationship = event.employment_relationship
            emp.fixed_term_from = event.fixed_term_from
            emp.fixed_term_to = event.fixed_term_to
            for field_name in (
                "oaed_placement",
                "oaed_program",
                "oaed_replacement",
                "oaed_replaced_afm",
                "oaed_replaced_amka",
                "trial_period",
                "trial_date_to",
                "working_card",
                "flexible_arrival_minutes",
                "break_within_schedule",
                "break_minutes",
            ):
                setattr(emp, field_name, getattr(event, field_name))
            # Η διόρθωση της φόρμας πρόσληψης αλλάζει την ΑΡΧΙΚΗ έκδοση της
            # σύμβασης — οι μεταγενέστερες μεταβολές μένουν άθικτες.
            initial = _terms_of(event, event.hire_date)
            if emp.contract_history:
                emp.contract_history[0] = initial
            else:
                emp.contract_history = [initial]
            emp.contract_history.sort(key=lambda t: t.effective_from or date.min)
            if emp.person and event.employment_id not in emp.person.employment_ids:
                emp.person.employment_ids.append(event.employment_id)
            emp.schedule = dict(event.weekly_pattern)
            emp.schedule_history = [
                (
                    event.hire_date,
                    dict(event.weekly_pattern),
                    event.schedule_end_date,
                )
            ]
            emp.schedule_mode = event.schedule_mode
            emp.standing_history = []
            emp.pending_weekly = []
            emp.daily_schedule = {
                date.fromisoformat(iso): hours
                for iso, hours in event.daily_schedule.items()
            }
            emp.declared_days = {}

        elif isinstance(event, ContractChanged):
            emp = self.employments[event.employee_id]
            emp.pay_type = event.pay_type
            emp.monthly_gross = event.monthly_gross
            emp.daily_wage = event.daily_wage
            emp.weekly_hours = event.weekly_hours
            # None = η μεταβολή δεν άγγιξε το σύστημα εργασίας -> μένει ως έχει
            if event.weekly_system is not None:
                emp.contract_weekly_system = event.weekly_system
            emp.contract_history.append(_terms_of(event, event.effective_from))
            emp.contract_history.sort(key=lambda t: t.effective_from or date.min)
            emp.contract_changes.append(
                (_terms_of(event, event.effective_from), emp.hire_submitted))

        elif isinstance(event, EmploymentDeleted):
            # Εκ παραδρομής καταχώρηση: η απασχόληση φεύγει ΟΛΟΚΛΗΡΗ από τα
            # projections (το event log μένει άθικτο). Ό,τι κρατά κλειδί
            # απασχόλησης καθαρίζεται μαζί, ώστε το replay να δίνει το ίδιο
            # αποτέλεσμα με μια βάση που δεν την είδε ποτέ.
            emp = self.employments.pop(event.employee_id, None)
            if emp is not None and emp.person is not None:
                person = emp.person
                person.employment_ids = [
                    eid for eid in person.employment_ids if eid != event.employee_id
                ]
            for store in (self._payrolls, self._gifts, self._allowances):
                for key in [k for k in store if k[0] == event.employee_id]:
                    del store[key]
            self._leave_compensations.pop(event.employee_id, None)
            self._severances.pop(event.employee_id, None)
            self.ergani_submissions = [
                s
                for s in self.ergani_submissions
                if getattr(s, "employee_id", None) != event.employee_id
            ]

        elif isinstance(event, LeaveCancelled):
            self.employments[event.employee_id].cancelled_leaves.add(event.leave_id)

        elif isinstance(event, LeaveDeclarationSubmitted):
            for eid, days in event.days.items():
                if eid in self.employments:   # διαγραμμένη απασχόληση: αγνοείται
                    self.employments[eid].leave_declared.update(
                        {date.fromisoformat(iso): code for iso, code in days.items()})

        elif isinstance(event, ErganiSubmissionRecorded):
            self.ergani_submissions.append(event)
            if (
                event.doc_type in ("WebE3N", "E3")
                and event.status != "failed"
                and event.employee_id in self.employments
            ):
                self.employments[event.employee_id].hire_submitted = True

        elif isinstance(event, EmployeeTerminated):
            emp = self.employments[event.employee_id]
            emp.termination_date = event.termination_date
            emp.termination_reason = event.reason

        elif isinstance(event, InsuranceProfileAssigned):
            emp = self.employments[event.employee_id]
            emp.insurance_profile = event

        elif isinstance(event, WorkScheduleAssigned):
            emp = self.employments[event.employee_id]
            emp.schedule_history.append(
                (event.effective_from, dict(event.weekly_pattern), event.end_date)
            )
            latest = max(
                enumerate(emp.schedule_history),
                key=lambda item: (item[1][0], item[0]),
            )[1]
            emp.schedule = dict(latest[1])
            if emp.schedule_mode != "variable":  # μεταβλητό: μόνο template
                emp.pending_weekly = [
                    p for p in emp.pending_weekly if p[0] != event.effective_from
                ] + [(event.effective_from, dict(event.weekly_pattern), event.end_date)]
            # Η τελευταία υποβολή επικρατεί: το νέο εβδομαδιαίο ακυρώνει τις
            # παλαιότερες ημερήσιες μεταβολές του διαστήματος ισχύος του.
            emp.daily_schedule = {
                d: h for d, h in emp.daily_schedule.items()
                if not _within(d, event.effective_from, event.end_date)
            }

        elif isinstance(event, DailyScheduleAssigned):
            # Νέα υποβολή αντικαθιστά τις ίδιες ημέρες (last-write-wins)
            sched = self.employments[event.employee_id].daily_schedule
            for iso, hours in event.days.items():
                sched[date.fromisoformat(iso)] = hours

        elif isinstance(event, EmploymentScheduleModeSet):
            emp = self.employments[event.employee_id]
            emp.schedule_mode = event.mode
            if event.mode == "variable":
                emp.pending_weekly = []

        elif isinstance(event, ScheduleDeclaredToErgani):
            # STANDING εβδομαδιαίο (fixed): νεότερο από κάθε προηγούμενη
            # ημερήσια δήλωση του διαστήματός του — αυτές παύουν να ισχύουν.
            emp = self.employments[event.employee_id]
            emp.standing_history.append(
                (event.effective_from, event.end_date, dict(event.days))
            )
            emp.pending_weekly = [  # παλιά events χωρίς ημερομηνία: όλα
                p for p in emp.pending_weekly
                if event.effective_from is not None and p[0] != event.effective_from
            ]
            emp.declared_days = {
                d: h for d, h in emp.declared_days.items()
                if not _within(d, event.effective_from, event.end_date)
            }

        elif isinstance(event, ScheduleDaysSubmitted):
            emp = self.employments[event.employee_id]
            for iso, hours in event.days.items():
                emp.declared_days[date.fromisoformat(iso)] = hours or ""
            batch = self.schedule_batches.setdefault(
                event.batch_no, {"count": 0, "employees": set()}
            )
            batch["count"] += len(event.days)
            batch["employees"].add(event.employee_id)

        elif isinstance(event, AttendanceSheetApproved):
            self.attendance_sheets[(event.period_year, event.period_month)] = event

        elif isinstance(event, ScheduleSystemDeclared):
            self.schedule_systems[event.effective_from] = event.system

        elif isinstance(event, AttendanceRecorded):
            # ΠΑΛΑΙΟ event (πριν οι παρουσίες γίνουν παράγωγο της κάρτας):
            # μένει μόνο για να διαβάζονται τα παλιά logs — ΔΕΝ μετρά.
            pass

        elif isinstance(event, CardPunchRecorded):
            # ΩΜΗ διέλευση: μπαίνει στη θέση της κατά ώρα (η κάρτα μπορεί να
            # στείλει εκπρόθεσμα ή ο χρήστης να συμπληρώσει χτύπημα που ξεχάστηκε)
            emp = self.employments[event.employee_id]
            day = emp.card_punches.setdefault(event.punch_date, [])
            day.append(
                {
                    "m": int(event.minute),
                    "kind": event.kind,
                    "id": event.punch_id,
                    "source": event.source,
                    "note": event.note,
                    "blind": event.blind,
                }
            )
            day.sort(key=lambda p: (p["m"], 0 if p["kind"] == "in" else 1))
            emp._sync_attendance(event.punch_date)
            emp.card_checked.discard((event.punch_date.year, event.punch_date.month))
            self.card_pulse += 1

        elif isinstance(event, CardPunchCancelled):
            emp = self.employments.get(event.employee_id)
            if emp is not None:
                for d, day in list(emp.card_punches.items()):
                    kept = [p for p in day if p["id"] != event.punch_id]
                    if len(kept) == len(day):
                        continue
                    if kept:
                        emp.card_punches[d] = kept
                    else:
                        del emp.card_punches[d]
                    emp._sync_attendance(d)
                    emp.card_checked.discard((d.year, d.month))
                    break
            self.card_pulse += 1

        elif isinstance(event, CardMonthChecked):
            self.employments[event.employee_id].card_checked.add(
                (event.year, event.month)
            )

        elif isinstance(event, MonthPayBasisChosen):
            self.employments[event.employee_id].pay_basis[
                (event.year, event.month)] = (event.basis, bool(event.retro))

        elif isinstance(event, RetroAbsenceReasonSet):
            reasons = self.employments[event.employee_id].absence_reasons
            if event.code:
                reasons[event.day] = event.code
            else:
                reasons.pop(event.day, None)

        elif isinstance(event, LeaveRecorded):
            self.employments[event.employee_id].leaves.append(event)

        elif isinstance(event, LeaveRescheduled):
            emp = self.employments[event.employee_id]
            for index, leave in enumerate(emp.leaves):
                if leave.leave_id == event.leave_id:
                    emp.leaves[index] = LeaveRecorded(
                        event.employee_id,
                        event.leave_type,
                        event.date_from,
                        event.date_to,
                        event.notes,
                        event.leave_id,
                    )
                    break

        elif isinstance(event, ChildcareReductionSet):
            emp = self.employments[event.employee_id]
            emp.childcare_reduction = []
            start = event.date_from
            _, steps = config.CHILDCARE_REDUCTION_OPTIONS.get(event.option, ("", ()))
            for months, hours in steps:
                end = _add_months(start, months)
                emp.childcare_reduction.append((start, end - timedelta(days=1), hours))
                start = end

        elif isinstance(event, ChildBirthRecorded):
            self.employments[event.employee_id].child_birth_date = event.birth_date

        elif isinstance(event, SicknessBenefitRecorded):
            # Νέα καταχώρηση αντικαθιστά την προηγούμενη (διόρθωση ποσού)
            self.employments[event.employee_id].sickness_benefits[event.leave_id] = (
                event.amount
            )

        elif isinstance(event, MonthlyPayrollCalculated):
            self._payrolls[
                (event.employee_id, event.period_year, event.period_month)
            ] = event

        elif isinstance(event, GiftCalculated):
            self._gifts[(event.employee_id, event.gift_type, event.year)] = event

        elif isinstance(event, LeaveAllowanceCalculated):
            self._allowances[(event.employee_id, event.year)] = event
            emp = self.employments.get(event.employee_id)
            if emp is not None:
                emp.allowance_months[event.year] = emp.allowance_period_month(event.year)

        elif isinstance(event, LeaveCompensationCalculated):
            self._leave_compensations[event.employee_id] = event

        elif isinstance(event, SeveranceCalculated):
            self._severances[event.employee_id] = event

        elif isinstance(event, APDFileGenerated):
            self.apd_files.append(event)

        elif isinstance(event, APDSubmitted):
            self.apd_submissions.append(event)

        elif isinstance(event, FMYFileGenerated):
            self.fmy_files.append(event)

        elif isinstance(event, FMYSubmitted):
            self.fmy_submissions.append(event)

        elif isinstance(event, PaymentMade):
            self.payments.append(event)

        elif isinstance(event, ObligationScheduled):
            self.obligations[event.obligation_id] = Obligation(
                event.obligation_id,
                event.obligation_type,
                event.due_date,
                event.employee_id,
                event.period_year,
                event.period_month,
                original_due=event.due_date,
            )

        elif isinstance(event, ObligationFulfilled) and (
            event.obligation_id in self.obligations
        ):
            self.obligations[event.obligation_id].fulfilled = True

        elif isinstance(event, PayrollPeriodReopened):
            # Η μισθοδοσία ξαναγίνεται ΕΚΚΡΕΜΗΣ. Δεν σβήνεται κανένας
            # υπολογισμός: τα MonthlyPayrollCalculated αντικαθίστανται από
            # μόνα τους στο ίδιο φυσικό κλειδί όταν ξανατρέξει (κανόνας #2).
            self._reopen("MONTHLY_PAYROLL", event.period_year, event.period_month)

        elif isinstance(event, SubmissionWithdrawn):
            # Η υποβολή ΠΑΥΕΙ ΝΑ ΣΤΕΚΕΙ: η υποχρέωση ξανανοίγει και η δήλωση
            # φεύγει από τις τρέχουσες (το event μένει στο log ως ιστορικό).
            self._reopen(
                event.obligation_type, event.period_year, event.period_month)
            if event.obligation_type == "FMY_SUBMISSION":
                self.fmy_submissions = [
                    s for s in self.fmy_submissions
                    if (s.period_year, s.period_month)
                    != (event.period_year, event.period_month)
                ]
            else:
                self.apd_submissions = [
                    s for s in self.apd_submissions
                    if (s.fund, s.period_year, s.period_month)
                    != (event.fund, event.period_year, event.period_month)
                ]

    def _reopen(self, otype: str, py: int, pm: int) -> None:
        """Εκπληρωμένη υποχρέωση ξαναγίνεται εκκρεμής (αντίστροφο του
        `ObligationFulfilled`). Το «κλείσιμο περιόδου» είναι παράγωγο αυτού
        του flag, οπότε από εδώ ξεκλειδώνει ολόκληρος ο μήνας."""
        for ob in self.obligations.values():
            if (ob.fulfilled and ob.obligation_type == otype
                    and ob.period_year == py and ob.period_month == pm):
                ob.fulfilled = False
                return

    def rebuild(self, store):
        self.__init__()
        for agg, ev in store.load():
            self.handle(agg, ev)

    # -- queries -----------------------------------------------------------
    def schedule_system_on(self, d: date) -> str:
        """Το σύστημα δήλωσης που ισχύει τη `d`: η νεότερη δήλωση με έναρξη
        <= d· χωρίς δήλωση = "pre" (προαναγγελτικό, ο κανόνας του νόμου)."""
        starts = [s for s in self.schedule_systems if s <= d]
        return self.schedule_systems[max(starts)] if starts else "pre"

    def pending_obligations(self, as_of: date | None = None):
        obs = [o for o in self.obligations.values() if not o.fulfilled]
        if as_of:
            obs = [o for o in obs if o.due_date <= as_of]
        return sorted(obs, key=lambda o: o.due_date)


LeaveAllowanceCalculatedType = LeaveAllowanceCalculated
