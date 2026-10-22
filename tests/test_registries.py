"""
ΔΙΧΤΥ ΤΩΝ ΜΗΤΡΩΩΝ — πιάνει ό,τι σήμερα σπάει ΣΙΩΠΗΛΑ όταν προστίθεται κανόνας.

Το repo έχει ήδη τέσσερις validators που τρέχουν ΣΤΟ IMPORT και πιάνουν τα
περισσότερα λάθη θορυβωδώς:

    config.validate_families()                 μητρώο παραμέτρων
    obligation_types.validate()                μητρώο υποχρεώσεων (+ κύκλοι)
    projections._validate_param_registry()     παράμετροι <-> events
    events._validate_event_registry()          events <-> EVENT_TYPES
    webapp.actions (COMMANDS guard)            τύπος υποχρέωσης <-> εντολή

Αυτό εδώ καλύπτει τα υπόλοιπα — όσα ΔΕΝ μπορούν να ελεγχθούν με σκέτη
introspection, γιατί η απάντηση είναι «καλεί κανείς αυτό το πράγμα;». Τα
βρίσκουμε διαβάζοντας το AST των αντίστοιχων module.

Κάθε ένα από αυτά, αν λείψει, ΔΕΝ βγάζει κανένα μήνυμα: το πρόγραμμα τρέχει
και δίνει λάθος αποτέλεσμα.
"""

import ast
from pathlib import Path

import _bootstrap  # noqa: F401

import misevent.rules.config as config
import misevent.rules.obligation_types as ot
from misevent.domain.events import EVENT_TYPES
from misevent.domain.projections import PARAM_EVENT_FAMILY

ROOT = Path(_bootstrap.ROOT)


def _tree(relative_path: str) -> ast.Module:
    return ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 1. events
# Events που ΣΚΟΠΙΜΑ δεν έχουν arm στο projections: δεν κρατούν κατάσταση.
NO_PROJECTION = {
    # Σύνοψη που κλείνει ΜΟΝΟ την υποχρέωση WAGE_ADJUSTMENT (scheduler.py).
    # Οι ίδιες οι προσαρμογές είναι ξεχωριστά ContractChanged events.
    "WageAdjustmentApplied",
    # Ίδιο μοτίβο: κλείνει ΜΟΝΟ την RETRO_SCHEDULE — οι ημέρες είναι
    # DailyScheduleAssigned + ScheduleDaysSubmitted.
    "RetroScheduleSubmitted",
    # ΙΣΤΟΡΙΚΟ: η οικογένεια παραμέτρων «teka» καταργήθηκε (τα ποσοστά
    # επικουρικής έρχονται πάντα από το ΚΠΚ της κάλυψης). Το event μένει στο
    # μητρώο ΜΟΝΟ για να αποκωδικοποιούνται παλιά logs — καμία κατάσταση.
    "TekaRatesDefined",
}


def test_every_event_is_handled_by_projections():
    """ΣΙΩΠΗΛΟ #1: η αλυσίδα `Projections.handle` ΔΕΝ έχει τελικό `else`.

    Event χωρίς arm γράφεται κανονικά στο log και αγνοείται — για πάντα, και
    στο replay. Καμία εξαίρεση, κανένα μήνυμα, απλώς κατάσταση που λείπει.
    """
    tree = _tree("misevent/domain/projections.py")
    handled = {
        name.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "isinstance"
        for arg in node.args[1:]
        for name in ast.walk(arg)
        if isinstance(name, ast.Name)
    }
    # Οι παράμετροι πιάνονται από ΕΝΑ γενικό arm μέσω του μητρώου.
    handled |= {cls.__name__ for cls in PARAM_EVENT_FAMILY}

    orphans = sorted(set(EVENT_TYPES) - handled - NO_PROJECTION)
    assert not orphans, (
        "Events χωρίς arm στο `Projections.handle` — θα αγνοούνταν σιωπηλά: "
        + ", ".join(orphans)
        + ".\nΑν είναι σκόπιμο (δεν κρατούν κατάσταση), βάλ' τα στο NO_PROJECTION."
    )


def test_no_projection_allowlist_has_no_leftovers():
    """Το allowlist να μη μείνει να καλύπτει event που δεν υπάρχει πια."""
    unknown = sorted(NO_PROJECTION - set(EVENT_TYPES))
    assert not unknown, "Άγνωστα events στο NO_PROJECTION: " + ", ".join(unknown)


