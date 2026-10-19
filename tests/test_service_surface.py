"""
ΔΙΧΤΥ ΤΟΥ ΣΠΑΣΙΜΑΤΟΣ ΤΟΥ `services.py` ΣΕ MIXINS.

Το `PayrollService` είναι σπασμένο σε θεματικά mixins. Δύο πράγματα μπορούν να
πάνε στραβά και **ΚΑΝΕΝΑ ΔΕΝ ΒΓΑΖΕΙ ΣΦΑΛΜΑ**:

1. **Χαμένη μέθοδος** — ένα κομμάτι δεν μεταφέρθηκε. Θα σκάσει αργότερα, σε
   κάποια οθόνη, ως AttributeError.
2. **ΕΠΙΚΑΛΥΨΗ ΟΝΟΜΑΤΟΣ** — το ίδιο όνομα ορίζεται σε ΔΥΟ mixins. Το MRO
   παίρνει σιωπηλά το πρώτο και ο υπολογισμός αλλάζει **χωρίς κανένα μήνυμα**.
   Αυτό είναι το επικίνδυνο.

Το snapshot κλειδώνει την επιφάνεια. Αν προσθέσεις/αφαιρέσεις μέθοδο ΣΚΟΠΙΜΑ:
    uv run tests/test_service_surface.py --update
και δες το diff στο git — πρέπει να είναι ακριβώς ό,τι ήθελες.
"""

import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from misevent.app.services import PayrollService

SNAPSHOT = Path("tests/conformance/service_surface.json")


def public_surface(cls) -> list[str]:
    """Όλα τα ονόματα που ορίζει η κλάση + τα mixins της (χωρίς dunders)."""
    names = set()
    for klass in cls.__mro__:
        if klass is object:
            continue
        names |= {n for n in vars(klass) if not n.startswith("__")}
    return sorted(names)


# ------------------------------------------- 1. ΚΑΜΙΑ ΕΠΙΚΑΛΥΨΗ ΟΝΟΜΑΤΟΣ
def test_no_name_collision():
    """ΤΟ ΕΠΙΚΙΝΔΥΝΟ: το ίδιο όνομα σε δύο mixins — το MRO παίρνει σιωπηλά το
    πρώτο και ο υπολογισμός αλλάζει χωρίς κανένα μήνυμα."""
    owners: dict[str, list[str]] = {}
    for klass in PayrollService.__mro__:
        if klass is object:
            continue
        for name, value in vars(klass).items():
            if name.startswith("__"):
                continue
            owners.setdefault(name, []).append(klass.__name__)

    dupes = {n: ks for n, ks in owners.items() if len(ks) > 1}
    assert not dupes, "\n".join(
        f"ΕΠΙΚΑΛΥΨΗ «{name}» σε: {', '.join(klasses)} — το MRO παίρνει "
        f"σιωπηλά το {klasses[0]}"
        for name, klasses in sorted(dupes.items())
    )


# --------------------------------------------------- 2. Η ΕΠΙΦΑΝΕΙΑ ΚΛΕΙΔΩΝΕΙ
def test_surface_matches_snapshot():
    assert SNAPSHOT.exists(), (
        "λείπει το snapshot· τρέξε: uv run tests/test_service_surface.py --update"
    )
    surface = public_surface(PayrollService)
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    lost = [n for n in expected if n not in surface]
    added = [n for n in surface if n not in expected]
    problems = [f"ΧΑΘΗΚΕ η «{n}» — δεν μεταφέρθηκε σε κανένα mixin;" for n in lost]
    problems += [f"ΝΕΑ «{n}» — αν είναι σκόπιμο, τρέξε με --update" for n in added]
    assert not problems, "\n".join(problems)


# ------------------------------------- 3. ΤΑ ΔΗΜΟΣΙΑ ΟΝΟΜΑΤΑ ΤΟΥ MODULE
def test_module_level_names_still_importable():
    for name in ("PayrollService", "DISMISSAL_REASON", "VOLUNTARY_EXIT_REASON"):
        try:
            __import__("misevent.app.services", fromlist=[name]).__getattribute__(name)
        except AttributeError:
            raise AssertionError(f"Το `from services import {name}` έσπασε")


def _update_snapshot() -> int:
    """`--update`: ξανακλειδώνει την επιφάνεια. Δες ΠΑΝΤΑ το diff στο git."""
    surface = public_surface(PayrollService)
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(
        json.dumps(surface, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"OK - κλειδώθηκαν {len(surface)} ονόματα στο {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    if "--update" in sys.argv:
        sys.exit(_update_snapshot())
    test_no_name_collision()
    test_surface_matches_snapshot()
    test_module_level_names_still_importable()
    print("OK - επιφάνεια PayrollService: καμία επικάλυψη, ίδια με το snapshot")
