"""
Παραμετροποίηση συντελεστών μισθοδοσίας.

ΠΡΟΣΟΧΗ: Οι συντελεστές ΕΦΚΑ και η φορολογική κλίμακα αλλάζουν συχνά
(π.χ. μειώσεις εισφορών 2024/2025, νέα φορολογική κλίμακα 2026).
Οι συντελεστές έχουν ημερομηνία ισχύος ώστε αναδρομικοί επανυπολογισμοί
να χρησιμοποιούν τους σωστούς συντελεστές της κάθε περιόδου.

➜ ΠΗΓΗ ΑΛΗΘΕΙΑΣ είναι το ΜΗΤΡΩΟ ΠΑΡΑΜΕΤΡΩΝ από events (σελίδα «Παράμετροι»
του UI — events *Defined ανά οικογένεια). Οι λίστες αυτού του αρχείου είναι
ΠΡΟΤΕΙΝΟΜΕΝΕΣ ΑΡΧΙΚΕΣ ΤΙΜΕΣ: προσυμπληρώνουν το wizard πρώτου τρεξίματος.
Μόλις μια οικογένεια αποκτήσει events, ΑΥΤΑ υπερισχύουν πλήρως — το
projection ενημερώνει το παρόν module μέσω set_overrides() (παράγωγο state,
ξαναχτίζεται με replay).
"""

from dataclasses import dataclass, fields
from datetime import date

TRIENNIUM_FREEZE_START = date(2012, 2, 14)
TRIENNIUM_FREEZE_RESUME = date(2024, 1, 1)

# ---------------------------------------------------------------- Overrides
# family -> λίστα εγγραφών (dataclasses με valid_from) από τα events.
# Γεμίζει ΜΟΝΟ από το projection (projections.py) — ποτέ χειροκίνητα.
_OVERRIDES: dict[str, list] = {}


def set_overrides(family: str, entries: list) -> None:
    """Θέτει τις ενεργές εγγραφές μιας οικογένειας (από τα events)."""
    _OVERRIDES[family] = sorted(entries, key=lambda x: x.valid_from)


def clear_overrides() -> None:
    """Καθαρισμός πριν από πλήρες replay (rebuild των projections)."""
    _OVERRIDES.clear()


def effective_entries(family: str) -> tuple[list, str]:
    """(εγγραφές σε ισχύ, πηγή): από events αν υπάρχουν, αλλιώς config."""
    ov = _OVERRIDES.get(family)
    if ov:
        return ov, "events"
    return FAMILY_DEFAULTS[family], "config"


def gr_date(d) -> str:
    """Ημερομηνία σε ΕΛΛΗΝΙΚΗ μορφή `ΗΗ/ΜΜ/ΕΕΕΕ` — ΜΙΑ πηγή αλήθειας για τα
    μηνύματα του πυρήνα (στο UI την ίδια δουλειά κάνει το φίλτρο `date_gr`).
    ΠΟΤΕ μην τυπώνεις ημερομηνία με σκέτο `str()`/ISO σε κείμενο χρήστη."""
    return d.strftime("%d/%m/%Y") if isinstance(d, date) else str(d or "—")


def _pick(family: str, d: date, err: str):
    entries, _src = effective_entries(family)
    applicable = [x for x in entries if x.valid_from <= d]
    if not applicable:
        raise ValueError(err)
    best = max(applicable, key=lambda x: x.valid_from)
    # ⚠️ ΚΑΤΑΡΓΗΜΕΝΟΣ ΚΑΝΟΝΑΣ: ΣΚΑΕΙ, ΔΕΝ ΥΠΟΧΩΡΕΙ ΣΤΗΝ ΠΡΟΗΓΟΥΜΕΝΗ ΓΡΑΜΜΗ.
    # Μια σιωπηλή υποχώρηση θα σήμαινε μισθοδοσία με κανόνα που ο χρήστης
    # δήλωσε ρητά ότι έπαψε να ισχύει — λάθος ποσά, χωρίς κανένα μήνυμα.
    if getattr(best, "repealed", False):
        raise ValueError(f"{err} — ο κανόνας καταργήθηκε από {gr_date(best.valid_from)}")
    return best


def active_entries(family: str) -> list:
    """Οι εγγραφές μιας οικογένειας ΧΩΡΙΣ τις καταργήσεις — για όποιον
    διατρέχει το ιστορικό (π.χ. ο scheduler για τις αλλαγές ΓΣΣΕ), ώστε μια
    κατάργηση να μη γεννήσει υποχρέωση προσαρμογής."""
    entries, _src = effective_entries(family)
    return [e for e in entries if not getattr(e, "repealed", False)]


@dataclass(frozen=True)
class EfkaRates:
    valid_from: date
    employee: float  # ποσοστό εργαζομένου (μικτή ασφάλιση ΙΚΑ/ΕΦΚΑ)
    employer: float  # ποσοστό εργοδότη
    monthly_cap: float  # πλαφόν ασφαλιστέων αποδοχών / μήνα
    # ΚΑΤΑΡΓΗΣΗ κανόνα από αυτή την ημερομηνία (βλ. events.py). Το `_pick`
    # σκάει αντί να υποχωρήσει σιωπηλά στην προηγούμενη γραμμή.
    repealed: bool = False


@dataclass(frozen=True)
class TaxBracket:
    up_to: float  # άνω όριο κλιμακίου (float('inf') για το τελευταίο)
    rate: float


@dataclass(frozen=True)
class TaxScale:
    """Καθεστώς ΦΜΥ «standard»: ενιαία κλίμακα + μείωση φόρου βάσει τέκνων.

    Κάθε καθεστώς υπολογισμού είναι ΑΥΤΟΤΕΛΗΣ ΚΛΑΣΗ με κοινή διεπαφή
    `annual_tax()` (registry: TAX_MODELS). Νέος φορολογικός νόμος με
    διαφορετική ΛΟΓΙΚΗ (όχι απλώς άλλες τιμές) = νέα κλάση + εγγραφή στο
    TAX_MODELS — καμία αλλαγή στα υπάρχοντα καθεστώτα."""

    valid_from: date
    brackets: tuple
    # Μείωση φόρου βάσει τέκνων (άρθ. 16 ΚΦΕ) — 0,1,2,3,4 τέκνα, +extra ανά επιπλέον
    reductions: tuple = (777.0, 900.0, 1120.0, 1340.0, 1580.0)
    extra_child_reduction: float = 220.0
    # Η μείωση φθίνει 20€ ανά 1000€ εισοδήματος άνω των 12.000€ (έως 5 τέκνα εξαίρεση)
    phaseout_threshold: float = 12000.0
    phaseout_per_1000: float = 20.0
    # ΚΑΤΑΡΓΗΣΗ κανόνα από αυτή την ημερομηνία (βλ. events.py). Το `_pick`
    # σκάει αντί να υποχωρήσει σιωπηλά στην προηγούμενη γραμμή.
    # ⚠️ ΠΑΝΤΑ ΤΕΛΕΥΤΑΙΟ ΠΕΔΙΟ: η σειρά μετράει σε θετική κατασκευή.
    repealed: bool = False

    model_key = "standard"
    model_label = "Ενιαία κλίμακα + μείωση τέκνων"

    # -- κοινή διεπαφή καθεστώτων -----------------------------------------
    def bracket_rate(self, index: int, children: int, age: int | None) -> float:
        """Ο συντελεστής του κλιμακίου `index` — hook για καθεστώτα όπου οι
        συντελεστές εξαρτώνται από τέκνα/ηλικία (βλ. TaxScaleYouth2026)."""
        return self.brackets[index].rate

    def annual_tax_detail(
        self,
        annual_taxable: float,
        children: int,
        birth_date: date | None = None,
        on: date | None = None,
    ) -> dict:
        """ΤΟ ΙΔΙΟ με την `annual_tax()`, αλλά επιστρέφει ΟΛΑ τα ενδιάμεσα
        βήματα (κλιμάκιο-κλιμάκιο, μείωση, απομείωση) — ώστε η απόδειξη/
        ανάλυση να δείχνει ΠΩΣ προέκυψε ο φόρος, χωρίς να ξαναγράφει τη
        λογική. ΜΙΑ πηγή αλήθειας: η `annual_tax()` καλεί αυτήν εδώ."""
        age = None
        if birth_date is not None and on is not None:
            # Ηλικία που συμπληρώνεται μέσα στο φορολογικό έτος
            age = on.year - birth_date.year
        bands, tax, lower = [], 0.0, 0.0
        for i, br in enumerate(self.brackets):
            if annual_taxable > lower:
                rate = self.bracket_rate(i, children, age)
                base = min(annual_taxable, br.up_to) - lower
                bands.append(
                    {
                        "from": lower,
                        # None = τελευταίο («και άνω») — το UI/PDF δεν χειρίζεται inf
                        "to": None if br.up_to == float("inf") else br.up_to,
                        "rate": rate,
                        "base": round(base, 2),
                        "tax": round(base * rate, 2),
                    }
                )
                tax += base * rate
                lower = br.up_to
            else:
                break
        # Μείωση φόρου βάσει τέκνων
        idx = min(children, len(self.reductions) - 1)
        reduction = base_reduction = self.reductions[idx]
        extra = 0.0
        if children > len(self.reductions) - 1:
            extra = (children - (len(self.reductions) - 1)) * self.extra_child_reduction
            reduction += extra
        # Σταδιακή απομείωση άνω του ορίου (εξαίρεση 5+ τέκνα)
        phaseout = 0.0
        if annual_taxable > self.phaseout_threshold and children < 5:
            over = (annual_taxable - self.phaseout_threshold) / 1000.0
            phaseout = min(reduction, over * self.phaseout_per_1000)
            reduction = max(0.0, reduction - over * self.phaseout_per_1000)
        return {
            "model": self.model_key,
            "model_label": self.model_label,
            "valid_from": self.valid_from,
            "annual_taxable": round(annual_taxable, 2),
            "children": children,
            "age": age,
            "bands": bands,
            "tax_before_reduction": round(tax, 2),
            "base_reduction": round(base_reduction, 2),
            "extra_child_reduction": round(extra, 2),
            "phaseout_threshold": self.phaseout_threshold,
            "phaseout_per_1000": self.phaseout_per_1000,
            "phaseout": round(phaseout, 2),
            "reduction": round(reduction, 2),
            "total": max(0.0, tax - reduction),
        }

    def annual_tax(
        self,
        annual_taxable: float,
        children: int,
        birth_date: date | None = None,
        on: date | None = None,
    ) -> float:
        """Ετήσιος φόρος του καθεστώτος. `birth_date`: για καθεστώτα με
        παράμετρο ηλικίας (άγνωστη γέννηση = ενήλικος συντελεστής)."""
        return self.annual_tax_detail(annual_taxable, children, birth_date, on)["total"]


