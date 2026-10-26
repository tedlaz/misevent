"""
Χρήστες, δικαιώματα και μητρώο εταιρειών — event-sourced, stdlib-only.

Ίδια αρχιτεκτονική με τον υπόλοιπο πυρήνα: `AuthProjections` είναι το
in-memory read model που χτίζεται με replay, `AuthService` το command layer
όπου ζει ΟΛΟ το validation (ελληνικά `ValueError`). Καμία απευθείας εγγραφή
κατάστασης — τα πάντα περνούν από event.

ΜΟΝΤΕΛΟ ΔΙΚΑΙΩΜΑΤΩΝ (ανά χρήστη):
  is_admin              — τα πάντα: χρήστες, παράμετροι, ΟΛΕΣ οι εταιρείες
  all_companies         — πρόσβαση σε κάθε υπάρχουσα ΚΑΙ μελλοντική εταιρεία
  companies[]           — πρόσβαση σε συγκεκριμένες
  can_create_companies  — μπορεί να δημιουργεί ΔΙΚΕΣ ΤΟΥ (γίνεται owner και
                          αποκτά αυτόματα πρόσβαση σε αυτές)

ΑΣΦΑΛΕΙΑ: οι κωδικοί αποθηκεύονται ως `scrypt` hash με τυχαίο salt· τα
tokens (πρόσκλησης/επαναφοράς) αποθηκεύονται ΜΟΝΟ ως sha256 — το ωμό token
φαίνεται μία και μόνη φορά, τη στιγμή που εκδίδεται.
"""

import hashlib
import hmac
import os
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from misevent.domain.auth_events import (
    AUTH_EVENT_TYPES,
    CompanyArchived,
    CompanyProvisioned,
    CompanyRenamed,
    CompanyUnarchived,
    InvitationAccepted,
    InvitationIssued,
    InvitationRevoked,
    PasswordResetIssued,
    PasswordResetRequested,
    PasswordResetUsed,
    UserAccessSet,
    UserAdminSet,
    UserDeactivated,
    UserPasswordSet,
    UserReactivated,
    UserRegistered,
)

INVITE_VALID_DAYS = 7
RESET_VALID_DAYS = 2

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

# scrypt: παράμετροι κόστους. Το `n` πρέπει να είναι δύναμη του 2.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


