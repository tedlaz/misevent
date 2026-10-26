"""
Tests **Δώρου Πάσχα**: καταβολή τη **Μεγάλη Τετάρτη** και υπολογισμός στον
μισθό που ΙΣΧΥΕΙ **15 ημέρες ΠΡΙΝ** την καταβολή.

Ελέγχονται:
- οι ημερομηνίες (Μ. Τετάρτη = Πάσχα − 4, αναφορά μισθού = καταβολή − 15) και
  ότι η ΠΡΟΘΕΣΜΙΑ της υποχρέωσης είναι η ίδια η Μ. Τετάρτη,
- το ιστορικό σύμβασης (`EmploymentState.contract_on`),
- ότι μεταβολή μισθού **μέσα** στο δεκαπενθήμερο ΔΕΝ επηρεάζει το Δώρο,
  ενώ μεταβολή **πριν** την ημερομηνία αναφοράς το επηρεάζει,
- ότι προεπισκόπηση (`queries.gift_rows`) = υπολογισμός.

Εκτέλεση: uv run tests/test_easter_gift_wage.py
"""

from datetime import date, timedelta

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain import payroll_engine as pe
from misevent.domain.projections import Projections
from misevent.persistence.database import Database
from misevent.rules import obligation_types as ot
from misevent.rules.calendar_gr import holy_wednesday, orthodox_easter

FULL_WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}


def make_svc():
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
    svc.define_kpk_rates(
        "efka_main", "0101", date(2024, 1, 1), 0.1337, 0.2179,
        "main_pension=13,37/21,79|healthcare=0/0",
    )
    pid = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ",
        first_name="ΙΩΑΝΝΗΣ",
        father_name="Ν",
        mother_name="Μ",
        birth_date=date(1990, 5, 5),
        afm="111222336",
        amka="05059012343",
        ama="123456789",
    )
    return svc, proj, pid


def hire(svc, pid, hire_date, gross):
    eid = svc.start_employment(
        pid, hire_date, monthly_gross=gross, weekly_pattern=FULL_WEEK
    )
    svc.assign_insurance_profile(
        eid,
        ergani_specialty_code="419",
        ergani_specialty_description="ΛΟΙΠΟΙ ΥΠΑΛΛΗΛΟΙ ΓΡΑΦΕΙΟΥ",
        coverages=(
            {"fund": "efka_main", "kad": "0000",
             "specialty_code": "000000", "kpk": "0101"},
        ),
    )
    # Η πρόσληψη ολοκληρώνεται με την ΑΝΑΓΓΕΛΙΑ (§5.7α): χωρίς αυτήν η
    # απασχόληση είναι εκκρεμής και δεν υπολογίζεται Δώρο.
    svc.record_external_hire_submission(eid)
    return eid


def test_dates():
    """Μ. Τετάρτη = Πάσχα − 4 (ΟΧΙ − 3, που είναι η Μ. Πέμπτη) και η
    ημερομηνία αναφοράς του μισθού είναι 15 ημέρες πριν από αυτήν."""
    for year in (2024, 2025, 2026, 2027):
        easter = orthodox_easter(year)
        hw = holy_wednesday(year)
        assert hw == easter - timedelta(days=4), year
        assert hw.weekday() == 2, year  # Τετάρτη
        assert pe.easter_gift_payment_date(year) == hw
        assert pe.easter_gift_wage_date(year) == hw - timedelta(days=15)
        # Η προθεσμία της υποχρέωσης είναι η ημέρα καταβολής
        assert ot.due_date("EASTER_GIFT", year, 0) == hw, year
    print("OK ημερομηνίες: Μ. Τετάρτη = Πάσχα-4, αναφορά μισθού = -15 ημέρες")


