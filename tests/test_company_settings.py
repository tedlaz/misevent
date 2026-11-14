"""Οι ρυθμίσεις (κάρτα, πληρωμές, άδειες…) είναι ΤΗΣ ΕΤΑΙΡΕΙΑΣ, όχι της
εφαρμογής: δύο εταιρείες με κοινό store παραμέτρων κρατούν χωριστές ρυθμίσεις,
και αυτές ταξιδεύουν με το αρχείο της εταιρείας — όχι με το αρχείο παραμέτρων."""

import json

import _bootstrap  # noqa: F401

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.persistence.database import Database


def _company(db, shared, name):
    store = db.store(name, shared=shared)
    proj = Projections()
    store.subscribe(proj.handle)
    shared.subscribe(proj.handle)
    return PayrollService(store, proj)


def test_settings_are_per_company():
    db = Database()
    shared = db.store("shared")
    a, b = _company(db, shared, "A"), _company(db, shared, "B")
    a.set_misc_settings(digital_card=False, card_tolerance_minutes=10,
                        card_rounding_minutes=15, auto_payments=False)
    assert a.card_policy() == (10, 15) and not a.misc_settings()["digital_card"]
    assert b.card_policy() == (0, 30) and b.misc_settings()["digital_card"]
    assert b.misc_settings()["auto_payments"]

    # αρχείο ΠΑΡΑΜΕΤΡΩΝ (κοινό): χωρίς ρυθμίσεις εταιρείας
    assert "misc" not in a.export_parameters()
    # αρχείο ΕΤΑΙΡΕΙΑΣ: με τις ρυθμίσεις — και περνούν στην άλλη εταιρεία
    doc = a.export_company()
    assert doc["settings"]["card_tolerance_minutes"] == 10
    b.import_company(json.dumps({"schema": doc["schema"], "settings": doc["settings"]}))
    assert b.card_policy() == (10, 15) and not b.misc_settings()["digital_card"]
    assert not b.misc_settings()["auto_payments"]
