"""
Το «σύστημα»: τα singletons του event-sourced backend.

➜ ΠΟΛΛΕΣ ΕΤΑΙΡΕΙΕΣ. Το `store`/`proj`/`sched`/`svc` ΔΕΝ είναι
πλέον ένα σταθερό σύνολο αντικειμένων, αλλά **proxies προς την ΕΝΕΡΓΗ
εταιρεία του τρέχοντος αιτήματος** (`g.tenant`, βλ. `webapp/__init__.py`).
Έτσι κάθε view, query και εκτύπωση συνεχίζει να γράφει απλώς
`from ..system import proj, svc` και δουλεύει αυτόματα πολυ-εταιρικά —
καμία αλλαγή σε 20 modules.

Εκτός αιτήματος (scripts, tests, shell) οι proxies δείχνουν στην ΠΡΩΤΗ
εταιρεία του μητρώου, ώστε ό,τι δούλευε μονο-εταιρικά να συνεχίσει.

Ο Flask dev server είναι threaded, γι' αυτό:
  - τα SQLite connections είναι check_same_thread=False (event_store)
  - κάθε ΕΝΤΟΛΗ (command) εκτελείται μέσα σε `with lock:` (βλ. helpers.py).
    Το `lock` είναι ΕΝΑ για όλες τις εταιρείες: μια κοινή παράμετρος (π.χ.
    νέα ΓΣΣΕ) γράφει ταυτόχρονα στα projections ΟΛΩΝ των φορτωμένων.
"""

from flask import g, has_request_context
from werkzeug.local import LocalProxy

from .tenancy import Registry, Tenant

registry = Registry()

lock = registry.lock
auth = registry.auth  # AuthProjections: χρήστες, δικαιώματα, μητρώο εταιρειών
auth_store = registry.auth_store
auth_svc = registry.auth_svc
shared_store = registry.shared  # ΚΟΙΝΕΣ νομοθετικές παράμετροι


def set_tenant(company_id: str) -> Tenant:
    """Ορίζει την ενεργή εταιρεία του αιτήματος (το κάνει ο guard)."""
    g.tenant = registry.tenant(company_id)
    with lock:  # ό,τι έγραψε άλλη διεργασία (MCP server) στην ίδια βάση
        g.tenant.sync()
    return g.tenant


def current_tenant() -> Tenant:
    tenant = getattr(g, "tenant", None) if has_request_context() else None
    if tenant is not None:
        return tenant
    # ⚠️ ΚΑΜΙΑ σιωπηλή υποχώρηση σε «κάποια» εταιρεία όταν υπάρχει
    # ΤΑΥΤΟΠΟΙΗΜΕΝΟΣ χρήστης χωρίς ενεργή εταιρεία: θα του έδειχνε δεδομένα
    # εταιρείας στην οποία μπορεί να μην έχει καθόλου πρόσβαση.
    if has_request_context() and getattr(g, "user", None) is not None:
        raise RuntimeError("Δεν έχει επιλεγεί εταιρεία για αυτό το αίτημα")
    # Εκτός της κανονικής ροής (scripts, tests, συνθετικό request context):
    # η πρώτη εταιρεία του μητρώου, όπως ήταν και η μονο-εταιρική συμπεριφορά.
    companies = registry.auth.active_companies()
    if not companies:
        raise RuntimeError("Δεν έχει δημιουργηθεί καμία εταιρεία")
    return registry.tenant(companies[0].company_id)


store = LocalProxy(lambda: current_tenant().store)
proj = LocalProxy(lambda: current_tenant().proj)
sched = LocalProxy(lambda: current_tenant().sched)
svc = LocalProxy(lambda: current_tenant().svc)


def rebuild_all():
    """Πλήρες replay των παραγώγων της ΕΝΕΡΓΗΣ εταιρείας (rollback/restore)."""
    current_tenant().rebuild_all()
