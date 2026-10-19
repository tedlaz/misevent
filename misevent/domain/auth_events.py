"""
Events του χώρου ΧΡΗΣΤΩΝ & ΜΗΤΡΩΟΥ ΕΤΑΙΡΕΙΩΝ.

Ζουν σε ΔΙΚΟ ΤΟΥΣ log (`data/system.db`), χωριστά από τα δεδομένα μισθοδοσίας
κάθε εταιρείας — δεν είναι μισθολογικά γεγονότα και δεν πρέπει να μπαίνουν
στο backup/rollback μιας εταιρείας.

Ισχύουν οι ίδιοι κανόνες: append-only, καμία τροποποίηση. Η ανάκληση
δικαιώματος, η αλλαγή κωδικού και η ακύρωση πρόσκλησης είναι ΝΕΑ events που
υπερισχύουν των προηγούμενων (latest-wins ανά χρήστη/πρόσκληση).

⚠️ ΣΤΑ EVENTS ΔΕΝ ΓΡΑΦΕΤΑΙ ΠΟΤΕ ΚΩΔΙΚΟΣ Ή ΩΜΟ TOKEN — μόνο το hash τους. Το
log είναι μόνιμο και διαβάζεται από την οθόνη· ένα ωμό token εκεί θα ισοδυναμούσε
με μόνιμο κλειδί του λογαριασμού.
"""

from dataclasses import dataclass, field

from misevent.domain.events import DomainEvent


# ------------------------------------------------------------------ Χρήστες
@dataclass(frozen=True)
class UserRegistered(DomainEvent):
    """Νέος λογαριασμός. Ο πρώτος (bootstrap) είναι ο admin του συστήματος."""

    user_id: str
    email: str
    created_at: str  # ISO datetime
    is_admin: bool = False
    invited_by: str = ""  # user_id όποιου έστειλε την πρόσκληση


@dataclass(frozen=True)
class UserPasswordSet(DomainEvent):
    """Ορισμός/αλλαγή κωδικού. `must_change`: ο χρήστης οφείλει να τον
    αλλάξει στην επόμενη σύνδεση (αρχικός κωδικός ή reset από τον admin)."""

    user_id: str
    password_hash: str
    at: str
    must_change: bool = False


@dataclass(frozen=True)
class UserAdminSet(DomainEvent):
    """Προαγωγή σε admin ή αφαίρεση του ρόλου."""

    user_id: str
    is_admin: bool
    by: str = ""


@dataclass(frozen=True)
class UserAccessSet(DomainEvent):
    """ΟΛΟΚΛΗΡΗ η εικόνα πρόσβασης του χρήστη (αντικαθιστά την προηγούμενη):
    είτε σε ΟΛΕΣ τις εταιρείες, είτε σε συγκεκριμένες, με ή χωρίς δικαίωμα
    να δημιουργεί δικές του."""

    user_id: str
    all_companies: bool = False
    companies: list[str] = field(default_factory=list)
    can_create_companies: bool = False
    by: str = ""


@dataclass(frozen=True)
class UserDeactivated(DomainEvent):
    user_id: str
    by: str = ""


@dataclass(frozen=True)
class UserReactivated(DomainEvent):
    user_id: str
    by: str = ""


# -------------------------------------------------------------- Προσκλήσεις
@dataclass(frozen=True)
class InvitationIssued(DomainEvent):
    """Ο admin εκδίδει token πρόσκλησης. Ο προσκεκλημένος ορίζει ΜΟΝΟΣ ΤΟΥ
    email και κωδικό — γι' αυτό το email εδώ είναι απλή σημείωση."""

    invite_id: str
    token_hash: str
    expires_at: str
    created_by: str
    note: str = ""  # π.χ. σε ποιον δόθηκε


@dataclass(frozen=True)
class InvitationAccepted(DomainEvent):
    invite_id: str
    user_id: str
    at: str


@dataclass(frozen=True)
class InvitationRevoked(DomainEvent):
    invite_id: str
    by: str = ""


# ------------------------------------------------------- Επαναφορά κωδικού
@dataclass(frozen=True)
class PasswordResetRequested(DomainEvent):
    """Αίτημα του ΧΡΗΣΤΗ από την οθόνη σύνδεσης. Δεν δίνει από μόνο του
    πρόσβαση — ο admin το βλέπει και εκδίδει token."""

    request_id: str
    email: str
    at: str


@dataclass(frozen=True)
class PasswordResetIssued(DomainEvent):
    reset_id: str
    user_id: str
    token_hash: str
    expires_at: str
    by: str = ""
    request_id: str = ""


@dataclass(frozen=True)
class PasswordResetUsed(DomainEvent):
    reset_id: str
    user_id: str
    at: str


# --------------------------------------------------- Μητρώο εταιρειών
@dataclass(frozen=True)
class CompanyProvisioned(DomainEvent):
    """Δημιουργία ΧΩΡΟΥ εταιρείας (δικό της event log). Τα ΣΤΟΙΧΕΙΑ του
    εργοδότη (ΑΦΜ/ΑΜΕ/επωνυμία) καταχωρούνται μέσα στον χώρο της, με
    `CompanyRegistered` — εδώ ζει μόνο η ταυτότητα του χώρου."""

    company_id: str
    name: str
    created_at: str
    created_by: str = ""


@dataclass(frozen=True)
class CompanyRenamed(DomainEvent):
    company_id: str
    name: str


@dataclass(frozen=True)
class CompanyArchived(DomainEvent):
    """Απόσυρση από τη λίστα. Τα δεδομένα ΔΕΝ διαγράφονται ποτέ."""

    company_id: str
    by: str = ""


AUTH_EVENT_TYPES = {
    cls.__name__: cls
    for cls in [
        UserRegistered,
        UserPasswordSet,
        UserAdminSet,
        UserAccessSet,
        UserDeactivated,
        UserReactivated,
        InvitationIssued,
        InvitationAccepted,
        InvitationRevoked,
        PasswordResetRequested,
        PasswordResetIssued,
        PasswordResetUsed,
        CompanyProvisioned,
        CompanyRenamed,
        CompanyArchived,
    ]
}


def _validate_auth_event_registry() -> None:
    """Ίδιο δίχτυ με το `events._validate_event_registry`, για τον ΧΩΡΟ ΤΩΝ
    ΧΡΗΣΤΩΝ: κάθε event αυτού του module πρέπει να είναι στο
    `AUTH_EVENT_TYPES` (το μητρώο που δίνεται στο `EventStore` του
    `system.db`), αλλιώς σπάει το replay στο επόμενο restart."""
    from misevent.domain.events import _all_subclasses

    missing = sorted(
        cls.__name__
        for cls in _all_subclasses(DomainEvent)
        if cls.__module__ == __name__ and cls.__name__ not in AUTH_EVENT_TYPES
    )
    if missing:
        raise ValueError(
            "Events εκτός AUTH_EVENT_TYPES (δεν θα διαβάζονταν σε replay): "
            + ", ".join(missing)
        )


_validate_auth_event_registry()
