"""
Tests: τα **Δώρα** και το **Επίδομα Αδείας** μπαίνουν στο αρχείο ΦΜΥ (JL10)
στον μήνα που ΥΠΟΛΟΓΙΣΤΗΚΑΝ — **αθροιστικά** με τις αποδοχές του μήνα, σε
ΜΙΑ γραμμή ανά εργαζόμενο με είδος αποδοχών (4.9) = 1.

ΠΡΟΣΟΧΗ: δεν είναι η περίοδος ΑΠΔ του Δώρου (εκεί μπαίνει στο αρχείο του
ΕΠΟΜΕΝΟΥ μήνα, §5.4) — ο φόρος παρακρατείται όταν καταβάλλεται το Δώρο.

Εκτέλεση: python tests/test_fmy_gifts.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain.events import LeaveType
from misevent.domain.projections import Projections
from misevent.domain.scheduler import ObligationScheduler
from misevent.integrations.fmy_files import EARNINGS_KIND_REGULAR
from misevent.persistence.database import Database
from misevent.rules import obligation_types as ot

FULL_WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}

fails = []


def ok(desc, cond):
    print(f"  {'OK  ' if cond else 'FAIL'} {desc}")
    if not cond:
        fails.append(desc)


def setup(hire=date(2026, 1, 5)):
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    sched = ObligationScheduler(store, proj, horizon_months=1)
    store.subscribe(sched.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    svc.register_company(
        ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ",
    )
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
        pid, hire_date=hire, monthly_gross=1500.0,
        weekly_hours=40.0, weekly_pattern=FULL_WEEK,
    )
    svc.assign_specialty(eid, "ΥΠΑΛΛΗΛΟΣ")
    svc.submit_hire_e3(eid)
    svc.submit_hire_wto(eid)
    return svc, proj, eid


def payroll(svc, year, month):
    svc.approve_attendance(year, month)
    svc.run_monthly_payroll(year, month)
    # Η προσαρμογή στον κατώτατο προηγείται των Δώρων/Επιδόματος (§8)
    svc.apply_minimum_adjustments(year, month)


def test_easter_gift_in_its_month():
    """Δώρο Πάσχα: στον μήνα της προθεσμίας του (Μ. Τετάρτη)."""
    print("=== Δώρο Πάσχα στο ΦΜΥ του μήνα υπολογισμού ===")
    svc, proj, eid = setup()
    m = ot.due_date("EASTER_GIFT", 2026, 0).month  # 4 ή 5 — το Πάσχα κινείται
    for mm in range(1, m + 1):
        payroll(svc, 2026, mm)
    svc.run_easter_gifts(2026)

    g = next(x for x in proj.gifts if x.gift_type == "easter")
    ok("η περίοδος ΦΜΥ του Δώρου είναι ο μήνας της προθεσμίας",
       svc.gift_fmy_period(g) == (2026, m))

    rows = svc.fmy_rows(2026, m)
    ok("ΜΙΑ γραμμή για τον εργαζόμενο, είδος αποδοχών 1",
       len(rows) == 1 and rows[0].afm == "111222336"
       and rows[0].earnings_kind == EARNINGS_KIND_REGULAR)

    p = next(x for x in proj.payrolls
             if x.period_year == 2026 and x.period_month == m)
    r = rows[0]
    gift_taxable = round(g.gross - g.efka_employee, 2)
    ok("οι αποδοχές του Δώρου ΠΡΟΣΤΙΘΕΝΤΑΙ στις αποδοχές του μήνα",
       r.gross == round(p.gross + g.gross, 2)
       and r.deductions == round(p.efka_employee + g.efka_employee, 2)
       and r.net == round(p.taxable + gift_taxable, 2))
    ok("ο φόρος του Δώρου προστίθεται στον φόρο του μήνα",
       r.tax == round(p.fmy + g.fmy, 2))
    ok("το σύνολο ΦΜΥ = μισθοδοσία + Δώρο",
       svc.fmy_total(2026, m) == round(p.fmy + g.fmy, 2))
    ok("η δήλωση ΦΜΥ δηλώνει το ΙΔΙΟ ποσό με το αρχείο",
       svc.submit_fmy(2026, m) == svc.fmy_total(2026, m))

    prev = svc.fmy_rows(2026, m - 1)
    pprev = next(x for x in proj.payrolls
                 if x.period_year == 2026 and x.period_month == m - 1)
    ok("ο προηγούμενος μήνας ΔΕΝ περιέχει το Δώρο",
       len(prev) == 1 and prev[0].tax == pprev.fmy)


def test_allowance_in_leave_month():
    """Επίδομα Αδείας: στον μήνα της πρώτης κανονικής άδειας."""
    print("\n=== Επίδομα Αδείας στον μήνα της άδειας ===")
    svc, proj, eid = setup()
    for mm in range(1, 6):
        payroll(svc, 2026, mm)
    # η άδεια πρέπει να καταχωρηθεί ΠΡΙΝ κλείσει η μισθοδοσία του Ιουνίου
    svc.record_leave(eid, LeaveType.ANNUAL, date(2026, 6, 15), date(2026, 6, 19))
    payroll(svc, 2026, 6)
    svc.run_leave_allowance(2026, 6)

    a = next(iter(proj.allowances))
    ok("η περίοδος ΦΜΥ του επιδόματος είναι ο μήνας της άδειας",
       svc.allowance_fmy_period(a) == (2026, 6))
    p = next(x for x in proj.payrolls
             if x.period_year == 2026 and x.period_month == 6)
    rows = svc.fmy_rows(2026, 6)
    ok("το επίδομα προστίθεται στη γραμμή του Ιουνίου",
       len(rows) == 1
       and rows[0].gross == round(p.gross + a.gross, 2)
       and rows[0].tax == round(p.fmy + a.fmy, 2))


def test_exit_settlement_month():
    """Δώρο που εκκαθαρίζεται ΣΤΗΝ ΑΠΟΧΩΡΗΣΗ: στον μήνα της αποχώρησης."""
    print("\n=== Εκκαθάριση αποχώρησης ===")
    svc, proj, eid = setup()
    for mm in range(1, 6):
        payroll(svc, 2026, mm)
    # η αποχώρηση δηλώνεται όσο ο Ιούνιος είναι ακόμη ανοιχτός (§5.6)
    svc.terminate(eid, date(2026, 6, 30), reason="ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")

    g = next(x for x in proj.gifts if x.gift_type == "christmas")
    ok("το pro-rata Δώρο Χριστουγέννων δηλώνεται στον μήνα αποχώρησης",
       svc.gift_fmy_period(g) == (2026, 6))
    ok("ο Ιούνιος περιέχει τον φόρο του Δώρου",
       svc.fmy_total(2026, 6) >= g.fmy)
    ok("δεν μεταφέρεται στον Δεκέμβριο", svc.fmy_rows(2026, 12) == [])


def test_file_has_one_row_per_employee():
    """Το JL10 γράφει ΜΙΑ αναλυτική ανά εργαζόμενο, με τα Δώρα μέσα."""
    print("\n=== Το αρχείο JL10 ===")
    import io
    import zipfile

    from misevent.integrations import fmy_files

    svc, proj, eid = setup()
    m = ot.due_date("EASTER_GIFT", 2026, 0).month
    for mm in range(1, m + 1):
        payroll(svc, 2026, mm)
    svc.run_easter_gifts(2026)

    # ΚΑΝΕΝΑ αρχείο στον δίσκο: η δήλωση καταγράφει, το περιεχόμενο παράγεται
    # on the fly (ίδιο με ό,τι κατεβάζει ο χρήστης από τα «Αρχεία ΦΜΥ»).
    name = svc.generate_fmy_file(2026, m, created=date(2026, 6, 15))
    ok("λογικό όνομα λήψης", name == fmy_files.zip_name(2026, m))
    with zipfile.ZipFile(io.BytesIO(svc.fmy_zip_bytes(2026, m))) as z:
        ok("το ZIP περιέχει μόνο το JL10", z.namelist() == [fmy_files.FILE_NAME])
        data = z.read(fmy_files.FILE_NAME).decode(fmy_files.ENCODING)
    lines = [ln for ln in data.split("\r\n") if ln]
    detail = [ln for ln in lines if ln[0] == "3"]
    ok("ΜΙΑ αναλυτική εγγραφή (τακτικές μαζί με το Δώρο)", len(detail) == 1)
    ok("είδος αποδοχών 01", detail[0][54:56] == "01")
    ok("όλες οι εγγραφές 148 χαρακτήρες",
       all(len(ln) == 148 for ln in lines))

    g = next(x for x in proj.gifts if x.gift_type == "easter")
    p = next(x for x in proj.payrolls
             if x.period_year == 2026 and x.period_month == m)
    f = next(iter(proj.fmy_files))
    ok("ένας εργαζόμενος στο event", f.employee_count == 1)
    ok("το σύνολο ακαθαρίστων του event = μισθός + Δώρο",
       round(f.total_gross, 2) == round(p.gross + g.gross, 2))
    ok("το σύνολο φόρου του event = fmy_total",
       round(f.total_tax, 2) == svc.fmy_total(2026, m))
