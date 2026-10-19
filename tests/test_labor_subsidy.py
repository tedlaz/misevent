"""
Επιδότηση εργατικής εισφοράς (αρ. 14 ν. 2837/2000, ΑΠΔ κωδ. 102).

Μισθός ΑΚΡΙΒΩΣ ίσος με τον κατώτατο (+ προσαυξήσεις) → δεν παρακρατείται
6,67% επί του σκέτου κατώτατου. Η ΑΠΔ δηλώνει την ΠΛΗΡΗ εισφορά, την
επιδότηση χωριστά (No 49 + εγγραφή τύπου 4), καταβλητέο = ό,τι κρατήθηκε.

Εκτέλεση: uv run pytest tests/test_labor_subsidy.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd
from test_apd_from_events import apd_contributions, setup

from misevent.domain.events import LeaveType

SUBSIDY_880 = 58.70  # 6,67% × 880 (κατώτατος Ιαν. 2026)
SUBSIDY_920 = 61.36  # 6,67% × 920 (κατώτατος από 1/4/2026)


def run(svc, *months):
    for m in months:
        svc.approve_attendance(2026, m)
        svc.run_monthly_payroll(2026, m)


def payroll(proj, month):
    return next(p for p in proj.payrolls if p.period_month == month)


def test_exact_minimum_gets_subsidy_in_payroll_and_apd():
    svc, proj, _eid = setup(monthly_gross=880.0)
    run(svc, 1, 2)
    p = payroll(proj, 2)
    assert p.analysis["subsidy_employee"] == SUBSIDY_880
    # ΣΤΑΘΕΡΟ ποσό: η κράτηση είναι η πλήρης μείον την επιδότηση
    full = round(880 * 0.1337, 2)
    assert p.efka_employee == round(full - SUBSIDY_880, 2)

    rows = [r for _i, r in svc.build_apd_rows(2026, 2)["efka"]]
    main = rows[0]
    assert main.subsidy_employee == SUBSIDY_880
    # Η ΑΠΔ δηλώνει την ΠΛΗΡΗ εισφορά και καταβάλλεται ό,τι κρατήθηκε
    assert round(sum(r.contrib_employee for r in rows), 2) == full
    assert round(sum(r.payable for r in rows), 2) == round(
        p.efka_employee + p.efka_employer, 2)
    assert apd_contributions(svc, 2026, 2) == round(
        p.efka_employee + p.efka_employer + SUBSIDY_880, 2)
    text = svc.apd_file_bytes("efka", 2026, 2).decode("cp1253")
    assert "4102006670" in text.replace("\r", "").replace("\n", ""), "εγγραφή τύπου 4"


def test_premiums_do_not_cancel_subsidy():
    """Υπερεργασία/υπερωρίες/νυχτερινά: το δικαίωμα κρίνεται στον ΣΥΜΒΑΤΙΚΟ
    μισθό, όχι στα μικτά του μήνα — η επιδότηση μένει ίδια."""
    svc, proj, eid = setup(monthly_gross=880.0)
    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)
    svc.approve_attendance(2026, 2, {eid: {
        "overwork": 2.0, "overtime": 2.0, "normal_n": 4.0}})
    svc.run_monthly_payroll(2026, 2)
    p = payroll(proj, 2)
    assert p.analysis["premium_total"] > 0
    assert p.analysis["subsidy_employee"] == SUBSIDY_880
    assert next(r for _i, r in svc.build_apd_rows(2026, 2)["efka"]
                ).subsidy_employee == SUBSIDY_880


def test_hire_month_is_proportional():
    svc, proj, _eid = setup(monthly_gross=880.0, hire=date(2026, 1, 5))
    run(svc, 1)
    p = payroll(proj, 1)
    assert p.insurance_days < 25
    assert p.analysis["subsidy_employee"] == round(
        0.0667 * 880 * p.insurance_days / 25, 2)


def test_above_minimum_gets_nothing():
    svc, proj, _eid = setup(monthly_gross=880.01)
    run(svc, 1, 2)
    assert payroll(proj, 2).analysis["subsidy_employee"] == 0.0
    assert all(not r.subsidy_employee
               for _i, r in svc.build_apd_rows(2026, 2)["efka"])


def test_declared_exclusion_survives_profile_rewrite():
    svc, proj, eid = setup(monthly_gross=880.0)
    svc.assign_specialty(eid, "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ", auxiliary_scheme="efka",
                         labor_subsidy_excluded=True)
    # π.χ. διόρθωση φόρμας πρόσληψης: ξαναγράφει το προφίλ χωρίς τη σημαία
    svc.assign_specialty(eid, "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ", auxiliary_scheme="efka")
    run(svc, 1, 2)
    assert payroll(proj, 2).analysis["subsidy_employee"] == 0.0


def test_sickness_half_days_count_half():
    """Τριήμερο (μισό ημερομίσθιο) → ½ ημέρα· οι λοιπές ημέρες ασθενείας
    μετρούν ως εργάσιμες. 25 ημέρες με 3 τριήμερο + 2 → 23,5/25."""
    svc, proj, eid = setup(monthly_gross=880.0)
    svc.record_leave(eid, LeaveType.SICK, date(2026, 2, 2), date(2026, 2, 6))
    run(svc, 1, 2)
    p = payroll(proj, 2)
    sick = proj.employments[eid].sick_days_in_month(2026, 2)
    assert (sick["half"], sick["full"]) == (3, 2)
    units = p.insurance_days - 1.5
    assert p.analysis["subsidy_employee"] == round(0.0667 * 880 * units / 25, 2)


def test_gifts_and_allowance_on_bare_minimum():
    """Δώρα/Επίδομα: ποσοστό × το κλάσμα μισθού της βάσης, ΧΩΡΙΣ 0,04166."""
    svc, proj, eid = setup(monthly_gross=880.0)
    emp = proj.employments[eid]
    # Πάσχα 2026: μισθός 15 ημέρες πριν τη Μ. Τετάρτη = 880 = κατώτατος
    easter = svc.gift_preview_for(emp, "easter", 2026)
    base = (116 / 8) * (1 / 15) * 440  # πρόσληψη 5/1: 116 από 120 ημέρες
    assert easter.subsidy_employee == round(0.0667 * 880 * base / 880, 2)
    # Επίδομα 2026: αποτιμάται 31/7 — κατώτατος ΤΟΤΕ 920 ≠ 880 → τίποτα
    assert svc.allowance_preview_for(emp, 2026).subsidy_employee == 0.0
    # Με 920 (1ο έτος, 12 μήνες): μισός μισθός → μισή επιδότηση
    svc, proj, eid = setup(monthly_gross=920.0)
    allowance = svc.allowance_preview_for(proj.employments[eid], 2026)
    assert allowance.subsidy_employee == round(0.0667 * 920 / 2, 2)


def test_christmas_gift_reaches_the_apd():
    """920 από την πρόσληψη: Ιαν–Μαρ ΠΑΝΩ από τον κατώτατο (880 → καμία
    επιδότηση), από 1/4 ΑΚΡΙΒΩΣ ο κατώτατος (920) → Δώρο με επιδότηση."""
    svc, proj, _eid = setup(monthly_gross=920.0)
    for m in range(1, 13):
        if m == 4:
            svc.apply_minimum_adjustments(2026, 4)
        run(svc, m)
    assert payroll(proj, 3).analysis["subsidy_employee"] == 0.0
    assert payroll(proj, 5).analysis["subsidy_employee"] == SUBSIDY_920
    svc.run_christmas_gifts(2026)
    gift = next(g for g in proj.gifts if g.gift_type == "christmas")
    assert gift.subsidy_employee == SUBSIDY_920

    rows = [r for _i, r in svc.build_apd_rows(2027, 1)["efka"]
            if r.earnings_type == "003"]
    assert rows[0].subsidy_employee == SUBSIDY_920
    assert round(sum(r.payable for r in rows), 2) == round(
        gift.efka_employee + gift.efka_employer, 2)


def test_sickness_beyond_employer_limit_counts_nothing():
    """Πάνω από το όριο που πληρώνει η επιχείρηση (13 ημέρες, <1 έτος) οι
    ημέρες είναι άνευ αποδοχών: λείπουν από τις ημέρες ασφάλισης, άρα και
    από την επιδότηση. Μέχρι το όριο μετρούν ως εργάσιμες (τριήμερο ½)."""
    svc, proj, eid = setup(monthly_gross=880.0)
    svc.record_leave(eid, LeaveType.SICK, date(2026, 2, 2), date(2026, 2, 27))
    run(svc, 1, 2)
    p = payroll(proj, 2)
    sick = proj.employments[eid].sick_days_in_month(2026, 2)
    assert sick["half"] == 3 and sick["half"] + sick["full"] == 13
    assert p.insurance_days < 25, "οι ημέρες πάνω από το όριο κόβουν ασφάλιση"
    units = p.insurance_days - 1.5
    assert p.analysis["subsidy_employee"] == round(0.0667 * 880 * units / 25, 2)
