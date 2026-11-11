"""Καθεστώτα ΦΜΥ (config.TAX_MODELS): νέο καθεστώς = κλάση + εγγραφή, ΧΩΡΙΣ
αλλαγές στο UI· άγνωστο καθεστώς στο replay ΣΚΑΕΙ (δεν πέφτει σιωπηλά στο
standard = λάθος φόρος)."""

from dataclasses import dataclass
from datetime import date

import pytest


def test_unknown_tax_model_fails_on_replay():
    from misevent.domain.events import ParametersDefined
    from misevent.domain.projections import param_entry

    ev = ParametersDefined(
        family="tax", valid_from=date(2027, 1, 1),
        values={"model": "removed_model", "brackets": [{"up_to": None, "rate": 0.2}]},
    )
    with pytest.raises(ValueError, match="removed_model"):
        param_entry(ev)


def test_new_tax_model_form_is_generated(monkeypatch):
    from misevent.rules import config
    from webapp.views import settings as st

    @dataclass(frozen=True)
    class FakeModel(config.TaxScale):
        flat_rates: tuple = config._ui((0.1, 0.2), "Συντελεστές (%)", pct=True)
        max_age: int = config._ui(30, "Ηλικία")
        model_key = "fake"
        model_label = "Δοκιμαστικό"

    monkeypatch.setitem(config.TAX_MODELS, "fake", FakeModel)

    forms = {m["key"]: m for m in st._tax_model_forms()}
    assert [f["name"] for f in forms["fake"]["fields"]] == ["fake__flat_rates", "fake__max_age"]
    p = st._tax_prefill(None)
    assert p["fake__flat_rates"] == "10;20" and p["fake__max_age"] == "30"
    params = st._tax_model_params({"fake__flat_rates": "5;15", "fake__max_age": "28"}, "fake")
    assert params == {"flat_rates": [0.05, 0.15], "max_age": 28}


def test_youth2026_form_roundtrip():
    """Οι προεπιλογές του youth2026 περνούν από τη φόρμα και γυρίζουν ίδιες."""
    from misevent.rules import config
    from webapp.views import settings as st

    p = st._tax_prefill(None)
    params = st._tax_model_params(p, "youth2026")
    for f in config.tax_model_ui_fields("youth2026"):
        got = params[f.name]
        want = list(f.default) if isinstance(f.default, tuple) else f.default
        assert got == pytest.approx(want), f.name


def test_tax_modal_save_roundtrip(fresh_system):
    """Το modal αποδίδει τα πεδία του youth2026 και η αποθήκευση τα γράφει."""
    from _bootstrap import ui_client

    from misevent.rules import config
    from webapp import create_app

    client = ui_client(create_app())
    body = client.get("/settings/tax/modal").get_data(as_text=True)
    assert 'name="youth2026__band1_child_rates"' in body
    form = {"valid_from": "2027-01-01", "model": "youth2026",
            "brackets": "10000;9\n20000;22\n30000;28\n40000;36\n;44",
            "reductions": "777;900;1120;1340;1580",
            "youth2026__band1_child_rates": "8;8;8;8;0",
            "youth2026__youth_low_max_age": "29"}
    client.post("/settings/tax", data=form)
    s = config.tax_scale_for(date(2027, 2, 1))
    assert s.band1_child_rates == pytest.approx((0.08, 0.08, 0.08, 0.08, 0.0))
    assert s.youth_low_max_age == 29