@dataclass(frozen=True)
class TaxScaleYouth2026(TaxScale):
    """Καθεστώς ΦΜΥ «youth2026» — κλίμακα από 1/1/2026 (data/taxscales2026.md):
    οι συντελεστές των ΤΡΙΩΝ ΠΡΩΤΩΝ κλιμακίων εξαρτώνται από τα ΤΕΚΝΑ, και
    των δύο πρώτων επιπλέον από την ΗΛΙΚΙΑ του εργαζομένου.

    - **1ο κλιμάκιο** (0–10.000): 9%, αλλά **0% με 4+ τέκνα** (περ. γ).
    - **2ο κλιμάκιο** (10.000–20.000): 20/18/16/9% για 0/1/2/3 τέκνα,
      **0% με 4+ τέκνα** (περ. β + γ).
    - **Ηλικία** στα δύο πρώτα κλιμάκια (περ. ε): **έως 25 ετών → 0%**,
      **26–30 → έως 9%**.
    - **3ο κλιμάκιο** (20.000–30.000): 26/24/22/20/18% για 0–4 τέκνα και
      **−2 μονάδες ανά επιπλέον τέκνο** άνω των 4 (περ. δ) — ΧΩΡΙΣ επίδραση
      ηλικίας.
    - Τα υπόλοιπα κλιμάκια (30–40, 40–60, >60) είναι σταθερά.

    ⚠️ Οι προεπιλεγμένες τιμές είναι ΕΝΔΕΙΚΤΙΚΕΣ — επιβεβαιώστε τις από την
    ΑΑΔΕ πριν από παραγωγική χρήση και καταχωρήστε τις από τη σελίδα
    «Παράμετροι» (event TaxScaleDefined με model="youth2026")."""

    # Συντελεστές ανά αριθμό τέκνων (0,1,2,3,4+) — η τελευταία τιμή ισχύει
    # και για περισσότερα τέκνα (εκτός του 3ου κλιμακίου, βλ. band3_step).
    band1_child_rates: tuple = (0.09, 0.09, 0.09, 0.09, 0.0)
    band2_child_rates: tuple = (0.20, 0.18, 0.16, 0.09, 0.0)
    band3_child_rates: tuple = (0.26, 0.24, 0.22, 0.20, 0.18)
    # Περαιτέρω μείωση του 3ου κλιμακίου ανά τέκνο άνω των 4 (περ. δε)
    band3_extra_child_step: float = 0.02
    band1_index: int = 0  # 0–10 χιλ.
    band2_index: int = 1  # 10–20 χιλ.
    band3_index: int = 2  # 20–30 χιλ.
    youth_zero_max_age: int = 25  # έως 25: μηδενικός συντελεστής (1ο+2ο)
    youth_low_max_age: int = 30  # 26–30: μειωμένος συντελεστής (1ο+2ο)
    youth_low_rate: float = 0.09

    model_key = "youth2026"
    model_label = "Κλίμακα 2026 (συντελεστές ανά τέκνα/ηλικία)"

    @staticmethod
    def _child_rate(rates: tuple, children: int, step: float = 0.0):
        """Ο συντελεστής ενός κλιμακίου για `children` τέκνα (None = δεν
        παραμετροποιείται). `step` = περαιτέρω μείωση ανά επιπλέον τέκνο."""
        if not rates:
            return None
        last = len(rates) - 1
        n = max(children, 0)
        rate = rates[min(n, last)]
        if step and n > last:
            rate -= (n - last) * step
        return max(0.0, rate)

    def bracket_rate(self, index: int, children: int, age: int | None) -> float:
        if index == self.band1_index:
            rate = self._child_rate(self.band1_child_rates, children)
        elif index == self.band2_index:
            rate = self._child_rate(self.band2_child_rates, children)
        elif index == self.band3_index:
            rate = self._child_rate(
                self.band3_child_rates, children, self.band3_extra_child_step
            )
        else:
            return self.brackets[index].rate
        if rate is None:
            rate = self.brackets[index].rate
        # Ηλικία: ΜΟΝΟ στα δύο πρώτα κλιμάκια (περ. ε)
        if age is not None and index in (self.band1_index, self.band2_index):
            if age <= self.youth_zero_max_age:
                rate = min(rate, 0.0)
            elif age <= self.youth_low_max_age:
                rate = min(rate, self.youth_low_rate)
        return rate


# Registry καθεστώτων ΦΜΥ: κλειδί (TaxScaleDefined.model) -> κλάση.
# Η ΛΟΓΙΚΗ κάθε καθεστώτος είναι κώδικας — ο χρήστης επιλέγει από τη σελίδα
# «Παράμετροι» ΠΟΙΟ καθεστώς ισχύει από πότε και με ποιες ΤΙΜΕΣ.
TAX_MODELS: dict[str, type] = {
    TaxScale.model_key: TaxScale,
    TaxScaleYouth2026.model_key: TaxScaleYouth2026,
}


def tax_model_param_fields(model: str) -> list[str]:
    """Τα ΕΠΙΠΛΕΟΝ πεδία παραμέτρων ενός καθεστώτος (πέραν του standard)."""
    cls = TAX_MODELS[model]
    base = {f.name for f in fields(TaxScale)}
    return [f.name for f in fields(cls) if f.name not in base]


# --- Ενδεικτικές τιμές (ΕΠΙΒΕΒΑΙΩΣΤΕ πριν από χρήση) ---
EFKA_HISTORY = [
    EfkaRates(
        valid_from=date(2025, 1, 1),
        employee=0.1337,
        employer=0.2179,
        monthly_cap=7572.62,
    ),
]

TAX_HISTORY = [
    TaxScale(
        valid_from=date(2025, 1, 1),
        brackets=(
            TaxBracket(10000, 0.09),
            TaxBracket(20000, 0.22),
            TaxBracket(30000, 0.28),
            TaxBracket(40000, 0.36),
            TaxBracket(float("inf"), 0.44),
        ),
    ),
    # Κλίμακα 2026 (data/taxscales2026.md): νέα κλιμάκια 40–60/>60 και
    # συντελεστές των τριών πρώτων κλιμακίων ανά τέκνα/ηλικία. Η μείωση
    # φόρου φτάνει τα 1.780 € στα 5 τέκνα (+220 € για κάθε επόμενο).
    TaxScaleYouth2026(
        valid_from=date(2026, 1, 1),
        brackets=(
            TaxBracket(10000, 0.09),
            TaxBracket(20000, 0.20),
            TaxBracket(30000, 0.26),
            TaxBracket(40000, 0.34),
            TaxBracket(60000, 0.39),
            TaxBracket(float("inf"), 0.44),
        ),
        reductions=(777.0, 900.0, 1120.0, 1340.0, 1580.0, 1780.0),
    ),
]

# Πόσοι "μισθοί" τον χρόνο για την αναγωγή του ΦΜΥ (12 + Δώρα + Επ. Αδείας = 14)
ANNUAL_SALARIES = 14

# Προσαύξηση Δώρων κατά τον συντελεστή αδείας
GIFT_LEAVE_COEFFICIENT = 0.04166


def efka_rates_for(d: date) -> EfkaRates:
    return _pick("efka", d, f"Δεν υπάρχουν συντελεστές ΕΦΚΑ για {gr_date(d)}")


def tax_scale_for(d: date) -> TaxScale:
    return _pick("tax", d, f"Δεν υπάρχει φορολογική κλίμακα για {gr_date(d)}")


# --- Επικουρική ασφάλιση ΤΕΚΑ (νέοι ασφαλισμένοι από 1/1/2022) ---
# ⚠️ ΔΕΝ ΥΠΑΡΧΕΙ ΟΙΚΟΓΕΝΕΙΑ ΠΑΡΑΜΕΤΡΩΝ «teka». Τα ποσοστά της επικουρικής
# έρχονται ΠΑΝΤΑ από το ΚΠΚ της κάλυψης (Μητρώο ΚΠΚ, π.χ. 4169) — όπως και
# του ΕΦΚΑ. Υπήρχε καρτέλα με «fallback συντελεστές ΤΕΚΑ» που ΔΕΝ τη διάβαζε
# κανένας υπολογισμός: ζητούσε από τον χρήστη τιμές που δεν χρησιμοποιούνταν
# πουθενά. Το event `TekaRatesDefined` μένει στο μητρώο τύπων ΜΟΝΟ για να
# διαβάζονται παλιά logs (το replay το αγνοεί).


# --- Ασφαλιστικά ταμεία (καλύψεις ειδικοτήτων) ---
# Κάθε ειδικότητα δηλώνει ΠΑΚΕΤΑ ΥΠΟΒΟΛΗΣ με τριάδα ΚΑΔ/ειδικότητα/ΚΠΚ.
# Μπορεί να έχει πολλές εναλλακτικές του ίδιου fund με `when` selectors
# (π.χ. 0101 όταν auxiliary=efka, 0103 όταν auxiliary=teka).
#
# Το κλειδί κάθε FUNDS entry ΑΠΟΣΥΝΘΕΤΕΤΑΙ σε τρεις ΑΝΕΞΑΡΤΗΤΟΥΣ άξονες:
#   - `branch`  = ΚΛΑΔΟΣ ασφάλισης (τι ασφαλίζει): main (κύρια σύνταξη+υγεία,
#                 υποχρεωτική), auxiliary (επικουρική σύνταξη), welfare (πρόνοια/
#                 εφάπαξ).
#   - `carrier` = ΦΟΡΕΑΣ (ποιος το διαχειρίζεται): efka / teka / other. Ο
#                 φορέας καθορίζει ΠΟΥ υποβάλλεται (βλ. `apd`).
#   - `apd`     = σε ποιο αρχείο ΑΠΔ γράφεται: "efka" | "teka" | None (εκτός
#                 ΑΠΔ, π.χ. ταμεία μηχανικών — οι κρατήσεις υπολογίζονται στη
#                 μισθοδοσία, η υποβολή γίνεται εκτός συστήματος).
# «Ενσωματωμένο στην κύρια» = ΑΠΛΩΣ ΑΠΟΥΣΙΑ ξεχωριστής κάλυψης για τον κλάδο
# (το κόστος είναι μέσα στο ΚΠΚ της κύριας). Νέος συνδυασμός κλάδου/φορέα =
# ΜΙΑ νέα εγγραφή εδώ — η δρομολόγηση στην ΑΠΔ και οι κρατήσεις διαβάζουν
# αυτά τα metadata, δεν κάνουν hardcoded έλεγχο στο κλειδί.
BRANCHES = {
    "main": "Κύρια (σύνταξη + υγεία)",
    "auxiliary": "Επικουρική σύνταξη",
    "welfare": "Πρόνοια / εφάπαξ",
}

