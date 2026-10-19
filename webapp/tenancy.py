"""
ΠΟΛΛΕΣ ΕΤΑΙΡΕΙΕΣ — ένα event log ανά εταιρεία, ΜΙΑ βάση για όλα.

Κάθε εταιρεία είναι ένας πλήρης, ΑΝΕΞΑΡΤΗΤΟΣ κόσμος του πυρήνα: δικό της
`EventStore`, `Projections`, `ObligationScheduler`, `PayrollService`.
Η απομόνωση είναι ΛΟΓΙΚΗ (δικό της `store` μέσα στον κοινό πίνακα), όχι
δομική — γι' αυτό κανένα query δεν γράφεται έξω από το `EventStore`, που
φιλτράρει πάντα σε `store = ?`.

    <data>/misevent.db      ΟΛΑ τα δεδομένα, με stores:
        system                  χρήστες + μητρώο εταιρειών
        shared                  ΚΟΙΝΕΣ νομοθετικές παράμετροι
        company:<company_id>    τα δεδομένα μιας εταιρείας

Ο φάκελος `<data>` είναι `instance/`, ή ό,τι λέει το `MISEVENT_DATA_DIR`
(το χρησιμοποιούν τα tests για να τρέχουν σε δικό τους, καθαρό χώρο). Το
`MISEVENT_DB` δείχνει απευθείας στο αρχείο της βάσης, αν χρειαστεί να ζει
αλλού από τα υπόλοιπα δεδομένα.
⚠️ ΟΧΙ `data/`: εκεί ζουν **δεδομένα αναφοράς του repo** (γραμμογραφήσεις
JL10, δείγματα ΚΠΚ/ειδικοτήτων, κάρτα ΕΡΓΑΝΗ) που ΕΙΝΑΙ στο git — η ζωντανή
βάση δεν ανακατεύεται μαζί τους.

ΚΟΙΝΕΣ ΠΑΡΑΜΕΤΡΟΙ: το store `shared` δίνεται ως `shared=…` σε κάθε εταιρεία
(βλ. `event_store.EventStore`), και ΟΛΑ τα φορτωμένα projections γράφονται ως
subscribers ΚΑΙ σε αυτό. Έτσι μια νέα ΓΣΣΕ γράφεται ΜΙΑ φορά και ισχύει
αμέσως παντού.

⚠️ Οι εταιρείες φορτώνονται ΤΕΜΠΕΛΙΚΑ και μένουν στη μνήμη (τα projections
είναι in-memory). Για δεκάδες εταιρείες αυτό είναι μια χαρά· για εκατοντάδες
θα χρειαστεί πολιτική αποφόρτωσης.
"""

import os
import threading
from pathlib import Path

from misevent.app.auth import AuthProjections, AuthService
from misevent.app.services import PayrollService
from misevent.domain.auth_events import AUTH_EVENT_TYPES
from misevent.domain.projections import Projections
from misevent.domain.scheduler import ObligationScheduler
from misevent.persistence.database import (
    SHARED_STORE,
    SYSTEM_STORE,
    Database,
    company_store,
)

DB_NAME = "misevent.db"
DEFAULT_COMPANY_NAME = "Εταιρεία 1"


def data_dir() -> Path:
    return Path(os.environ.get("MISEVENT_DATA_DIR") or "instance")


def db_file(root: Path | None = None) -> Path:
    """Το ΕΝΑ αρχείο βάσης. Ένας διακόπτης, ένα σημείο."""
    override = os.environ.get("MISEVENT_DB")
    if override:
        return Path(override)
    return (root or data_dir()) / DB_NAME


class Tenant:
    """Ο κόσμος ΜΙΑΣ εταιρείας — ό,τι έστηνε πριν το `system.py` καθολικά."""

    def __init__(self, company_id: str, db: Database, shared):
        self.company_id = company_id
        self.store_name = company_store(company_id)

        self.store = db.store(self.store_name, shared=shared)

        self.proj = Projections()
        self.proj.rebuild(self.store)  # replay: ΠΡΩΤΑ τα κοινά, μετά τα δικά της
        self.store.subscribe(self.proj.handle)

        self.sched = ObligationScheduler(self.store, self.proj, horizon_months=1)
        self.store.subscribe(self.sched.handle)
        self.svc = PayrollService(self.store, self.proj)

        # Οι ΚΟΙΝΕΣ παράμετροι ενημερώνουν ΚΑΘΕ φορτωμένη εταιρεία ζωντανά.
        for handler in (self.proj.handle, self.sched.handle):
            shared.subscribe(handler)

    def rebuild_all(self):
        """Πλήρες replay των παραγώγων μετά από rollback/restore."""
        self.proj.rebuild(self.store)
        self.sched.rebuild()

    @property
    def event_count(self) -> int:
        return self.store.event_count()


