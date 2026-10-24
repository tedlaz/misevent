"""
ΜΗΤΡΩΟ ΤΥΠΩΝ ΥΠΟΧΡΕΩΣΕΩΝ — μία δηλωτική εγγραφή ανά τύπο.

Πριν από αυτό το αρχείο, κάθε νέος τύπος υποχρέωσης απαιτούσε αλλαγές σε
πέντε σημεία (scheduler, dependencies, labels, actions, queries). Τώρα ό,τι
είναι **δεδομένο** ζει εδώ:

  - πότε γεννιέται     (`spawn`)
  - τι προθεσμία έχει  (`due`)
  - τι προαπαιτεί      (`requires`)
  - ποιο event την κλείνει (`closed_by`)
  - ετικέτα/εικονίδιο για το UI
  - αν επιδέχεται παράταση (`extension`) — προς το παρόν μόνο δήλωση

Ό,τι είναι **συμπεριφορά** μένει κώδικας: η εντολή που εκτελεί η
ενεργοποίηση (`webapp/actions.py`) και το preview (`webapp/queries.py`).
Εδώ αποθηκεύεται μόνο το ΟΝΟΜΑ της εντολής (`command`).

⚠️ Το config ΔΕΝ είναι αναδρομικό: οι ήδη προγραμματισμένες υποχρεώσεις
έχουν την προθεσμία τους γραμμένη μέσα στο `ObligationScheduled` event και
δεν ξαναγράφονται. Αλλαγή κανόνα προθεσμίας αφορά ό,τι γεννιέται από εδώ
και μπρος. Οι ΕΞΑΡΤΗΣΕΙΣ όμως είναι παράγωγες (υπολογίζονται κατά την
ανάγνωση), οπότε αλλαγή στο `requires` επηρεάζει αμέσως και τα παλιά.
"""

from calendar import monthrange
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from misevent.rules.calendar_gr import last_working_day_of_month, orthodox_easter

# --------------------------------------------------------------- Λεξιλόγιο
#
# ΠΕΡΙΟΔΟΣ: (year, month). Οι ετήσιες υποχρεώσεις έχουν month = 0.
#
# `spawn` — πότε γεννιέται ο τύπος:
#   "period"      μαζί με κάθε μισθολογική περίοδο (κυλιόμενα)
#   "period_teka" όπως το "period", αλλά ΜΟΝΟ αν υπάρχει ασφαλισμένος ΤΕΚΑ
#   "period_card" όπως το "period", αλλά ΜΟΝΟ με «Διάφορα → digital_card»
#   "annual"      μία φορά τον χρόνο, με το άνοιγμα περιόδου του έτους
#   "reactive"    από domain event (π.χ. πρώτη κανονική άδεια)
#   "on_source"   όταν ΚΛΕΙΣΕΙ ο τύπος `payout_source` (καταβολές Δώρων)
#
# `due` — κανόνας προθεσμίας (κλειστό λεξιλόγιο, βλ. `due_date()`):
#   ("eom", k)          τελευταία εργάσιμη της περιόδου + k μήνες
#   ("eom_cal", k)      τελευταία ΗΜΕΡΟΛΟΓΙΑΚΗ ημέρα της περιόδου + k μήνες
#                       (28/29/30/31 — ακόμη κι αν είναι αργία/Σαββατοκύριακο)
#   ("fixed", mm, dd)   σταθερή ημερομηνία μέσα στο έτος
#   ("easter_minus", k) k ημέρες πριν το Ορθόδοξο Πάσχα (Μ. Τετάρτη = 4)
#   ("as_source",)      ίδια με την υποχρέωση-πηγή (καταβολές)
#
# `requires` — προαπαιτούμενα ως (ΤΥΠΟΣ, selector[, only_month]):
#   "same"        ίδια περίοδος (y, m)
#   "prev"        προηγούμενη περίοδος
#   "year"        η ετήσια του ίδιου έτους (y, 0)
#   "prev_year"   η ετήσια του προηγούμενου έτους (y-1, 0)
#   "month:N"     η περίοδος (y, N) — π.χ. μισθοδοσία Μαρτίου
#   only_month=N  το προαπαιτούμενο ισχύει ΜΟΝΟ για περίοδο μήνα N
#
# `track` — ΣΕ ΠΟΙΑ ΣΤΗΛΗ του χρονολογίου εμφανίζεται (webapp: 3 στήλες):
#   "payroll"    μισθολογικές (παρουσίες, μισθοδοσία, Δώρα, επιδόματα)
#   "submission" υποβολές σε φορείς (ΑΠΔ ΕΦΚΑ/ΤΕΚΑ, ΦΜΥ)
#   "payment"    καταβολές (μισθοί, εισφορές, ΦΜΥ, Δώρα)
# Είναι ΔΕΔΟΜΕΝΟ, όχι συμπεριφορά: νέος τύπος = μία λέξη εδώ, καμία αλλαγή
# στο UI. Ο έλεγχος γίνεται στο validate() (import time).


