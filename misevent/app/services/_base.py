"""
Κοινή βάση του PayrollService: κατασκευή, σταθερές του τομέα και οι
helpers που χρησιμοποιούν πολλά mixins.
"""

import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from typing import NamedTuple

import misevent.integrations.ergani as ergani_mod
from misevent.domain.events import ErganiSubmissionRecorded
from misevent.persistence.documents import DocumentStore

# ΕΓΓΡΑΦΑ ΠΡΟΣΩΠΟΥ που πάνε με την αναγγελία πρόσληψης (WebE3N, PDF base64).
# Κλειδί = το πεδίο του WebE3N, ώστε η υποβολή να μη χρειάζεται αντιστοίχιση.
PERSON_DOCUMENT_KINDS = {
    "foreign_file": "Νομιμοποιητικά έγγραφα πολίτη τρίτης χώρας",
    "young_file": "Βιβλιάριο ανηλίκου",
    # Η δήλωση οικειοθελούς αποχώρησης: αποθηκεύεται στην αποχώρηση και
    # επισυνάπτεται στο WebE5N όταν σταλεί από την οθόνη ΕΡΓΑΝΗ
    "resignation": "Δήλωση αποχώρησης",
}
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024

# ΤΡΟΠΟΣ ΥΠΟΒΟΛΗΣ ΕΡΓΑΝΗ της τρέχουσας εντολής (ίδιο μοτίβο με το
# `event_store.transaction`). None = REST API· ("preview",) = μόνο το payload·
# ("manual", πρωτόκολλο) = ο χρήστης το υπέβαλε ο ίδιος στο portal. Ζει ΜΟΝΟ
# στο `_ergani_submit`, ώστε κάθε έντυπο να έχει και τους τρεις τρόπους χωρίς
# δικό του κώδικα — και η χειροκίνητη υποβολή ΙΔΙΕΣ συνέπειες με το API.
_ERGANI_MODE: ContextVar[tuple | None] = ContextVar("misevent_ergani_mode", default=None)


class ErganiPreview(Exception):
    """Το payload που ΘΑ υποβαλλόταν (`ergani_preview`) — σηκώνεται ΠΡΙΝ από
    κάθε εγγραφή, οπότε η εντολή δεν αφήνει κανένα ίχνος."""

    def __init__(self, doc_type: str, endpoint: str, payload: dict):
        super().__init__(doc_type)
        self.doc_type, self.endpoint, self.payload = doc_type, endpoint, payload


@contextmanager
def ergani_preview():
    """Η εντολή χτίζει το έντυπο και σταματά με `ErganiPreview`."""
    token = _ERGANI_MODE.set(("preview",))
    try:
        yield
    finally:
        _ERGANI_MODE.reset(token)


@contextmanager
def ergani_manual(reference: str):
    """Η εντολή καταγράφει το έντυπο ως υποβληθέν ΧΕΙΡΟΚΙΝΗΤΑ στο portal
    (με τον αρ. πρωτοκόλλου του χρήστη) — καμία κλήση στο API."""
    reference = str(reference or "").strip()
    if not reference:
        raise ValueError("Δώστε τον αριθμό πρωτοκόλλου της υποβολής στο portal ΕΡΓΑΝΗ")
    token = _ERGANI_MODE.set(("manual", reference))
    try:
        yield
    finally:
        _ERGANI_MODE.reset(token)


def ergani_is_manual() -> bool:
    return (_ERGANI_MODE.get() or ("",))[0] == "manual"


# γεννά αποζημίωση απόλυσης στην εκκαθάριση της αποχώρησης.
VOLUNTARY_EXIT_REASON = "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ"
DISMISSAL_REASON = "ΚΑΤΑΓΓΕΛΙΑ_ΣΥΜΒΑΣΗΣ"
FIXED_TERM_END_REASON = "ΛΗΞΗ_ΣΥΜΒΑΣΗΣ_ΟΡΙΣΜΕΝΟΥ"

# ΚΑΝΟΝΙΚΗ ΑΔΕΙΑ — μακρά υπηρεσία: 25 εργάσιμες ημέρες (αντί 20/21/22) με 12
# έτη συνολικής εργασίας σε ΟΠΟΙΟΝΔΗΠΟΤΕ εργοδότη ή 10 έτη ΣΤΟΝ ΙΔΙΟ. Ισχύει
# από ΚΑΘΕ έτος, και στο πρώτο αναλογικά προς τον μήνα πρόσληψης.
LONG_SERVICE_ANY_EMPLOYER_YEARS = 12.0
LONG_SERVICE_SAME_EMPLOYER_YEARS = 10.0
LONG_SERVICE_LEAVE_DAYS = 25