# Αναλυτικοί κλάδοι που μπορούν να συνθέτουν ένα ΚΠΚ. Τα κλειδιά γράφονται
# και μέσα στο `data/init_parameters.json` (ανάλυση κλάδων ανά ΚΠΚ), άρα
# παραμένουν σταθερά και ASCII.
INSURANCE_COMPONENTS = {
    "main_pension": "Κύρια σύνταξη",
    "healthcare": "Υγειονομική περίθαλψη",
    "auxiliary": "Επικουρική ασφάλιση",
    "dypa": "Συνεισπραττόμενα / ΔΥΠΑ",
    "welfare": "Πρόνοια / εφάπαξ",
    "heavy": "Βαρέα",
    "other": "Λοιπή εισφορά",
}
CARRIERS = {
    "efka": "ΕΦΚΑ",
    "teka": "ΤΕΚΑ",
    "other": "Άλλο ταμείο",
}

# --- ΒΑΘΜΙΔΑ (tier) ενός ΚΠΚ ---
# Η ασφάλιση έχει τρεις βαθμίδες (κύρια / επικουρική / πρόνοια), αλλά η κύρια
# και η επικουρική μπορούν να ΠΑΚΕΤΑΡΙΣΤΟΥΝ σε ΕΝΑ ΚΠΚ του e-ΕΦΚΑ (0101).
# Γι' αυτό οι βαθμίδες των ΚΠΚ είναι ΤΕΣΣΕΡΙΣ. Η βαθμίδα είναι στοιχείο
# ΤΑΥΤΟΤΗΤΑΣ του ΚΠΚ (δεν αλλάζει με τον χρόνο) και καθορίζει σε ποιον ΡΟΛΟ
# πακέτου ειδικότητας επιτρέπεται να μπει.
TIERS = {
    "main": "Κύρια",
    "auxiliary": "Επικουρική",
    "main_auxiliary": "Κύρια + Επικουρική",
    "welfare": "Πρόνοια",
}

# ➜ ΚΑΔ/ΕΙΔΙΚΟΤΗΤΑ: ΖΟΥΝ ΠΑΝΤΑ ΣΤΗΝ ΕΙΔΙΚΟΤΗΤΑ, ΓΙΑ ΚΑΘΕ ΒΑΘΜΙΔΑ.
#   Η τριάδα ΚΑΔ/ειδικότητα/ΚΠΚ της ΑΠΔ (No 31/35/37) εξαρτάται από το
#   ΕΠΑΓΓΕΛΜΑ και τη δραστηριότητα — το ΚΠΚ είναι μόνο το πακέτο κάλυψης
#   (ποιος εισπράττει, τι βαθμίδα, τι ποσοστά) και ΔΕΝ κουβαλά κωδικούς.


# Φορέας + βαθμίδα -> κλειδί FUNDS (πού δρομολογείται η κάλυψη). Το ΤΕΚΑ
# επιτρέπει ΜΟΝΟ επικουρική — ο κανόνας επιβάλλεται από την ΑΠΟΥΣΙΑ των
# υπόλοιπων συνδυασμών εδώ (καμία ξεχωριστή συνθήκη στον κώδικα).
FUND_BY_CARRIER_TIER = {
    ("efka", "main"): "efka_main",
    ("efka", "main_auxiliary"): "efka_main",
    ("efka", "auxiliary"): "efka_aux",
    ("efka", "welfare"): "efka_welfare",
    ("teka", "auxiliary"): "teka",
}


def fund_for(carrier: str, tier: str) -> str:
    """Το ταμείο (κλειδί FUNDS) ενός ΚΠΚ από φορέα + βαθμίδα."""
    try:
        return FUND_BY_CARRIER_TIER[(carrier, tier)]
    except KeyError:
        raise ValueError(
            f"Ο φορέας «{CARRIERS.get(carrier, carrier)}» δεν επιτρέπει "
            f"βαθμίδα «{TIERS.get(tier, tier)}»"
        ) from None


def tiers_for_carrier(carrier: str) -> list[str]:
    """Οι βαθμίδες που επιτρέπει ένας φορέας (για τα dropdowns του UI)."""
    return [t for t in TIERS if (carrier, t) in FUND_BY_CARRIER_TIER]


# --- ΡΟΛΟΙ ΠΑΚΕΤΩΝ ΕΙΔΙΚΟΤΗΤΑΣ ---
# Κάθε ειδικότητα δηλώνει ΕΝΑ πακέτο ανά ΡΟΛΟ. Ο ρόλος ορίζει (α) ποια
# βαθμίδα ΚΠΚ δέχεται, (β) πού δρομολογείται και (γ) ΠΟΤΕ ισχύει για τον
# εργαζόμενο — ο selector `when` ΠΑΡΑΓΕΤΑΙ από τον ρόλο, δεν γράφεται πια
# στο χέρι. Έτσι η επίλυση (resolve_coverages) μένει η ίδια, γενική.
@dataclass(frozen=True)
class SpecialtyRole:
    label: str
    tier: str  # η βαθμίδα ΚΠΚ που πρέπει να έχει το πακέτο
    fund: str  # κλειδί FUNDS όπου δρομολογείται
    when: dict  # συνθήκη επιλογής για τον εργαζόμενο


SPECIALTY_ROLES = {
    # Επικουρική στον e-ΕΦΚΑ: κύρια + επικουρική σε ΕΝΑ πακέτο.
    "MAIN_EPIK": SpecialtyRole(
        "Κύρια + Επικουρική ΕΦΚΑ", "main_auxiliary", "efka_main", {"auxiliary": "efka"}
    ),
    # Επικουρική στο ΤΕΚΑ: κύρια ΜΟΝΗ ΤΗΣ + ξεχωριστό πακέτο ΤΕΚΑ.
    "MAIN": SpecialtyRole("Κύρια ΕΦΚΑ", "main", "efka_main", {"auxiliary": "teka"}),
    "TEKA": SpecialtyRole(
        "Επικουρική ΤΕΚΑ", "auxiliary", "teka", {"auxiliary": "teka"}
    ),
    # Πρόνοια: διαφοροποιημένη (νέοι/παλαιοί) ή ενιαία.
    "PRON_NEW": SpecialtyRole(
        "Πρόνοια (νέοι)",
        "welfare",
        "efka_welfare",
        {"welfare": "yes", "insurance_class": "new"},
    ),
    "PRON_OLD": SpecialtyRole(
        "Πρόνοια (παλαιοί)",
        "welfare",
        "efka_welfare",
        {"welfare": "yes", "insurance_class": "old"},
    ),
    "PRON_UNIFORM": SpecialtyRole(
        "Πρόνοια", "welfare", "efka_welfare", {"welfare": "yes"}
    ),
}

# Το πακέτο ΤΕΚΑ είναι ΕΝΑ και ΣΤΑΘΕΡΟ για κάθε ειδικότητα (ΚΑΔ/ειδικότητα/ΚΠΚ
# της ΑΠΔ ΤΕΚΑ) — δεν το δηλώνει ο χρήστης· το βάζει το `define_specialty`.
TEKA_PACKAGE = {"kad": "0083", "specialty_code": "000860", "kpk": "4169"}

# Ποιοι ρόλοι ενεργοποιούνται ανά φορέα επικουρικής (βαθμίδες 1+2).
CARRIER_ROLES = {
    "efka": ("MAIN_EPIK",),
    "teka": ("MAIN", "TEKA"),
}
# Ποιος ρόλος πρόνοιας (βαθμίδα 3) ανά επιλογή του εργαζομένου.
PRON_ROLES = {"new": "PRON_NEW", "old": "PRON_OLD", "uniform": "PRON_UNIFORM"}


def role_when(role: str) -> dict:
    """Η ΠΑΡΑΓΟΜΕΝΗ συνθήκη επιλογής ενός ρόλου (αντίγραφο — δεν πειράζεται
    το μητρώο)."""
    return dict(SPECIALTY_ROLES[role].when)


def active_roles(epik_carrier: str, pron: dict, pron_choice: str) -> list[str]:
    """Οι ενεργοί ρόλοι για έναν συγκεκριμένο εργαζόμενο.

    `pron_choice`: "new" | "old" | "yes" | "no" (η επιλογή πρόνοιας)."""
    roles = list(CARRIER_ROLES.get(epik_carrier, ()))
    if (pron or {}).get("enabled") and pron_choice != "no":
        if (pron or {}).get("differentiated"):
            roles.append(PRON_ROLES.get(pron_choice, "PRON_NEW"))
        else:
            roles.append("PRON_UNIFORM")
    return roles


# Ρυθμίσεις προνοιακής ασφάλισης μιας ειδικότητας (βαθμίδα 3).
PRON_DEFAULTS = {
    "enabled": False,  # η ειδικότητα έχει καθόλου πρόνοια;
    "name": "",  # ονομασία, π.χ. «Πρόνοια Εμπόρων»
    "mandatory": True,  # ο εργαζόμενος ΔΕΝ μπορεί να επιλέξει «Όχι»
    "differentiated": True,  # ξεχωριστοί κωδικοί νέων / παλαιών
}


def normalize_pron(pron) -> dict:
    """Πλήρες dict ρυθμίσεων πρόνοιας από μερική/άδεια είσοδο."""
    src = dict(pron or {})
    out = dict(PRON_DEFAULTS)
    for key, default in PRON_DEFAULTS.items():
        if key not in src:
            continue
        out[key] = str(src[key]).strip() if isinstance(default, str) else bool(src[key])
    return out


def role_label(role: str, pron_name: str = "") -> str:
    """Ετικέτα ρόλου· οι προνοιακοί παίρνουν την ονομασία της ειδικότητας."""
    base = SPECIALTY_ROLES[role].label
    if not pron_name or SPECIALTY_ROLES[role].tier != "welfare":
        return base
    suffix = {"PRON_NEW": " (νέοι)", "PRON_OLD": " (παλαιοί)"}.get(role, "")
    return pron_name + suffix


@dataclass(frozen=True)
class FundSpec:
    label: str  # ετικέτα UI
    apd: str | None  # "efka" | "teka" | None (εκτός ΑΠΔ)
    branch: str = "main"  # κλειδί του BRANCHES (κλάδος ασφάλισης)
    carrier: str = "efka"  # κλειδί του CARRIERS (φορέας)
    required: bool = False  # η κύρια ΕΦΚΑ είναι υποχρεωτική σε κάθε ειδικότητα
    # ΔΗΛΩΤΙΚΟΣ κανόνας εφαρμοσιμότητας ανά εργαζόμενο: κενό = ισχύει για
    # όλους· αλλιώς όνομα «σημαίας» (flag) του εργαζομένου που πρέπει να είναι
    # αληθής για να ισχύσει η κάλυψη (π.χ. teka -> "teka_member": νέοι
    # ασφαλισμένοι από 2022). Νέος κανόνας = μόνο νέα σημαία εδώ + πέρασμά της
    # στην πρόσληψη — η επίλυση (resolve_coverages) είναι γενική.
    applies_if: str = ""


