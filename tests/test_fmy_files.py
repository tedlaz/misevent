"""
Tests γραμμογράφησης JL10 (Δήλωση απόδοσης ΦΜΥ).
Οι θέσεις ελέγχονται ΑΝΑ BYTE, από τις στήλες ΑΠΟ/ΕΩΣ του data/JL10a.md.
Εκτέλεση: python test_fmy_files.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.integrations.fmy_files import (
    DeclarantInfo,
    EarningsRow,
    FMYFileBuilder,
    FMYFormatError,
    dt_ymd,
)

DECL = DeclarantInfo(
    afm="044589898",
    legal_name="ΔΑΣΟΣ ΕΠΕ",
    activity="ΞΥΛΕΙΑ",
    city="ΑΘΗΝΑ",
    street="ΔΑΣΟΥΣ",
    street_no="20",
    postal_code="14456",
)

ROW = EarningsRow(
    afm="111222333",
    last_name="ΓΕΩΡΓΙΟΥ",
    first_name="ΜΑΡΙΑ",
    father_name="ΓΕΩ",
    amka="01019012345",
    children=2,
    gross=1288.00,
    deductions=173.87,
    net=1114.13,
    tax=41.25,
)


def build(rows=(ROW,), **kw):
    b = FMYFileBuilder(DECL, 2026, 5, created=date(2026, 6, 15), **kw)
    for r in rows:
        b.add(r)
    return [ln for ln in b.render().split("\r\n") if ln]


def at(line, frm, to):
    """Το πεδίο των θέσεων ΑΠΟ..ΕΩΣ του εγγράφου (1-based, inclusive)."""
    return line[frm - 1 : to]


def test_record_lengths():
    lines = build()
    assert len(lines) == 4, len(lines)
    assert all(len(ln) == 148 for ln in lines), [len(ln) for ln in lines]
    assert "".join(ln[0] for ln in lines) == "0123"
    print("OK μήκη εγγραφών (όλες 148) και σειρά τύπων 0-1-2-3")


def test_header_positions():
    h = build()[0]
    assert at(h, 1, 1) == "0"  # 1.1 τύπος
    assert at(h, 2, 9) == "JL10    "  # 1.2 όνομα αρχείου Χ(8)
    assert at(h, 10, 17) == "20260615"  # 1.3 ΕΕΕΕΜΜΗΗ
    assert at(h, 18, 21) == "2026"  # 1.4 αρ. κύκλου
    assert at(h, 22, 148) == " " * 127  # 1.5 FILLER
    assert dt_ymd(None) == "00000000"
    print("OK θέσεις κεφαλίδας (εγγραφή 0)")


def test_declarant_positions():
    d = build()[1]
    assert at(d, 1, 1) == "1"  # 2.1
    assert at(d, 2, 5) == "2026"  # 2.2 έτος
    assert at(d, 6, 23) == "ΔΑΣΟΣ ΕΠΕ".ljust(18)  # 2.3α Χ(18)
    assert at(d, 24, 32) == " " * 9  # 2.3β όνομα (νομικό πρόσωπο)
    assert at(d, 33, 35) == " " * 3  # 2.3γ πατρώνυμο
    assert at(d, 36, 36) == "0"  # 2.4 επωνυμία
    assert at(d, 37, 45) == "044589898"  # 2.5 ΑΦΜ
    assert at(d, 46, 61) == "ΞΥΛΕΙΑ".ljust(16)  # 2.6 δραστηριότητα
    assert at(d, 62, 71) == "ΑΘΗΝΑ".ljust(10)  # 2.7 πόλη
    assert at(d, 72, 87) == "ΔΑΣΟΥΣ".ljust(16)  # 2.8 οδός
    assert at(d, 88, 92) == "20   "  # 2.9 αριθμός
    assert at(d, 93, 97) == "14456"  # 2.10 ΤΚ
    assert at(d, 98, 99) == "05"  # 2.11 μήνας
    assert at(d, 100, 148) == " " * 49  # 2.12 FILLER
    # Φυσικό πρόσωπο -> ένδειξη 1 και συμπληρωμένο όνομα/πατρώνυμο
    b = FMYFileBuilder(
        DeclarantInfo(afm="044589898", legal_name="ΠΑΠΠΑΣ", first_name="ΝΙΚΟΣ",
                      father_name="ΓΕΩ", is_person=True),
        2026, 5, created=date(2026, 6, 15),
    )
    b.add(ROW)
    p = [ln for ln in b.render().split("\r\n") if ln][1]
    assert at(p, 6, 23) == "ΠΑΠΠΑΣ".ljust(18)  # 2.3α επώνυμο
    assert at(p, 24, 32) == "ΝΙΚΟΣ".ljust(9)  # 2.3β όνομα
    assert at(p, 33, 35) == "ΓΕΩ"  # 2.3γ πατρώνυμο
    assert at(p, 36, 36) == "1"  # 2.4 ένδειξη ονοματεπωνύμου
    # Εταιρία: κενά όνομα/πατρώνυμο και ένδειξη 0
    assert at(d, 24, 32) == " " * 9 and at(d, 33, 35) == "   "
    assert at(d, 36, 36) == "0"
    print("OK θέσεις στοιχείων δηλούντος (εγγραφή 1)")


def test_totals_positions():
    t = build()[2]
    assert at(t, 1, 1) == "2"  # 3.1
    assert at(t, 2, 17) == "0" * 10 + "128800"  # 3.2 ακαθάριστες 9(14)V99
    assert at(t, 18, 33) == "0" * 11 + "17387"  # 3.3 κρατήσεις
    assert at(t, 34, 49) == "0" * 10 + "111413"  # 3.4 καθαρές
    assert at(t, 50, 64) == "0" * 15  # 3.5 FILLER
    assert at(t, 65, 79) == "0" * 11 + "4125"  # 3.6 φόρος 9(13)V99
    assert at(t, 80, 94) == "0" * 15  # 3.7 ειδική εισφορά
    assert at(t, 95, 108) == "0" * 14  # 3.8 ψηφιακό τέλος 9(12)V99
    assert at(t, 109, 121) == "0" * 13  # 3.9 FILLER
    assert at(t, 122, 148) == " " * 27  # 3.10 FILLER
    print("OK θέσεις συνόλων (εγγραφή 2) — άθροισμα των αναλυτικών")


def test_detail_positions():
    r = build()[3]
    assert at(r, 1, 1) == "3"  # 4.1
    assert at(r, 2, 10) == "111222333"  # 4.2 ΑΦΜ
    assert at(r, 11, 11) == " "  # 4.3 FILLER
    assert at(r, 12, 29) == "ΓΕΩΡΓΙΟΥ".ljust(18)  # 4.4 επώνυμο
    assert at(r, 30, 38) == "ΜΑΡΙΑ".ljust(9)  # 4.5 όνομα
    assert at(r, 39, 41) == "ΓΕΩ"  # 4.6 πατρώνυμο
    assert at(r, 42, 52) == "01019012345"  # 4.7 ΑΜΚΑ
    assert at(r, 53, 54) == "02"  # 4.8 τέκνα
    assert at(r, 55, 56) == "01"  # 4.9 είδος αποδοχών (τακτικές)
    assert at(r, 57, 67) == "0" * 5 + "128800"  # 4.10 ακαθάριστες 9(9)V99
    assert at(r, 68, 77) == "0" * 5 + "17387"  # 4.11 κρατήσεις 9(8)V99
    assert at(r, 78, 88) == "0" * 5 + "111413"  # 4.12 καθαρές 9(9)V99
    assert at(r, 89, 89) == "0"  # 4.13 μη αλλοδαπός
    assert at(r, 90, 91) == "  "  # 4.14 χώρα
    assert at(r, 92, 93) == "00"  # 4.15 συντελεστής
    assert at(r, 94, 98) == "00000"  # 4.16 FILLER
    assert at(r, 99, 108) == "0" * 6 + "4125"  # 4.17 φόρος 9(8)V99
    assert at(r, 109, 118) == "0" * 10  # 4.18 ειδική εισφορά
    assert at(r, 119, 127) == "0" * 9  # 4.19 ψηφιακό τέλος 9(7)V99
    assert at(r, 128, 135) == "0" * 8  # 4.20 FILLER
    assert at(r, 136, 139) == "0000"  # 4.21 έτος αναφοράς
    assert at(r, 140, 148) == "0000/0000"  # 4.22 διάταξη νόμου (δείγμα ΑΑΔΕ)
    print("OK θέσεις αναλυτικών αμοιβών (εγγραφή 3)")


def test_totals_are_the_sum():
    """Τα σύνολα της εγγραφής 2 = άθροισμα ΟΛΩΝ των εγγραφών 3."""
    import dataclasses

    other = dataclasses.replace(
        ROW, afm="044555666", last_name="ΠΑΠΠΑΣ", first_name="ΚΩΣΤΑΣ",
        amka="04049112345", children=0, gross=900.0, deductions=121.5,
        net=778.5, tax=12.4,
    )
    lines = build((ROW, other))
    t = lines[2]
    assert len(lines) == 5
    assert at(t, 2, 17) == "0" * 10 + "218800"  # 1288 + 900
    assert at(t, 18, 33) == "0" * 11 + "29537"  # 173,87 + 121,50 = 295,37
    assert at(t, 65, 79) == "0" * 11 + "5365"  # 41,25 + 12,40
    # Ταξινόμηση κατά ΑΦΜ -> ο ΠΑΠΠΑΣ (044…) πριν τον ΓΕΩΡΓΙΟΥ (111…)
    assert at(lines[3], 2, 10) == "044555666"
    assert at(lines[4], 2, 10) == "111222333"
    print("OK σύνολα = άθροισμα αναλυτικών, ταξινόμηση κατά ΑΦΜ")


def test_encoding_error_is_clear():
    """Το JL10 γράφεται σε cp1253 (βλ. fmy_files docstring). Ένα codepage
    χωρίς ελληνικά πρέπει να δίνει ΣΑΦΕΣ λάθος, όχι χαλασμένο αρχείο.
    Ελέγχει και τη ΣΥΣΚΕΥΑΣΙΑ: ΚΑΝΕΝΑ αρχείο στον δίσκο — το ZIP παράγεται
    στη μνήμη, με ΕΝΑ JL10 μέσα (αυτό κατεβάζει ο χρήστης)."""
    import io
    import zipfile

    b = FMYFileBuilder(DECL, 2026, 5, created=date(2026, 6, 15))
    b.add(ROW)
    try:
        b.encoded(encoding="cp437")
        raise AssertionError("έπρεπε να σκάσει σε cp437")
    except FMYFormatError as exc:
        assert "cp437" in str(exc) and "1253" in str(exc), str(exc)
    # Στο codepage του δείγματος γράφεται κανονικά
    assert b.zip_name() == "FMY_2026_05.zip"
    blob = b.zip_bytes()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert z.namelist() == ["JL10"]
        data = z.read("JL10")
    assert len(data) == 4 * 150  # 4 εγγραφές x (148 + CRLF)
    assert data.endswith(b"\r\n")
    assert data == b.encoded()  # ίδια bytes με το ωμό αρχείο
    # Αναπαραγώγιμο: ίδια δεδομένα -> ίδιο ZIP byte-for-byte
    assert blob == b.zip_bytes()
    print("OK σαφές σφάλμα σε cp437 · ZIP FMY_ΕΕΕΕ_ΜΜ.zip με το JL10 μέσα")