@dataclass(frozen=True)
class ObligationType:
    code: str
    label: str
    icon: str
    spawn: str
    due: tuple
    command: str  # όνομα εντολής (υλοποίηση: webapp/actions.py)
    requires: tuple = ()
    # Το προαπαιτούμενο μετράει ΑΚΟΜΗ ΚΙ ΑΝ δεν έχει προγραμματιστεί ακόμη
    # (τα Δώρα: η μισθοδοσία-trigger μπορεί να είναι μήνες μπροστά)
    strict: bool = False
    payout_source: str = ""  # για spawn="on_source"
    track: str = "payroll"  # στήλη χρονολογίου: payroll|submission|payment
    # Παράταση προθεσμίας: δηλώνεται εδώ, την επιβάλλει το command layer
    # (`services.extend_obligation`). Κλειδιά: `allowed`, `max_days` και
    # `within_period_year` — «ποτέ πέρα από το τέλος του έτους της περιόδου»,
    # για υποχρεώσεις που δεν έχουν νόημα σε επόμενη χρήση (Επίδομα Αδείας).
    extension: dict = field(default_factory=lambda: {"allowed": False})


def _t(code, label, icon, spawn, due, command, **kw):
    return ObligationType(code, label, icon, spawn, due, command, **kw)


TYPES: dict[str, ObligationType] = {t.code: t for t in [
    # --------------------------------------------------- Μηνιαία αλυσίδα
    # Ψηφιακή κάρτα: εισαγωγή του αρχείου κάρτας της περιόδου + έλεγχος
    # αρτιότητας ΟΛΩΝ (`svc.check_card_period`) — μη-strict προαπαιτούμενο
    # της έγκρισης: χωρίς digital_card δεν γεννιέται, άρα δεν μπλοκάρει.
    _t("CARD_IMPORT", "Εισαγωγή κάρτας περιόδου", "💳",
       spawn="period_card", due=("eom", 0), command="check_card_period",
       requires=(("MONTHLY_PAYROLL", "prev"),),
       extension={"allowed": True, "max_days": 10}),

    _t("ATTENDANCE_APPROVAL", "Έγκριση παρουσιών", "⏱️",
       spawn="period", due=("eom", 0), command="approve_attendance",
       requires=(("MONTHLY_PAYROLL", "prev"), ("CARD_IMPORT", "same")),
       extension={"allowed": True, "max_days": 10}),

    _t("MONTHLY_PAYROLL", "Υπολογισμός μισθοδοσίας", "🧮",
       # Τέλος ΗΜΕΡΟΛΟΓΙΑΚΟΥ μήνα, όχι η τελ. εργάσιμη: ο μήνας μισθοδοσίας
       # κλείνει στις 30/31 (28/29 τον Φεβρουάριο) — ποτέ νωρίτερα.
       spawn="period", due=("eom_cal", 0), command="run_monthly_payroll",
       # Η προσαρμογή ΓΣΣΕ υπάρχει ΜΟΝΟ σε περιόδους αλλαγής κατώτατου —
       # μη-strict: δεν μπλοκάρει όταν δεν έχει προγραμματιστεί
       requires=(("ATTENDANCE_APPROVAL", "same"),
                 ("WAGE_ADJUSTMENT", "same"))),

    _t("SALARY_PAYMENT", "Πληρωμή μισθών", "💶", track="payment",
       spawn="period", due=("eom", 0), command="pay_salaries",
       requires=(("MONTHLY_PAYROLL", "same"),)),

    _t("APD_SUBMISSION", "Υποβολή ΑΠΔ e-ΕΦΚΑ", "📤", track="submission",
       spawn="period", due=("eom", 1), command="submit_apd_efka",
       requires=(
           ("MONTHLY_PAYROLL", "same"),
           # Δώρα/Επίδομα της ΠΡΟΗΓΟΥΜΕΝΗΣ περιόδου μπαίνουν σε αυτό το αρχείο
           ("LEAVE_ALLOWANCE", "prev"),
           ("CHRISTMAS_GIFT", "prev_year", 1),
           ("EASTER_GIFT", "year", 5),
       )),

    _t("EFKA_PAYMENT", "Καταβολή εισφορών ΕΦΚΑ", "🏛️", track="payment",
       spawn="period", due=("eom", 1), command="pay_efka",
       requires=(("APD_SUBMISSION", "same"),)),

    _t("APD_TEKA_SUBMISSION", "Υποβολή ΑΠΔ ΤΕΚΑ", "📤", track="submission",
       spawn="period_teka", due=("eom", 1), command="submit_apd_teka",
       requires=(
           ("MONTHLY_PAYROLL", "same"),
           # Η ΑΠΔ ΤΕΚΑ υποβάλλεται ΜΕΤΑ την ΑΠΔ e-ΕΦΚΑ της ίδιας περιόδου
           ("APD_SUBMISSION", "same"),
           ("LEAVE_ALLOWANCE", "prev"),
           ("CHRISTMAS_GIFT", "prev_year", 1),
           ("EASTER_GIFT", "year", 5),
       )),

    _t("TEKA_PAYMENT", "Καταβολή εισφορών ΤΕΚΑ", "🏦", track="payment",
       spawn="period_teka", due=("eom", 1), command="pay_teka",
       requires=(("APD_TEKA_SUBMISSION", "same"),)),

    _t("FMY_SUBMISSION", "Δήλωση απόδοσης ΦΜΥ", "🧾", track="submission",
       spawn="period", due=("eom", 2), command="submit_fmy",
       requires=(("MONTHLY_PAYROLL", "same"),)),

    _t("FMY_PAYMENT", "Καταβολή ΦΜΥ", "🏛️", track="payment",
       spawn="period", due=("eom", 2), command="pay_fmy",
       requires=(("FMY_SUBMISSION", "same"),)),

    # ------------------------------------------------------------- Δώρα
    # Η προσαρμογή ΓΣΣΕ ΠΡΟΗΓΕΙΤΑΙ των Δώρων/Επιδόματος όταν η νέα ΓΣΣΕ
    # τίθεται σε ισχύ μέσα στην περίοδο πληρωμής τους (ΓΣΣΕ: 1η Απριλίου →
    # Δώρο Πάσχα· 1η Δεκεμβρίου → Δώρο Χριστουγέννων), ώστε το ποσό να
    # υπολογιστεί στους ΠΡΟΣΑΡΜΟΣΜΕΝΟΥΣ μισθούς. Το WAGE_ADJUSTMENT είναι
    # CONDITIONAL: μετράει ΜΟΝΟ αν όντως αλλάζει η ΓΣΣΕ εκείνη την περίοδο.
    # Προθεσμία = η ημέρα ΚΑΤΑΒΟΛΗΣ: **Μεγάλη Τετάρτη** = Πάσχα − 4 (ΟΧΙ −3,
    # που είναι η Μ. Πέμπτη). Πάνω σε αυτήν πατά και η ημερομηνία αναφοράς του
    # μισθού (15 ημέρες πριν — `payroll_engine.easter_gift_wage_date`).
    _t("EASTER_GIFT", "Δώρο Πάσχα", "🥚",
       spawn="annual", due=("easter_minus", 4), command="run_easter_gifts",
       requires=(("MONTHLY_PAYROLL", "month:3"),
                 ("WAGE_ADJUSTMENT", "month:4")), strict=True),

    _t("CHRISTMAS_GIFT", "Δώρο Χριστουγέννων", "🎄",
       spawn="annual", due=("fixed", 12, 21), command="run_christmas_gifts",
       requires=(("MONTHLY_PAYROLL", "month:11"),
                 ("WAGE_ADJUSTMENT", "month:12")), strict=True),

    # Επίδομα Αδείας: αντιδραστικό — γεννιέται με την ΠΡΩΤΗ κανονική άδεια
    # κάθε εργαζόμενου, στην περίοδο (μήνα) εκείνης της άδειας. Αν η ΓΣΣΕ
    # αλλάζει τον ΙΔΙΟ μήνα, η προσαρμογή προηγείται (ίδια περίοδος).
    # ΠΑΡΑΤΑΣΗ: το ποσό του 1ου/2ου έτους στηρίζεται στην παραδοχή ότι ο
    # εργαζόμενος θα εργαστεί έως 31/12 — ο χρήστης μπορεί να ΑΝΑΒΑΛΕΙ τον
    # υπολογισμό μέχρι να το ξέρει, το πολύ ως το τέλος του έτους.
    # ⚠️ Όσο μένει ανοιχτή, μπλοκάρει την ΑΠΔ της επόμενης περιόδου (είναι
    # προαπαιτούμενό της) — η αναβολή είναι συνειδητή επιλογή, όχι ανώδυνη.
    _t("LEAVE_ALLOWANCE", "Επίδομα Αδείας", "🏖️",
       spawn="reactive", due=("eom", 0), command="run_leave_allowance",
       requires=(("WAGE_ADJUSTMENT", "same"),),
       extension={"allowed": True, "max_days": 365,
                  "within_period_year": True}),

    # Προσαρμογή ΓΣΣΕ: αντιδραστικό — γεννιέται στην περίοδο όπου τίθεται σε
    # ισχύ νέος κατώτατος (config.MINIMUM_WAGE_HISTORY). Ο χρήστης το
    # ενεργοποιεί: όσοι ενεργοί αμείβονται κάτω από το προσωπικό τους όριο
    # προσαρμόζονται στο όριο με ContractChanged (+ Μεταβολή ΕΡΓΑΝΗ).
    _t("WAGE_ADJUSTMENT", "Προσαρμογή στον κατώτατο μισθό", "⚖️",
       spawn="reactive", due=("eom", 0),
       command="apply_minimum_adjustments"),

    # Απολογιστική δήλωση ωραρίου: αντιδραστικό — γεννιέται με την ΕΓΚΡΙΣΗ
    # ΠΑΡΟΥΣΙΩΝ ενός μήνα που ήταν σε απολογιστικό σύστημα (ScheduleSystem-
    # Declared). Η έγκριση ΕΤΟΙΜΑΖΕΙ τις ημέρες (κλείνει αμέσως αν δεν
    # υπάρχει απόκλιση)· η ενεργοποίηση τις ΥΠΟΒΑΛΛΕΙ στο ΕΡΓΑΝΗ, έως το
    # τέλος του ΕΠΟΜΕΝΟΥ ημερολογιακού μήνα (ΥΑ 32689/2025, άρθρο 2Α Ε).
    _t("RETRO_SCHEDULE", "Υποβολή απολογιστικού ΕΡΓΑΝΗ", "🕘", track="submission",
       spawn="reactive", due=("eom_cal", 1), command="submit_retro_schedule",
       requires=(("ATTENDANCE_APPROVAL", "same"),)),

    # Δήλωση αδειών (WTOLeave): αντιδραστικό — γεννιέται με την καταχώρηση
    # άδειας, για κάθε μήνα που καλύπτει. Απολογιστική, έως το τέλος του
    # ΕΠΟΜΕΝΟΥ ημερολογιακού μήνα· μετά την έγκριση παρουσιών (ο μήνας
    # έκλεισε, οι άδειες είναι οριστικές). Η δηλωμένη άδεια κλειδώνει.
    _t("LEAVE_DECLARATION", "Δήλωση αδειών ΕΡΓΑΝΗ", "🏖️", track="submission",
       spawn="reactive", due=("eom_cal", 1), command="submit_leave_declaration",
       requires=(("ATTENDANCE_APPROVAL", "same"),)),

    # ------------------------------------------- Καταβολές Δώρων/Επιδόματος
    _t("EASTER_GIFT_PAYMENT", "Καταβολή Δώρου Πάσχα", "💶", track="payment",
       spawn="on_source", payout_source="EASTER_GIFT",
       due=("as_source",), command="pay_easter_gift",
       requires=(("EASTER_GIFT", "same"),)),

    _t("CHRISTMAS_GIFT_PAYMENT", "Καταβολή Δώρου Χριστουγέννων", "💶", track="payment",
       spawn="on_source", payout_source="CHRISTMAS_GIFT",
       due=("as_source",), command="pay_christmas_gift",
       requires=(("CHRISTMAS_GIFT", "same"),)),

    _t("LEAVE_ALLOWANCE_PAYMENT", "Καταβολή Επιδόματος Αδείας", "💶", track="payment",
       spawn="on_source", payout_source="LEAVE_ALLOWANCE",
       due=("as_source",), command="pay_leave_allowance",
       requires=(("LEAVE_ALLOWANCE", "same"),)),
]}