def test_contract_on():
    """Το ιστορικό σύμβασης δίνει τους όρους ΚΑΘΕ ημερομηνίας."""
    svc, proj, pid = make_svc()
    eid = hire(svc, pid, date(2025, 1, 2), 1200.0)
    svc.change_contract(eid, date(2026, 2, 1), "monthly", monthly_gross=1400.0)
    emp = proj.employments[eid]
    assert emp.contract_on(date(2025, 6, 1)).monthly_gross == 1200.0
    assert emp.contract_on(date(2026, 1, 31)).monthly_gross == 1200.0
    assert emp.contract_on(date(2026, 2, 1)).monthly_gross == 1400.0
    # Πριν από την πρόσληψη: οι όροι της πρόσληψης (ποτέ κενή τιμή)
    assert emp.contract_on(date(2024, 1, 1)).monthly_gross == 1200.0
    assert emp.contract_on(None).monthly_gross == 1400.0
    print("OK contract_on: ο μισθός κάθε ημερομηνίας από το ιστορικό σύμβασης")


def _easter_gross(svc, proj, eid, year):
    svc.run_easter_gifts(year)
    g = [x for x in proj.gifts
         if x.employee_id == eid and x.gift_type == "easter" and x.year == year]
    assert len(g) == 1
    return g[0].gross


def test_raise_inside_15_days_ignored():
    """Αύξηση ΜΕΤΑ την ημερομηνία αναφοράς (μέσα στο δεκαπενθήμερο πριν την
    καταβολή) ΔΕΝ αλλάζει το Δώρο Πάσχα."""
    year = 2026
    svc, proj, pid = make_svc()
    eid = hire(svc, pid, date(2025, 1, 2), 1200.0)
    # Ισχύς 1 ημέρα ΜΕΤΑ την ημερομηνία αναφοράς του μισθού
    svc.change_contract(
        eid, pe.easter_gift_wage_date(year) + timedelta(days=1),
        "monthly", monthly_gross=2000.0,
    )
    gross = _easter_gross(svc, proj, eid, year)
    expected = pe.calc_easter_gift(
        1200.0, 0, year, date(2025, 1, 2),
        rates=svc.contribution_rates(proj.employments[eid].insurance_profile,
                                     pe.easter_gift_payment_date(year)),
    ).gross
    assert abs(gross - expected) < 0.01, (gross, expected)
    # ...και σίγουρα ΟΧΙ στα μικτά του νέου μισθού
    assert gross < 1000.0
    print(f"OK αύξηση εντός 15ημέρου αγνοήθηκε: Δώρο {gross:.2f} € (μισθός 1200)")


def test_raise_before_reference_counts():
    """Αύξηση ΠΡΙΝ την ημερομηνία αναφοράς μετράει κανονικά."""
    year = 2026
    svc, proj, pid = make_svc()
    eid = hire(svc, pid, date(2025, 1, 2), 1200.0)
    svc.change_contract(
        eid, pe.easter_gift_wage_date(year) - timedelta(days=1),
        "monthly", monthly_gross=2000.0,
    )
    gross = _easter_gross(svc, proj, eid, year)
    expected = pe.calc_easter_gift(
        2000.0, 0, year, date(2025, 1, 2),
        rates=svc.contribution_rates(proj.employments[eid].insurance_profile,
                                     pe.easter_gift_payment_date(year)),
    ).gross
    assert abs(gross - expected) < 0.01, (gross, expected)
    print(f"OK αύξηση πριν την αναφορά μέτρησε: Δώρο {gross:.2f} € (μισθός 2000)")


def test_preview_equals_calculation():
    """Η προεπισκόπηση της /payroll δίνει ΑΚΡΙΒΩΣ ό,τι και ο υπολογισμός."""
    year = 2026
    svc, proj, pid = make_svc()
    eid = hire(svc, pid, date(2025, 1, 2), 1200.0)
    svc.change_contract(
        eid, pe.easter_gift_wage_date(year) + timedelta(days=1),
        "monthly", monthly_gross=2000.0,
    )
    preview = svc.gift_preview_for(proj.employments[eid], "easter", year)
    gross = _easter_gross(svc, proj, eid, year)
    assert abs(preview.gross - gross) < 0.01
    print("OK προεπισκόπηση = υπολογισμός (ίδια ημερομηνία αναφοράς μισθού)")
