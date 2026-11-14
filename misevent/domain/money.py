"""Χρήματα: ΜΙΑ στρογγυλοποίηση (HALF_UP) και αριθμητική σε ακέραια cents.

Η μηχανή (`payroll_engine`) υπολογίζει σε `int` cents· τα events (στον κώδικα),
το UI και η ΑΠΔ βλέπουν ευρώ με 2 δεκαδικά (`eur`). Στη ΒΑΣΗ τα ποσά των events
γράφονται σε cents — βλ. `events.MONEY_PATHS`. Σκέτο `round(x, 2)` σε ποσά
ΑΠΑΓΟΡΕΥΕΤΑΙ: στρογγυλεύει τη δυαδική τιμή (`round(2.675, 2) == 2.67`).
"""

from decimal import ROUND_HALF_UP, Decimal

_ONE = Decimal(1)


def dec(x) -> Decimal:
    """Ακριβής δεκαδική μορφή: float μέσω `repr` (2.675 -> Decimal('2.675'))."""
    if isinstance(x, Decimal):
        return x
    return Decimal(repr(x)) if isinstance(x, float) else Decimal(x)


def cents(euros) -> int:
    """Ευρώ -> ακέραια cents, HALF_UP (μακριά από το μηδέν στα αρνητικά)."""
    return int((dec(euros) * 100).quantize(_ONE, ROUND_HALF_UP))


def mul(c: int, rate) -> int:
    """cents × ποσοστό (κλάσμα) -> cents, HALF_UP."""
    return int((Decimal(c) * dec(rate)).quantize(_ONE, ROUND_HALF_UP))


def eur(c: int) -> float:
    """cents -> ευρώ (float με ακριβώς 2 δεκαδικά στο repr)."""
    return c / 100


def r2(x) -> float:
    """Στρογγυλοποίηση ποσού σε λεπτά (HALF_UP), σε ευρώ."""
    return eur(cents(x))
