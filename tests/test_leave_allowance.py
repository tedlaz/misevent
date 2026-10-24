"""
ΕΠΙΔΟΜΑ ΑΔΕΙΑΣ — ο κανόνας της αναλογίας και το τίμημά του.

Η αναλογία του 1ου/2ου έτους μετριέται από την πρόσληψη **έως 31/12** (ή έως
την αποχώρηση). Ο μήνας της πρώτης άδειας ορίζει την ΠΕΡΙΟΔΟ της υποχρέωσης,
ΟΧΙ το ποσό: όσο μετρούσαμε μέχρι εκεί, όποιος έπαιρνε άδεια πριν τους ~7
μήνες έπαιρνε λιγότερο από τον μισό μισθό — και το υπόλοιπο ΧΑΝΟΤΑΝ, αφού το
επίδομα είναι ένα και μόνο ανά (εργαζόμενο, έτος).

Το τίμημα είναι μια ΠΑΡΑΔΟΧΗ: ότι ο εργαζόμενος θα μείνει ως το τέλος του
έτους. Γι' αυτό ελέγχονται εδώ ΚΑΙ τα δύο αντίβαρά της — η ονομαστική
προειδοποίηση πριν τον υπολογισμό και η δυνατότητα αναβολής έως 31/12.

Εκτέλεση: uv run pytest tests/test_leave_allowance.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain.events import LeaveType
from misevent.domain.projections import Projections
from misevent.domain.scheduler import ObligationScheduler
from misevent.persistence.database import Database

FULL_WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}


def setup(hire: date, monthly_gross: float = 1200.0, schedule: bool = True,
          horizon: int = 1):
    """Εταιρεία με έναν εργαζόμενο — ό,τι χρειάζεται ο υπολογισμός.

    `schedule=False`: χωρίς χρονολόγιο υποχρεώσεων, ώστε η αποχώρηση να μη
    δεσμεύεται από την ΤΡΕΧΟΥΣΑ περίοδο μισθοδοσίας (§2) — εδώ μας ενδιαφέρει
    το ΠΟΣΟ της εκκαθάρισης, όχι η αλυσίδα."""
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    if schedule:
        store.subscribe(
            ObligationScheduler(store, proj, horizon_months=horizon).handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0, "ΔΑΣΟΥΣ", "20", "14456", "ΑΘΗΝΑ")
    svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
    svc.define_kpk_rates(
        "efka_main", "0101", date(2024, 1, 1), 0.1337, 0.2179,
        "main_pension=10,37/18,79|healthcare=0/0|auxiliary=3/3",
    )
    svc.define_specialty(
        "ΥΠΑΛΛΗΛΟΣ",
        {"MAIN_EPIK": {"kad": "0110", "specialty_code": "122100",
                       "kpk": "0101"}},
    )
    pid = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ", first_name="ΙΩΑΝΝΗΣ", father_name="ΝΙΚΟΛΑΟΣ",
        mother_name="ΜΑΡΙΑ", birth_date=date(1990, 5, 5), afm="111222336",
        amka="05059012343", ama="123456789",
    )
    eid = svc.start_employment(
        pid, hire_date=hire, monthly_gross=monthly_gross,
        weekly_hours=40.0, weekly_pattern=FULL_WEEK,
    )
    svc.assign_specialty(eid, "ΥΠΑΛΛΗΛΟΣ")
    svc.submit_hire_e3(eid)
    svc.submit_hire_wto(eid)
    return svc, proj, eid


def test_early_leave_does_not_shrink_the_allowance():
    """ΤΟ ΙΔΙΟ ΤΟ ΛΑΘΟΣ: άδεια τον Μάρτιο, πρόσληψη Ιανουαρίου.

    Με τον παλιό κανόνα έβγαινε αναλογία 3 μηνών (288 €) και το υπόλοιπο δεν
    ερχόταν ποτέ. Τώρα μετράει ολόκληρο το έτος -> πλαφόν μισού μισθού."""
    svc, proj, eid = setup(date(2026, 1, 5))
    svc.record_leave(eid, LeaveType.ANNUAL, date(2026, 3, 16), date(2026, 3, 20))
    svc.run_leave_allowance(2026, 3)

    assert len(proj.allowances) == 1
    assert proj.allowances[0].gross == 600.0, "1200/2 — ο μισός μισθός"


def test_mid_year_hire_keeps_its_proportion():
    """Η αναλογία ΔΕΝ καταργήθηκε: πρόσληψη Σεπτεμβρίου -> 4 μήνες (Σεπ-Δεκ),
    2 ημερομίσθια ο μήνας επί 1200/25 = 384 €. Ένα «πάντα ολόκληρο» θα
    πλήρωνε 600 € σε κάποιον που δούλεψε τέσσερις μήνες."""
    svc, proj, eid = setup(date(2026, 9, 1))
    svc.record_leave(eid, LeaveType.ANNUAL, date(2026, 10, 12), date(2026, 10, 13))
    svc.run_leave_allowance(2026, 10)

    assert proj.allowances[0].gross == 384.0


def test_termination_is_what_cuts_the_proportion():
    """Η αποχώρηση — και ΜΟΝΟ αυτή — κόβει τους μήνες: άδεια Μάρτιο και
    αποχώρηση 30/6 δίνει 6 μήνες (576 €), όχι 3 (288 €)."""
    svc, proj, eid = setup(date(2025, 4, 8), schedule=False)  # 2ο έτος το 2026
    svc.record_leave(eid, LeaveType.ANNUAL, date(2026, 3, 16), date(2026, 3, 20))

    emp = proj.employments[eid]
    assert svc.allowance_preview_for(emp, 2026, term=date(2026, 6, 30)).gross == 576.0

    svc.terminate(eid, date(2026, 6, 30), "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
    assert len(proj.allowances) == 1
    assert proj.allowances[0].gross == 576.0


def test_third_year_is_untouched():
    """3ο έτος+: ολόκληρο, ό,τι μήνα κι αν παρθεί η άδεια (αμετάβλητο)."""
    svc, proj, eid = setup(date(2024, 4, 8))
    svc.record_leave(eid, LeaveType.ANNUAL, date(2026, 2, 16), date(2026, 2, 20))
    svc.run_leave_allowance(2026, 2)

    assert proj.allowances[0].gross == 600.0


# ─────────────────────────── Τα δύο αντίβαρα της παραδοχής ──────────────────

def test_warning_and_postponement(fresh_system):
    """Η ΠΑΡΑΔΟΧΗ πρέπει να είναι ορατή και αναστρέψιμη.

    (α) `allowance_warnings`: ονομαστική προειδοποίηση για 1ο/2ο έτος, σιωπή
    όπου δεν υπάρχει παραδοχή (3ο έτος+, ή γνωστή αποχώρηση)·
    (β) η υποχρέωση αναβάλλεται έως 31/12 — και ΟΧΙ πέρα από εκεί·
    (γ) η προεπισκόπηση της οθόνης είναι Η ΙΔΙΑ κλήση με τον υπολογισμό.
    """
    from misevent.domain.events import LeaveType
    from webapp import create_app
    from webapp import queries as q
    from webapp.clock import today
    from webapp.system import proj, svc

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    with app.test_request_context("/"):
        year = today().year
        # Οι εργαζόμενοι του seed προσλαμβάνονται ΜΕΣΑ στο τρέχον έτος -> 1ο έτος
        emp = next(e for e in proj.employments.values() if e.active)
        assert emp.hire_date.year == year, "αναμενόταν εργαζόμενος 1ου έτους"
        m = 10 if today().month <= 10 else 11
        svc.record_leave(emp.employment_id, LeaveType.ANNUAL,
                         date(year, m, 12), date(year, m, 13))

        # (α) η προειδοποίηση ονομάζει τον εργαζόμενο και το ποσό
        warnings = q.allowance_warnings(year, m)
        assert len(warnings) == 1 and emp.full_name in warnings[0]
        assert f"31/12/{year}" in warnings[0]
        # 3ο έτος+ (ίδιος εργαζόμενος, δύο χρόνια μετά): καμία παραδοχή
        svc.record_leave(emp.employment_id, LeaveType.ANNUAL,
                         date(year + 2, 3, 10), date(year + 2, 3, 11))
        assert q.allowance_warnings(year + 2, 3) == []

        # (γ) προεπισκόπηση οθόνης == υπολογισμός (ΜΙΑ πηγή)
        rows = q.gift_rows("allowance", year, preview=True, month=m)
        row = next(r for r in rows if r["Εργαζόμενος"] == emp.full_name)
        assert row["Μικτά"] == svc.allowance_preview_for(emp, year).gross

        # (β) αναβολή: έως 31/12 ναι, μετά όχι
        ob = next(o for o in proj.obligations.values()
                  if o.obligation_type == "LEAVE_ALLOWANCE"
                  and o.period_year == year and o.period_month == m)
        detail = q.detail_payload(ob.obligation_id, today())
        assert detail["can_extend"]
        assert detail["extend_max"] == date(year, 12, 31)
        assert "ΑΠΔ" in detail["extend_note"], "η αναβολή δεν είναι ανώδυνη"

        try:
            svc.extend_obligation(ob.obligation_id, date(year + 1, 1, 15))
            raise AssertionError("η παράταση βγήκε έξω από το έτος της περιόδου")
        except ValueError as e:
            assert "τέλος του έτους" in str(e)
        svc.extend_obligation(ob.obligation_id, date(year, 12, 31), "αναμονή")
        assert proj.obligations[ob.obligation_id].due_date == date(year, 12, 31)


def _allowance_obs(proj, year):
    return {(o.period_month, o.fulfilled) for o in proj.obligations.values()
            if o.obligation_type == "LEAVE_ALLOWANCE" and o.period_year == year}


def test_no_leave_in_the_year_means_mandatory_december_allowance():
    """Το επίδομα ΠΡΕΠΕΙ να καταβληθεί μέσα στο έτος: όποιος δεν πήρε άδεια
    (ούτε αποχώρησε) το παίρνει ΥΠΟΧΡΕΩΤΙΚΑ τον Δεκέμβριο — η υποχρέωση
    γεννιέται με την περίοδο Δεκεμβρίου, χωρίς καμία LeaveRecorded."""
    svc, proj, eid = setup(date(2026, 12, 1))  # horizon 1 -> ανοίγει ο Δεκέμβριος
    emp = proj.employments[eid]
    assert emp.allowance_period_month(2026) == 12
    assert emp.allowance_period_month(2025) is None  # δεν απασχολήθηκε
    assert _allowance_obs(proj, 2026) == {(12, False)}
    assert [e.employment_id for e in svc.allowance_beneficiaries(2026, 12)] == [eid]

    svc.run_leave_allowance(2026, 12)
    assert _allowance_obs(proj, 2026) == {(12, True)}
    assert svc._allowance_apd_period(eid, 2026) == (2027, 1)  # ΑΠΔ Ιανουαρίου


def _close_november(svc, eid, year):
    """Ό,τι κάνει ο τακτικός υπολογισμός Νοεμβρίου για τον scheduler."""
    from misevent.domain.events import MonthlyPayrollCalculated
    svc.store.append(eid, MonthlyPayrollCalculated(
        eid, year, 11, gross=1200, efka_employee=0, efka_employer=0,
        taxable=1200, fmy=0, net=1200))


def test_december_obligation_waits_for_november_payroll():
    """Με ορίζοντα 12 μηνών ο Δεκέμβριος ανοίγει ΗΔΗ από την πρόσληψη — αλλά
    το επίδομα Δεκεμβρίου γεννιέται ΜΟΝΟ όταν κλείσει ο Νοέμβριος· ως τότε
    μια άδεια θα το μετέφερε και η υποχρέωση θα έμενε χωρίς δικαιούχους."""
    svc, proj, eid = setup(date(2026, 1, 5), horizon=12)
    assert _allowance_obs(proj, 2026) == set()
    _close_november(svc, eid, 2026)
    assert _allowance_obs(proj, 2026) == {(12, False)}


def test_leave_taken_during_the_year_means_no_december_obligation():
    svc, proj, eid = setup(date(2026, 1, 5), horizon=12)
    svc.record_leave(eid, LeaveType.ANNUAL, date(2026, 3, 16), date(2026, 3, 20))
    _close_november(svc, eid, 2026)
    assert _allowance_obs(proj, 2026) == {(3, False)}  # μόνο ο μήνας της άδειας


def test_december_preview_before_november_closes(fresh_system):
    """Πριν γεννηθεί η υποχρέωση Δεκεμβρίου, η /payroll δείχνει ΗΔΗ όσους δεν
    πήραν άδεια — προεπισκόπηση με την παραδοχή ότι εργάζονται έως 31/12,
    χωρίς κουμπί υπολογισμού (`expected`)."""
    from webapp import create_app
    from webapp import queries as q
    from webapp.clock import today
    from webapp.system import proj, svc

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    with app.test_request_context("/"):
        year = today().year
        assert 12 not in {m for m, _ in _allowance_obs(proj, year)}
        dec = svc.allowance_beneficiaries(year, 12)
        assert dec, "αναμενόταν εργαζόμενος χωρίς άδεια"
        periods = {p["month"]: p for p in q.allowance_periods(year)}
        p = periods[12]
        assert p["expected"] and not p["done"]
        names = {r["Εργαζόμενος"] for r in p["rows"]}
        assert {e.full_name for e in dec} <= names


def test_earlier_leave_moves_the_period_without_a_second_allowance():
    """Άδεια Οκτωβρίου, ΜΕΤΑ άδεια Σεπτεμβρίου: η περίοδος μετακινείται στον
    Σεπτέμβριο και η υποχρέωση Οκτωβρίου (χωρίς δικαιούχο πια) ΚΛΕΙΝΕΙ —
    ένα επίδομα ανά απασχόληση και έτος, όχι δύο."""
    svc, proj, eid = setup(date(2026, 1, 5), horizon=12)
    svc.record_leave(eid, LeaveType.ANNUAL, date(2026, 10, 12), date(2026, 10, 13))
    assert _allowance_obs(proj, 2026) == {(10, False)}
    svc.record_leave(eid, LeaveType.ANNUAL, date(2026, 9, 14), date(2026, 9, 15))
    assert _allowance_obs(proj, 2026) == {(9, False), (10, True)}
    assert svc.allowance_beneficiaries(2026, 10) == []


def test_calculated_allowance_freezes_its_period():
    """Υπολογισμένο επίδομα Οκτωβρίου + μεταγενέστερη άδεια Σεπτεμβρίου: ο
    εργαζόμενος ΜΕΝΕΙ στον Οκτώβριο — καμία νέα υποχρέωση, κανένα 2ο επίδομα."""
    svc, proj, eid = setup(date(2026, 1, 5), horizon=12)
    svc.record_leave(eid, LeaveType.ANNUAL, date(2026, 10, 12), date(2026, 10, 13))
    svc.run_leave_allowance(2026, 10)
    svc.record_leave(eid, LeaveType.ANNUAL, date(2026, 9, 14), date(2026, 9, 15))
    assert proj.employments[eid].allowance_period_month(2026) == 10
    assert _allowance_obs(proj, 2026) == {(10, True)}
    assert svc.allowance_beneficiaries(2026, 9) == []


def test_orphan_allowance_closes_on_load():
    """Ορφανή υποχρέωση από πριν τον έλεγχο (ανοιχτή, χωρίς δικαιούχο):
    κλείνει με τη φόρτωση του scheduler, χωρίς να χρειαστεί αλλαγή άδειας."""
    from misevent.domain.events import ObligationScheduled

    svc, proj, eid = setup(date(2026, 1, 5), horizon=12)
    svc.store.append("COMPANY", ObligationScheduled(
        "orphan01", "LEAVE_ALLOWANCE", date(2026, 10, 30), "", 2026, 10))
    assert _allowance_obs(proj, 2026) == {(10, False)}
    ObligationScheduler(svc.store, proj)
    assert _allowance_obs(proj, 2026) == {(10, True)}