class Registry:
    """Μητρώο εταιρειών + χρηστών: τα singletons ΟΛΟΥ του συστήματος."""

    def __init__(self, root: Path | None = None):
        self.root = root or data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        # ΕΝΑ κλείδωμα για όλες τις εταιρείες: μια κοινή παράμετρος γράφει σε
        # πολλά projections ταυτόχρονα, οπότε per-tenant lock δεν θα αρκούσε.
        self.lock = threading.Lock()

        path = db_file(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = Database(str(path))

        self.shared = self.db.store(SHARED_STORE)

        self.auth_store = self.db.store(SYSTEM_STORE, registry=AUTH_EVENT_TYPES)
        self.auth = AuthProjections()
        self.auth.rebuild(self.auth_store)
        self.auth_store.subscribe(self.auth.handle)
        self.auth_svc = AuthService(self.auth_store, self.auth)

        self._tenants: dict[str, Tenant] = {}

        self.auth_svc.bootstrap_admin()
        self._ensure_default_company()

    def reload(self):
        """Πλήρες replay ΟΛΟΥ του συστήματος μετά από restore της βάσης.

        Οι φορτωμένες εταιρείες πετιούνται (φορτώνονται ξανά τεμπέλικα) — και
        μαζί οι handlers τους από το κοινό store, αλλιώς τα παλιά projections
        θα συνέχιζαν να ακούν τις κοινές παραμέτρους."""
        self.shared._subscribers.clear()
        self._tenants.clear()
        self.auth.rebuild(self.auth_store)
        for ref in self.auth.active_companies():
            self.tenant(ref.company_id)  # replay τώρα: ένα χαλασμένο event σκάει ΕΔΩ
        self.auth_svc.bootstrap_admin()
        self._ensure_default_company()

    # ------------------------------------------------------------ εταιρείες
    def store_name(self, company_id: str) -> str:
        return company_store(company_id)

    def tenant(self, company_id: str) -> Tenant:
        """Φορτώνει (μία φορά) και επιστρέφει τον κόσμο μιας εταιρείας."""
        if company_id not in self.auth.companies:
            raise ValueError("Η εταιρεία δεν βρέθηκε")
        tenant = self._tenants.get(company_id)
        if tenant is None:
            tenant = Tenant(company_id, self.db, self.shared)
            self._tenants[company_id] = tenant
        return tenant

    def create_company(self, name: str, created_by: str = "") -> str:
        """Νέος ΚΕΝΟΣ χώρος εταιρείας. Τα στοιχεία του εργοδότη καταχωρούνται
        μετά, μέσα στον χώρο της, από τη σελίδα «Εταιρεία»."""
        company_id = self.auth_svc.provision_company(name, created_by=created_by)
        self.tenant(company_id)  # στήνει τον κόσμο της, ώστε να υπάρχει αμέσως
        if created_by:
            self.auth_svc.grant_company(created_by, company_id, by=created_by)
        return company_id

    def display_name(self, company_id: str) -> str:
        """Η επωνυμία όπως καταχωρήθηκε ΜΕΣΑ στην εταιρεία, αλλιώς η ονομασία
        του χώρου (μια νεοσύστατη δεν έχει ακόμη στοιχεία εργοδότη)."""
        ref = self.auth.companies.get(company_id)
        if ref is None:
            return ""
        tenant = self._tenants.get(company_id)
        if tenant and tenant.proj.company:
            return tenant.proj.company.legal_name or ref.name
        return ref.name

    def _ensure_default_company(self):
        """Ποτέ κενό σύστημα: χωρίς εταιρεία δεν υπάρχει τίποτα να δει ο
        χρήστης μετά τη σύνδεση."""
        if not self.auth.companies:
            self.create_company(DEFAULT_COMPANY_NAME)
