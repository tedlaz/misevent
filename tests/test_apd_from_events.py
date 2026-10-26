"""
Η ΑΠΔ ΔΗΛΩΝΕΙ Ο,ΤΙ ΥΠΟΛΟΓΙΣΤΗΚΕ — δεν ξαναϋπολογίζει.

Οι εισφορές του αρχείου έρχονται από τα events (μισθοδοσία, Δώρα, Επίδομα
Αδείας) και μοιράζονται στις γραμμές· ΔΕΝ προκύπτουν από `μικτά × τρέχον
ποσοστό ΚΠΚ`. Η διαφορά δεν είναι θεωρητική: όσο ίσχυε το δεύτερο, μια
ενημέρωση ποσοστού μετά τον υπολογισμό άλλαζε ΣΙΩΠΗΛΑ το υποβαλλόμενο αρχείο
ενώ οι αποδείξεις και τα καθαρά έμεναν στα παλιά ποσά.

ΤΟ ΑΝΑΛΛΟΙΩΤΟ: άθροισμα εισφορών ΟΛΩΝ των αρχείων της περιόδου = εισφορές
που υπολογίστηκαν. Στο σεντ.

Εκτέλεση: uv run pytest tests/test_apd_from_events.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain.events import LeaveType
from misevent.domain.projections import Projections
from misevent.domain.scheduler import ObligationScheduler
from misevent.integrations import ergani_parameters
from misevent.persistence.database import Database

FULL_WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}
KPK_RATES = "main_pension=10,37/18,79|healthcare=0/0|auxiliary=3/3"
NEW_KPK_RATES = "main_pension=11,37/19,79|healthcare=0/0|auxiliary=3/3"


def setup_teka(monthly_gross=1500.0, hire=date(2026, 1, 5)):
    """Ο ΙΔΙΟΣ κόσμος, με ΤΕΚΑ: κύρια 0103 (ΕΦΚΑ) + επικουρική 4169 (ΤΕΚΑ)
    -> ΔΥΟ αρχεία ΑΠΔ για την ίδια περίοδο."""
    svc, proj, eid = setup(monthly_gross, hire, teka=True)
    return svc, proj, eid


def setup(monthly_gross=1500.0, hire=date(2026, 1, 5), teka=False,
          birth_date=date(1990, 5, 5), sex=""):
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    # ΜΕ χρονολόγιο: το άνοιγμα περιόδου δουλεύει πάνω στις υποχρεώσεις
    store.subscribe(ObligationScheduler(store, proj, horizon_months=1).handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
    svc.define_kpk_rates(
        "efka_main", "0101", date(2024, 1, 1), 0.1337, 0.2179, KPK_RATES)
    svc.define_kpk("efka", "0103", "ΙΚΑ ΜΙΚΤΑ (κύρια)", "main")
    svc.define_kpk_rates(
        "efka_main", "0103", date(2024, 1, 1), 0.1037, 0.1879,
        "main_pension=10,37/18,79|healthcare=0/0")
    svc.define_kpk("teka", "4169", "ΤΕΚΑ Επικουρική", "auxiliary")
    svc.define_kpk_rates("teka", "4169", date(2022, 6, 1), 0.03, 0.03,
                         "auxiliary=3/3")
    svc.define_specialty(
        "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ",
        {"MAIN_EPIK": {"kad": "0141", "specialty_code": "411100",
                       "kpk": "0101"},
         "MAIN": {"kad": "0141", "specialty_code": "411100", "kpk": "0103"},
         "TEKA": {"kad": "0083", "specialty_code": "000860", "kpk": "4169"}},
        ergani_specialty_code="419", ergani_specialty_description="ΛΟΙΠΟΙ ΥΠΑΛΛΗΛΟΙ ΓΡΑΦΕΙΟΥ",
    )
    pid = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ", first_name="ΙΩΑΝΝΗΣ", father_name="ΝΙΚΟΛΑΟΣ",
        mother_name="ΜΑΡΙΑ", birth_date=birth_date, afm="111222336",
        amka="05059012343", ama="123456789",
        # Φύλο ⇒ ΟΛΑ τα στοιχεία ταυτότητας WebE3N (`check_web_e3n_identity`)
        **({"sex": sex,
            "nationality_code": ergani_parameters.nationalities()[0]["code"],
            "identity_type": ergani_parameters.identity_types()[0]["code"],
            "identity_number": "ΑΒ123456"} if sex else {}),
    )
    eid = svc.start_employment(
        pid, hire_date=hire, monthly_gross=monthly_gross,
        weekly_hours=40.0, weekly_pattern=FULL_WEEK,
    )
    svc.assign_specialty(
        eid, "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ",
        auxiliary_scheme="teka" if teka else "efka",
    )
    svc.submit_hire_e3(eid)
    svc.submit_hire_wto(eid)
    return svc, proj, eid


def apd_contributions(svc, year, month):
    """Εισφορές (εργαζ. + εργοδ.) ΟΛΩΝ των αρχείων ΑΠΔ της περιόδου."""
    total = 0.0
    for pairs in svc.build_apd_rows(year, month).values():
        for _info, r in pairs:
            total += r.contrib_employee + r.contrib_employer
    return round(total, 2)


def payroll_contributions(proj, year, month):
    return round(
        sum(p.efka_employee + p.efka_employer for p in proj.payrolls
            if (p.period_year, p.period_month) == (year, month)),
        2,
    )


def test_apd_declares_what_payroll_calculated():
    """ΤΟ ΑΝΑΛΛΟΙΩΤΟ: το αρχείο δεν αλλάζει σύνολα, μόνο τα αναλύει."""
    svc, proj, _eid = setup()
    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)

    assert apd_contributions(svc, 2026, 1) == payroll_contributions(proj, 2026, 1)


def test_sickness_rows_share_the_same_total():
    """Με ασθένεια η αμοιβή σπάει σε 001 + 008: οι εισφορές μοιράζονται
    κατ' αναλογία και το άθροισμα μένει ΑΚΡΙΒΩΣ το ίδιο (καμία απώλεια
    λεπτού στη στρογγυλοποίηση)."""
    svc, proj, eid = setup()
    svc.record_leave(eid, LeaveType.SICK, date(2026, 1, 5), date(2026, 1, 9))
    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)

    kinds = {r.earnings_type for _i, r in svc.build_apd_rows(2026, 1)["efka"]}
    assert {"001", "008"} <= kinds, "αναμενόταν ξεχωριστή γραμμή ασθενείας"
    assert apd_contributions(svc, 2026, 1) == payroll_contributions(proj, 2026, 1)


def test_retroactive_kpk_change_does_not_move_the_file():
    """Ο ΠΥΡΗΝΑΣ ΤΟΥ ΠΡΟΒΛΗΜΑΤΟΣ: αλλαγή ποσοστού ΜΕΤΑ τον υπολογισμό δεν
    αγγίζει το αρχείο — το αρχείο ακολουθεί μόνο τον επανυπολογισμό."""
    svc, proj, _eid = setup()
    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)
    before = apd_contributions(svc, 2026, 1)

    svc.define_kpk_rates(
        "efka_main", "0101", date(2026, 1, 1), 0.1437, 0.2279, NEW_KPK_RATES)
    assert apd_contributions(svc, 2026, 1) == before, (
        "το υποβαλλόμενο αρχείο άλλαξε χωρίς επανυπολογισμό μισθοδοσίας"
    )
    assert svc.contribution_drift(2026, 1), "η ασυμφωνία πρέπει να φαίνεται"

    svc.reopen_payroll_period(2026, 1, "διόρθωση ΚΠΚ")
    svc.run_monthly_payroll(2026, 1)
    after = apd_contributions(svc, 2026, 1)
    assert after > before, "μετά τον επανυπολογισμό το αρχείο ακολουθεί"
    assert after == payroll_contributions(proj, 2026, 1)
    assert svc.contribution_drift(2026, 1) == []


def test_gift_contributions_come_from_the_gift_event():
    """Τα Δώρα μπαίνουν στο αρχείο του ΕΠΟΜΕΝΟΥ μήνα με τις εισφορές που
    υπολογίστηκαν — και το εργοδοτικό σκέλος γράφεται πλέον στο event."""
    svc, proj, _eid = setup()
    for m in range(1, 13):
        svc.approve_attendance(2026, m)
        svc.run_monthly_payroll(2026, m)
    svc.run_christmas_gifts(2026)

    gift = next(g for g in proj.gifts if g.gift_type == "christmas")
    assert gift.efka_employer > 0, "το event κρατά πλέον και τις εισφορές εργοδότη"

    rows = [r for _i, r in svc.build_apd_rows(2027, 1)["efka"]
            if r.earnings_type == "003"]
    assert rows, "το Δώρο δηλώνεται στην ΑΠΔ Ιανουαρίου"
    got = round(sum(r.contrib_employee + r.contrib_employer for r in rows), 2)
    assert got == round(gift.efka_employee + gift.efka_employer, 2)


def test_legacy_events_keep_their_totals():
    """ΠΑΛΙΑ events (χωρίς ανάλυση ανά κάλυψη) δεν μένουν έξω: τα ΣΥΝΟΛΑ τους
    σπάνε με τα σημερινά ποσοστά, αλλά το άθροισμα παραμένει το δικό τους."""
    svc, proj, _eid = setup()
    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)
    # Προσομοίωση παλιού event: η ανάλυση χωρίς το κλειδί «contributions»
    p = proj.payrolls[0]
    p.analysis.pop("contributions")

    assert apd_contributions(svc, 2026, 1) == payroll_contributions(proj, 2026, 1)


def test_teka_file_declares_its_own_stored_share():
    """ΤΕΚΑ: δεύτερο αρχείο, ΙΔΙΟΣ κανόνας. Η επικουρική δηλώνει ΑΚΡΙΒΩΣ το
    μερίδιο που κράτησε ο υπολογισμός για την κάλυψή της — και τα δύο αρχεία
    μαζί αθροίζουν στο σύνολο της μισθοδοσίας."""
    svc, proj, _eid = setup_teka()
    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)

    files = svc.build_apd_rows(2026, 1)
    assert set(files) == {"efka", "teka"}, "αναμενόταν ξεχωριστό αρχείο ΤΕΚΑ"

    p = proj.payrolls[0]
    stored = {ln["fund"]: round(ln["employee"] + ln["employer"], 2)
              for ln in p.analysis["contributions"]}
    teka_file = round(sum(r.contrib_employee + r.contrib_employer
                          for _i, r in files["teka"]), 2)
    assert teka_file == stored["teka"], "η ΑΠΔ ΤΕΚΑ δεν δηλώνει ό,τι κρατήθηκε"
    assert apd_contributions(svc, 2026, 1) == payroll_contributions(proj, 2026, 1)

    # Και εδώ: αναδρομική αλλαγή ποσοστού ΤΕΚΑ δεν αγγίζει το αρχείο
    svc.define_kpk_rates("teka", "4169", date(2026, 1, 1), 0.05, 0.05,
                         "auxiliary=5/5")
    after = round(sum(r.contrib_employee + r.contrib_employer
                      for _i, r in svc.build_apd_rows(2026, 1)["teka"]), 2)
    assert after == teka_file


def test_fmy_declares_the_tax_that_was_withheld():
    """ΦΜΥ: ο φόρος του αρχείου JL10 είναι ΑΘΡΟΙΣΜΑ των events — μισθοδοσία
    ΚΑΙ Δώρα/Επίδομα που φορολογήθηκαν τον ίδιο μήνα. Καμία επανεκτίμηση:
    αλλαγή κλίμακας μετά τον υπολογισμό δεν αγγίζει τη δήλωση."""
    svc, proj, _eid = setup(monthly_gross=2500.0)
    for m in range(1, 13):
        svc.approve_attendance(2026, m)
        svc.run_monthly_payroll(2026, m)
    svc.run_christmas_gifts(2026)

    dec = next(p for p in proj.payrolls if p.period_month == 12)
    gift = next(g for g in proj.gifts if g.gift_type == "christmas")
    assert dec.fmy > 0 and gift.fmy > 0, "αναμενόταν παρακράτηση φόρου"

    rows = svc.fmy_rows(2026, 12)
    assert round(sum(r.tax for r in rows), 2) == round(dec.fmy + gift.fmy, 2)
    assert svc.fmy_total(2026, 12) == round(dec.fmy + gift.fmy, 2)
    # Οι αποδοχές της δήλωσης είναι επίσης των events (μισθοδοσία + Δώρο)
    assert round(sum(r.gross for r in rows), 2) == round(dec.gross + gift.gross, 2)
