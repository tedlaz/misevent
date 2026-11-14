"""Ποσά σε ακέραια cents στη βάση, ευρώ στον κώδικα (events.MONEY_PATHS)."""

import json
from dataclasses import fields

import _bootstrap  # noqa: F401
import pytest

from misevent.domain import events as ev


def test_every_float_field_is_classified():
    """Νέο float πεδίο σε event: ή ποσό (MONEY_PATHS) ή ρητά όχι (NOT_MONEY)."""
    for name, cls in ev.EVENT_TYPES.items():
        money = {p.split(".")[0] for p in ev.MONEY_PATHS.get(name, ())}
        for f in fields(cls):
            if "float" in str(f.type) and f.name not in money:
                assert f.name in ev.NOT_MONEY, f"{name}.{f.name}: ποσό ή όχι;"
    for name, paths in ev.MONEY_PATHS.items():
        names = {f.name for f in fields(ev.EVENT_TYPES[name])}
        for p in paths:
            assert p.split(".")[0] in names, f"{name}: άγνωστη διαδρομή {p}"


def test_round_trip_in_cents():
    e = ev.MonthlyPayrollCalculated(
        "E1", 2026, 3, 2.68, 0.36, 0.59, 2.32, 0.0, 2.32,
        analysis={"gross": 1234.56, "day_pay": 49.308, "hourly_rate": 5.895,
                  "premium_lines": {"night": {"amount": 10.1, "hours": 2.5}},
                  "contributions": [{"employee": 0.36, "employee_rate": 0.1337}]},
    )
    stored = json.loads(e.to_record("E1")["payload"])
    assert stored["gross"] == 268 and stored["net"] == 232
    a = stored["analysis"]
    assert a["gross"] == 123456 and a["day_pay"] == 49.308 and a["hourly_rate"] == 5.895
    assert a["premium_lines"]["night"] == {"amount": 1010, "hours": 2.5}
    assert a["contributions"][0] == {"employee": 36, "employee_rate": 0.1337}
    back = ev.upcast("MonthlyPayrollCalculated", stored)
    assert back["gross"] == 2.68 and back["analysis"]["gross"] == 1234.56
    assert back["analysis"]["contributions"][0]["employee"] == 0.36


def test_unrounded_amount_is_refused():
    with pytest.raises(ValueError, match="2 δεκαδικά"):
        ev.PaymentMade("salary", 2026, 3, 10.005).to_record("E1")
