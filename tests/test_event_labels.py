"""Κάθε event έχει ελληνική ετικέτα για το Audit log (webapp/labels.py)."""

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.domain.events import EVENT_TYPES
from webapp.labels import EVENT_LABELS


def test_every_event_has_a_greek_label():
    missing = sorted(set(EVENT_TYPES) - set(EVENT_LABELS))
    assert not missing, f"Λείπει ετικέτα στο labels.EVENT_LABELS: {missing}"
