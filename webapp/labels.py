"""Ετικέτες/εικονίδια UI.

Οι ετικέτες/εικονίδια των ΥΠΟΧΡΕΩΣΕΩΝ έρχονται από το μητρώο τύπων
(`obligation_types.py`) — μία πηγή αλήθειας μαζί με τις προθεσμίες και τις
εξαρτήσεις. Εδώ μένουν μόνο οι ετικέτες που αφορούν ΜΟΝΟ το UI.
"""

from misevent.rules import obligation_types as ot

DUE_SOON_DAYS = 14  # πορτοκαλί: λήγει εντός Χ ημερών

# Επιδοτήσεις εισφορών ανά κωδ. ΑΠΔ — ΜΙΑ πηγή για τις αποδείξεις (UI + PDF)
SUBSIDY_LABELS = {
    "102": "Επιδότηση εργατικής εισφοράς (ΔΥΠΑ, ΑΠΔ κωδ. 102)",
    "103": "Μείωση εισφοράς μητέρας (ν. 3655/2008, ΑΠΔ κωδ. 103)",
    "107": "Επιδότηση εισφοράς νέων έως 25 (ν. 4583/2018, ΑΠΔ κωδ. 107)",
}

# Μήνες σε ΓΕΝΙΚΗ («Μισθοδοσία Απριλίου 2026») — index 1..12.
# ΜΙΑ πηγή αλήθειας: τη διαβάζουν και οι εκτυπώσεις PDF.
MONTHS_GEN = (
    "", "Ιανουαρίου", "Φεβρουαρίου", "Μαρτίου", "Απριλίου", "Μαΐου",
    "Ιουνίου", "Ιουλίου", "Αυγούστου", "Σεπτεμβρίου", "Οκτωβρίου",
    "Νοεμβρίου", "Δεκεμβρίου",
)

# Μήνες σε ΟΝΟΜΑΣΤΙΚΗ («Δεκέμβριος 2026») — index 1..12. Για όπου ο μήνας
# στέκεται ΜΟΝΟΣ του (στήλη πίνακα, tooltip γραφήματος), όχι ως συμπλήρωμα
# άλλης λέξης: εκεί θέλει γενική (`MONTHS_GEN`).
MONTHS_NOM = (
    "", "Ιανουάριος", "Φεβρουάριος", "Μάρτιος", "Απρίλιος", "Μάιος",
    "Ιούνιος", "Ιούλιος", "Αύγουστος", "Σεπτέμβριος", "Οκτώβριος",
    "Νοέμβριος", "Δεκέμβριος",
)

# Σύντομα ονόματα ημερών (Δευτέρα=0) — ΜΙΑ πηγή για τα Ζωντανά
DAY_NAMES = ("Δε", "Τρ", "Τε", "Πε", "Πα", "Σα", "Κυ")

# Σύντομοι μήνες για άξονες γραφημάτων («Μαρ 2026») — index 1..12.
MONTHS_SHORT = (
    "", "Ιαν", "Φεβ", "Μαρ", "Απρ", "Μάι", "Ιουν",
    "Ιουλ", "Αυγ", "Σεπ", "Οκτ", "Νοε", "Δεκ",
)

OB_LABELS = ot.LABELS
OB_ICONS = ot.ICONS
# Οι 3 στήλες του χρονολογίου υποχρεώσεων (κωδικός, τίτλος, εικονίδιο) και η
# στήλη κάθε τύπου — ΜΙΑ πηγή αλήθειας: το μητρώο `obligation_types`.
OB_TRACKS = ot.TRACK_OF
TRACKS = ot.TRACKS

# Τρόπος υπολογισμού των ημερών απόκλισης κάρτας (`MonthPayBasisChosen`)
PAY_BASIS = {
    "auto": "Αυτόματα (κανόνας συστήματος)",
    "schedule": "Με βάση το πρόγραμμα",
    "card": "Με βάση τα χτυπήματα κάρτας",
}