# ------------------------------------------- Overrides από events (UI)
# Το event `ObligationTypeConfigured` υπερκαλύπτει τα ΔΕΔΟΜΕΝΑ μιας εγγραφής
# (κανόνα προθεσμίας, πολιτική παράτασης) — τα γεμίζει το projection, όπως
# τα overrides παραμέτρων του config. Η ΣΥΜΠΕΡΙΦΟΡΑ (spawn/requires/command)
# μένει στον κώδικα. ΔΕΝ είναι αναδρομικά: αφορούν ό,τι προγραμματίζεται
# από εδώ και μπρος (οι παλιές υποχρεώσεις κρατούν την προθεσμία τους).
_OVERRIDES: dict[str, dict] = {}

DUE_KINDS = {
    "eom": ("Τελ. εργάσιμη περιόδου + k μήνες", 1),
    "eom_cal": ("Τελ. ημερολογιακή ημέρα περιόδου + k μήνες", 1),
    "fixed": ("Σταθερή ημερομηνία (μήνας, ημέρα)", 2),
    "easter_minus": ("k ημέρες πριν το Πάσχα", 1),
}


def set_override(code: str, cfg: dict) -> None:
    _OVERRIDES[code] = cfg


def clear_overrides() -> None:
    _OVERRIDES.clear()


