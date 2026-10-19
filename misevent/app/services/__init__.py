"""
Application Services — τα «commands» της εφαρμογής.
Κάθε command επικυρώνει, υπολογίζει (μέσω payroll_engine) και καταγράφει το
αποτέλεσμα ως event στο event store.

⚠️ ΤΟ `PayrollService` ΕΙΝΑΙ ΣΠΑΣΜΕΝΟ ΣΕ ΘΕΜΑΤΙΚΑ MIXINS — ήταν 5.632 γραμμές
και 179 μέθοδοι σε ΕΝΑ αρχείο. Δουλεύει ακριβώς επειδή η κλάση έχει **μόνο 3
instance attrs** (`store`, `proj`, `ergani`): κανένα mixin δεν κρατά δική του
κατάσταση, όλα μιλούν μέσω `self`.

➜ Η ΔΙΕΠΑΦΗ ΕΙΝΑΙ Η ΚΛΑΣΗ, ΟΧΙ ΤΟ ΑΡΧΕΙΟ: ένα mixin καλεί ελεύθερα μεθόδους
άλλου (π.χ. το PayrollMixin καλεί `self.payroll_days` του EntitlementsMixin).
ΜΗΝ προσπαθήσεις να το «καθαρίσεις» με imports μεταξύ mixins — θα φτιάξεις
κύκλους. Αν ένα κομμάτι πρέπει να μοιραστεί ΧΩΡΙΣ `self`, πάει στο `_base.py`.

➜ ⚠️ ΤΟ ΕΠΙΚΙΝΔΥΝΟ ΛΑΘΟΣ ΕΙΝΑΙ Η **ΕΠΙΚΑΛΥΨΗ ΟΝΟΜΑΤΟΣ** σε δύο mixins: το MRO
παίρνει σιωπηλά το πρώτο και ο υπολογισμός αλλάζει ΧΩΡΙΣ σφάλμα. Το πιάνει το
`tests/test_service_surface.py`, που κλειδώνει και το σύνολο των ονομάτων.
"""

from ._base import (
    DISMISSAL_REASON,
    LONG_SERVICE_ANY_EMPLOYER_YEARS,
    LONG_SERVICE_SAME_EMPLOYER_YEARS,
    VOLUNTARY_EXIT_REASON,
    ApdEarnings,
    ServiceBase,
)
from .apd import ApdMixin
from .attendance import AttendanceMixin
from .company import CompanyMixin
from .contract import ContractMixin
from .employment import EmploymentMixin
from .entitlements import EntitlementsMixin
from .fmy import FmyMixin
from .gifts import GiftsMixin
from .kpk import KpkMixin
from .leaves import LeavesMixin
from .parameters import ParametersMixin
from .payments import PaymentsMixin
from .payroll import PayrollMixin
from .schedules import SchedulesMixin
from .sheets import SheetsMixin
from .specialties import SpecialtiesMixin

__all__ = [
    "PayrollService",
    "ApdEarnings",
    "DISMISSAL_REASON",
    "VOLUNTARY_EXIT_REASON",
    "LONG_SERVICE_ANY_EMPLOYER_YEARS",
    "LONG_SERVICE_SAME_EMPLOYER_YEARS",
]


class PayrollService(
    CompanyMixin,
    EmploymentMixin,
    ContractMixin,
    KpkMixin,
    ParametersMixin,
    SpecialtiesMixin,
    AttendanceMixin,
    LeavesMixin,
    SchedulesMixin,
    SheetsMixin,
    EntitlementsMixin,
    PayrollMixin,
    GiftsMixin,
    ApdMixin,
    FmyMixin,
    PaymentsMixin,
    ServiceBase,
):
    """Το command layer της εφαρμογής — βλ. docstring του module."""
