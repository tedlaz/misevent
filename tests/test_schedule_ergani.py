"""
Tests δήλωσης προγράμματος στο ΕΡΓΑΝΗ — day-level coverage (submitted/modified/
pending), αριθμημένα batches (ανά εταιρεία), έλεγχος συνέχειας, revert.

Εκτέλεση: python test_schedule_ergani.py
"""

from datetime import date, timedelta

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.persistence.database import Database

FULL_WEEK = {
    "MON": "09:00-17:00", "TUE": "09:00-17:00", "WED": "09:00-17:00",
    "THU": "09:00-17:00", "FRI": "09:00-17:00",
}
HIRE = date(2026, 3, 2)  # Δευτέρα
WED = date(2026, 3, 4)   # εργάσιμη
FRI = date(2026, 3, 6)   # εργάσιμη


def setup(mode="fixed", hire=HIRE):
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    pid = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ", first_name="ΙΩΑΝΝΗΣ",
        father_name="ΝΙΚΟΛΑΟΣ", mother_name="ΜΑΡΙΑ",
        birth_date=date(1990, 5, 5), afm="111222336",
        amka="05059012343", ama="123456789",
    )
    eid = svc.start_employment(
        pid, hire_date=hire, monthly_gross=1500.0,
        weekly_hours=40.0, weekly_pattern=FULL_WEEK, schedule_mode=mode,
        schedule_weeks=1 if mode == "variable" else 0,
    )
    # Μετά τη δημιουργία, ώστε το variable fixture να παραμένει pending και
    # να ελέγχουν τα tests τη ρητή ημερήσια υποβολή (όχι auto-submit).
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    # Η αναγγελία πρόσληψης (Ε3) ολοκληρώνει την πρόσληψη — χωρίς αυτήν η
    # απασχόληση μένει «εκκρεμής» και δεν μετράει πουθενά (§5.7α).
    svc.record_external_hire_submission(eid)
    return svc, proj, eid


def wto_count(proj, eid):
    return sum(
        1 for s in proj.ergani_submissions
        if s.employee_id == eid and s.doc_type == "WTO"
    )


def test_fixed_no_auto_submit_at_hire():
    """Η πρόσληψη ΔΕΝ υποβάλλει ΟΧΕ — οι ημέρες είναι αδήλωτες (pending)."""
    svc, proj, eid = setup("fixed")
    emp = proj.employments[eid]
    assert wto_count(proj, eid) == 0
    assert emp.day_status(WED) == "pending"
    assert svc.month_schedule_incomplete(2026, 3) is True
    print("OK   fixed: καμία αυτόματη υποβολή, ημέρες αδήλωτες")


def test_fixed_standing_covers_all():
    """submit_hire_wto: standing εβδομαδιαίο → όλες οι ημέρες υποβεβλημένες."""
    svc, proj, eid = setup("fixed")
    svc.submit_hire_wto(eid)
    emp = proj.employments[eid]
    assert wto_count(proj, eid) == 1
    assert emp.standing_history
    assert emp.day_status(WED) == "submitted"
    assert emp.day_status(WED + timedelta(days=140)) == "submitted"
    assert svc.month_schedule_incomplete(2026, 3) is False
    print("OK   fixed: standing: όλες οι ημέρες submitted")


def test_daily_edit_is_modified_not_submitted():
    """Αλλαγή ημέρας μετά το standing = «modified», χωρίς υποβολή στο ΕΡΓΑΝΗ."""
    svc, proj, eid = setup("fixed")
    svc.submit_hire_wto(eid)
    before = wto_count(proj, eid)
    svc.set_daily_schedule(eid, {FRI: ""})  # Παρασκευή -> ρεπό (αλλαγή)
    emp = proj.employments[eid]
    assert wto_count(proj, eid) == before, "η αλλαγή δεν υποβάλλεται αυτόματα"
    assert emp.day_status(FRI) == "modified"
    assert emp.day_status(WED) == "submitted"
    print("OK   αλλαγή ημέρας = modified, όχι αυτόματη υποβολή")


