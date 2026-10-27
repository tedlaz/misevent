"""
«ΧΩΡΑΕΙ;» — διαθέσιμες εργάσιμες ως 31/12 έναντι υπολοίπου κανονικής άδειας,
χωρίς τις περιόδους αιχμής της εταιρείας (Διάφορα → `peak_periods`).

⚠️ Ζητά `fresh_system`: στήνει εταιρεία και αλλάζει προτιμήσεις.
"""

from datetime import date

import pytest


def test_day_ranges():
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from misevent.rules.calendar_gr import day_ranges

    assert day_ranges("", 2026) == set()
    days = day_ranges("15/12-31/12, 01/08-02/08", 2026)
    assert len(days) == 17 + 2
    assert date(2026, 12, 15) in days and date(2026, 12, 14) not in days
    for bad in ("15/12", "31/12-15/12", "32/12-31/12", "α/β-γ/δ"):
        with pytest.raises(ValueError):
            day_ranges(bad, 2026)


def test_leave_fit(fresh_system):
    import _bootstrap  # noqa: F401

    from webapp import create_app
    from webapp import queries as q
    from webapp.system import proj, svc

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200
    with app.test_request_context("/"):
        e = next(e for e in proj.employments.values() if e.termination_date is None)
        today = date(max(e.hire_date.year, 2026), 12, 1)
        base = q.leave_fit(e, today)
        assert base["fit_blocked"] == 0
        remaining = q.leave_status(e, today.year)["remaining"]
        assert base["fit_slack"] == base["fit_available"] - remaining

        svc.set_misc_settings(peak_periods="15/12-31/12")
        peak = q.leave_fit(e, today)
        assert peak["fit_blocked"] > 0
        assert peak["fit_available"] + peak["fit_blocked"] == base["fit_available"]

        with pytest.raises(ValueError):
            svc.set_misc_settings(peak_periods="31/12-15/12")
        svc.set_misc_settings(peak_periods="")
