"""
ΔΙΟΡΘΩΣΗ ΥΠΟΛΟΓΙΣΜΕΝΗΣ ΜΙΣΘΟΔΟΣΙΑΣ — μέχρι την υποβολή, και μετά από
λανθασμένη υποβολή.

Το πρόβλημα που λύνει: ένα ποσοστό ΚΠΚ που δεν ενημερώθηκε φαίνεται μόνο όταν
ο ΕΦΚΑ απορρίψει το αρχείο. Μέχρι τότε η μισθοδοσία πρέπει να διορθώνεται —
και επειδή το αρχείο ανεβαίνει ΧΕΙΡΟΚΙΝΗΤΑ, η «υποβολή» είναι δήλωση του
χρήστη, άρα ανακαλείται.

Τρία πράγματα κλειδώνονται εδώ:
  · ο επανυπολογισμός ΔΕΝ γίνεται σιωπηλά — περνά από ρητό άνοιγμα·
  · το άνοιγμα ξεκλειδώνει ΠΡΑΓΜΑΤΙΚΑ τον μήνα (παρουσίες/άδειες), αφού το
    «κλείσιμο περιόδου» είναι παράγωγο των εκπληρωμένων υποχρεώσεων·
  · τα νέα events είναι μέρος του log: το replay δίνει ΤΑΥΤΟΣΗΜΗ κατάσταση.

Εκτέλεση: uv run pytest tests/test_payroll_reopen.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain.events import LeaveType
from misevent.domain.projections import Projections
from misevent.domain.scheduler import ObligationScheduler
from misevent.persistence.database import Database

FULL_WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}
KPK_RATES = "main_pension=10,37/18,79|healthcare=0/0|auxiliary=3/3"
# Το ΔΙΟΡΘΩΜΕΝΟ ποσοστό (+1 μονάδα στην κύρια σύνταξη, και στα δύο σκέλη):
# 14,37% / 22,79% — η ανάλυση κλάδων πρέπει να αθροίζει στο σύνολο (kpk.py)
NEW_KPK_RATES = "main_pension=11,37/19,79|healthcare=0/0|auxiliary=3/3"


def setup():
    """Εταιρεία με έναν μισθωτό και ενεργό χρονολόγιο υποχρεώσεων."""
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    store.subscribe(ObligationScheduler(store, proj, horizon_months=1).handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0, "ΔΑΣΟΥΣ", "20", "14456", "ΑΘΗΝΑ")
    svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
    svc.define_kpk_rates(
        "efka_main", "0101", date(2024, 1, 1), 0.1337, 0.2179, KPK_RATES)
    svc.define_specialty(
        "ΥΠΑΛΛΗΛΟΣ",
        {"MAIN_EPIK": {"kad": "0110", "specialty_code": "122100",
                       "kpk": "0101"}},
        ergani_specialty_code="419", ergani_specialty_description="ΛΟΙΠΟΙ ΥΠΑΛΛΗΛΟΙ ΓΡΑΦΕΙΟΥ",
    )
    pid = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ", first_name="ΙΩΑΝΝΗΣ", father_name="ΝΙΚΟΛΑΟΣ",
        mother_name="ΜΑΡΙΑ", birth_date=date(1990, 5, 5), afm="111222336",
        amka="05059012343", ama="123456789",
    )
    eid = svc.start_employment(
        pid, hire_date=date(2026, 1, 5), monthly_gross=1500.0,
        weekly_hours=40.0, weekly_pattern=FULL_WEEK,
    )
    svc.assign_specialty(eid, "ΥΠΑΛΛΗΛΟΣ")
    svc.submit_hire_e3(eid)
    svc.submit_hire_wto(eid)
    return svc, proj, eid


def payroll(svc, year, month):
    svc.approve_attendance(year, month)
    svc.run_monthly_payroll(year, month)


def raises(fn, *args, **kwargs) -> str:
    """Το μήνυμα του ValueError — ή σκάει αν ΔΕΝ απορρίφθηκε."""
    try:
        fn(*args, **kwargs)
    except ValueError as e:
        return str(e)
    raise AssertionError("αναμενόταν απόρριψη, η ενέργεια πέρασε")


def test_recalculation_needs_an_explicit_reopen():
    """Ο επανυπολογισμός ΔΕΝ γίνεται σιωπηλά.

    Το projection κρατά latest-wins ανά (εργαζόμενο, έτος, μήνα), άρα χωρίς
    φραγμό τα ποσά θα άλλαζαν αθόρυβα κάτω από ό,τι έχει ήδη υποβληθεί."""
    svc, proj, _eid = setup()
    payroll(svc, 2026, 1)
    before = proj.payrolls[0].efka_employee

    msg = raises(svc.run_monthly_payroll, 2026, 1)
    assert "έχει ήδη υπολογιστεί" in msg and "άνοιξέ" in msg

    # Διόρθωση ΚΠΚ (ο λόγος που χρειάζεται ο επανυπολογισμός) και άνοιγμα
    svc.define_kpk_rates(
        "efka_main", "0101", date(2026, 1, 1), 0.1437, 0.2279, NEW_KPK_RATES)
    assert svc.reopen_payroll_period(2026, 1, "λάθος ΚΠΚ") == []
    svc.run_monthly_payroll(2026, 1)

    assert len(proj.payrolls) == 1, "ο επανυπολογισμός αντικαθιστά, δεν προσθέτει"
    assert proj.payrolls[0].efka_employee > before, "τα ποσά δεν διορθώθηκαν"


def test_reopen_really_unlocks_the_month():
    """Το άνοιγμα ξεκλειδώνει ΤΟΝ ΜΗΝΑ, όχι μόνο το κουμπί: το «κλείσιμο
    περιόδου» είναι παράγωγο των εκπληρωμένων υποχρεώσεων."""
    svc, _proj, eid = setup()
    payroll(svc, 2026, 1)
    assert svc.closed_payroll_period() == (2026, 1)
    assert "έχει ήδη κλείσει" in raises(
        svc.record_leave, eid, LeaveType.ANNUAL, date(2026, 1, 20),
        date(2026, 1, 21))

    svc.reopen_payroll_period(2026, 1, "διόρθωση παρουσιών")
    assert svc.closed_payroll_period() is None
    assert svc.current_payroll_period() == (2026, 1)
    svc.record_leave(eid, LeaveType.ANNUAL, date(2026, 1, 20), date(2026, 1, 21))
    svc.approve_attendance(2026, 1)  # επανέγκριση συγκεντρωτικού
    svc.run_monthly_payroll(2026, 1)
    assert svc.closed_payroll_period() == (2026, 1), "η περίοδος ξανάκλεισε"


def test_submission_blocks_the_reopen_until_it_is_withdrawn():
    """Η ΑΠΔ ΕΦΚΑ κλείνει τη μισθοδοσία — αλλά η υποβολή είναι ΔΗΛΩΣΗ και
    ανακαλείται· τότε ξανανοίγει."""
    svc, proj, _eid = setup()
    payroll(svc, 2026, 1)
    svc.generate_apd_files(2026, 1, funds=("efka",))
    svc.submit_apd(2026, 1, "efka")

    assert "δήλωσε πρώτα λανθασμένη υποβολή" in raises(
        svc.reopen_payroll_period, 2026, 1)
    assert "αιτιολογία είναι υποχρεωτική" in raises(
        svc.withdraw_submission, "APD_SUBMISSION", 2026, 1, "  ")

    svc.withdraw_submission("APD_SUBMISSION", 2026, 1, "απορρίφθηκε από ΕΦΚΑ")
    assert proj.apd_submissions == [], "η υποβολή δεν στέκει πια"
    ob = next(o for o in proj.obligations.values()
              if o.obligation_type == "APD_SUBMISSION" and o.period_year == 2026)
    assert not ob.fulfilled, "η υποχρέωση δεν ξανάνοιξε"
    svc.reopen_payroll_period(2026, 1, "διόρθωση")
    assert svc.closed_payroll_period() is None


def test_paid_contributions_are_not_reversible():
    """Η ΚΑΤΑΒΟΛΗ δεν αντιστρέφεται: η υποβολή δεν ανακαλείται μετά από αυτήν."""
    svc, _proj, _eid = setup()
    payroll(svc, 2026, 1)
    svc.generate_apd_files(2026, 1, funds=("efka",))
    svc.submit_apd(2026, 1, "efka")
    svc.pay_efka(2026, 1, svc.apd_fund_totals(2026, 1)["efka"]["contrib"])

    msg = raises(svc.withdraw_submission, "APD_SUBMISSION", 2026, 1, "λάθος")
    assert "δεν αντιστρέφεται" in msg


def test_only_the_last_period_reopens():
    """Οι περίοδοι υπολογίζονται με τη σειρά και ο ΦΜΥ είναι σωρευτικός —
    άνοιγμα παλαιότερου μήνα θα άφηνε τους επόμενους σε σαθρή βάση."""
    svc, _proj, _eid = setup()
    payroll(svc, 2026, 1)
    payroll(svc, 2026, 2)

    msg = raises(svc.reopen_payroll_period, 2026, 1)
    assert "μόνο η τελευταία" in msg and "2/2026" in msg
    svc.reopen_payroll_period(2026, 2)  # ο τελευταίος ανοίγει κανονικά


def test_contribution_drift_is_visible_before_submission():
    """Ο ΑΝΙΧΝΕΥΤΗΣ: η ΑΠΔ ξαναϋπολογίζει με τα ΤΡΕΧΟΝΤΑ ποσοστά ΚΠΚ, οπότε
    μια εκ των υστέρων αλλαγή αποκλίνει σιωπηλά από ό,τι πληρώθηκε."""
    svc, proj, _eid = setup()
    payroll(svc, 2026, 1)
    assert svc.contribution_drift(2026, 1) == [], "καθαρή περίοδος"
    assert proj.payrolls[0].analysis["rates"] == {
        "employee": 0.1337, "employer": 0.2179
    }, "τα ποσοστά του υπολογισμού πρέπει να είναι ανιχνεύσιμα"

    svc.define_kpk_rates(
        "efka_main", "0101", date(2026, 1, 1), 0.1437, 0.2279, NEW_KPK_RATES)
    drift = svc.contribution_drift(2026, 1)
    assert len(drift) == 1
    assert drift[0]["used"] == (0.1337, 0.2179)
    assert drift[0]["now"] == (0.1437, 0.2279)
    assert not drift[0]["estimated"]

    svc.reopen_payroll_period(2026, 1)
    svc.run_monthly_payroll(2026, 1)
    assert svc.contribution_drift(2026, 1) == [], "ο επανυπολογισμός τη σβήνει"


def test_replay_is_identical():
    """Τα δύο νέα events είναι ΜΕΡΟΣ ΤΟΥ LOG, όχι μπάλωμα: το replay δίνει
    ταυτόσημη κατάσταση."""
    svc, proj, _eid = setup()
    payroll(svc, 2026, 1)
    svc.generate_apd_files(2026, 1, funds=("efka",))
    svc.submit_apd(2026, 1, "efka")
    svc.withdraw_submission("APD_SUBMISSION", 2026, 1, "λάθος αρχείο")
    svc.reopen_payroll_period(2026, 1, "διόρθωση")
    svc.run_monthly_payroll(2026, 1)

    fresh = Projections()
    fresh.rebuild(svc.store)
    assert fresh.apd_submissions == proj.apd_submissions
    assert {o.obligation_id: o.fulfilled for o in fresh.obligations.values()} == {
        o.obligation_id: o.fulfilled for o in proj.obligations.values()
    }
    assert [p.efka_employee for p in fresh.payrolls] == [
        p.efka_employee for p in proj.payrolls
    ]