# -------------------------------------------------------------- 2. παράμετροι
def test_every_parameter_family_has_a_reader():
    """ΣΙΩΠΗΛΟ #2: οικογένεια χωρίς `x_for(d)` είναι ΝΕΚΡΗ.

    Το μητρώο, η εντολή, το projection και η καρτέλα στο /settings δουλεύουν
    όλα — απλώς κανένας υπολογισμός δεν διαβάζει ποτέ την παράμετρο. Οι
    validators του import δεν το πιάνουν, γιατί δομικά είναι όλα σωστά.

    Ο reader ΠΡΕΠΕΙ να περνά από το `_pick`: εκεί ζει η συμπεριφορά της
    κατάργησης (σκάει, δεν υποχωρεί σιωπηλά στην προηγούμενη γραμμή).
    """
    tree = _tree("misevent/rules/config.py")
    read = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "_pick"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    orphans = sorted(set(config.FAMILIES) - read)
    assert not orphans, (
        "Οικογένειες παραμέτρων χωρίς reader `_pick(\"<key>\", …)` στο config.py "
        "— καταχωρούνται αλλά δεν τις διαβάζει κανείς: " + ", ".join(orphans)
    )


# Widgets που ξέρει να ζωγραφίσει το `partials/_param_modal.html`.
# Ό,τι άλλο πέφτει στο τελικό `else` (money) — σιωπηλά λάθος πεδίο.
RENDERABLE_WIDGETS = {"int", "pct", "money"}


def test_every_field_kind_is_renderable():
    """ΣΙΩΠΗΛΟ #3: νέο `FIELD_KINDS[...]["ui"]` που δεν ξέρει το template.

    Το modal έχει `if int / elif pct / else money`. Άγνωστο widget δεν σκάει —
    ζωγραφίζει money. Για ποσοστό αυτό σημαίνει και λάθος κλίμακα (το `pct`
    πολλαπλασιάζεται/διαιρείται με 100 στο settings.py).
    """
    bad = sorted(
        f"{kind} -> {spec['ui']}"
        for kind, spec in config.FIELD_KINDS.items()
        if spec["ui"] not in RENDERABLE_WIDGETS
    )
    assert not bad, (
        "FIELD_KINDS με widget που δεν ζωγραφίζει το _param_modal.html: "
        + ", ".join(bad)
    )


# ------------------------------------------------------------- 3. υποχρεώσεις
def _fulfilled_types() -> set[str]:
    """Τύποι υποχρέωσης που κάποιο event τους κλείνει, από το AST του scheduler.

    Το `_fulfill` καλείται είτε με σταθερά (`_fulfill("MONTHLY_PAYROLL", …)`)
    είτε με μεταβλητή που πήρε τιμή από τριαδικό (`t = "A" if … else "B"`)
    ή από το `PAYMENT_TYPES`. Μαζεύουμε και τα τρία.
    """
    tree = _tree("misevent/domain/scheduler.py")
    literal: set[str] = set()
    via_name: set[str] = set()

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "_fulfill" or not node.args:
            continue
        target = node.args[0]
        if isinstance(target, ast.Constant):
            literal.add(target.value)
        elif isinstance(target, ast.Name):
            via_name.add(target.id)

    # Κάθε σταθερά που ανατίθεται σε τέτοια μεταβλητή, οπουδήποτε στο module.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if not names & via_name:
            continue
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                literal.add(sub.value)
        # `t = PAYMENT_TYPES.get(...)` -> όλες οι τιμές του χάρτη
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Name) and sub.id == "PAYMENT_TYPES":
                from misevent.domain.scheduler import PAYMENT_TYPES

                literal |= set(PAYMENT_TYPES.values())

    return literal


def test_every_obligation_type_can_be_fulfilled():
    """ΣΙΩΠΗΛΟ #4: υποχρέωση που κανένα event δεν την κλείνει.

    Το `closed_by` αναφέρεται στο docstring του obligation_types αλλά ΔΕΝ
    είναι πεδίο του `ObligationType` — η εκπλήρωση είναι χειρόγραφες κλήσεις
    `_fulfill` μέσα στον scheduler. Αν ξεχαστεί, η υποχρέωση γεννιέται, δεν
    κλείνει ΠΟΤΕ, και **μπλοκάρει όσες εξαρτώνται από αυτήν** — χωρίς σφάλμα.
    """
    orphans = sorted(set(ot.TYPES) - _fulfilled_types())
    assert not orphans, (
        "Τύποι υποχρέωσης που κανένα event δεν τους εκπληρώνει (θα έμεναν "
        "ανοιχτοί για πάντα και θα μπλόκαραν την αλυσίδα): " + ", ".join(orphans)
    )


def test_every_obligation_type_has_a_command():
    """Το ίδιο δίχτυ με το `webapp/actions.py`, χωρίς να χρειάζεται το Flask:
    κάθε τύπος πρέπει να έχει υλοποιημένη εντολή."""
    from webapp.actions import COMMANDS

    missing = sorted({t.command for t in ot.TYPES.values()} - set(COMMANDS))
    assert not missing, "Τύποι υποχρέωσης χωρίς εντολή: " + ", ".join(missing)
