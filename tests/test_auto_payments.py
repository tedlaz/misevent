"""
ΑΥΤΟΜΑΤΗ ΕΞΟΦΛΗΣΗ ΠΛΗΡΩΜΩΝ (Διάφορα → `auto_payments`).

Ενεργή: οι πληρωμές δεν εμφανίζονται στο χρονολόγιο και εξοφλούνται μόλις
ξεκλειδώσουν — εδώ η «Πληρωμή μισθών» μόλις υπολογιστεί η μισθοδοσία.

⚠️ Ζητά `fresh_system`: στήνει εταιρεία και αλλάζει προτιμήσεις.
"""


def test_auto_payments(fresh_system):
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app
    from webapp import queries as q
    from webapp.actions import trigger
    from webapp.clock import today
    from webapp.helpers import run_command
    from webapp.system import proj, svc

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    def ob(code):
        return next(
            o for o in sorted(proj.obligations.values(), key=lambda o: o.due_date)
            if o.obligation_type == code and not o.fulfilled
        )

    def tracks():
        return {o["track"] for o in q.obligation_list(today(), show_locked=True)}

    with app.test_request_context("/"):
        assert "payment" not in tracks()  # προεπιλογή: ενεργή

        assert run_command(svc.set_misc_settings, auto_payments=False)[0]
        assert "payment" in tracks()

        assert run_command(svc.set_misc_settings, auto_payments=True)[0]
        assert "payment" not in tracks()

        pay = ob("SALARY_PAYMENT")
        ok, msg = run_command(trigger, ob("ATTENDANCE_APPROVAL").obligation_id)
        assert ok, msg
        ok, msg = run_command(trigger, ob("MONTHLY_PAYROLL").obligation_id)
        assert ok, msg
        assert proj.obligations[pay.obligation_id].fulfilled
        assert "εξοφλήθηκαν αυτόματα" in msg
        assert any(p.payment_type == "salary" for p in proj.payments)
