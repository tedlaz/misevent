from datetime import date

from misevent.rules import config
from webapp.views import employments


def test_minimum_compensation_uses_the_same_floor_as_server(monkeypatch):
    on = date(2026, 7, 31)
    monkeypatch.setattr(employments, "today", lambda: on)
    form = {
        "hire_date": on.isoformat(),
        "pay_type": "monthly",
        "weekly_hours": "30",
        "service_years": "3",
        "service_months": "0",
        "married": "1",
        "extra_allowances_pct": "5",
    }

    result = employments._minimum_compensation(form)
    expected = config.minimum_floor_value(
        "monthly", 30, on, service_years=3, married=True, extra_pct=5
    )

    assert result["floor"] == expected
    assert result["unit"] == "€/μήνα"
    assert "1 τριετίες" in result["description"]
    assert "γάμος" in result["description"]


def test_minimum_compensation_defaults_to_today_and_supports_daily(monkeypatch):
    on = date(2026, 7, 31)
    monkeypatch.setattr(employments, "today", lambda: on)

    result = employments._minimum_compensation(
        {
            "pay_type": "daily",
            "weekly_hours": "40",
            "service_years": "0",
            "service_months": "0",
            "extra_allowances_pct": "0",
        }
    )

    assert result["floor"] == config.minimum_floor_value("daily", 40, on)
    assert result["unit"] == "€/ημέρα"


def test_minimum_compensation_ignores_frozen_service_years(monkeypatch):
    """Ο παγωμένος χρόνος είναι ΕΠΙΠΛΕΟΝ και ΔΕΝ διαμορφώνει τριετίες: το
    όριο βγαίνει μόνο από τα 3 έτη προϋπηρεσίας, όσος κι αν είναι."""
    on = date(2026, 7, 31)
    monkeypatch.setattr(employments, "today", lambda: on)

    result = employments._minimum_compensation(
        {
            "hire_date": on.isoformat(),
            "pay_type": "monthly",
            "weekly_hours": "40",
            "service_years": "3",
            "service_months": "0",
            "frozen_service_years": "6",
            "frozen_service_months": "0",
            "extra_allowances_pct": "0",
        }
    )

    expected = config.minimum_floor_value(
        "monthly", 40, on, service_years=3, married=False, extra_pct=0
    )
    assert result["floor"] == expected
    assert "1 τριετίες" in result["description"]