# ΚΑΤΑΣΤΑΣΗ ΑΠΑΣΧΟΛΗΣΗΣ — ετικέτα & τόνος ανά `EmploymentState.lifecycle`
# (ΜΙΑ πηγή: το projection αποφασίζει, εδώ μόνο πώς γράφεται). Η «εκκρεμής»
# είναι καταχωρημένη πρόσληψη ΧΩΡΙΣ αναγγελία Ε3 — δεν είναι Ενεργή.
EMP_LIFECYCLE = {
    "pending": ("Εκκρεμής πρόσληψη", "warn"),
    "active": ("Ενεργή", "ok"),
    "terminated": ("Έληξε", "off"),
}

# Επιλογή προνοιακής ασφάλισης του εργαζομένου στην πρόσληψη. Οι τιμές
# είναι αυτές που δέχεται η `svc.assign_specialty(pron_choice=…)`:
# διαφοροποιημένη πρόνοια -> new/old, ενιαία -> yes, εξαίρεση -> no.
PRON_CHOICES = {
    "new": "Νέος ασφαλισμένος",
    "old": "Παλαιός ασφαλισμένος",
    "yes": "Ναι",
    "no": "Όχι (χωρίς πρόνοια)",
}

# Τύποι αποδοχών ΑΠΔ
ET_LABELS = {
    "001": "Τακτικές αποδοχές",
    "003": "Δώρο Χριστουγέννων",
    "004": "Δώρο Πάσχα",
    "005": "Επίδομα Αδείας",
    "008": "Αποδοχές ασθενείας",
}

# Πεδία προσώπου στο ιστορικό μεταβολών (PersonDetailsChanged)
PERSON_FIELD_LABELS = {
    "children": "Τέκνα",
    "marital_status": "Οικογ. κατάσταση",
    "email": "Email",
    "identity_type": "Τύπος ταυτότητας",
    "identity_number": "Αριθμός ταυτότητας",
    "identity_issuer": "Εκδούσα αρχή",
    "identity_issue_date": "Ημ. έκδοσης ταυτότητας",
    "identity_expiry_date": "Λήξη ταυτότητας",
    "education_level": "Επίπεδο μόρφωσης",
    "res_permit_inst_number": "Άδεια άμεσης πρόσβασης",
    "res_permit_inst_expiry_date": "Λήξη άδειας άμεσης πρόσβασης",
    "res_permit_ap_number": "Άδεια με πρόσθετη έγκριση",
    "res_permit_ap_expiry_date": "Λήξη άδειας με πρόσθετη έγκριση",
    "res_permit_visa_number": "Θεώρηση εισόδου",
    "res_permit_visa_to": "Λήξη θεώρησης",
    "address": "Οδός / αριθμός",
    "postal_code": "Τ.Κ.",
    "municipality": "Δήμος",
    "prefecture": "Νομός",
    "phone": "Τηλέφωνο",
}
MARITAL_LABELS = {"0": "Άγαμος", "1": "Έγγαμος", "2": "Διαζευγμένος", "3": "Χήρος"}

LEAVE_LABELS = {
    "kanoniki_adeia": "Κανονική άδεια",
    "astheneia": "Ασθένεια",
    "adeia_aneu_apodoxon": "Άνευ αποδοχών",
    "mitrotita": "Μητρότητα",
    "goniki": "Γονική",
    "frontida_teknou": "Φροντίδα τέκνου (ισόχρονη)",
}

# Υποχρεώσεις που ΣΤΕΛΝΟΥΝ στο ΕΡΓΑΝΗ: μένουν στο χρονολόγιο ως προθεσμία,
# αλλά ενεργοποιούνται ΜΟΝΟ από την οθόνη ΕΡΓΑΝΗ (`actions.trigger(...,
# via_ergani=True)`) — εκεί ζει ΚΑΘΕ αποστολή στο ΕΡΓΑΝΗ (`views/ergani.py`).
ERGANI_ONLY = {"RETRO_SCHEDULE", "LEAVE_DECLARATION"}