def spec(code: str) -> ObligationType:
    """Η ενεργή εγγραφή του τύπου: μητρώο + τυχόν override από event."""
    base = TYPES[code]
    o = _OVERRIDES.get(code)
    if not o:
        return base
    return replace(
        base,
        due=tuple(o["due"]) if o.get("due") else base.due,
        extension=o.get("extension") or base.extension,
    )


def is_overridden(code: str) -> bool:
    return bool(_OVERRIDES.get(code))


def validate_due_rule(rule: tuple) -> None:
    """Έλεγχος κανόνα προθεσμίας πριν γραφτεί ObligationTypeConfigured —
    το κλειστό λεξιλόγιο του due_date(). ValueError με ελληνικό μήνυμα."""
    if not rule or rule[0] not in DUE_KINDS:
        raise ValueError(
            "Άγνωστος κανόνας προθεσμίας — επιτρέπονται: "
            + ", ".join(DUE_KINDS)
        )
    kind, args = rule[0], rule[1:]
    if len(args) != DUE_KINDS[kind][1]:
        raise ValueError(f"Ο κανόνας «{kind}» θέλει {DUE_KINDS[kind][1]} αριθμό/ούς")
    if any(not isinstance(a, int) for a in args):
        raise ValueError("Οι παράμετροι του κανόνα προθεσμίας είναι ακέραιοι")
    if kind in ("eom", "eom_cal") and not (0 <= args[0] <= 12):
        raise ValueError("Μετατόπιση μηνών εκτός ορίων (0–12)")
    if kind == "fixed" and not (1 <= args[0] <= 12 and 1 <= args[1] <= 28):
        raise ValueError("Σταθερή ημερομηνία εκτός ορίων (μήνας 1–12, ημέρα 1–28)")
    if kind == "easter_minus" and not (0 <= args[0] <= 60):
        raise ValueError("Ημέρες πριν το Πάσχα εκτός ορίων (0–60)")


