"""
Tests εισαγωγής παρουσιών από αρχείο «Κάρτας Εργασίας» ΕΡΓΑΝΗ (xlsx).
Χρησιμοποιεί το ανωνυμοποιημένο δείγμα data/karta_2026-03-05.xlsx.

Εκτέλεση: python test_karta_import.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.integrations import karta_import as ki
from misevent.persistence.database import Database

KARTA = "data/karta_2026-03-05.xlsx"
FULL_WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}


def test_parse_real_file():
    punches = ki.parse_karta(KARTA)
    assert len(punches) == 206
    afms = {p.afm for p in punches}
    assert len(afms) == 11
    assert all(len(a) == 9 and a.isdigit() for a in afms)
    # όλες οι ημερομηνίες μέσα στον Φεβρουάριο 2026
    assert {(p.day.year, p.day.month) for p in punches} == {(2026, 2)}
    print("OK διάβασμα πραγματικού αρχείου (206 βάρδιες, 11 ΑΦΜ)")


def test_multiple_shifts_summed():
    """Η 2026-02-09 του ΑΦΜ 100011110 έχει δύο βάρδιες (08-12 + 17-19) ->
    μία ημέρα, 6 ώρες."""
    daily = ki.daily_hours(ki.parse_karta(KARTA))
    assert daily[("100011110", date(2026, 2, 9))] == 6.0
    # μονή βάρδια 08:00-12:00 = 4 ώρες
    assert daily[("100011110", date(2026, 2, 2))] == 4.0
    print("OK πολλαπλές βάρδιες ίδιας ημέρας αθροίζονται")


def test_bad_file_rejected():
    try:
        ki.parse_karta(b"not a zip")
    except ValueError as e:
        assert "xlsx" in str(e)
        print("OK μη έγκυρο αρχείο απορρίπτεται καθαρά")
    else:
        raise AssertionError("έπρεπε να πεταχτεί ValueError")


def _svc_with_person(afm):
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    pid = svc.register_person(
        last_name="ΜΕΜΗ", first_name="ΒΑΣΙΛ", father_name="Α", mother_name="Β",
        birth_date=date(1990, 1, 1), afm=afm, amka="01019012341", ama="123456789",
    )
    eid = svc.start_employment(pid, date(2026, 1, 5), monthly_gross=1400.0,
                               weekly_pattern=FULL_WEEK)
    svc.assign_insurance_profile(
        eid,
        coverages=({"fund": "efka_main", "kad": "0000",
                    "specialty_code": "000000", "kpk": "0101"},),
    )
    # Η πρόσληψη ολοκληρώνεται με την ΑΝΑΓΓΕΛΙΑ Ε3 (§5.7α) — χωρίς αυτήν η
    # απασχόληση είναι εκκρεμής και η κάρτα δεν αντιστοιχίζεται σε αυτήν.
    svc.submit_hire_e3(eid)
    return svc, proj, eid


def test_import_matches_by_afm():
    """Η εισαγωγή αντιστοιχίζει με ΑΦΜ και καταγράφει παρουσίες."""
    svc, proj, eid = _svc_with_person("100011110")
    res = svc.import_karta_attendance(open(KARTA, "rb").read(), 2026, 2)

    assert res["periods"] == [(2026, 2)]
    assert res["employees"] == 1  # μόνο ο ΜΕΜΗ ταιριάζει
    # τα υπόλοιπα 10 ΑΦΜ δεν έχουν απασχόληση -> άγνωστα
    assert len(res["unmatched"]) == 10
    assert "100011110" not in res["unmatched"]

    emp = proj.employments[eid]
    # ημέρες παρουσίας του ΜΕΜΗ τον Φεβρουάριο (μία ανά ημερολογιακή ημέρα)
    feb_days = ki.daily_hours(ki.parse_karta(KARTA))
    expected = {d for (a, d) in feb_days if a == "100011110"}
    assert emp.attendance_days_in_month(2026, 2) == len(expected)
    assert emp.attendance[date(2026, 2, 9)] == 6.0  # αθροισμένη βάρδια
    print(f"OK εισαγωγή με ΑΦΜ: {emp.attendance_days_in_month(2026, 2)} ημέρες, "
          "10 άγνωστα ΑΦΜ")


def test_import_feeds_attendance_draft():
    """Οι εισαγόμενες παρουσίες τροφοδοτούν το συγκεντρωτικό (source=recorded)."""
    svc, proj, eid = _svc_with_person("100011110")
    svc.submit_hire_wto(eid)
    svc.import_karta_attendance(open(KARTA, "rb").read(), 2026, 2)

    draft = svc.attendance_draft(2026, 2)[eid]
    assert draft["source"] == "recorded"
    feb = ki.daily_hours(ki.parse_karta(KARTA))
    expected = len({d for (a, d) in feb if a == "100011110"})
    # + οι ημέρες προγράμματος ΧΩΡΙΣ χτύπημα: μετρούν με το πρόγραμμα
    assert draft["worked"] == expected + len(draft["no_punch_days"])
    print(f"OK το συγκεντρωτικό δείχνει {draft['worked']} ημέρες (πηγή: recorded)")


def test_flexible_layout():
    """Τίτλος πάνω από την επικεφαλίδα, άλλη σειρά στηλών, χωρίς τόνους,
    «Είσοδος/Έξοδος», ISO/Excel ημερομηνίες και ώρες -> διαβάζονται όλα."""
    import io
    import zipfile

    rows = [
        ("Κάρτα εργασίας",),
        ("ΕΞΟΔΟΣ", "εισοδος", "ημερομηνια", "Παράρτημα", "αφμ"),
        ("17:00:00", "8:01", "2026-09-14", "0", "115511740"),
        ("0.75", "0.3333333", "46280", "0", "115511740"),  # 15/9/2026 08:00-18:00
        ("", "09:00", "16/9/2026 00:00", "0", "115511740"),
    ]
    xml = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
           '2006/main"><sheetData>' + "".join(
               f'<row r="{i}">' + "".join(
                   f'<c r="{c}{i}" t="str"><v>{v}</v></c>' for c, v in zip("ABCDE", r)
               ) + "</row>" for i, r in enumerate(rows, 1))
           + "</sheetData></worksheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", xml)
    got = [(p.day, p.time_in, p.time_out) for p in ki.parse_karta(buf.getvalue())]
    assert got == [
        (date(2026, 9, 14), "08:01", "17:00"),
        (date(2026, 9, 15), "08:00", "18:00"),
        (date(2026, 9, 16), "09:00", ""),
    ], got
    print("OK ελαστική ανάγνωση στηλών/ημερομηνιών/ωρών")


def test_import_skips_records_of_other_months():
    """Οι εγγραφές εκτός της περιόδου ΔΕΝ καταγράφονται — παραλείπονται."""
    svc, proj, eid = _svc_with_person("100011110")
    res = svc.import_karta_attendance(open(KARTA, "rb").read(), 2026, 3)
    assert res["recorded"] == 0 and res["skipped"]
    assert {(d.year, d.month) for d in res["skipped"]} == {(2026, 2)}
    assert not proj.employments[eid].attendance