def test_latest_submission_wins():
    """Υποβολή = εβδομαδιαίο Ή ημερήσιο· ισχύει η ΤΕΛΕΥΤΑΙΑ που καλύπτει την ημέρα."""
    svc, proj, eid = setup("fixed")
    svc.submit_hire_wto(eid)  # αρχικό εβδομαδιαίο
    svc.set_daily_schedule(eid, {FRI: "09:00-19:00"})
    svc.submit_schedule_days({eid: [FRI]})  # ημερήσιο
    emp = proj.employments[eid]
    assert emp.day_schedule(FRI) == emp.day_baseline(FRI) == "09:00-19:00"
    later = {k: "10:00-18:00" for k in FULL_WEEK}
    svc.set_schedule(eid, HIRE, later)  # νεότερο εβδομαδιαίο: μόνο καταγραφή
    assert emp.day_status(FRI) == "modified" and emp.pending_weekly
    svc.submit_weekly_schedules({eid: [HIRE]})  # υποβολή από «Πρόγραμμα → ΕΡΓΑΝΗ»
    assert not emp.pending_weekly
    assert emp.day_schedule(FRI) == emp.day_baseline(FRI) == "10:00-18:00"
    assert emp.day_status(FRI) == "submitted"
    # νεότερο ημερήσιο ξανά επικρατεί
    svc.set_daily_schedule(eid, {FRI: "08:00-18:00"})
    svc.submit_schedule_days({eid: [FRI]})
    assert emp.day_baseline(FRI) == "08:00-18:00"
    assert emp.day_baseline(WED) == "10:00-18:00"
    print("OK   η τελευταία υποβολή (εβδομαδιαία/ημερήσια) επικρατεί")


def test_submit_batch_declares_and_numbers():
    """submit_schedule_days: αριθμημένο batch, οι ημέρες γίνονται submitted."""
    svc, proj, eid = setup("fixed")
    svc.submit_hire_wto(eid)
    svc.set_daily_schedule(eid, {FRI: "09:00-19:00"})
    assert svc.next_schedule_batch_no() == 1
    res = svc.submit_schedule_days({eid: [FRI]})
    emp = proj.employments[eid]
    assert res == {"batch_no": 1, "count": 1, "employees": 1}
    assert emp.day_status(FRI) == "submitted"
    assert proj.schedule_batches[1]["count"] == 1
    # δεύτερο batch -> #2
    svc.set_daily_schedule(eid, {WED: "10:00-18:00"})
    res2 = svc.submit_schedule_days({eid: [WED]})
    assert res2["batch_no"] == 2
    print("OK   batch: αριθμείται ανά εταιρεία, ημέρες γίνονται submitted")


def test_revert_restores_declared_value():
    """revert_days: επαναφορά τροποποιημένης ημέρας στη δηλωμένη τιμή."""
    svc, proj, eid = setup("fixed")
    svc.submit_hire_wto(eid)
    svc.set_daily_schedule(eid, {WED: "10:00-18:00"})  # αλλαγή
    emp = proj.employments[eid]
    assert emp.day_status(WED) == "modified"
    n = svc.revert_days({eid: [WED]})
    assert n == 1
    assert emp.day_status(WED) == "submitted"
    assert emp.day_schedule(WED) == "09:00-17:00"  # πίσω στο standing baseline
    print("OK   revert: επαναφορά στη δηλωμένη τιμή ΕΡΓΑΝΗ")


def test_variable_all_pending():
    """ΜΕΤΑΒΛΗΤΟ: όλες οι ημέρες αδήλωτες· submit_hire_wto απορρίπτεται."""
    svc, proj, eid = setup("variable")
    emp = proj.employments[eid]
    assert emp.day_status(WED) == "pending"
    try:
        svc.submit_hire_wto(eid)
        assert False, "το variable δεν έχει standing ΟΧΕ"
    except ValueError:
        pass
    print("OK   variable: όλες οι ημέρες pending, χωρίς standing ΟΧΕ")


def test_continuity_blocks_gaps():
    """Δεν επιτρέπεται υποβολή που αφήνει προγενέστερη ΑΔΗΛΩΤΗ ημέρα."""
    svc, proj, eid = setup("variable")
    later = HIRE + timedelta(days=14)  # τρίτη εβδομάδα
    try:
        svc.submit_schedule_days({eid: [later]})
        assert False, "πρέπει να μπλοκάρει λόγω προγενέστερων αδήλωτων"
    except ValueError as exc:
        assert "προγενέστερη" in str(exc)
    # Με τη σειρά: πρώτες δύο εβδομάδες, μετά η τρίτη
    emp = proj.employments[eid]
    first_two = [
        d for monday in (HIRE, HIRE + timedelta(days=7))
        for d in emp.active_days_in_week(monday)
    ]
    svc.submit_schedule_days({eid: first_two})
    svc.submit_schedule_days({eid: emp.active_days_in_week(later)})
    assert emp.day_status(later) == "submitted"
    print("OK   συνέχεια: εμποδίζει τα κενά, με τη σειρά περνά")