# ----------------------------------------------------- Παράγωγοι πίνακες
def by_spawn(kind: str) -> list[ObligationType]:
    return [t for t in TYPES.values() if t.spawn == kind]


PAYOUT_SOURCE = {
    t.code: t.payout_source for t in TYPES.values() if t.payout_source
}
STRICT_TYPES = {t.code for t in TYPES.values() if t.strict}
# Τύποι που ΔΕΝ προγραμματίζονται πάντα (υπό συνθήκη): reactive (άδεια/ΓΣΣΕ),
# period_teka (μόνο με ασφ. ΤΕΚΑ), on_source (μόνο αν υπολογιστεί η πηγή).
# Ως ΠΡΟΑΠΑΙΤΟΥΜΕΝΑ μετράνε ΜΟΝΟ όταν υπάρχουν — ΠΟΤΕ ως phantom «pending»:
# αλλιώς ένας strict τύπος (Δώρα) θα μπλόκαρε παντοτινά περιμένοντας κάτι που
# ίσως δεν προκύψει ποτέ (π.χ. προσαρμογή ΓΣΣΕ σε χρονιά χωρίς αλλαγή).
CONDITIONAL_TYPES = {
    t.code for t in TYPES.values() if t.spawn not in ("period", "annual")
}
# Οι στήλες του χρονολογίου, με τη σειρά εμφάνισης — ΜΙΑ πηγή αλήθειας
# (κώδικας: `track` κάθε τύπου· UI: webapp/labels.py -> obligations.html).
TRACKS = (
    ("payroll", "Μισθολογικές", "🧮"),
    ("submission", "Υποβολές", "📤"),
    ("payment", "Πληρωμές", "💶"),
)
TRACK_OF = {t.code: t.track for t in TYPES.values()}

