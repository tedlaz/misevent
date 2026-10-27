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

import atexit
import os
import shutil
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
from misevent.persistence.documents import DocumentStore

from .ergani_credentials import SecretStore

DB_NAME = "misevent.db"
DOCUMENTS_DB_NAME = "documents.db"  # blobs, εκτός event log — δίπλα στη βάση
SECRETS_DB_NAME = "secrets.db"  # κωδικοί ΕΡΓΑΝΗ ανά εταιρεία, κρυπτογραφημένοι
SECRETS_KEY_NAME = "secrets.key"
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

    def __init__(self, company_id: str, db: Database, shared, documents: DocumentStore,
                 secrets: SecretStore):
        self.company_id = company_id
        self.store_name = company_store(company_id)

        self.store = db.store(self.store_name, shared=shared)

        self.proj = Projections()
        self.proj.rebuild(self.store)  # replay: ΠΡΩΤΑ τα κοινά, μετά τα δικά της
        self.store.subscribe(self.proj.handle)
        # Πόσα events έχουν δει τα projections — βλ. `sync`.
        self._seen = self.store.event_count()
        self.store.subscribe(self._saw)

        self.sched = ObligationScheduler(self.store, self.proj, horizon_months=1)
        self.store.subscribe(self.sched.handle)
        self.svc = PayrollService(self.store, self.proj,
                                  ergani_client=secrets.client(self.store_name),
                                  documents=documents.scope(self.store_name))

        # Οι ΚΟΙΝΕΣ παράμετροι ενημερώνουν ΚΑΘΕ φορτωμένη εταιρεία ζωντανά.
        for handler in (self.proj.handle, self.sched.handle):
            shared.subscribe(handler)

    def rebuild_all(self):
        """Πλήρες replay των παραγώγων μετά από rollback/restore."""
        self.proj.rebuild(self.store)
        self.sched.rebuild()
        self._seen = self.store.event_count()

    def _saw(self, *_):
        self._seen += 1

    def sync(self):
        """Events που έγραψε ΑΛΛΗ διεργασία (web UI <-> MCP server, ίδια βάση)
        δεν περνούν από τους subscribers μας -> replay όταν αλλάξει το πλήθος."""
        # ponytail: σύγκριση ΠΛΗΘΟΥΣ — rollback + ίσα νέα events σε άλλη
        # διεργασία ξεφεύγουν· αν χρειαστεί, σύγκρινε και MAX(seq)/event_id.
        if self.store.event_count() != self._seen:
            self.rebuild_all()

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
        self.documents = DocumentStore(str(path.with_name(DOCUMENTS_DB_NAME)))
        self.secrets = SecretStore(str(path.with_name(SECRETS_DB_NAME)),
                                   key_path=path.with_name(SECRETS_KEY_NAME))

        self.shared = self.db.store(SHARED_STORE)

        self.auth_store = self.db.store(SYSTEM_STORE, registry=AUTH_EVENT_TYPES)
        self.auth = AuthProjections()
        self.auth.rebuild(self.auth_store)
        self.auth_store.subscribe(self.auth.handle)
        self.auth_svc = AuthService(self.auth_store, self.auth)

        self._tenants: dict[str, Tenant] = {}

        self._ensure_default_company()
        # Καθαρό κλείσιμο -> η SQLite σβήνει τα -wal/-shm δίπλα στις βάσεις.
        atexit.register(self.close)

    def close(self):
        self.db.close()
        self.documents.close()

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
        self._ensure_default_company()

    def needs_setup(self) -> bool:
        """Νέα εγκατάσταση: κανένας χρήστης — ο πρώτος ορίζεται στη σελίδα
        «Αρχική ρύθμιση» (κανένας προεπιλεγμένος λογαριασμός)."""
        return not self.auth.users

    def is_fresh(self) -> bool:
        """Καμία εταιρεία με δεδομένα — τότε ανοίγει ο οδηγός «Ξεκινώντας»
        (νέα εταιρεία / restore / εισαγωγή από ΑΠΔ)."""
        return all(self.tenant(c.company_id).event_count == 0
                   for c in self.auth.active_companies())

    def empty_company(self, user=None) -> str | None:
        """Ένας ΚΕΝΟΣ χώρος εταιρείας (χωρίς events), προσβάσιμος από τον
        χρήστη: η πρώτη εταιρεία μιας νέας εγκατάστασης τον ΞΑΝΑΧΡΗΣΙΜΟΠΟΙΕΙ,
        αντί να μείνει δίπλα της ένας άδειος «προεπιλεγμένος»."""
        companies = (self.auth.companies_for(user) if user
                     else self.auth.active_companies())
        return next((c.company_id for c in companies
                     if self.tenant(c.company_id).event_count == 0), None)

    # ------------------------------------------------------------ εταιρείες
    def store_name(self, company_id: str) -> str:
        return company_store(company_id)

    def tenant(self, company_id: str) -> Tenant:
        """Φορτώνει (μία φορά) και επιστρέφει τον κόσμο μιας εταιρείας."""
        if company_id not in self.auth.companies:
            raise ValueError("Η εταιρεία δεν βρέθηκε")
        tenant = self._tenants.get(company_id)
        if tenant is None:
            tenant = Tenant(company_id, self.db, self.shared, self.documents, self.secrets)
            self._tenants[company_id] = tenant
        return tenant

    def set_ergani(self, company_id: str, environment: str, username: str,
                   password: str = ""):
        """Στοιχεία ΕΡΓΑΝΗ μιας εταιρείας + νέος client ΑΜΕΣΩΣ (χωρίς restart)."""
        tenant = self.tenant(company_id)
        self.secrets.set(tenant.store_name, environment, username, password)
        tenant.svc.ergani = self.secrets.client(tenant.store_name)

    def create_company(self, name: str, created_by: str = "") -> str:
        """Νέος ΚΕΝΟΣ χώρος εταιρείας. Τα στοιχεία του εργοδότη καταχωρούνται
        μετά, μέσα στον χώρο της, από τη σελίδα «Εταιρεία»."""
        company_id = self.auth_svc.provision_company(name, created_by=created_by)
        self.tenant(company_id)  # στήνει τον κόσμο της, ώστε να υπάρχει αμέσως
        if created_by:
            self.auth_svc.grant_company(created_by, company_id, by=created_by)
        return company_id

    def purge_company(self, company_id: str):
        """ΟΡΙΣΤΙΚΗ διαγραφή εταιρείας ΑΠΟ ΠΑΝΤΟΥ: events, έγγραφα, κωδικοί
        ΕΡΓΑΝΗ, safety backups και η εγγραφή της στο μητρώο. ΔΕΝ αναιρείται.
        ⚠️ Backups ΟΛΟΥ του συστήματος (.zip) που έχουν ήδη κατέβει την
        περιέχουν ακόμη — αυτά δεν είναι στη διάθεσή μας."""
        ref = self.auth.companies.get(company_id)
        if ref is None:
            raise ValueError("Η εταιρεία δεν βρέθηκε")
        if not ref.archived and len(self.auth.active_companies()) == 1:
            raise ValueError("Πρέπει να παραμείνει τουλάχιστον μία εταιρεία")
        tenant = self._tenants.pop(company_id, None)
        if tenant:  # να μην ακούει πια τις κοινές παραμέτρους
            for handler in (tenant.proj.handle, tenant.sched.handle):
                self.shared._subscribers.remove(handler)
        store_name = company_store(company_id)
        self.secrets.delete(store_name)
        self.documents.purge(store_name)
        self.db.store(store_name).purge()
        # ΤΕΛΕΥΤΑΙΟ: αν κάτι πιο πάνω αποτύχει, η εταιρεία φαίνεται ακόμη
        # στη λίστα και η διαγραφή ξαναδοκιμάζεται.
        self.auth_store.purge(company_id)
        self.auth.rebuild(self.auth_store)
        shutil.rmtree(self.root / "backups" / company_id, ignore_errors=True)

    def claim_company(self, name: str, user) -> str:
        """Χώρος για ΝΕΑ εταιρεία: ο κενός υπάρχων (μετονομασμένος) ή νέος."""
        company_id = self.empty_company(user)
        if company_id is None:
            return self.create_company(name, created_by=user.user_id)
        self.auth_svc.rename_company(company_id, name)
        return company_id

    def display_name(self, company_id: str) -> str:
        """Το όνομα της εταιρείας ΣΤΟ ΜΕΝΟΥ της εφαρμογής — αυτό που δόθηκε
        στη δημιουργία (μετονομάζεται από τη σελίδα «Εταιρείες»). ΔΕΝ είναι
        η επίσημη ονομασία: αυτή ζει στα στοιχεία εργοδότη
        (`CompanyRegistered.official_name`) και τη χρησιμοποιούν οι εκτυπώσεις."""
        ref = self.auth.companies.get(company_id)
        return ref.name if ref else ""

    def official_name(self, company_id: str) -> str:
        """Η επίσημη ονομασία από τα στοιχεία εργοδότη ("" αν δεν έχουν μπει)."""
        if company_id not in self.auth.companies:
            return ""
        company = self.tenant(company_id).proj.company
        return company.official_name if company else ""

    def _ensure_default_company(self):
        """Ποτέ κενό σύστημα: χωρίς εταιρεία δεν υπάρχει τίποτα να δει ο
        χρήστης μετά τη σύνδεση."""
        if not self.auth.companies:
            self.create_company(DEFAULT_COMPANY_NAME)
        # Πρώτο τρέξιμο: οι αρχικές τιμές (data/init_parameters.json) γράφονται
        # αυτόματα — ΚΟΙΝΑ events, οπότε αρκεί μία οποιαδήποτε εταιρεία.
        svc = self.tenant(next(iter(self.auth.companies))).svc
        if not svc.parameters_confirmed():
            svc.confirm_default_parameters()
