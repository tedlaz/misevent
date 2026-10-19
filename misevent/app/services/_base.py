"""
Κοινή βάση του PayrollService: κατασκευή, σταθερές του τομέα και οι
helpers που χρησιμοποιούν πολλά mixins.
"""

import json
from datetime import date
from typing import NamedTuple

import misevent.integrations.ergani as ergani_mod
from misevent.domain.events import ErganiSubmissionRecorded

# γεννά αποζημίωση απόλυσης στην εκκαθάριση της αποχώρησης.
VOLUNTARY_EXIT_REASON = "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ"
DISMISSAL_REASON = "ΚΑΤΑΓΓΕΛΙΑ_ΣΥΜΒΑΣΗΣ"

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
    def __init__(self, store, projections, ergani_client=None):
        self.store = store
        self.proj = projections
        # Client ΕΡΓΑΝΗ ΙΙ (Web API). Προεπιλογή: simulate mode.
        self.ergani = ergani_client or ergani_mod.ErganiClient(simulate=True)

    # ------------------------------------------------------------ ΕΡΓΑΝΗ
    def _ergani_submit(self, doc_type: str, employee_id: str, payload: dict):
        """Υποβολή στο ΕΡΓΑΝΗ + καταγραφή ως event. Αποτυχία δικτύου
        καταγράφεται ως failed χωρίς να μπλοκάρει την τοπική ενέργεια."""
        try:
            res = self.ergani.submit(doc_type, payload)
        except ergani_mod.ErganiError as exc:
            res = {
                "status": "failed",
                "reference": str(exc),
                "payload": json.dumps(payload, ensure_ascii=False, default=str),
            }
        self.store.append(
            employee_id or "COMPANY",
            ErganiSubmissionRecorded(
                doc_type,
                employee_id,
                res["payload"],
                res["status"],
                res["reference"],
                self.ergani.environment,
            ),
        )
        return res