def test_month_incomplete_until_all_days_declared():
    """Ο μήνας ατελής (κρύβει την «Έγκριση Παρουσιών») μέχρι να δηλωθούν όλες
    οι ημέρες."""
    svc, proj, eid = setup("variable")
    assert svc.month_schedule_incomplete(2026, 3) is True
    # όλες οι submittable ημέρες του Μαρτίου, εβδομάδα-εβδομάδα (συνέχεια)
    monday = HIRE
    while monday <= date(2026, 3, 31):
        days = svc.week_submittable_days(eid, monday)
        if days:
            svc.submit_schedule_days({eid: days})
        monday += timedelta(days=7)
    assert svc.month_schedule_incomplete(2026, 3) is False
    print("OK   ο μήνας ολοκληρώνεται όταν δηλωθούν όλες οι ημέρες")


def test_company_wide_batch_covers_multiple_employees():
    """Ένα batch μπορεί να καλύπτει πολλούς εργαζόμενους με ίδιο batch_no."""
    svc, proj, eid = setup("variable")
    pid2 = svc.register_person(
        last_name="ΓΕΩΡΓΙΟΥ", first_name="ΜΑΡΙΑ", father_name="ΚΩΝ",
        mother_name="ΣΟΦΙΑ", birth_date=date(1991, 4, 4), afm="222333440",
        amka="04049112347", ama="223344556",
    )
    eid2 = svc.start_employment(
        pid2, hire_date=HIRE, monthly_gross=1400.0, weekly_hours=40.0,
        weekly_pattern=FULL_WEEK, schedule_mode="fixed",
    )
    svc.record_external_hire_submission(eid2)
    emp1, emp2 = proj.employments[eid], proj.employments[eid2]
    sel = {
        eid: emp1.active_days_in_week(HIRE),
        eid2: emp2.active_days_in_week(HIRE),
    }
    res = svc.submit_schedule_days(sel)
    assert res["employees"] == 2
    assert res["batch_no"] == 1
    assert proj.schedule_batches[1]["employees"] == {eid, eid2}
    print("OK   company-wide: ένα batch για πολλούς εργαζόμενους")


def test_daily_declaration_allows_hours_different_from_contract():
    """Η ημερήσια δήλωση επιτρέπει πρόσθετες, αλλά όχι λιγότερες ώρες."""
    svc, proj, eid = setup("fixed")
    emp = proj.employments[eid]
    # Πρώτη ημερήσια δήλωση: 32 αντί 40 ώρες — απορρίπτεται.
    svc.set_daily_schedule(eid, {WED: ""})
    try:
        svc.submit_schedule_days({eid: emp.active_days_in_week(HIRE)})
        assert False, "πρέπει να μπλοκάρει: 32 < 40 ώρες/εβδομάδα"
    except ValueError as exc:
        assert "λιγότερες" in str(exc)

    # Επαναφορά στις 40 ώρες -> επιτρέπεται.
    svc.set_daily_schedule(eid, {WED: "09:00-17:00"})
    res = svc.submit_schedule_days({eid: emp.active_days_in_week(HIRE)})
    assert res["batch_no"] == 1
    assert res["count"] == 7

    # Μεταγενέστερα μία ημέρα 10ωρη — επίσης επιτρέπεται.
    svc.set_daily_schedule(eid, {WED: "09:00-19:00"})
    res = svc.submit_schedule_days({eid: [WED]})
    assert res["batch_no"] == 2
    print("OK   ημερήσια δήλωση: επιτρέπεται απόκλιση από συμβατικές ώρες")


def test_targeted_change_after_declaration_keeps_contract_minimum():
    """Και η μεταβολή δηλωμένης εβδομάδας δεν πέφτει κάτω από τη σύμβαση."""
    svc, proj, eid = setup("fixed")
    svc.submit_hire_wto(eid)  # standing -> όλες οι ημέρες δηλωμένες
    svc.set_daily_schedule(eid, {FRI: ""})  # 32 ώρες πλέον
    try:
        svc.submit_schedule_days({eid: [FRI]})
        assert False, "πρέπει να μπλοκάρει: η εβδομάδα έπεσε στις 32 ώρες"
    except ValueError as exc:
        assert "λιγότερες" in str(exc)

    # Πρόσθετες ώρες επιτρέπονται και σε στοχευμένη αλλαγή.
    svc.set_daily_schedule(eid, {FRI: "09:00-19:00"})
    res = svc.submit_schedule_days({eid: [FRI]})
    assert res["count"] == 1
    print("OK   ημερήσια μεταβολή: κατώτατο σύμβασης, υπέρβαση επιτρέπεται")


