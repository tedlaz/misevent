"""Εταιρεία DEMO: 5 εργαζόμενοι, κλειστές περίοδοι από 1/1 ως τον προηγούμενο μήνα."""

from datetime import date

import _bootstrap  # noqa: F401

from misevent.rules import config


def test_demo_company(fresh_system):
    from _bootstrap import ui_client

    from webapp import clock, create_app
    from webapp.system import auth, registry

    app = create_app()
    client = ui_client(app)
    r = client.post("/companies/demo")
    assert r.status_code == 302, r.get_data(as_text=True)[:500]

    (cid,) = [c.company_id for c in auth.companies.values() if c.name == "DEMO"]
    proj = registry.tenant(cid).proj
    emps = list(proj.employments.values())
    with app.test_request_context():
        today = clock.today()

    assert len(emps) == 5
    kinds = sorted((e.pay_type, e.weekly_hours < 40) for e in emps)
    assert kinds == [("daily", False), ("daily", True),
                     ("monthly", False), ("monthly", False), ("monthly", True)]
    assert all(e.schedule_mode == "variable" for e in emps if e.pay_type == "daily")
    assert any(today.year - e.person.birth_date.year < 25 for e in emps)

    # Κάθε μήνας πριν τον τρέχοντα: μισθοδοσία για όλους, καμία εκκρεμότητα.
    closed = {(p.period_year, p.period_month) for p in proj.payrolls}
    assert closed == {(today.year, m) for m in range(1, today.month)}
    assert len(proj.payrolls) == 5 * (today.month - 1)
    stuck = [o.obligation_type for o in proj.obligations.values()
             if not o.fulfilled and o.period_month
             and (o.period_year, o.period_month) < (today.year, today.month)]
    assert not stuck, stuck
    # Ο μισθωτός πλήρους παίρνει τον βασικό της περιόδου.
    for p in proj.payrolls:
        e = proj.employments[p.employee_id]
        if e.pay_type == "monthly" and e.weekly_hours == 40:
            on = config.minimum_wage_for(date(p.period_year, p.period_month, 1))
            assert abs(p.gross - on.monthly) < 0.01, (p.period_month, p.gross)

    # Ημερομίσθιος 20ω, ίδιο ημερομίσθιο/ημέρες με τον πλήρους -> ΜΙΣΑ μικτά,
    # σε κάθε μήνα ΚΑΙ στο Δώρο (αμοιβή ημέρας = ωρομίσθιο × ώρες ημέρας).
    daily = {e.weekly_hours: e.employee_id for e in emps if e.pay_type == "daily"}
    full, part = daily[40.0], daily[20.0]
    gross = {(p.employee_id, p.period_month): p.gross for p in proj.payrolls}
    for m in range(1, today.month):
        assert abs(gross[part, m] * 2 - gross[full, m]) < 0.02, (m, gross[part, m], gross[full, m])
    gifts = {g.employee_id: g.gross for g in proj.gifts if g.gift_type == "easter"}
    if gifts:
        assert abs(gifts[part] * 2 - gifts[full]) < 0.02, gifts