LABELS = {t.code: t.label for t in TYPES.values()}
ICONS = {t.code: t.icon for t in TYPES.values()}


# ------------------------------------------------------------ Υπολογισμοί
def _shift(y: int, m: int, k: int) -> tuple[int, int]:
    total = (y * 12 + (m - 1)) + k
    return total // 12, total % 12 + 1


# Τύποι που εμφανίζονται ΠΡΩΤΟΙ μέσα στον μήνα της προθεσμίας τους: η
# προσαρμογή στον κατώτατο προηγείται πάντα (παρουσίες, μισθοδοσία, Δώρα).
FIRST_IN_MONTH = frozenset({"WAGE_ADJUSTMENT"})


def sort_key(ob) -> tuple:
    """Σειρά εμφάνισης υποχρεώσεων: κατά προθεσμία, με τα `FIRST_IN_MONTH`
    στην κορυφή του μήνα τους."""
    d = ob.due_date
    return (d.year, d.month, ob.obligation_type not in FIRST_IN_MONTH,
            d, ob.obligation_type)


def due_date(code: str, y: int, m: int, source_due: date | None = None) -> date:
    """Η προθεσμία της υποχρέωσης, από τον ΕΝΕΡΓΟ κανόνα `due` του τύπου
    (μητρώο + τυχόν override από ObligationTypeConfigured)."""
    rule = spec(code).due
    kind = rule[0]
    if kind == "eom":
        ny, nm = _shift(y, m or 12, rule[1])
        return last_working_day_of_month(ny, nm)
    if kind == "eom_cal":
        ny, nm = _shift(y, m or 12, rule[1])
        return date(ny, nm, monthrange(ny, nm)[1])
    if kind == "fixed":
        return date(y, rule[1], rule[2])
    if kind == "easter_minus":
        return orthodox_easter(y) - timedelta(days=rule[1])
    if kind == "as_source":
        if source_due is None:
            raise ValueError(f"{code}: απαιτείται η προθεσμία της πηγής")
        return source_due
    raise ValueError(f"Άγνωστος κανόνας προθεσμίας: {rule}")