FUNDS = {
    "efka_main": FundSpec(
        "ΕΦΚΑ κύρια", apd="efka", branch="main", carrier="efka", required=True
    ),
    "efka_aux": FundSpec(
        "ΕΦΚΑ επικουρική",
        apd="efka",
        branch="auxiliary",
        carrier="efka",
        applies_if="efka_aux_member",
    ),
    "teka": FundSpec(
        "ΤΕΚΑ", apd="teka", branch="auxiliary", carrier="teka", applies_if="teka_member"
    ),
    "efka_welfare": FundSpec(
        "ΕΦΚΑ πρόνοια/εφάπαξ", apd="efka", branch="welfare", carrier="efka"
    ),
    "other": FundSpec("Άλλο ταμείο", apd=None, branch="welfare", carrier="other"),
}


# ΑΠΔ No 36 «Ειδική περίπτωση ασφάλισης» της ΚΥΡΙΑΣ εγγραφής. Οι κανόνες
# εξετάζουν τον λογικό κλάδο και το αρχείο ΑΠΔ κάθε ΠΡΟΣΘΕΤΟΥ πακέτου:
#   auxiliary + teka -> 09 (επικουρική ΤΕΚΑ, ξεχωριστή ΑΠΔ)
#   auxiliary + efka -> 10 (χωριστή επικουρική μέσα στον e-ΕΦΚΑ)
#   welfare   + efka -> 10 (πρόνοια/εφάπαξ μέσα στον e-ΕΦΚΑ, π.χ. ΤΑΠΙΤ)
# Η σειρά είναι ρητή προτεραιότητα: αν συνυπάρχουν ΤΕΚΑ και πρόνοια ΕΦΚΑ,
# υπερισχύει το 09. Νέος συνδυασμός = μόνο μία εγγραφή εδώ.
APD_SPECIAL_CASE_RULES: tuple[dict, ...] = (
    {"branch": "auxiliary", "apd": "teka", "code": "09"},
    {"branch": "auxiliary", "apd": "efka", "code": "10"},
    {"branch": "welfare", "apd": "efka", "code": "10"},
)


def insurance_special_case(coverages) -> str:
    """No 36 από τα metadata των πρόσθετων επιλυμένων πακέτων."""
    targets = {
        (fund_branch(c.get("fund", "")), fund_apd(c.get("fund", "")))
        for c in coverages or ()
    }
    return next(
        (
            rule["code"]
            for rule in APD_SPECIAL_CASE_RULES
            if (rule["branch"], rule["apd"]) in targets
        ),
        "00",
    )


def fund_branch(fund: str) -> str:
    """Ο κλάδος (branch) μιας γνωστής κάλυψης."""
    return FUNDS[fund].branch


def fund_carrier(fund: str) -> str:
    """Ο φορέας (carrier) μιας γνωστής κάλυψης."""
    return FUNDS[fund].carrier


def fund_apd(fund: str) -> str | None:
    """Το αρχείο ΑΠΔ όπου γράφεται η κάλυψη (efka/teka/None)."""
    return FUNDS[fund].apd


def funds_by_branch(branch: str) -> list[str]:
    """Τα κλειδιά FUNDS ενός κλάδου, με τη σειρά δήλωσης."""
    return [k for k, v in FUNDS.items() if v.branch == branch]


def coverage_carriers(coverages, branch: str) -> list[str]:
    """Οι ΦΟΡΕΙΣ (ετικέτες CARRIERS) των καλύψεων ενός κλάδου, χωρίς
    επαναλήψεις — π.χ. επικουρική με ΕΦΚΑ + ΤΕΚΑ -> ["ΕΦΚΑ", "ΤΕΚΑ"].
    Data-driven (κανένας έλεγχος σε συγκεκριμένο κλειδί ταμείου): το UI
    δείχνει έτσι τι ισχύει για τον εργαζόμενο ανά κλάδο ασφάλισης."""
    out: list[str] = []
    for c in coverages or ():
        fund = (c or {}).get("fund", "")
        if fund_branch(fund) != branch:
            continue
        label = CARRIERS.get(fund_carrier(fund), fund_carrier(fund))
        if label not in out:
            out.append(label)
    return out


def coverage_applies(fund: str, flags: dict, when: dict | None = None) -> bool:
    """Αν η κάλυψη του `fund` ισχύει για εργαζόμενο με τις δοσμένες «σημαίες».
    Κενό applies_if = ισχύει πάντα· αλλιώς πρέπει η αντίστοιχη σημαία να είναι
    αληθής (π.χ. teka -> flags["teka_member"])."""
    # Οι νέες ειδικότητες έχουν δηλωτικούς selectors πάνω στην ίδια την
    # κάλυψη (π.χ. {"auxiliary": "teka"}). Αυτό επιτρέπει εναλλακτικά ΚΠΚ
    # του ΙΔΙΟΥ fund χωρίς νέο hardcoded flag ή νέο FundSpec.
    if when:
        return all(str((flags or {}).get(k, "none")) == str(v) for k, v in when.items())
    spec = FUNDS.get(fund)
    cond = spec.applies_if if spec else ""
    return (not cond) or bool((flags or {}).get(cond))


def resolve_coverages(coverages, flags: dict) -> list[dict]:
    """Οι καλύψεις μιας ειδικότητας που ΙΣΧΥΟΥΝ για συγκεκριμένο εργαζόμενο,
    βάσει των δηλωτικών κανόνων applies_if (γενική επίλυση — νέος κανόνας δεν
    χρειάζεται αλλαγή κώδικα εδώ)."""
    return [
        dict(c)
        for c in coverages
        if coverage_applies(c.get("fund", ""), flags, c.get("when") or {})
    ]


# --- Μητρώο ΚΠΚ: το πακέτο κάλυψης καθορίζει τα ποσοστά κρατήσεων ---
# Κάθε ΚΠΚ (ανά ταμείο) αντιστοιχεί σε συγκεκριμένα ποσοστά εργαζομένου/
# εργοδότη, με ημερομηνία ισχύος. Η μισθοδοσία και οι ΑΠΔ υπολογίζουν τις
# κρατήσεις από το ΚΠΚ των καλύψεων του εργαζομένου.
#
# ΤΟ ΜΗΤΡΩΟ ΔΕΝ ΕΧΕΙ ΠΡΟΕΠΙΛΟΓΕΣ ΕΔΩ: κάθε εγγραφή του καταγράφεται ως
# event (`KPKRatesDefined`) — χειροκίνητα από το UI (σελίδα Παράμετροι,
# «Μητρώο ΚΠΚ») ή μαζικά από αρχείο CSV (svc.import_kpk_rates). Έτσι το
# μητρώο ξαναχτίζεται πλήρως με replay, όπως όλα τα δεδομένα.
#
# Κάθε επιλυμένο ΚΠΚ μετρά ΑΚΡΙΒΩΣ ΜΙΑ φορά. Ένα κύριο ΚΠΚ μπορεί να είναι
# σύνθετο (0101 = κύρια+υγεία+επικουρική e-ΕΦΚΑ+ΔΥΠΑ) ή χωρίς επικουρική
# (0103). Στο ΤΕΚΑ επιλύονται 0103 + 4169, όχι 0101 + 4169.
@dataclass(frozen=True)
class KPKRates:
    valid_from: date
    fund: str  # κλειδί του FUNDS
    kpk: str
    employee: float  # ποσοστό εργαζομένου
    employer: float  # ποσοστό εργοδότη
    components: tuple = ()


# --- Νόμιμος κατώτατος μισθός / ημερομίσθιο (ΓΣΣΕ / ΚΥΑ) ---
# Οι αποδοχές δεν επιτρέπεται να υπολείπονται των ορίων που ισχύουν
# για την περίοδο της μισθοδοσίας. Πηγές: ΚΥΑ 8934/2026 (ΦΕΚ Β΄1759,
# από 1/4/2026), προηγούμενες αποφάσεις 2024/2025.
@dataclass(frozen=True)
class MinimumWage:
    valid_from: date
    monthly: float  # κατώτατος μισθός υπαλλήλων (πλήρης απασχόληση)
    daily: float  # κατώτατο ημερομίσθιο εργατοτεχνιτών
    # ΚΑΤΑΡΓΗΣΗ κανόνα από αυτή την ημερομηνία (βλ. events.py). Το `_pick`
    # σκάει αντί να υποχωρήσει σιωπηλά στην προηγούμενη γραμμή.
    repealed: bool = False


MINIMUM_WAGE_HISTORY = [
    MinimumWage(valid_from=date(2024, 4, 1), monthly=830.00, daily=37.07),
    MinimumWage(valid_from=date(2025, 4, 1), monthly=880.00, daily=39.30),
    MinimumWage(valid_from=date(2026, 4, 1), monthly=920.00, daily=41.09),
]


def minimum_wage_for(d: date) -> MinimumWage:
    return _pick("minimum_wage", d, f"Δεν υπάρχει κατώτατος μισθός για {gr_date(d)}")


def minimum_wage_entries() -> list:
    """Οι εγγραφές ΓΣΣΕ/ΚΥΑ σε ισχύ — τις διαβάζει ο scheduler για να γεννήσει
    υποχρεώσεις WAGE_ADJUSTMENT και το UI για το «ξεκινά το μητρώο από».
    ⚠️ ΧΩΡΙΣ τις καταργήσεις: μια κατάργηση δεν είναι νέα ΓΣΣΕ και δεν
    πρέπει να γεννήσει υποχρέωση προσαρμογής μισθών."""
    return active_entries("minimum_wage")


# --- Άδεια ασθενείας: τριήμερο εργοδότη & ετήσιο όριο ---
# Οι πρώτες ημέρες κάθε κυλιόμενου έτους ασθένειας βαρύνουν τον εργοδότη με
# ΜΙΣΟ ημερομίσθιο. Οι υπόλοιπες, μέχρι το όριο, πληρώνονται κανονικά αλλά
# αφαιρείται το επίδομα που καταβάλλει ο ΕΦΚΑ. Πάνω από το όριο: άνευ αποδοχών.
# Το όριο εξαρτάται από την προϋπηρεσία στον ίδιο εργοδότη.
@dataclass(frozen=True)
class SickLeaveRules:
    valid_from: date
    half_wage_days: int  # τριήμερο εργοδότη — ΜΙΑ φορά ανά κυλιόμενο έτος
    limit_under_year: int  # όριο ημερών, προϋπηρεσία < 1 έτος
    limit_over_year: int  # όριο ημερών, προϋπηρεσία >= 1 έτος
    # ΚΑΤΑΡΓΗΣΗ κανόνα από αυτή την ημερομηνία (βλ. events.py). Το `_pick`
    # σκάει αντί να υποχωρήσει σιωπηλά στην προηγούμενη γραμμή.
    repealed: bool = False