def _decode_import(data) -> str:
    """Κείμενο ανεβασμένου CSV — μία πηγή αλήθειας για την κωδικοποίηση."""
    if not isinstance(data, bytes):
        return data
    for enc in ("utf-8-sig", "cp1253"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("Μη αναγνώσιμο αρχείο (κωδικοποίηση)")


def _pct4(value) -> float:
    """Ποσοστό (κλάσμα) με την ακρίβεια που έχει νόημα: 4 δεκαδικά, δηλαδή 2
    δεκαδικά επί τοις εκατό. Κόβει τα υπολείμματα του float στην εξαγωγή
    (0.13369999999999999 -> 0.1337)."""
    return round(float(value), 4)


def _parse_import_date(raw: str) -> date:
    """Ημερομηνία CSV: 2025-01-01 ή 01/01/2025."""
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        d_, m_, y_ = raw.strip().split("/")
        return date(int(y_), int(m_), int(d_))


class ApdEarnings(NamedTuple):
    """Μία «αμοιβή» της περιόδου προς δήλωση στην ΑΠΔ: τύπος αποδοχών, ποσό,
    ημέρες ασφάλισης και — όπου το απαιτεί ο τύπος — το ΔΙΑΣΤΗΜΑ που αφορά.

    `date_from`/`date_to`: **μόνο για τύπους με δικό τους διάστημα** (008
    Αποδοχές ασθενείας → έναρξη/λήξη της ασθένειας). Για τις τακτικές (001)
    το διάστημα είναι ο μήνας απασχόλησης και υπολογίζεται στη
    `build_apd_rows`· τα Δώρα/Επίδομα (003/004/005) δεν έχουν διάστημα.

    `contrib`: οι ΥΠΟΛΟΓΙΣΜΕΝΕΣ εισφορές αυτής της αμοιβής ανά κάλυψη,
    `{(ταμείο, ΚΠΚ): (εργαζομένου, εργοδότη)}` — έρχονται από τα events της
    μισθοδοσίας/Δώρων, ΔΕΝ ξαναϋπολογίζονται από την ΑΠΔ."""

    earnings_type: str
    gross: float
    days: int
    date_from: date | None = None
    date_to: date | None = None
    contrib: dict | None = None


class ServiceBase:
    def __init__(self, store, projections, ergani_client=None, documents=None):
        self.store = store
        self.proj = projections
        # Client ΕΡΓΑΝΗ ΙΙ (Web API). Προεπιλογή: simulate mode.
        self.ergani = ergani_client or ergani_mod.ErganiClient(simulate=True)
        # Έγγραφα (blobs) — ΞΕΧΩΡΙΣΤΗ βάση, εκτός event log (βλ.
        # persistence/documents.py). Προεπιλογή: στη μνήμη (tests/scripts).
        self.documents = documents or DocumentStore().scope(getattr(store, "name", ""))

    # ------------------------------------------------------------ ΕΡΓΑΝΗ
    def _ergani_nothing_to_send(self, doc_type: str):
        """Εντολή ΕΡΓΑΝΗ χωρίς τίποτα να στείλει (π.χ. απολογιστικό χωρίς
        ημέρες) σε προεπισκόπηση: σταματά ΕΔΩ, πριν γράψει το «ολοκληρώθηκε
        χωρίς υποβολή» — η προεπισκόπηση δεν αφήνει ποτέ ίχνος."""
        if (_ERGANI_MODE.get() or ("",))[0] == "preview":
            raise ErganiPreview(doc_type, "", None)

    def _ergani_submit(self, doc_type: str, employee_id: str, payload: dict,
                       endpoint: str = "", subject: str = ""):
        """Υποβολή στο ΕΡΓΑΝΗ + καταγραφή ως event. Αποτυχία δικτύου
        καταγράφεται ως failed χωρίς να μπλοκάρει την τοπική ενέργεια.
        Το event κρατά το payload με τα συνημμένα PDF ΣΥΝΟΠΤΙΚΑ (μέγεθος +
        sha256) — το ίδιο το αρχείο ζει στη βάση εγγράφων.

        Τρόπος (`_ERGANI_MODE`): preview = `ErganiPreview` πριν από κάθε
        εγγραφή· manual = καμία κλήση, `status="manual"` με το πρωτόκολλο του
        χρήστη — όλα τα υπόλοιπα (κάλυψη, υποχρεώσεις) ακριβώς όπως στο API."""
        mode = _ERGANI_MODE.get()
        environment = self.ergani.environment
        if mode and mode[0] == "preview":
            raise ErganiPreview(doc_type, endpoint, ergani_mod.redact_files(payload))
        if mode and mode[0] == "manual":
            res = {"status": "manual", "reference": mode[1]}
            environment = "portal"
        else:
            try:
                res = self.ergani.submit(doc_type, payload, endpoint)
            except ergani_mod.ErganiError as exc:
                res = {"status": "failed", "reference": str(exc)}
        res["payload"] = json.dumps(
            ergani_mod.redact_files(payload), ensure_ascii=False, default=str)
        self.store.append(
            employee_id or "COMPANY",
            ErganiSubmissionRecorded(
                doc_type,
                employee_id,
                res["payload"],
                res["status"],
                res["reference"],
                environment,
                subject,
            ),
        )
        return res