def prerequisite_keys(code: str, y: int, m: int) -> list[tuple[str, int, int]]:
    """Τα κλειδιά (τύπος, έτος, μήνας) που πρέπει να έχουν ολοκληρωθεί."""
    spec = TYPES.get(code)
    if spec is None:
        return []
    keys = []
    for req in spec.requires:
        rtype, sel = req[0], req[1]
        only_month = req[2] if len(req) > 2 else None
        if only_month is not None and m != only_month:
            continue
        if sel == "same":
            keys.append((rtype, y, m))
        elif sel == "prev":
            py, pm = _shift(y, m, -1) if m else (y - 1, 12)
            keys.append((rtype, py, pm))
        elif sel == "year":
            keys.append((rtype, y, 0))
        elif sel == "prev_year":
            keys.append((rtype, y - 1, 0))
        elif sel.startswith("month:"):
            keys.append((rtype, y, int(sel.split(":")[1])))
        else:
            raise ValueError(f"{code}: άγνωστος selector «{sel}»")
    return keys


def validate() -> None:
    """Έλεγχος συνέπειας του μητρώου: άγνωστοι τύποι, κύκλοι.

    Καλείται κατά το import — ένα typo δεν πρέπει να κλειδώνει σιωπηλά
    ολόκληρη την αλυσίδα."""
    for code, spec in TYPES.items():
        for req in spec.requires:
            if req[0] not in TYPES:
                raise ValueError(f"{code}: άγνωστο προαπαιτούμενο «{req[0]}»")
        if spec.spawn == "on_source" and spec.payout_source not in TYPES:
            raise ValueError(f"{code}: άγνωστη πηγή «{spec.payout_source}»")
        if spec.track not in {t[0] for t in TRACKS}:
            raise ValueError(f"{code}: άγνωστη στήλη «{spec.track}»")

    # Κύκλοι στον γράφο των ΤΥΠΩΝ (αγνοώντας περιόδους: ένας κύκλος εδώ θα
    # ήταν κύκλος και στις υποχρεώσεις της ίδιας περιόδου)
    WHITE, GREY, BLACK = 0, 1, 2
    color = dict.fromkeys(TYPES, WHITE)

    def visit(code, path):
        color[code] = GREY
        for req in TYPES[code].requires:
            # Το "prev"/"month:N" δείχνει σε ΑΛΛΗ περίοδο -> δεν κάνει κύκλο
            if req[1] != "same":
                continue
            nxt = req[0]
            if color[nxt] == GREY:
                raise ValueError(
                    "Κύκλος εξαρτήσεων: " + " -> ".join([*path, code, nxt])
                )
            if color[nxt] == WHITE:
                visit(nxt, [*path, code])
        color[code] = BLACK

    for code in TYPES:
        if color[code] == WHITE:
            visit(code, [])


validate()