SICK_LEAVE_HISTORY = [
    SickLeaveRules(
        valid_from=date(2024, 1, 1),
        half_wage_days=3,
        limit_under_year=13,
        limit_over_year=26,
    ),
]


def sick_leave_rules_for(d: date) -> SickLeaveRules:
    return _pick("sick_leave", d, f"Δεν υπάρχουν κανόνες ασθενείας για {gr_date(d)}")


# --- Προσαυξήσεις κατώτατου μισθού: τριετίες, γάμος, λοιπές (πτυχίο κ.λπ.) ---
# Ο κατώτατος της ΓΣΣΕ/ΚΥΑ προσαυξάνεται με βάση τα δηλωθέντα στην πρόσληψη:
#   - τριετίες (προϋπηρεσία): μισθωτοί έως 3 x 10%, ημερομίσθιοι έως 6 x 5%
#     (ΕΓΣΣΕ εργατοτεχνιτών) — «ωριμάζουν» και με τον χρόνο στον εργοδότη
#   - γάμος 10% (ΔΕΝ είναι υποχρεωτικός — μετρά μόνο αν δηλωθεί)
#   - λοιπές (πτυχίο κ.λπ.): ελεύθερο ποσοστό %
# Το ΚΑΤΩΤΑΤΟ ΟΡΙΟ ελέγχου = βασικός κατώτατος x (1 + άθροισμα προσαυξήσεων).
@dataclass(frozen=True)
class AllowanceRules:
    valid_from: date
    monthly_triennium_pct: float = 0.10  # προσαύξηση ανά τριετία (μισθωτοί)
    monthly_max_triennia: int = 3
    daily_triennium_pct: float = 0.05  # ανά τριετία (ημερομίσθιοι/ΕΓΣΣΕ)
    daily_max_triennia: int = 6
    marriage_pct: float = 0.10  # επίδομα γάμου (προαιρετικό)
    # ΚΑΤΑΡΓΗΣΗ κανόνα από αυτή την ημερομηνία (βλ. events.py). Το `_pick`
    # σκάει αντί να υποχωρήσει σιωπηλά στην προηγούμενη γραμμή.
    repealed: bool = False


ALLOWANCE_HISTORY = [
    AllowanceRules(valid_from=date(2024, 1, 1)),
]


def allowance_rules_for(d: date) -> AllowanceRules:
    return _pick("allowance", d, f"Δεν υπάρχουν κανόνες προσαυξήσεων για {gr_date(d)}")


# --- Επιδότηση εργατικής εισφοράς (αρ. 14 ν. 2837/2000, ΑΠΔ κωδ. 102) ---
# Όποιος αμείβεται ΑΚΡΙΒΩΣ με τον κατώτατο (+ τριετίες/γάμο) δεν πληρώνει το
# ποσοστό σύνταξης εργαζομένου πάνω στον ΣΚΕΤΟ κατώτατο· το καλύπτει η ΔΥΠΑ.
@dataclass(frozen=True)
class LaborSubsidy:
    valid_from: date
    pct: float = 0.0667  # ποσοστό εργαζομένου κλάδου σύνταξης
    # ΚΑΤΑΡΓΗΣΗ κανόνα από αυτή την ημερομηνία (βλ. events.py). Το `_pick`
    # σκάει αντί να υποχωρήσει σιωπηλά στην προηγούμενη γραμμή.
    repealed: bool = False


LABOR_SUBSIDY_HISTORY = [
    LaborSubsidy(valid_from=date(2024, 1, 1)),
]


def labor_subsidy_for(d: date) -> LaborSubsidy:
    return _pick("labor_subsidy", d, f"Δεν υπάρχει επιδότηση εργατικής εισφοράς για {gr_date(d)}")


# --- Μείωση 50% εισφοράς σύνταξης μητέρας (αρ. 141§2 ν. 3655/2008, ΑΠΔ κωδ. 103) ---
# Το δωδεκάμηνο απασχόλησης μετά τον μήνα τοκετού η μητέρα πληρώνει το ΜΙΣΟ
# ποσοστό σύνταξης εργαζομένου· η διαφορά είναι επιδότηση (No 49).
@dataclass(frozen=True)
class MotherSubsidy:
    valid_from: date
    pct: float = 0.03335  # ½ × 6,67% σύνταξης εργαζομένου, επί ασφαλιστέων
    repealed: bool = False


MOTHER_SUBSIDY_HISTORY = [
    MotherSubsidy(valid_from=date(2020, 1, 1)),
]


def mother_subsidy_for(d: date) -> MotherSubsidy:
    return _pick("mother_subsidy", d, f"Δεν υπάρχει μείωση εισφοράς μητέρας για {gr_date(d)}")


# --- Μειωμένη εργοδοτική εισφορά νέων έως 25 (αρ. 86 ν. 4583/2018, ΑΠΔ κωδ. 107) ---
@dataclass(frozen=True)
class YouthSubsidy:
    valid_from: date
    pct: float = 0.0666  # εργοδοτική σύνταξης, επί ασφαλιστέων αποδοχών
    repealed: bool = False


YOUTH_SUBSIDY_HISTORY = [
    YouthSubsidy(valid_from=date(2019, 1, 1)),
]


def youth_subsidy_for(d: date) -> YouthSubsidy:
    return _pick("youth_subsidy", d, f"Δεν υπάρχει επιδότηση εισφοράς νέων για {gr_date(d)}")


# --- Απαλλαγή εισφορών προσαυξήσεων (αρ. 41 ν. 5184/2025, Εγκ. e-ΕΦΚΑ 21/2025) ---
# Πλήρης απασχόληση: οι ΠΡΟΣΑΥΞΗΣΕΙΣ νύχτας/υπερεργασίας/υπερωρίας/Κυριακών-
# αργιών ΔΕΝ έχουν εισφορές (εργαζομένου ΚΑΙ εργοδότη) — η απλή αξία της ώρας
# έχει. Στην ΑΠΔ δηλώνεται ΜΟΝΟ το ποσό με εισφορές. Ο ΦΜΥ δεν αλλάζει.
@dataclass(frozen=True)
class PremiumExemption:
    valid_from: date
    share: float = 1.0  # μερίδιο της προσαύξησης που απαλλάσσεται
    # ΚΑΤΑΡΓΗΣΗ κανόνα από αυτή την ημερομηνία (βλ. events.py). Το `_pick`
    # σκάει αντί να υποχωρήσει σιωπηλά στην προηγούμενη γραμμή.
    repealed: bool = False


PREMIUM_EXEMPTION_HISTORY = [
    PremiumExemption(valid_from=date(2025, 11, 1)),  # μισθολογική περίοδος 11/2025
]


def premium_exemption_for(d: date) -> PremiumExemption:
    return _pick("premium_exemption", d,
                 f"Δεν υπάρχει απαλλαγή εισφορών προσαυξήσεων για {gr_date(d)}")


def freeze_span_years() -> float:
    """Το ΜΗΚΟΣ του παγώματος 14/02/2012–31/12/2023 σε δεκαδικά έτη.

    Ανώτατο όριο για τον δηλωθέντα «παγωμένο χρόνο» της πρόσληψης: κανείς δεν
    μπορεί να έχει παγωμένη προϋπηρεσία μεγαλύτερη από το ίδιο το διάστημα."""
    return (TRIENNIUM_FREEZE_RESUME - TRIENNIUM_FREEZE_START).days / 365.25


def triennia_service_years(
    declared_service_years: float,
    hire_date: date | None,
    on: date,
) -> float:
    """Μετρήσιμη προϋπηρεσία για τις τριετίες κατώτατου.

    Από 14/02/2012 έως 31/12/2023 ο χρόνος ΔΕΝ ωριμάζει τριετίες. Η
    `declared_service_years` είναι ΗΔΗ η προϋπηρεσία που μετρά στις τριετίες
    (ο παγωμένος χρόνος δηλώνεται ΧΩΡΙΣΤΑ, `frozen_service_years`, και δεν
    αθροίζεται εδώ)· αφαιρούμε μόνο την επικάλυψη του χρόνου στον τρέχοντα
    εργοδότη με το πάγωμα.
    """
    total = max(0.0, float(declared_service_years or 0.0))
    if hire_date is None or on <= hire_date:
        return max(0.0, total)

    elapsed_days = (on - hire_date).days
    frozen_start = max(hire_date, TRIENNIUM_FREEZE_START)
    frozen_end = min(on, TRIENNIUM_FREEZE_RESUME)
    frozen_days = max(0, (frozen_end - frozen_start).days)
    return max(0.0, total + (elapsed_days - frozen_days) / 365.25)