# --------------------------------------------------------------- κρυπτογραφία
def hash_password(password: str) -> str:
    """`scrypt$n$r$p$salt$hash` — αυτοπεριγραφικό, ώστε αλλαγή παραμέτρων να
    μη χαλάει τους ήδη αποθηκευμένους κωδικούς."""
    salt = os.urandom(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(stored: str, password: str) -> bool:
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _expiry(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _expired(iso: str) -> bool:
    try:
        return datetime.fromisoformat(iso) < datetime.now(UTC)
    except ValueError:
        return True


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


# ------------------------------------------------------------------ κατάσταση
@dataclass
class User:
    user_id: str
    email: str
    created_at: str
    is_admin: bool = False
    active: bool = True
    password_hash: str = ""
    must_change_password: bool = False
    all_companies: bool = False
    companies: list[str] = field(default_factory=list)
    can_create_companies: bool = False
    invited_by: str = ""

    def can_access(self, company_id: str) -> bool:
        if not self.active:
            return False
        return self.is_admin or self.all_companies or company_id in self.companies


@dataclass
class Invitation:
    invite_id: str
    token_hash: str
    expires_at: str
    created_by: str
    note: str = ""
    accepted_by: str = ""
    revoked: bool = False

    @property
    def open(self) -> bool:
        return not self.accepted_by and not self.revoked and not _expired(self.expires_at)

    @property
    def status(self) -> str:
        if self.accepted_by:
            return "accepted"
        if self.revoked:
            return "revoked"
        return "expired" if _expired(self.expires_at) else "open"


@dataclass
class PasswordReset:
    reset_id: str
    user_id: str
    token_hash: str
    expires_at: str
    used: bool = False

    @property
    def open(self) -> bool:
        return not self.used and not _expired(self.expires_at)


@dataclass
class CompanyRef:
    """Ταυτότητα του ΧΩΡΟΥ μιας εταιρείας — όχι τα στοιχεία του εργοδότη."""

    company_id: str
    name: str
    created_at: str
    created_by: str = ""
    archived: bool = False


class AuthProjections:
    def __init__(self):
        self.users: dict[str, User] = {}
        self.invitations: dict[str, Invitation] = {}
        self.resets: dict[str, PasswordReset] = {}
        self.companies: dict[str, CompanyRef] = {}
        # Εκκρεμή αιτήματα επαναφοράς (τα βλέπει ο admin): email -> τελευταίο
        self.reset_requests: dict[str, dict] = {}

    # -------------------------------------------------------------- projector
    def handle(self, aggregate_id: str, e):
        if isinstance(e, UserRegistered):
            self.users[e.user_id] = User(
                user_id=e.user_id,
                email=e.email,
                created_at=e.created_at,
                is_admin=e.is_admin,
                invited_by=e.invited_by,
            )
        elif isinstance(e, UserPasswordSet) and e.user_id in self.users:
            u = self.users[e.user_id]
            u.password_hash = e.password_hash
            u.must_change_password = e.must_change
            # Ο ορισμός νέου κωδικού κλείνει το εκκρεμές αίτημα επαναφοράς.
            self.reset_requests.pop(u.email, None)
        elif isinstance(e, UserAdminSet) and e.user_id in self.users:
            self.users[e.user_id].is_admin = e.is_admin
        elif isinstance(e, UserAccessSet) and e.user_id in self.users:
            u = self.users[e.user_id]
            u.all_companies = e.all_companies
            u.companies = list(e.companies or [])
            u.can_create_companies = e.can_create_companies
        elif isinstance(e, UserDeactivated) and e.user_id in self.users:
            self.users[e.user_id].active = False
        elif isinstance(e, UserReactivated) and e.user_id in self.users:
            self.users[e.user_id].active = True

        elif isinstance(e, InvitationIssued):
            self.invitations[e.invite_id] = Invitation(
                invite_id=e.invite_id,
                token_hash=e.token_hash,
                expires_at=e.expires_at,
                created_by=e.created_by,
                note=e.note,
            )
        elif isinstance(e, InvitationAccepted) and e.invite_id in self.invitations:
            self.invitations[e.invite_id].accepted_by = e.user_id
        elif isinstance(e, InvitationRevoked) and e.invite_id in self.invitations:
            self.invitations[e.invite_id].revoked = True

        elif isinstance(e, PasswordResetRequested):
            self.reset_requests[e.email] = {"request_id": e.request_id, "at": e.at}
        elif isinstance(e, PasswordResetIssued):
            self.resets[e.reset_id] = PasswordReset(
                reset_id=e.reset_id,
                user_id=e.user_id,
                token_hash=e.token_hash,
                expires_at=e.expires_at,
            )
        elif isinstance(e, PasswordResetUsed) and e.reset_id in self.resets:
            self.resets[e.reset_id].used = True

        elif isinstance(e, CompanyProvisioned):
            self.companies[e.company_id] = CompanyRef(
                company_id=e.company_id,
                name=e.name,
                created_at=e.created_at,
                created_by=e.created_by,
            )
        elif isinstance(e, CompanyRenamed) and e.company_id in self.companies:
            self.companies[e.company_id].name = e.name
        elif isinstance(e, CompanyArchived) and e.company_id in self.companies:
            self.companies[e.company_id].archived = True
        elif isinstance(e, CompanyUnarchived) and e.company_id in self.companies:
            self.companies[e.company_id].archived = False

    def rebuild(self, store):
        self.__init__()
        for agg, ev in store.load():
            self.handle(agg, ev)

    # ---------------------------------------------------------------- queries
    def by_email(self, email: str) -> User | None:
        email = normalize_email(email)
        for u in self.users.values():
            if u.email == email:
                return u
        return None

    def active_companies(self) -> list[CompanyRef]:
        return sorted(
            (c for c in self.companies.values() if not c.archived),
            key=lambda c: c.name.lower(),
        )

    def companies_for(self, user: User) -> list[CompanyRef]:
        """Οι εταιρείες που επιτρέπεται να δει ο χρήστης."""
        return [c for c in self.active_companies() if user.can_access(c.company_id)]

    def admins(self) -> list[User]:
        return [u for u in self.users.values() if u.is_admin and u.active]

    def find_invitation(self, token: str) -> Invitation | None:
        digest = token_digest(token)
        for inv in self.invitations.values():
            if hmac.compare_digest(inv.token_hash, digest):
                return inv
        return None

    def find_reset(self, token: str) -> PasswordReset | None:
        digest = token_digest(token)
        for r in self.resets.values():
            if hmac.compare_digest(r.token_hash, digest):
                return r
        return None


# ------------------------------------------------------------------- commands
class AuthService:
    """Command layer. ΕΔΩ μπαίνει το validation — καμία οθόνη δεν το παρακάμπτει."""

    def __init__(self, store, projections: AuthProjections):
        self.store = store
        self.proj = projections

    # -- helpers ----------------------------------------------------------
    def _user(self, user_id: str) -> User:
        u = self.proj.users.get(user_id)
        if not u:
            raise ValueError("Ο χρήστης δεν βρέθηκε")
        return u

    @staticmethod
    def _check_email(email: str) -> str:
        email = normalize_email(email)
        if not _EMAIL_RE.match(email):
            raise ValueError("Μη έγκυρη διεύθυνση email")
        return email

    @staticmethod
    def _check_password(password: str, repeat: str | None = None) -> str:
        password = password or ""
        if not password:
            raise ValueError("Δώστε κωδικό")
        if repeat is not None and password != repeat:
            raise ValueError("Οι δύο κωδικοί δεν ταιριάζουν")
        return password

    # -- λογαριασμοί ------------------------------------------------------
    def register_user(
        self,
        email: str,
        password: str,
        *,
        is_admin: bool = False,
        must_change: bool = False,
        invited_by: str = "",
        password_repeat: str | None = None,
    ) -> str:
        email = self._check_email(email)
        self._check_password(password, password_repeat)
        if self.proj.by_email(email):
            raise ValueError(f"Υπάρχει ήδη χρήστης με email {email}")
        user_id = str(uuid.uuid4())
        self.store.append(
            user_id,
            UserRegistered(
                user_id=user_id,
                email=email,
                created_at=_now(),
                is_admin=is_admin,
                invited_by=invited_by,
            ),
        )
        self.store.append(
            user_id,
            UserPasswordSet(
                user_id=user_id,
                password_hash=hash_password(password),
                at=_now(),
                must_change=must_change,
            ),
        )
        return user_id

    def setup_first_admin(self, email: str, password: str, repeat: str) -> str:
        """ΠΡΩΤΗ εκκίνηση: ο χρήστης ορίζει ο ίδιος τον διαχειριστή — κανένας
        προεπιλεγμένος λογαριασμός/κωδικός. ΜΟΝΟ σε σύστημα χωρίς χρήστες:
        μετά, νέοι χρήστες μπαίνουν μόνο με πρόσκληση."""
        if self.proj.users:
            raise ValueError("Η αρχική ρύθμιση έχει ήδη γίνει — συνδεθείτε")
        return self.register_user(email, password, is_admin=True, password_repeat=repeat)

    def authenticate(self, email: str, password: str) -> User:
        """Επιστρέφει τον χρήστη ή πετά ValueError. Το μήνυμα είναι σκόπιμα
        ΕΝΙΑΙΟ, ώστε να μην αποκαλύπτει ποια emails υπάρχουν."""
        generic = "Λάθος email ή κωδικός"
        user = self.proj.by_email(email)
        if not user or not user.password_hash:
            raise ValueError(generic)
        if not verify_password(user.password_hash, password or ""):
            raise ValueError(generic)
        if not user.active:
            raise ValueError("Ο λογαριασμός έχει απενεργοποιηθεί")
        return user

    def change_password(
        self,
        user_id: str,
        new_password: str,
        *,
        current_password: str | None = None,
        password_repeat: str | None = None,
    ) -> str:
        user = self._user(user_id)
        if current_password is not None and not verify_password(
            user.password_hash, current_password
        ):
            raise ValueError("Ο τρέχων κωδικός δεν είναι σωστός")
        self._check_password(new_password, password_repeat)
        if verify_password(user.password_hash, new_password):
            raise ValueError("Ο νέος κωδικός πρέπει να διαφέρει από τον προηγούμενο")
        self.store.append(
            user_id,
            UserPasswordSet(
                user_id=user_id,
                password_hash=hash_password(new_password),
                at=_now(),
                must_change=False,
            ),
        )
        return "Ο κωδικός άλλαξε"

    def set_admin(self, user_id: str, is_admin: bool, by: str = "") -> str:
        user = self._user(user_id)
        if not is_admin and user.is_admin and len(self.proj.admins()) <= 1:
            raise ValueError("Πρέπει να παραμείνει τουλάχιστον ένας διαχειριστής")
        if user.is_admin == is_admin:
            return "Καμία αλλαγή"
        self.store.append(user_id, UserAdminSet(user_id=user_id, is_admin=is_admin, by=by))
        return (
            f"Ο χρήστης {user.email} έγινε διαχειριστής"
            if is_admin
            else f"Αφαιρέθηκαν τα δικαιώματα διαχειριστή από τον {user.email}"
        )

    def set_access(
        self,
        user_id: str,
        *,
        all_companies: bool = False,
        companies: list[str] | None = None,
        can_create_companies: bool = False,
        by: str = "",
    ) -> str:
        user = self._user(user_id)
        chosen = list(dict.fromkeys(companies or []))
        if set(chosen) - set(self.proj.companies):
            raise ValueError("Επιλέχθηκε εταιρεία που δεν υπάρχει")
        self.store.append(
            user_id,
            UserAccessSet(
                user_id=user_id,
                all_companies=all_companies,
                companies=[] if all_companies else chosen,
                can_create_companies=can_create_companies,
                by=by,
            ),
        )
        return f"Ενημερώθηκαν τα δικαιώματα του {user.email}"

    def set_active(self, user_id: str, active: bool, by: str = "") -> str:
        user = self._user(user_id)
        if not active and user.is_admin and len(self.proj.admins()) <= 1:
            raise ValueError("Πρέπει να παραμείνει τουλάχιστον ένας διαχειριστής")
        if user.active == active:
            return "Καμία αλλαγή"
        ev = (
            UserReactivated(user_id=user_id, by=by)
            if active
            else UserDeactivated(user_id=user_id, by=by)
        )
        self.store.append(user_id, ev)
        return f"Ο λογαριασμός {user.email} " + ("ενεργοποιήθηκε" if active else "απενεργοποιήθηκε")

    # -- προσκλήσεις ------------------------------------------------------
    def issue_invitation(self, created_by: str, note: str = "") -> tuple[str, str]:
        """Επιστρέφει (invite_id, ΩΜΟ token). Το token ΔΕΝ ξαναφαίνεται."""
        token = new_token()
        invite_id = str(uuid.uuid4())
        self.store.append(
            invite_id,
            InvitationIssued(
                invite_id=invite_id,
                token_hash=token_digest(token),
                expires_at=_expiry(INVITE_VALID_DAYS),
                created_by=created_by,
                note=(note or "").strip(),
            ),
        )
        return invite_id, token

    def revoke_invitation(self, invite_id: str, by: str = "") -> str:
        inv = self.proj.invitations.get(invite_id)
        if not inv:
            raise ValueError("Η πρόσκληση δεν βρέθηκε")
        if inv.accepted_by:
            raise ValueError("Η πρόσκληση έχει ήδη χρησιμοποιηθεί")
        self.store.append(invite_id, InvitationRevoked(invite_id=invite_id, by=by))
        return "Η πρόσκληση ακυρώθηκε"

    def check_invitation(self, token: str) -> Invitation:
        inv = self.proj.find_invitation(token)
        if not inv:
            raise ValueError("Η πρόσκληση δεν βρέθηκε")
        if inv.accepted_by:
            raise ValueError("Η πρόσκληση έχει ήδη χρησιμοποιηθεί")
        if inv.revoked:
            raise ValueError("Η πρόσκληση έχει ακυρωθεί")
        if _expired(inv.expires_at):
            raise ValueError("Η πρόσκληση έχει λήξει — ζητήστε νέα από τον διαχειριστή")
        return inv

    def accept_invitation(
        self, token: str, email: str, password: str, password_repeat: str | None = None
    ) -> str:
        """Ο προσκεκλημένος ορίζει ΜΟΝΟΣ ΤΟΥ email και κωδικό. Ξεκινά ΧΩΡΙΣ
        πρόσβαση σε εταιρεία — τη δίνει μετά ο διαχειριστής."""
        inv = self.check_invitation(token)
        user_id = self.register_user(
            email,
            password,
            invited_by=inv.created_by,
            password_repeat=password_repeat,
        )
        self.store.append(
            inv.invite_id,
            InvitationAccepted(invite_id=inv.invite_id, user_id=user_id, at=_now()),
        )
        return user_id

    # -- επαναφορά κωδικού -------------------------------------------------
    def request_password_reset(self, email: str) -> str:
        """Αίτημα από την οθόνη σύνδεσης. Καταγράφεται ΜΟΝΟ αν το email
        αντιστοιχεί σε χρήστη — αλλά η απάντηση προς τα έξω είναι πάντα ίδια,
        ώστε να μην αποκαλύπτεται ποιοι λογαριασμοί υπάρχουν."""
        user = self.proj.by_email(email)
        if user:
            self.store.append(
                "SYSTEM",
                PasswordResetRequested(
                    request_id=str(uuid.uuid4()), email=user.email, at=_now()
                ),
            )
        return "Το αίτημα καταγράφηκε. Ο διαχειριστής θα σας στείλει σύνδεσμο επαναφοράς."

    def issue_password_reset(self, user_id: str, by: str = "") -> tuple[str, str]:
        """Ο admin εκδίδει token επαναφοράς. Επιστρέφει (reset_id, ΩΜΟ token)."""
        user = self._user(user_id)
        token = new_token()
        reset_id = str(uuid.uuid4())
        pending = self.proj.reset_requests.get(user.email) or {}
        self.store.append(
            reset_id,
            PasswordResetIssued(
                reset_id=reset_id,
                user_id=user_id,
                token_hash=token_digest(token),
                expires_at=_expiry(RESET_VALID_DAYS),
                by=by,
                request_id=pending.get("request_id", ""),
            ),
        )
        return reset_id, token

    def check_reset(self, token: str) -> PasswordReset:
        r = self.proj.find_reset(token)
        if not r:
            raise ValueError("Ο σύνδεσμος επαναφοράς δεν βρέθηκε")
        if r.used:
            raise ValueError("Ο σύνδεσμος έχει ήδη χρησιμοποιηθεί")
        if _expired(r.expires_at):
            raise ValueError("Ο σύνδεσμος έχει λήξει — ζητήστε νέο από τον διαχειριστή")
        return r

    def use_reset(self, token: str, new_password: str, password_repeat: str | None = None) -> str:
        r = self.check_reset(token)
        self._check_password(new_password, password_repeat)
        self.store.append(
            r.user_id,
            UserPasswordSet(
                user_id=r.user_id,
                password_hash=hash_password(new_password),
                at=_now(),
                must_change=False,
            ),
        )
        self.store.append(
            r.reset_id, PasswordResetUsed(reset_id=r.reset_id, user_id=r.user_id, at=_now())
        )
        return "Ο κωδικός ορίστηκε — μπορείτε να συνδεθείτε"

    # -- μητρώο εταιρειών --------------------------------------------------
    def provision_company(self, name: str, created_by: str = "", company_id: str = "") -> str:
        name = (name or "").strip()
        if not name:
            raise ValueError("Απαιτείται ονομασία εταιρείας")
        if any(
            c.name.lower() == name.lower() and not c.archived
            for c in self.proj.companies.values()
        ):
            raise ValueError(f"Υπάρχει ήδη εταιρεία με την ονομασία «{name}»")
        company_id = company_id or str(uuid.uuid4())
        self.store.append(
            company_id,
            CompanyProvisioned(
                company_id=company_id,
                name=name,
                created_at=_now(),
                created_by=created_by,
            ),
        )
        return company_id

    def grant_company(self, user_id: str, company_id: str, by: str = "") -> str:
        """Προσθέτει ΜΙΑ εταιρεία στα δικαιώματα (π.χ. ο δημιουργός της)."""
        user = self._user(user_id)
        if user.all_companies or user.is_admin or company_id in user.companies:
            return "Καμία αλλαγή"
        self.store.append(
            user_id,
            UserAccessSet(
                user_id=user_id,
                all_companies=user.all_companies,
                companies=[*user.companies, company_id],
                can_create_companies=user.can_create_companies,
                by=by,
            ),
        )
        return "Δόθηκε πρόσβαση στην εταιρεία"

    def rename_company(self, company_id: str, name: str) -> str:
        if company_id not in self.proj.companies:
            raise ValueError("Η εταιρεία δεν βρέθηκε")
        name = (name or "").strip()
        if not name:
            raise ValueError("Απαιτείται ονομασία εταιρείας")
        self.store.append(company_id, CompanyRenamed(company_id=company_id, name=name))
        return "Η ονομασία ενημερώθηκε"

    def archive_company(self, company_id: str, by: str = "") -> str:
        """«Διαγραφή» = απόσυρση: τα δεδομένα μένουν και επαναφέρονται."""
        ref = self.proj.companies.get(company_id)
        if ref is None or ref.archived:
            raise ValueError("Η εταιρεία δεν βρέθηκε")
        if len(self.proj.active_companies()) == 1:
            raise ValueError("Πρέπει να παραμείνει τουλάχιστον μία εταιρεία")
        self.store.append(company_id, CompanyArchived(company_id=company_id, by=by))
        return "Η εταιρεία αποσύρθηκε από τη λίστα"

    def unarchive_company(self, company_id: str, by: str = "") -> str:
        ref = self.proj.companies.get(company_id)
        if ref is None or not ref.archived:
            raise ValueError("Η εταιρεία δεν είναι αποσυρμένη")
        if any(c.name.lower() == ref.name.lower() for c in self.proj.active_companies()):
            raise ValueError(f"Υπάρχει ήδη ενεργή εταιρεία με την ονομασία «{ref.name}» "
                             "— μετονόμασέ την πρώτα")
        self.store.append(company_id, CompanyUnarchived(company_id=company_id, by=by))
        return "Η εταιρεία επανήλθε στη λίστα"


__all__ = [
    "AUTH_EVENT_TYPES",
    "AuthProjections",
    "AuthService",
    "CompanyRef",
    "Invitation",
    "User",
    "hash_password",
    "new_token",
    "normalize_email",
    "token_digest",
    "verify_password",
]