def test_partial_hire_week_exempt_from_hours_check():
    """Η μερική εβδομάδα πρόσληψης (λιγότερες ενεργές ημέρες) εξαιρείται."""
    wed_hire = date(2026, 3, 4)  # Τετάρτη — μερική πρώτη εβδομάδα
    svc, proj, eid = setup("variable", hire=wed_hire)
    emp = proj.employments[eid]
    days = emp.active_days_in_week(date(2026, 3, 2))  # Δευ της εβδομάδας
    # Τε–Πα = 24 ώρες, αλλά μερική εβδομάδα -> δεν ελέγχεται
    res = svc.submit_schedule_days({eid: days})
    assert res["count"] == len(days)
    print("OK   μερική εβδομάδα πρόσληψης εξαιρείται από τον έλεγχο ωρών")


def test_weekly_end_date_stops_schedule():
    """Σταθερό εβδομαδιαίο με ημ. λήξης: μετά τη λήξη δεν προβλέπεται εργασία."""
    svc, proj, eid = setup("fixed")
    emp = proj.employments[eid]
    end = date(2026, 3, 31)
    svc.set_schedule(eid, date(2026, 3, 9), dict(FULL_WEEK), end_date=end)
    assert emp.is_scheduled(date(2026, 3, 11)) is True   # εντός ισχύος (Τε)
    assert emp.is_scheduled(date(2026, 4, 1)) is False   # μετά τη λήξη
    assert emp.day_schedule(date(2026, 4, 8)) == ""       # καμία εργασία
    # Νεότερο εβδομαδιαίο μετά τη λήξη ξαναδίνει πρόγραμμα
    svc.set_schedule(eid, date(2026, 4, 6), dict(FULL_WEEK))
    assert emp.is_scheduled(date(2026, 4, 8)) is True
    print("OK   λήξη εβδομαδιαίου: μετά τη λήξη κανένα πρόγραμμα (εκτός νεότερου)")


def test_weekly_end_before_start_rejected():
    """Λήξη πριν την έναρξη απορρίπτεται."""
    svc, proj, eid = setup("fixed")
    try:
        svc.set_schedule(eid, date(2026, 3, 9), dict(FULL_WEEK),
                         end_date=date(2026, 3, 1))
        assert False, "η λήξη δεν μπορεί να προηγείται της έναρξης"
    except ValueError as exc:
        assert "λήξης" in str(exc)
    print("OK   λήξη πριν την έναρξη απορρίπτεται")


def test_weekly_end_date_survives_rebuild():
    """Η λήξη του εβδομαδιαίου ξαναχτίζεται ακέραια από τα events."""
    svc, proj, eid = setup("fixed")
    svc.set_schedule(eid, date(2026, 3, 9), dict(FULL_WEEK),
                     end_date=date(2026, 3, 31))
    rebuilt = Projections()
    rebuilt.rebuild(svc.store)
    emp = rebuilt.employments[eid]
    assert emp.is_scheduled(date(2026, 4, 1)) is False
    print("OK   rebuild: η λήξη του εβδομαδιαίου παραμένει")


def test_rebuild_preserves_coverage():
    """Το coverage ξαναχτίζεται ακέραιο από τα events (rebuild)."""
    svc, proj, eid = setup("fixed")
    svc.submit_hire_wto(eid)
    svc.set_daily_schedule(eid, {FRI: "09:00-19:00"})
    svc.submit_schedule_days({eid: [FRI]})
    svc.set_daily_schedule(eid, {WED: "10:00-18:00"})
    before = proj.employments[eid].day_status(WED)  # modified

    rebuilt = Projections()
    rebuilt.rebuild(svc.store)
    after = rebuilt.employments[eid].day_status(WED)
    assert before == after == "modified", (before, after)
    assert rebuilt.schedule_batches[1]["count"] == 1
    print("OK   rebuild: η κάλυψη & τα batches παραμένουν ακέραια")


def test_unsubmitted_schedule_blocks_approval_visibly(monkeypatch):
    """Αδήλωτο πρόγραμμα: η «Έγκριση παρουσιών» ΔΕΝ κρύβεται — εμφανίζεται
    μπλοκαρισμένη με τον εργαζόμενο και τις ημέρες που λείπουν."""
    from types import SimpleNamespace

    from webapp import queries as q

    svc, proj, eid = setup("fixed")
    monkeypatch.setattr(q, "proj", proj)
    monkeypatch.setattr(q, "svc", svc)
    ob = SimpleNamespace(obligation_type="ATTENDANCE_APPROVAL", fulfilled=False,
                         period_year=2026, period_month=3, obligation_id="x")
    assert not q.is_locked(ob, {})
    assert "Αδήλωτο πρόγραμμα" in q.blocked_reason(ob)
    svc.submit_hire_wto(eid)
    assert q.blocked_reason(ob) is None