# Η άδεια ως ΚΑΤΑΣΤΑΣΗ «τώρα» (Επισκόπηση → Προσωπικό τώρα)
LEAVE_NOW = {
    "kanoniki_adeia": "Σε άδεια κανονική",
    "astheneia": "Σε άδεια ασθενείας",
    "adeia_aneu_apodoxon": "Σε άδεια άνευ αποδοχών",
    "mitrotita": "Σε άδεια μητρότητας",
    "goniki": "Σε γονική άδεια",
    "frontida_teknou": "Σε άδεια φροντίδας τέκνου",
}

ERGANI_LABELS = {
    "WebE3N": "Αναγγελία Πρόσληψης",
    # Ιστορικές εγγραφές πριν από την υιοθέτηση του επίσημου WebE3N schema.
    "E3": "Ε3 Αναγγελία Πρόσληψης",
    "WTO": "Ψηφιακή Οργάνωση Χρόνου Εργασίας",
    "E4_METAVOLI": "Δήλωση Μεταβολής",
    "E5_LIXI": "Αναγγελία Λήξης",
    "WTOLeave": "Δήλωση αδειών",
}

TERMINATION_REASONS = [
    ("ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ", "Οικειοθελής αποχώρηση"),
    ("ΚΑΤΑΓΓΕΛΙΑ_ΣΥΜΒΑΣΗΣ", "Καταγγελία σύμβασης"),
    ("ΛΗΞΗ_ΣΥΜΒΑΣΗΣ_ΟΡΙΣΜΕΝΟΥ", "Λήξη ορισμένου χρόνου"),
]

# ------------------------------------------------------------- ΠΛΟΗΓΗΣΗ
# Η αρχική, οι Παρουσίες, η Μισθοδοσία και οι υποβολές (ΕΡΓΑΝΗ/ΑΠΔ/ΦΜΥ) μένουν ΕΞΩ
# από τις ομάδες: πρέπει να είναι
# πάντα ένα κλικ μακριά, ό,τι κι αν είναι ανοιχτό.
NAV_TOP = [
    ("dashboard.index", "📊", "Επισκόπηση"),
    ("attendance.index", "⏱️", "Παρουσίες"),
    ("payroll.index", "🧮", "Μισθοδοσία"),
    ("ergani.index", "🏛️", "ΕΡΓΑΝΗ"),
    ("eapd.index", "📄", "ΑΠΔ"),
    ("fmy.index", "🧾", "ΦΜΥ"),
]

# Ομάδες του sidebar: (κλειδί, ετικέτα, εικονίδιο, [σελίδες]).
# Κάθε σελίδα: (endpoint, εικονίδιο, ετικέτα, admin_only).
#
# ⚠️ Το `κλειδί` είναι ΣΤΑΘΕΡΟ αναγνωριστικό (λατινικά): πάνω σε αυτό
# θυμάται ο browser ποιες ομάδες είναι ανοιχτές (`nav-fold.js`). Αν το
# αλλάξεις, οι χρήστες χάνουν τις προτιμήσεις τους — η ελληνική ετικέτα
# αλλάζει ελεύθερα.
#
# ⚠️ Το `admin_only` ΠΡΕΠΕΙ να συμφωνεί με το `ADMIN_ENDPOINTS` του
# `webapp/__init__.py`: εκεί ο guard κάνει `abort(403)`, οπότε ένας σύνδεσμος
# χωρίς τη σημαία θα οδηγούσε απλό χρήστη σε σελίδα σφάλματος.
NAV_GROUPS = [
    ("staff", "Προσωπικό", "👥", [
        ("persons.index", "👥", "Εργαζόμενοι", False),
        ("employments.calendar_page", "📆", "Ημερολόγιο", False),
    ]),
    ("pay", "Αμοιβές", "🧮", [
        ("payments.index", "💶", "Πληρωμές", False),
        ("dashboard.stats", "📈", "Στατιστικά", False),
    ]),
    ("setup", "Ρυθμίσεις", "⚙️", [
        ("company.index", "🏢", "Εταιρεία", False),
    ]),
    # Σελίδες πάνω από την ενεργή εταιρεία (SYSTEM_ENDPOINTS). Οι
    # «Παράμετροι» είναι ΚΟΙΝΕΣ για όλες τις εταιρείες (νομοθετικές
    # παράμετροι, Μητρώο ΚΠΚ) — ανήκουν εδώ, όχι στις Ρυθμίσεις μιας
    # εταιρείας.
    ("system", "Σύστημα", "🗂️", [
        ("companies.index", "🗂️", "Εταιρείες", False),
        ("apd_import.index", "📥", "Εισαγωγή από ΑΠΔ", False),
        ("settings.index", "⚙️", "Παράμετροι", True),
        ("users.index", "🔑", "Χρήστες", True),
        ("backup.index", "💾", "Backup", True),
    ]),
]


