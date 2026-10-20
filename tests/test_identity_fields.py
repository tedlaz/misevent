"""
Αριθμός ταυτότητας (λατινικά -> ελληνικά για ΔΑΤ), έλεγχος ΑΜΑ, και σβήσιμο
προαιρετικών πεδίων στη διόρθωση προσώπου.

Εκτέλεση: uv run pytest tests/test_identity_fields.py
"""

from datetime import date

import _bootstrap  # noqa: F401
import pytest

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.persistence.database import Database
from misevent.rules import constraints

DAT = constraints.GREEK_ID_TYPE


def test_greek_id_number_latin_lookalikes_become_greek():
    assert constraints.norm_identity_number("az 503-638", DAT) == "ΑΖ503638"
    assert constraints.norm_identity_number("ΑΖ503638", DAT) == "ΑΖ503638"
    with pytest.raises(ValueError, match="λατινικό γράμμα"):
        constraints.norm_identity_number("AS503638", DAT)  # S: χωρίς ελληνικό δίδυμο
    # Διαβατήριο: τα λατινικά είναι νόμιμα, μένουν
    assert constraints.norm_identity_number("ab1234567", "ΔΙΑ") == "AB1234567"


def test_ama_plausibility():
    for fake in ("000000001", "000000000", "111111111", "000000999"):
        assert not constraints.ama_plausible(fake)
    for real in ("001234567", "123456789", "000012345"):
        assert constraints.ama_plausible(real)


def _svc():
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    pid = svc.register_person(
        last_name="ΔΟΚΙΜΗ", first_name="ΜΑΡΙΑ", father_name="ΙΩΑΝΝΗΣ",
        mother_name="ΕΛΕΝΗ", birth_date=date(1990, 5, 5), afm="111222336",
        amka="05059012343", ama="001234567", email="a@b.gr", sex="1",
        nationality_code="048", identity_type=DAT, identity_number="ab123456",
        identity_issuer="ΑΤ ΑΘΗΝΩΝ", identity_issue_date=date(2020, 1, 1))
    return svc, proj, pid


def test_register_and_correct_normalize_id_and_reject_fake_ama():
    svc, proj, pid = _svc()
    assert proj.persons[pid].identity_number == "ΑΒ123456"  # ελληνικά
    svc.correct_person(pid, identity_number="AZ503638")
    assert proj.persons[pid].identity_number == "ΑΖ503638"
    with pytest.raises(ValueError, match="δεν είναι πραγματικός"):
        svc.correct_person(pid, ama="000000001")


def test_correction_clears_optional_fields_but_absent_means_unchanged():
    svc, proj, pid = _svc()
    # Κενά (όπως τα στέλνει η φόρμα διόρθωσης): ΣΒΗΝΟΝΤΑΙ
    svc.correct_person(pid, ama="", email="", identity_issuer="",
                       identity_issue_date=None)
    p = proj.persons[pid]
    assert (p.ama, p.email, p.identity_issuer, p.identity_issue_date) == ("", "", "", None)
    # Πεδίο που δεν στάλθηκε (None για κείμενο): ΧΩΡΙΣ αλλαγή
    svc.correct_person(pid, ama="001234567")
    svc.correct_person(pid, ama=None, children=1)
    assert proj.persons[pid].ama == "001234567"
