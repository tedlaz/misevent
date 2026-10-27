"""Όνομα μενού (δημιουργία) ≠ επίσημη ονομασία (στοιχεία εργοδότη)."""

import _bootstrap  # noqa: F401


def test_menu_name_vs_official_name(fresh_system):
    from webapp.system import registry

    cid = registry.create_company("Παπαδόπουλος")
    assert registry.official_name(cid) == ""  # χωρίς στοιχεία εργοδότη

    registry.tenant(cid).svc.register_company(
        ame="1234567890", afm="999888771", legal_name="ΠΑΠΑΔΟΠΟΥΛΟΣ",
        first_name="ΓΕΩΡΓΙΟΣ", father_name="ΙΩΑΝΝΗΣ",
    )
    assert registry.display_name(cid) == "Παπαδόπουλος"  # το μενού δεν αλλάζει
    assert registry.official_name(cid) == "ΠΑΠΑΔΟΠΟΥΛΟΣ ΓΕΩΡΓΙΟΣ (πατρώνυμο ΙΩΑΝΝΗΣ)"

    registry.auth_svc.rename_company(cid, "Γιώργος")
    assert registry.display_name(cid) == "Γιώργος"
