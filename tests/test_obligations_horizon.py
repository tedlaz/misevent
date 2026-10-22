"""
ΟΡΙΖΟΝΤΑΣ «ΕΚΚΡΕΜΩΝ» (Διάφορα → `obligations_horizon_months`).

Οι μελλοντικές εκκρεμείς φτάνουν έως το τέλος του μήνα αναφοράς + Ν
(προεπιλογή 1: τον 3/2026 έως 30/4/2026)· οι εκπρόθεσμες εμφανίζονται πάντα.

⚠️ Ζητά `fresh_system`: στήνει εταιρεία και αλλάζει προτιμήσεις.
"""


def test_obligations_horizon(fresh_system):
    from calendar import monthrange
    from datetime import date

    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app
    from webapp import queries as q
    from webapp.system import proj, svc

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert c.post("/seed").status_code == 200
    with app.test_request_context("/"):
        # αναφορά = η πρώτη προθεσμία του seed, ώστε να υπάρχουν μελλοντικές
        ref = min(o.due_date for o in proj.obligations.values())

        def month_end(k):  # τέλος του μήνα ref + k
            y, m = ref.year + (ref.month - 1 + k) // 12, (ref.month - 1 + k) % 12 + 1
            return date(y, m, monthrange(y, m)[1])

        def last_due(**kw):
            return max((o["due"] for o in q.obligation_list(ref, show_locked=True, **kw)),
                       default=date.min)

        assert proj.misc_settings["obligations_horizon_months"] == 1
        assert last_due() <= month_end(1)
        assert last_due(view="all") > month_end(1)  # οι «Όλες» δεν κόβονται

        svc.set_misc_settings(obligations_horizon_months=0)
        assert last_due() <= month_end(0)
        svc.set_misc_settings(obligations_horizon_months=12)
        assert last_due() > month_end(1)
        svc.set_misc_settings(obligations_horizon_months=1)


def test_wage_adjustment_first_in_month():
    """Η «Προσαρμογή στον κατώτατο μισθό» προηγείται πάντα μέσα στον μήνα —
    και του Δώρου Πάσχα με νωρίτερη προθεσμία (το προαπαιτεί)."""
    from datetime import date
    from types import SimpleNamespace as O

    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from misevent.rules import obligation_types as ot

    obs = [
        O(obligation_type="MONTHLY_PAYROLL", due_date=date(2026, 3, 31)),
        O(obligation_type="ATTENDANCE_APPROVAL", due_date=date(2026, 4, 30)),
        O(obligation_type="EASTER_GIFT", due_date=date(2026, 4, 8)),
        O(obligation_type="WAGE_ADJUSTMENT", due_date=date(2026, 4, 30)),
        O(obligation_type="MONTHLY_PAYROLL", due_date=date(2026, 4, 30)),
    ]
    assert [o.obligation_type for o in sorted(obs, key=ot.sort_key)] == [
        "MONTHLY_PAYROLL", "WAGE_ADJUSTMENT", "EASTER_GIFT",
        "ATTENDANCE_APPROVAL", "MONTHLY_PAYROLL",
    ]