def nav_for(is_admin: bool, endpoint: str) -> list[dict]:
    """Το μενού ΟΠΩΣ θα αποδοθεί: φιλτραρισμένο ανά δικαιώματα, με σημειωμένη
    την ενεργή σελίδα και ανοιχτή την ομάδα που την περιέχει.

    Υπολογίζεται ΕΔΩ και όχι στο template: το φιλτράρισμα πλειάδων μέσα σε
    Jinja θα ήταν δυσανάγνωστο, και έτσι η «ποια ομάδα είναι ανοιχτή» είναι
    ένας κανόνας σε ένα σημείο.
    """
    groups = []
    for key, label, icon, items in NAV_GROUPS:
        visible = [
            {"endpoint": e, "icon": i, "label": lb, "on": e == endpoint}
            for e, i, lb, admin_only in items
            if not admin_only or is_admin
        ]
        if not visible:
            continue
        groups.append({
            "key": key,
            "label": label,
            "icon": icon,
            "items": visible,
            "open": any(v["on"] for v in visible),
        })
    return groups


# Ελληνικές ετικέτες των events για το Audit log. Το όνομα κλάσης μένει ως
# τεχνικό στοιχείο δίπλα. Νέο event χωρίς ετικέτα → σκάει το
# tests/test_event_labels.py (στο UI πέφτει στο όνομα κλάσης).
EVENT_LABELS = {
    "PersonRegistered": "Καταχώρηση προσώπου",
    "PersonDetailsCorrected": "Διόρθωση στοιχείων προσώπου",
    "PersonDetailsChanged": "Μεταβολή στοιχείων προσώπου",
    "EmploymentStarted": "Πρόσληψη",
    "EmploymentHireDetailsCorrected": "Διόρθωση φόρμας πρόσληψης",
    "EmploymentDeleted": "Διαγραφή απασχόλησης (εκ παραδρομής)",
    "EmployeeTerminated": "Αποχώρηση εργαζομένου",
    "ContractChanged": "Αλλαγή σύμβασης",
    "LeaveRecorded": "Καταχώρηση άδειας",
    "LeaveCancelled": "Ακύρωση άδειας",
    "LeaveRescheduled": "Διόρθωση άδειας",
    "SicknessBenefitRecorded": "Επίδομα ασθενείας ΕΦΚΑ",
    "ChildcareReductionSet": "Μειωμένο ωράριο φροντίδας τέκνου",
    "ChildBirthRecorded": "Τοκετός (μείωση εισφοράς μητέρας)",
    "ErganiSubmissionRecorded": "Υποβολή ΕΡΓΑΝΗ",
    "CompanyRegistered": "Στοιχεία εργοδότη",
    "CompanyBranchDefined": "Παράρτημα εταιρείας",
    "CompanySpecialtyDefined": "Ειδικότητα εταιρείας",
    "KPKDefined": "Ορισμός ΚΠΚ",
    "KPKRatesDefined": "Ποσοστά ΚΠΚ",
    "WageAdjustmentApplied": "Προσαρμογή στον κατώτατο",
    "InsuranceProfileAssigned": "Ασφαλιστικό προφίλ",
    "APDFileGenerated": "Παραγωγή αρχείου ΑΠΔ",
    "WorkScheduleAssigned": "Εβδομαδιαίο ωράριο",
    "DailyScheduleAssigned": "Ημερήσιο πρόγραμμα",
    "EmploymentScheduleModeSet": "Τύπος προγράμματος",
    "ScheduleDeclaredToErgani": "Δήλωση ωραρίου στο ΕΡΓΑΝΗ",
    "ScheduleDaysSubmitted": "Υποβολή ημερήσιων προγραμμάτων ΕΡΓΑΝΗ",
    "ScheduleSystemDeclared": "Σύστημα δήλωσης αλλαγών ωραρίου",
    "RetroScheduleSubmitted": "Απολογιστική δήλωση ωραρίου",
    "LeaveDeclarationSubmitted": "Δήλωση αδειών ΕΡΓΑΝΗ",
    "AttendanceRecorded": "Παρουσία ημέρας",
    "CardPunchRecorded": "Χτύπημα κάρτας",
    "CardPunchCancelled": "Ακύρωση χτυπήματος κάρτας",
    "CardMonthChecked": "Ολοκλήρωση ελέγχου κάρτας μήνα",
    "CardPeriodChecked": "Εισαγωγή & έλεγχος κάρτας περιόδου",
    "MonthPayBasisChosen": "Τρόπος υπολογισμού μήνα (πρόγραμμα/κάρτα)",
    "RetroAbsenceReasonSet": "Λόγος απουσίας (απολογιστικό)",
    "AttendanceSheetApproved": "Έγκριση συγκεντρωτικού παρουσιών",
    "MonthlyPayrollCalculated": "Υπολογισμός μισθοδοσίας",
    "GiftCalculated": "Υπολογισμός Δώρου",
    "LeaveAllowanceCalculated": "Υπολογισμός Επιδόματος Αδείας",
    "APDSubmitted": "Υποβολή ΑΠΔ",
    "FMYFileGenerated": "Παραγωγή αρχείου ΦΜΥ",
    "FMYSubmitted": "Υποβολή ΦΜΥ",
    "PaymentMade": "Πληρωμή",
    "MinimumWageDefined": "Παράμετρος: κατώτατος μισθός",
    "EfkaRatesDefined": "Παράμετρος: ΕΦΚΑ / πλαφόν",
    "TekaRatesDefined": "Παράμετρος: ΤΕΚΑ",
    "TaxScaleDefined": "Παράμετρος: φορολογική κλίμακα",
    "SickLeaveRulesDefined": "Παράμετρος: κανόνες ασθενείας",
    "AllowanceRulesDefined": "Παράμετρος: προσαυξήσεις κατώτατου",
    "LaborSubsidyDefined": "Παράμετρος: επιδότηση εργατικής εισφοράς",
    "MotherSubsidyDefined": "Παράμετρος: μείωση εισφοράς μητέρας",
    "YouthSubsidyDefined": "Παράμετρος: επιδότηση εισφοράς νέων έως 25",
    "PremiumExemptionDefined": "Παράμετρος: απαλλαγή εισφορών προσαυξήσεων",
    "SeveranceRulesDefined": "Παράμετρος: αποζημίωση απόλυσης",
    "WorkHourRulesDefined": "Παράμετρος: όρια & προσαυξήσεις ωρών",
    "ObligationTypeConfigured": "Ρύθμιση τύπου υποχρέωσης",
    "ObligationExtended": "Παράταση προθεσμίας",
    "ObligationScheduled": "Προγραμματισμός υποχρέωσης",
    "ObligationFulfilled": "Εκπλήρωση υποχρέωσης",
    "PayrollPeriodReopened": "Επανάνοιγμα περιόδου μισθοδοσίας",
    "SubmissionWithdrawn": "Ανάκληση υποβολής",
    "MiscSettingsChanged": "Αλλαγή προτιμήσεων",
    "LeaveCompensationCalculated": "Αποζημίωση μη ληφθείσας άδειας",
    "SeveranceCalculated": "Αποζημίωση απόλυσης",
}