def triennia_count(total_service_years: float, max_triennia: int) -> int:
    """Αριθμός ΣΥΜΠΛΗΡΩΜΕΝΩΝ τριετιών από τη συνολική προϋπηρεσία.

    Η προϋπηρεσία στρογγυλοποιείται σε **2 δεκαδικά ΠΡΙΝ** τη διαίρεση, ώστε
    η τριετία να «ωριμάζει» σωστά στο όριο: π.χ. προϋπηρεσία 2,913 έτη + 1
    μήνας ≈ 2,9963 → 3,00 → 1 τριετία (η μηνιαία αύξηση 1/12 φτάνει το όριο).
    Χωρίς τη στρογγυλοποίηση η ακέραιη διαίρεση θα έκοβε το 2,9963 σε 0."""
    return min(int(round(total_service_years, 2) // 3), max_triennia)


def seniority_factor(
    pay_type: str, total_service_years: float, married: bool, extra_pct: float, on: date
) -> float:
    """Ο συντελεστής προσαύξησης του κατώτατου ορίου (>= 1.0).

    `total_service_years`: συνολική προϋπηρεσία (δηλωθείσα + στον τρέχοντα
    εργοδότη) κατά την ημερομηνία ελέγχου. `extra_pct`: λοιπές προσαυξήσεις
    ως ποσοστό (π.χ. 10 = 10%)."""
    r = allowance_rules_for(on)
    if pay_type == "daily":
        tri = triennia_count(total_service_years, r.daily_max_triennia)
        factor = 1.0 + tri * r.daily_triennium_pct
    else:
        tri = triennia_count(total_service_years, r.monthly_max_triennia)
        factor = 1.0 + tri * r.monthly_triennium_pct
    if married:
        factor += r.marriage_pct
    return factor + max(0.0, extra_pct) / 100.0


# --- Αποζημίωση απόλυσης (άρθρο 64 Ν.4808/2021· Ν.2112/1920, Ν.4093/2012) ---
# Μήνες αποζημίωσης ΧΩΡΙΣ προειδοποίηση ανά ΣΥΜΠΛΗΡΩΜΕΝΑ έτη υπηρεσίας στον
# ίδιο εργοδότη. ΜΕ προειδοποίηση = το ΜΙΣΟ. <1 έτος -> 0· 16+ -> 12 (πλαφόν).
# Το σύνολο προσαυξάνεται κατά 1/6 (αναλογία Δώρων/επιδόματος). ΕΝΔΕΙΚΤΙΚΟ —
# επιβεβαιώστε πριν από παραγωγική χρήση (η μεταβατική 17+ ετών ΔΕΝ έχει
# υλοποιηθεί — βλ. §8).
SEVERANCE_MONTHS = (
    (1, 2),
    (4, 3),
    (6, 4),
    (8, 5),
    (10, 6),
    (11, 7),
    (12, 8),
    (13, 9),
    (14, 10),
    (15, 11),
    (16, 12),
)
SEVERANCE_GIFT_FACTOR = 7.0 / 6.0  # προσαύξηση 1/6

# Ειδική αυτοτελής φορολόγηση αποζημίωσης απόλυσης (αφορολόγητο ως 60.000€) —
# ΕΝΔΕΙΚΤΙΚΗ κλίμακα. (όριο, συντελεστής επιπλέον κλιμακίου).
SEVERANCE_TAX_BRACKETS = (
    (60000.0, 0.0),
    (100000.0, 0.10),
    (150000.0, 0.20),
    (float("inf"), 0.30),
)


@dataclass(frozen=True)
class SeveranceRules:
    """Αποζημίωση απόλυσης ως ΧΡΟΝΙΚΑ ΚΛΙΜΑΚΩΤΗ παράμετρος.

    Ήταν hardcoded σταθερές: ένας νόμος που άλλαζε τον πίνακα απαιτούσε
    αλλαγή κώδικα, ΧΩΡΙΣ ημερομηνία ισχύος — άρα οι παλιές αποζημιώσεις
    θα ξαναϋπολογίζονταν με τον ΝΕΟ πίνακα. Τώρα κάθε αποχώρηση διαβάζει
    τον πίνακα που ίσχυε ΤΟΤΕ."""

    valid_from: date
    # ((συμπληρωμένα έτη, μήνες αποζημίωσης), …) — αύξοντα κατώφλια
    months: tuple = SEVERANCE_MONTHS
    gift_factor: float = SEVERANCE_GIFT_FACTOR  # προσαύξηση 1/6
    # ((άνω όριο, συντελεστής), …) — το τελευταίο ανοιχτό (inf)
    tax_brackets: tuple = SEVERANCE_TAX_BRACKETS
    repealed: bool = False


SEVERANCE_HISTORY = [SeveranceRules(date(2022, 1, 1))]


def severance_rules_for(d: date) -> SeveranceRules:
    return _pick("severance", d, f"Δεν υπάρχουν κανόνες αποζημίωσης για {gr_date(d)}")


def severance_months(
    service_years: float, with_notice: bool = False, on: date | None = None
) -> float:
    """Μήνες αποζημίωσης απόλυσης για τα συμπληρωμένα έτη υπηρεσίας.
    ΜΕ προειδοποίηση = το μισό· <1 έτος -> 0.

    `on` = η ημερομηνία ΑΠΟΛΥΣΗΣ, ώστε να ισχύσει ο πίνακας εκείνης της
    στιγμής. None = ο τρέχων (συμβατότητα με παλιές κλήσεις)."""
    if service_years < 1:
        return 0.0
    table = severance_rules_for(on).months if on else SEVERANCE_MONTHS
    months = 0
    for threshold, mo in table:
        if service_years >= threshold:
            months = mo
    return months / 2.0 if with_notice else float(months)


def severance_gift_factor(on: date | None = None) -> float:
    return severance_rules_for(on).gift_factor if on else SEVERANCE_GIFT_FACTOR


def severance_tax(gross: float, on: date | None = None) -> float:
    """Αυτοτελής φόρος αποζημίωσης απόλυσης (κλιμακωτά, αφορολόγητο 60.000€)."""
    brackets = severance_rules_for(on).tax_brackets if on else SEVERANCE_TAX_BRACKETS
    tax = 0.0
    prev = 0.0
    for up_to, rate in brackets:
        if gross <= prev:
            break
        tax += (min(gross, up_to) - prev) * rate
        prev = up_to
    return round(tax, 2)


def minimum_floor_value(
    pay_type: str,
    weekly_hours: float,
    on: date,
    service_years: float = 0.0,
    married: bool = False,
    extra_pct: float = 0.0,
) -> float:
    """Το κατώτατο ΝΟΜΙΜΟ όριο αμοιβής για την ημερομηνία `on`: βασικός
    κατώτατος (ΓΣΣΕ/ΚΥΑ) × αναλογία ωρών (μερική) × συντελεστή προσαυξήσεων
    (τριετίες/γάμος/λοιπές). ΜΙΑ πηγή αλήθειας — τη χρησιμοποιούν το
    `services.minimum_floor` (με περιγραφή) και ο scheduler (ωρίμανση)."""
    mw = minimum_wage_for(on)
    factor = seniority_factor(pay_type, service_years, married, extra_pct, on)
    if pay_type == "daily":
        return round(mw.daily * factor, 2)
    ratio = min(weekly_hours, 40.0) / 40.0
    return round(mw.monthly * ratio * factor, 2)


# --- Κατάταξη ωρών εργασίας & προσαυξήσεις (βλ. parousies.md, work_hours.py) ---
# Κάθε ώρα εργασίας κατατάσσεται σε: ημέρα/νύχτα x εργάσιμη/αργία x
# εντός ωραρίου/πρόσθετη/υπερεργασία/υπερωρία/παράνομη υπερωρία.
# Οι προσαυξήσεις ημέρας-τύπου (νύχτα, αργία) ΠΡΟΣΤΙΘΕΝΤΑΙ στην προσαύξηση
# του ωρομισθίου (π.χ. υπερεργασία νύχτα-αργία = 20% + 25% + 75%).
@dataclass(frozen=True)
class WorkHourRules:
    valid_from: date
    night_start: int = 22  # έναρξη νύχτας (ώρα)
    night_end: int = 6  # λήξη νύχτας (ώρα επόμενης ημέρας)
    night_premium: float = 0.25  # προσαύξηση νυχτερινής απασχόλησης
    holiday_premium: float = 0.75  # προσαύξηση Κυριακών/αργιών
    extra_premium: float = 0.12  # πρόσθετη εργασία (μερική απασχόληση, έως 40ω)
    overwork_premium: float = 0.20  # υπερεργασία (40 → 45/48 ώρες/εβδομάδα)
    overtime_premium: float = 0.40  # νόμιμη υπερωρία
    illegal_premium: float = 0.80  # παράνομη υπερωρία
    weekly_legal_5: int = 45  # νόμιμο εβδομαδιαίο ωράριο πενθημέρου
    weekly_legal_6: int = 48  # νόμιμο εβδομαδιαίο ωράριο εξαημέρου
    daily_legal_5: int = 9  # νόμιμο ημερήσιο ωράριο πενθημέρου
    daily_legal_6: int = 8  # νόμιμο ημερήσιο ωράριο εξαημέρου
    daily_overwork_start: int = 8  # πάνω από 8ω/ημέρα η επόμενη ώρα είναι
    # υπερεργασία (πενθήμερο: η 9η ώρα)· έως το ημερήσιο νόμιμο. Στο εξαήμερο
    # η ζώνη είναι κενή (8=8), οπότε η 9η ώρα πάει κατευθείαν υπερωρία.
    overtime_day_cap: int = 3  # όριο νόμιμης υπερωρίας ανά ημέρα (ώρες)
    overtime_year_cap: int = 150  # όριο νόμιμης υπερωρίας ανά έτος (ώρες)
    # ΟΡΦΑΝΑ χτυπήματα κάρτας που το ΕΡΓΑΝΗ δέχεται να συμπληρωθούν από το
    # πρόγραμμα, ανά εργαζόμενο και μήνα (`svc.complete_punch_from_schedule`)
    orphan_month_cap: int = 3
    # ΑΠΟΛΟΓΙΣΤΙΚΟ: ημέρα με ΟΡΦΑΝΟ χτύπημα δηλώνεται ΠΑΝΤΑ (συμπληρωμένη από
    # το πρόγραμμα), ακόμη κι αν συμφωνεί με το δηλωμένο. 1 = ναι, 0 = μόνο
    # όταν αποκλίνει (όπως κάθε ημέρα κάρτας).
    retro_declare_orphans: int = 1
    # ΣΤΡΟΓΓΥΛΟΠΟΙΗΣΗ της ημερήσιας διάρκειας (λεπτά· κοντινότερο πολλαπλάσιο,
    # 30: 8,2ω -> 8) ΜΟΝΟ στις ΠΡΟΤΕΙΝΟΜΕΝΕΣ ώρες του φύλλου. 0/1 = καμία —
    # προεπιλογή, αφού ο νόμος δεν προβλέπει ανοχή (Εγκ. 37271/2024).
    # Παρουσίες και ΕΡΓΑΝΗ κρατούν ΠΑΝΤΑ τις ακριβείς ώρες.
    card_rounding_minutes: int = 0
    # ΑΝΟΧΗ ΣΥΜΦΩΝΙΑΣ ΚΑΡΤΑΣ ↔ ΠΡΟΓΡΑΜΜΑΤΟΣ (λεπτά, ανά άκρο βάρδιας):
    # ημέρα εντός ανοχής (μαζί με την ευέλικτη προσέλευση) ΠΛΗΡΩΝΕΤΑΙ ΜΕ ΤΟ
    # ΠΡΟΓΡΑΜΜΑ και δεν δηλώνεται απολογιστικά· εκτός ανοχής με την κάρτα
    # (`card_punches.day_matches`, `EmploymentState.pay_source`). Πολιτική
    # ΕΤΑΙΡΕΙΑΣ — ο νόμος δεν προβλέπει ανοχή· 0 = καμία (προεπιλογή).
    card_tolerance_minutes: int = 0
    # ΚΑΤΑΡΓΗΣΗ κανόνα από αυτή την ημερομηνία (βλ. events.py). Το `_pick`
    # σκάει αντί να υποχωρήσει σιωπηλά στην προηγούμενη γραμμή.
    repealed: bool = False


WORK_HOUR_HISTORY = [
    WorkHourRules(valid_from=date(2024, 1, 1)),
]


def work_hour_rules_for(d: date) -> WorkHourRules:
    return _pick("work_hours", d, f"Δεν υπάρχουν κανόνες ωρών εργασίας για {gr_date(d)}")


# ================================================= ΜΗΤΡΩΟ ΟΙΚΟΓΕΝΕΙΩΝ ΠΑΡΑΜΕΤΡΩΝ
#
# ΜΙΑ ΔΗΛΩΤΙΚΗ ΕΓΓΡΑΦΗ ΑΝΑ ΟΙΚΟΓΕΝΕΙΑ — κατά το πρότυπο του
# `obligation_types.TYPES`. Πριν από αυτό το μητρώο, η γνώση κάθε οικογένειας
# ήταν αντιγραμμένη σε πέντε σημεία (config, events, projections, services x2,
# settings) και μια νέα οικογένεια σήμαινε πέντε συντονισμένες αλλαγές.
#
# ΕΔΩ ζει ό,τι είναι **ΔΕΔΟΜΕΝΟ**: ποια κλάση, ποιο event, ποια πεδία με τι
# τύπο/ετικέτα/όρια, ποιες αρχικές τιμές, τι λέει η κάρτα του UI.
# ΚΩΔΙΚΑΣ μένει ό,τι είναι **ΣΥΜΠΕΡΙΦΟΡΑ**: πώς υπολογίζεται ο φόρος
# (TAX_MODELS), πώς διαβάζεται ο κανόνας (`*_for`).
#
# ⚠️ ΤΟ EVENT ΑΝΑΦΕΡΕΤΑΙ ΜΕ ΤΟ ΟΝΟΜΑ ΤΟΥ (string), ΟΧΙ ΜΕ ΤΗΝ ΚΛΑΣΗ. Το
# `config.py` είναι **φύλλο** του γράφου εξαρτήσεων: δεν κάνει import τίποτε
# από τον πυρήνα. Το `events.py` όμως κάνει import ΤΟ CONFIG — άρα μια αναφορά
# σε κλάση event εδώ θα έκανε κύκλο. Η ανάλυση ονόματος→κλάση γίνεται στο
# `projections.py`, το μόνο σημείο που βλέπει και τα δύο.


# Κλειστό λεξιλόγιο τύπων πεδίου (πρότυπο: `obligation_types.DUE_KINDS`).
# `ui` = ο τύπος widget που περιμένει το `_param_modal.html` — ΜΗΝ τον
# επεκτείνεις χωρίς να αλλάξεις και το template.
FIELD_KINDS: dict[str, dict] = {
    "money": {"ui": "money", "lo": 0.0, "hi": None, "err": "Το ποσό «{}» πρέπει να είναι θετικό"},
    "pct": {"ui": "pct", "lo": 0.0, "hi": 1.0, "err": "Το ποσοστό «{}» είναι εκτός ορίων (0–100%)"},
    "premium": {"ui": "pct", "lo": 0.0, "hi": 5.0, "err": "Η προσαύξηση «{}» είναι εκτός ορίων"},
    "int": {"ui": "int", "lo": 0, "hi": None, "err": "Η τιμή «{}» δεν μπορεί να είναι αρνητική"},
    "days": {"ui": "int", "lo": 1, "hi": 366, "err": "Οι ημέρες «{}» είναι εκτός ορίων (1–366)"},
    "hour": {"ui": "int", "lo": 0, "hi": 24, "err": "Η ώρα «{}» είναι εκτός ορίων (0–24)"},
    "flag": {"ui": "int", "lo": 0, "hi": 1, "err": "Το «{}» δέχεται 1 (ναι) ή 0 (όχι)"},
}


@dataclass(frozen=True)
class ParamField:
    """Ένα πεδίο μιας οικογένειας. Το `name` ΠΡΕΠΕΙ να υπάρχει και στη
    dataclass και στο event (το επιβάλλει το `validate_families`)."""

    name: str
    label: str  # ετικέτα UI, ελληνικά
    kind: str  # κλειδί του FIELD_KINDS
    ui: bool = True  # False = υπάρχει στο event αλλά ΔΕΝ ζητείται στη φόρμα


@dataclass(frozen=True)
class ParamFamily:
    key: str
    label: str
    cls: type
    event: str  # ΟΝΟΜΑ της event class — βλ. σχόλιο παραπάνω
    defaults: list
    fields: tuple
    note: str = ""  # περιγραφή κάρτας στο /settings
    card_label: str = ""  # τίτλος κάρτας όταν διαφέρει από το `label`
    critical: bool = False  # μπλοκάρει μισθοδοσία αν δεν έχει επιβεβαιωθεί
    ui_mode: str = "generic"  # "generic" | "custom" (δική του φόρμα — tax)
    # Πεδία που ΔΕΝ ρυθμίζονται από το UI και μεταφέρονται αυτούσια από την
    # εγγραφή που ισχύει (π.χ. τα ποσοστά ΕΦΚΑ: έρχονται από τα ΚΠΚ)
    carry_over: tuple = ()
    # Διασταυρώσεις ΜΕΤΑΞΥ πεδίων: ό,τι δεν είναι όριο ενός πεδίου. Επιστρέφει
    # ελληνικό μήνυμα σφάλματος ή None. Μένει εδώ, στην ίδια εγγραφή — όχι
    # διάσπαρτο στο services.py.
    check: object = None

    def ui_fields(self) -> list:
        return [f for f in self.fields if f.ui]


def _pf(name, label, kind, ui=True):
    return ParamField(name, label, kind, ui)


FAMILIES: dict[str, ParamFamily] = {f.key: f for f in [
    ParamFamily(
        "minimum_wage", "ΓΣΣΕ / Κατώτατος μισθός", MinimumWage,
        "MinimumWageDefined", MINIMUM_WAGE_HISTORY, critical=True,
        note="νέα ΓΣΣΕ/ΚΥΑ = νέα γραμμή· γεννά αυτόματα υποχρέωση "
             "προσαρμογής (WAGE_ADJUSTMENT) στην περίοδο ισχύος",
        fields=(
            _pf("monthly", "Κατώτατος μισθός (€)", "money"),
            _pf("daily", "Κατώτατο ημερομίσθιο (€)", "money"),
        ),
    ),
    ParamFamily(
        "efka", "ΕΦΚΑ — καθολικοί συντελεστές", EfkaRates,
        "EfkaRatesDefined", EFKA_HISTORY, critical=True,
        card_label="Πλαφόν ασφαλιστέων αποδοχών (ΕΦΚΑ)",
        note="τα ποσοστά κρατήσεων έρχονται ΠΑΝΤΑ από το ΚΠΚ της κάλυψης "
             "(Μητρώο ΚΠΚ, σελίδα Παράμετροι) — εδώ μόνο το μηνιαίο πλαφόν",
        # Τα ποσοστά είναι fallback· μεταφέρονται αυτούσια από την ισχύουσα
        # εγγραφή αντί να ζητηθούν ξανά (ήταν 12 γραμμές εξαίρεσης στο settings)
        carry_over=("employee", "employer"),
        fields=(
            _pf("employee", "Ποσοστό εργαζομένου", "pct", ui=False),
            _pf("employer", "Ποσοστό εργοδότη", "pct", ui=False),
            _pf("monthly_cap", "Πλαφόν (€/μήνα)", "money"),
        ),
    ),
    ParamFamily(
        "tax", "Φορολογική κλίμακα (ΦΜΥ)", TaxScale,
        "TaxScaleDefined", TAX_HISTORY, critical=True,
        # Δική του φόρμα: κλιμάκια, μειώσεις, επιλογή καθεστώτος (TAX_MODELS).
        # Η ΛΟΓΙΚΗ κάθε καθεστώτος είναι κλάση — δεν παραμετροποιείται.
        ui_mode="custom",
        fields=(
            _pf("brackets", "Κλιμάκια", "money", ui=False),
            _pf("reductions", "Μειώσεις φόρου", "money", ui=False),
            _pf("extra_child_reduction", "Επιπλέον μείωση ανά τέκνο", "money", ui=False),
            _pf("phaseout_threshold", "Όριο απομείωσης (€)", "money", ui=False),
            _pf("phaseout_per_1000", "Απομείωση ανά 1.000€", "money", ui=False),
        ),
    ),
    ParamFamily(
        "sick_leave", "Ασθένεια (τριήμερο / όρια)", SickLeaveRules,
        "SickLeaveRulesDefined", SICK_LEAVE_HISTORY,
        note="τριήμερο εργοδότη (μισό ημερομίσθιο) + ετήσιο όριο "
             "πληρωμένων ημερών ασθενείας",
        fields=(
            _pf("half_wage_days", "Τριήμερο εργοδότη (ημέρες)", "int"),
            _pf("limit_under_year", "Όριο ημερών, προϋπηρεσία <1 έτος", "days"),
            _pf("limit_over_year", "Όριο ημερών, προϋπηρεσία ≥1 έτος", "days"),
        ),
        check=lambda v: (
            None
            if v.get("half_wage_days", 0) <= v.get("limit_under_year", 0)
            <= v.get("limit_over_year", 0)
            else "Απαιτείται: τριήμερο ≤ όριο <1 έτους ≤ όριο ≥1 έτους"
        ),
    ),
    ParamFamily(
        "allowance", "Προσαυξήσεις κατώτατου", AllowanceRules,
        "AllowanceRulesDefined", ALLOWANCE_HISTORY,
        note="προσαυξήσεις του κατώτατου ορίου: τριετίες + γάμος "
             "(οι λοιπές δηλώνονται ανά πρόσληψη)",
        fields=(
            _pf("monthly_triennium_pct", "Τριετία μισθωτών (%)", "pct"),
            _pf("monthly_max_triennia", "Μέγ. τριετίες μισθωτών", "int"),
            _pf("daily_triennium_pct", "Τριετία ημερομισθίων (%)", "pct"),
            _pf("daily_max_triennia", "Μέγ. τριετίες ημερομισθίων", "int"),
            _pf("marriage_pct", "Επίδομα γάμου (%)", "pct"),
        ),
    ),
    ParamFamily(
        "labor_subsidy", "Επιδότηση εργατικής εισφοράς", LaborSubsidy,
        "LaborSubsidyDefined", LABOR_SUBSIDY_HISTORY,
        note="αρ. 14 ν. 2837/2000: μισθός ΑΚΡΙΒΩΣ ίσος με τον κατώτατο "
             "(+ προσαυξήσεις) → ποσοστό επί του σκέτου κατώτατου, ΑΠΔ κωδ. 102",
        fields=(
            _pf("pct", "Ποσοστό επιδότησης εργαζομένου (%)", "pct"),
        ),
    ),
    ParamFamily(
        "mother_subsidy", "Μείωση εισφοράς μητέρας", MotherSubsidy,
        "MotherSubsidyDefined", MOTHER_SUBSIDY_HISTORY,
        note="αρ. 141§2 ν. 3655/2008: 12 μήνες απασχόλησης μετά τον μήνα "
             "τοκετού, ½ σύνταξης εργαζομένου, ΑΠΔ κωδ. 103",
        fields=(
            _pf("pct", "Ποσοστό επιδότησης εργαζομένης (%)", "pct"),
        ),
    ),
    ParamFamily(
        "youth_subsidy", "Επιδότηση εισφοράς νέων έως 25", YouthSubsidy,
        "YouthSubsidyDefined", YOUTH_SUBSIDY_HISTORY,
        note="αρ. 86 ν. 4583/2018: έως την ημέρα των 25ων γενεθλίων, "
             "εργοδοτική σύνταξης επί ασφαλιστέων, ΑΠΔ κωδ. 107",
        fields=(
            _pf("pct", "Ποσοστό επιδότησης εργοδότη (%)", "pct"),
        ),
    ),
    ParamFamily(
        "premium_exemption", "Απαλλαγή εισφορών προσαυξήσεων", PremiumExemption,
        "PremiumExemptionDefined", PREMIUM_EXEMPTION_HISTORY,
        note="αρ. 41 ν. 5184/2025, Εγκ. e-ΕΦΚΑ 21/2025: πλήρης απασχόληση — "
             "οι προσαυξήσεις νύχτας/υπερεργασίας/υπερωρίας/αργιών χωρίς "
             "εισφορές (η απλή αξία της ώρας έχει)",
        fields=(
            _pf("share", "Μερίδιο προσαύξησης που απαλλάσσεται (%)", "pct"),
        ),
    ),
    ParamFamily(
        "severance", "Αποζημίωση απόλυσης", SeveranceRules,
        "SeveranceRulesDefined", SEVERANCE_HISTORY,
        # Δικό της modal: δύο ΠΙΝΑΚΕΣ (μήνες ανά έτη, κλιμάκια φόρου) δεν
        # χωρούν σε λίστα βαθμωτών πεδίων — ίδιος λόγος με την κλίμακα ΦΜΥ.
        ui_mode="custom",
        note="πίνακας μηνών ανά έτη υπηρεσίας, προσαύξηση 1/6 και αυτοτελής "
             "φόρος — ΕΝΔΕΙΚΤΙΚΑ, επιβεβαιώστε πριν από παραγωγική χρήση",
        fields=(
            _pf("months", "Μήνες ανά έτη υπηρεσίας", "int", ui=False),
            _pf("gift_factor", "Προσαύξηση Δώρων (1/6)", "money", ui=False),
            _pf("tax_brackets", "Κλιμάκια αυτοτελούς φόρου", "money", ui=False),
        ),
    ),
    ParamFamily(
        "work_hours", "Όρια & προσαυξήσεις ωρών", WorkHourRules,
        "WorkHourRulesDefined", WORK_HOUR_HISTORY,
        note="όρια & προσαυξήσεις της κατάταξης ωρών (νύχτα/αργία/"
             "πρόσθετη/υπερεργασία/υπερωρίες)",
        fields=(
            _pf("night_start", "Έναρξη νύχτας (ώρα)", "hour"),
            _pf("night_end", "Λήξη νύχτας (ώρα)", "hour"),
            _pf("night_premium", "Προσαύξηση νύχτας (%)", "premium"),
            _pf("holiday_premium", "Προσαύξηση Κυριακής/αργίας (%)", "premium"),
            _pf("extra_premium", "Πρόσθετη εργασία (%)", "premium"),
            _pf("overwork_premium", "Υπερεργασία (%)", "premium"),
            _pf("overtime_premium", "Νόμιμη υπερωρία (%)", "premium"),
            _pf("illegal_premium", "Παράνομη υπερωρία (%)", "premium"),
            _pf("weekly_legal_5", "Εβδομαδιαίο νόμιμο πενθημέρου (ώρες)", "int"),
            _pf("weekly_legal_6", "Εβδομαδιαίο νόμιμο εξαημέρου (ώρες)", "int"),
            _pf("daily_legal_5", "Ημερήσιο νόμιμο πενθημέρου (ώρες)", "int"),
            _pf("daily_legal_6", "Ημερήσιο νόμιμο εξαημέρου (ώρες)", "int"),
            _pf("daily_overwork_start", "Ημερήσια υπερεργασία από (ώρες)", "int"),
            _pf("overtime_day_cap", "Όριο υπερωρίας/ημέρα (ώρες)", "int"),
            _pf("overtime_year_cap", "Όριο υπερωρίας/έτος (ώρες)", "int"),
            _pf("orphan_month_cap", "Ορφανά χτυπήματα κάρτας/μήνα", "int"),
            _pf("retro_declare_orphans", "Δήλωση ορφανών στο απολογιστικό (1/0)", "flag"),
            _pf("card_rounding_minutes", "Στρογγυλοποίηση κάρτας (λεπτά)", "int"),
            _pf("card_tolerance_minutes", "Ανοχή κάρτας ↔ προγράμματος (λεπτά)", "int"),
        ),
        check=lambda v: (
            None
            if v.get("night_start", 0) != v.get("night_end", 0)
            else "Η έναρξη και η λήξη της νύχτας δεν μπορούν να ταυτίζονται"
        ),
    ),
]}


# ------------------------------------------------------- ΠΑΡΑΓΩΓΟΙ ΠΙΝΑΚΕΣ
# Δημόσιο API — οι καταναλωτές (projections, services, settings) δεν άλλαξαν.
FAMILY_CLASSES: dict[str, type] = {k: f.cls for k, f in FAMILIES.items()}
FAMILY_DEFAULTS: dict[str, list] = {k: f.defaults for k, f in FAMILIES.items()}
FAMILY_LABELS: dict[str, str] = {k: f.label for k, f in FAMILIES.items()}
# Οικογένειες χωρίς τις οποίες ΔΕΝ τρέχει μισθοδοσία (services._require_parameters)
CRITICAL_FAMILIES: tuple = tuple(k for k, f in FAMILIES.items() if f.critical)


def family_field(family: str, name: str) -> ParamField:
    for f in FAMILIES[family].fields:
        if f.name == name:
            return f
    raise ValueError(f"Άγνωστο πεδίο «{name}» της οικογένειας «{family}»")


def check_field_value(field: ParamField, value):
    """Έλεγχος μιας τιμής κατά τον τύπο της. ValueError στα ελληνικά."""
    spec = FIELD_KINDS[field.kind]
    try:
        v = int(value) if spec["ui"] == "int" else float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Μη έγκυρη τιμή στο πεδίο «{field.label}»") from None
    lo, hi = spec["lo"], spec["hi"]
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ValueError(spec["err"].format(field.label))
    if field.kind == "money" and v <= 0:
        raise ValueError(spec["err"].format(field.label))
    return v


def validate_families() -> None:
    """Έλεγχος συνέπειας του μητρώου — τρέχει στο IMPORT, όπως το
    `obligation_types.validate()`. Ένα typo δεν πρέπει να ανακαλύπτεται
    μήνες αργότερα, από λάθος ποσό σε μισθοδοσία."""
    from dataclasses import fields as _dc_fields

    for key, spec in FAMILIES.items():
        if spec.key != key:
            raise ValueError(f"{key}: ασυμφωνία κλειδιού με το «{spec.key}»")
        names = {f.name for f in _dc_fields(spec.cls)}
        if "valid_from" not in names:
            raise ValueError(f"{key}: η κλάση {spec.cls.__name__} δεν έχει valid_from")
        for f in spec.fields:
            if f.kind not in FIELD_KINDS:
                raise ValueError(f"{key}.{f.name}: άγνωστος τύπος «{f.kind}»")
            if f.name not in names:
                raise ValueError(
                    f"{key}.{f.name}: δεν υπάρχει στην {spec.cls.__name__}"
                )
        seen = [f.name for f in spec.fields]
        if len(seen) != len(set(seen)):
            raise ValueError(f"{key}: διπλό πεδίο στο μητρώο")
        for name in spec.carry_over:
            f = next((x for x in spec.fields if x.name == name), None)
            if f is None:
                raise ValueError(f"{key}: carry_over σε άγνωστο πεδίο «{name}»")
            if f.ui:
                raise ValueError(
                    f"{key}.{name}: πεδίο carry_over δεν επιτρέπεται να είναι ui=True"
                )
        if spec.ui_mode not in ("generic", "custom"):
            raise ValueError(f"{key}: άγνωστο ui_mode «{spec.ui_mode}»")
        for entry in spec.defaults:
            if not isinstance(entry, spec.cls):
                raise ValueError(
                    f"{key}: αρχική τιμή τύπου {type(entry).__name__}, "
                    f"περίμενα {spec.cls.__name__}"
                )


validate_families()

# --- Αιτίες μεταβολής σύμβασης (ContractChanged.reason) ---------------------
# ΜΗΤΡΩΟ: κωδικός -> πρότυπο αιτιολογίας. Το `{date}` γεμίζει από το
# `reason_date` του event (ημερομηνία που τεκμηριώνει την αιτία, π.χ. η ισχύς
# της νέας ΓΣΣΕ). Κενός κωδικός = χειροκίνητη μεταβολή: η αιτιολογία
# περιγράφεται από τη ΔΙΑΦΟΡΑ των όρων (webapp/views/employments.py).
# --- Μειωμένο ωράριο φροντίδας τέκνου (ChildcareReductionSet.option) --------
# Κωδικός -> (ετικέτα, διαδοχικά διαστήματα (μήνες, ώρες μείωσης/ημέρα)).
CHILDCARE_REDUCTION_OPTIONS: dict[str, tuple] = {
    "classic": ("1 ώρα/ημέρα για 30 μήνες", ((30, 1),)),
    "alternative": (
        "2 ώρες/ημέρα για 12 μήνες και 1 ώρα για 6 (με συμφωνία εργοδότη)",
        ((12, 2), (6, 1)),
    ),
}

CONTRACT_CHANGE_REASONS: dict[str, str] = {
    "gsse": "Προσαρμογή αποδοχών σε ΓΣΣΕ της {date}",
    "triennium": "Προσαρμογή αποδοχών λόγω τριετίας",
    "gsse+triennium": "Προσαρμογή αποδοχών σε ΓΣΣΕ της {date} και λόγω τριετίας",
    "minimum": "Προσαρμογή αποδοχών στο κατώτατο όριο",
}
