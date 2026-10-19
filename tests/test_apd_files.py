"""
Tests γραμμογράφησης ΑΠΔ e-ΕΦΚΑ & ΤΕΚΑ.
Εκτέλεση: python test_apd_files.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.integrations.apd_files import (
    APDFileBuilder,
    EmployerInfo,
    InsuranceRow,
    InsuredInfo,
    alpha,
    amount,
    dt,
    pct,
)

EMP = EmployerInfo(
    ame="1234567890",
    afm="999888777",
    legal_name="ΔΟΚΙΜΗ ΑΕ",
    street="ΟΔΟΣ",
    street_no="1",
    postal_code="54625",
    city="ΘΕΣΣΑΛΟΝΙΚΗ",
    local_office_code="1150706000",
    local_office_name="ΤΔ Α",
)

INS = InsuredInfo(
    ama="123456789",
    amka="01019012345",
    last_name="ΓΕΩΡΓΙΟΥ",
    first_name="ΜΑΡΙΑ",
    father_name="ΓΕΩΡΓΙΟΣ",
    mother_name="ΕΛΕΝΗ",
    birth_date=date(1990, 1, 1),
    afm="111222333",
)


def row(fund):
    return InsuranceRow(
        branch_no=0,
        kad="5510",
        full_schedule=True,
        all_working_days=True,
        sundays=0,
        specialty_code="411100",
        special_case="09" if fund == "efka" else "00",
        kpk="0101",
        period_month=2,
        period_year=2026,
        date_from=date(2026, 2, 1),
        date_to=date(2026, 2, 28),
        earnings_type="001",
        insurance_days=25,
        daily_wage=0.0,
        gross=1288.00,
        contrib_employee=133.19,
        contrib_employer=241.99,
        aux_fund_code="01" if fund == "efka" else "",
        weekly_system=5,
    )


def build(fund):
    b = APDFileBuilder(fund, EMP, 2, 2026, submission_date=date(2026, 3, 25))
    b.add_insured(INS, [row(fund)])
    return b.render().split("\r\n")


def test_formatters():
    assert amount(2340.30, 10) == "0000234030"  # παράδειγμα προδιαγραφών
    assert dt(date(2002, 1, 8)) == "08012002"  # παράδειγμα προδιαγραφών
    assert pct(6.67) == "006670"
    assert alpha("Πέτρος", 30) == "ΠΕΤΡΟΣ".ljust(30)  # κεφαλαία χωρίς τόνους
    print("OK formatters")


def test_record_lengths():
    for fund, r3len in (("efka", 162), ("teka", 148)):
        lines = build(fund)
        assert len(lines[0]) == 363, (fund, len(lines[0]))
        assert len(lines[1]) == 178
        assert len(lines[2]) == r3len, (fund, len(lines[2]))
        assert lines[3] == "EOF"
        assert lines[4] == ""  # καταληκτικό CRLF
    print("OK μήκη εγγραφών (ΕΦΚΑ 363/178/162, ΤΕΚΑ 363/178/148)")


def test_field_positions():
    lines = build("efka")
    r1, r2, r3 = lines[0], lines[1], lines[2]
    # Τύπος 1: τύπος εγγραφής/δήλωσης, περίοδος, ημ. υποβολής
    assert r1[0] == "1" and r1[1:3] == "01"
    assert r1[317:319] == "02" and r1[319:323] == "2026"  # μήνας/έτος αναφοράς
    assert r1[355:363] == "25032026"  # ημ/νία υποβολής
    # Τύπος 2: ΑΜΑ θέσεις 2-10, ΑΜΚΑ 11-21 (παράδειγμα προδιαγραφών)
    assert r2[0] == "2"
    assert r2[1:10] == "123456789" and r2[10:21] == "01019012345"
    # Τύπος 3 ΕΦΚΑ: ειδική περίπτωση 09, ΚΠΚ, τύπος αποδοχών, ημέρες,
    # αποδοχές, φορέας επικουρικής 01 (ΤΕΚΑ), σύστημα 5ήμερο
    assert r3[0] == "3"
    assert r3[18:20] == "09"  # ειδική περίπτωση ασφάλισης
    assert r3[20:24] == "0101"  # ΚΠΚ
    assert r3[46:49] == "001"  # τύπος αποδοχών
    assert r3[49:52] == "025"  # ημέρες ασφάλισης
    assert r3[62:72] == "0000128800"  # αποδοχές 1288,00
    assert r3[144:154] == "0000000000"  # γραμμάτιο προείσπραξης
    assert r3[154:156] == "01"  # φορέας επικουρικής = ΤΕΚΑ
    assert r3[156:158] == "  "  # φορέας ασθένειας: κενό (e-ΕΦΚΑ)
    assert r3[159] == "5"  # πενθήμερο
    # Τύπος 3 ΤΕΚΑ: ίδια δομή χωρίς τα 3 πεδία — μετά τις επιδοτήσεις
    t3 = build("teka")[2]
    assert t3[144] == "0" and t3[145] == "5" and t3[146:148] == "00"
    print("OK θέσεις πεδίων")


def test_totals():
    r1 = build("efka")[0]
    assert r1[323:331] == "00000025"  # σύνολο ημερών
    assert r1[331:343] == "000000128800"  # σύνολο αποδοχών
    assert r1[343:355] == "000000037518"  # σύνολο καταβλητέων 375,18
    print("OK σύνολα εγγραφής τύπου 1")


def test_one_type2_per_insured():
    """ΜΙΑ εγγραφή τύπου 2 ανά ασφαλισμένο και ΟΛΕΣ οι τύπου 3 του από κάτω —
    ακόμη κι όταν ο καλών προσθέτει μία γραμμή τη φορά (τακτικές, Δώρο,
    δεύτερη κάλυψη)."""
    import dataclasses

    gift = dataclasses.replace(row("efka"), earnings_type="003", gross=500.0)
    other = InsuredInfo(
        ama="223344556", amka="04049112345", last_name="ΠΑΠΠΑΣ",
        first_name="ΚΩΝΣΤΑΝΤΙΝΟΣ", father_name="ΝΙΚΟΛΑΟΣ", mother_name="ΣΟΦΙΑ",
        birth_date=date(1991, 4, 4), afm="444555666",
    )
    b = APDFileBuilder("efka", EMP, 2, 2026, submission_date=date(2026, 3, 25))
    b.add_insured(INS, [row("efka")])       # ίδιος ασφαλισμένος, 1η γραμμή
    b.add_insured(other, [row("efka")])     # άλλος ασφαλισμένος
    b.add_insured(INS, [gift])              # ίδιος ασφαλισμένος, 2η γραμμή
    lines = [ln for ln in b.render().split("\r\n") if ln and ln != "EOF"]
    types = "".join(ln[0] for ln in lines)
    # header, ΓΕΩΡΓΙΟΥ (2 γραμμές τύπου 3), ΠΑΠΠΑΣ (1 γραμμή)
    assert types == "123323", types
    assert lines[1][1:10] == INS.ama and lines[4][1:10] == other.ama
    # Καμία επανάληψη εγγραφής τύπου 2 για τον ίδιο ΑΜΑ
    amas = [ln[1:10] for ln in lines if ln[0] == "2"]
    assert len(amas) == len(set(amas)) == 2, amas
    # Τα σύνολα της τύπου 1 περιλαμβάνουν ΟΛΕΣ τις γραμμές
    assert lines[0][331:343] == "000000307600"  # 1288 + 500 + 1288
    print("OK μία εγγραφή τύπου 2 ανά ασφαλισμένο, οι τύπου 3 από κάτω")


def test_second_coverage_not_double_counted():
    """ΔΕΥΤΕΡΗ ΚΑΛΥΨΗ ΣΤΟ ΙΔΙΟ ΑΡΧΕΙΟ (π.χ. επικουρική ΕΦΚΑ δίπλα στην κύρια):
    γράφει τις ΙΔΙΕΣ αποδοχές/ημέρες με δικό της ΚΠΚ και δικές της εισφορές.

    Στην κεφαλίδα οι αποδοχές (No 17) και οι ημέρες (No 16) πρέπει να μετρούν
    ΜΙΑ φορά — αλλιώς η ΑΠΔ δηλώνει διπλάσιες αποδοχές από τις πραγματικές.
    Οι καταβλητέες εισφορές (No 18) αθροίζονται ΟΛΕΣ."""
    import dataclasses

    main = row("efka")  # ΚΠΚ 0101, 25 ημέρες, 1288,00, καταβλητέες 375,18
    aux = dataclasses.replace(
        main,
        kpk="0026",
        contrib_employee=13.53,
        contrib_employer=6.44,
        repeats_earnings=True,  # ΙΔΙΕΣ αποδοχές με την κύρια
    )
    b = APDFileBuilder("efka", EMP, 2, 2026, submission_date=date(2026, 3, 25))
    b.add_insured(INS, [main, aux])
    lines = [ln for ln in b.render().split("\r\n") if ln and ln != "EOF"]

    # Και οι δύο εγγραφές τύπου 3 υπάρχουν, με τις ΙΔΙΕΣ αποδοχές
    r3 = [ln for ln in lines if ln[0] == "3"]
    assert len(r3) == 2, len(r3)
    assert r3[0][62:72] == r3[1][62:72] == "0000128800"
    assert r3[0][20:24] == "0101" and r3[1][20:24] == "0026"  # No 37 ΚΠΚ

    hdr = lines[0]
    assert hdr[323:331] == "00000025", hdr[323:331]      # No 16: 25, ΟΧΙ 50
    assert hdr[331:343] == "000000128800", hdr[331:343]  # No 17: 1288, ΟΧΙ 2576
    # No 18: 375,18 (κύρια) + 19,97 (επικουρική) = 395,15 — ΑΘΡΟΙΣΜΑ ΟΛΩΝ
    assert hdr[343:355] == "000000039515", hdr[343:355]
    print("OK δεύτερη κάλυψη: αποδοχές/ημέρες μία φορά, εισφορές αθροιστικά")


def test_no36_special_case():
    """No 36 «Ειδική περίπτωση ασφάλισης» της ΚΥΡΙΑΣ εγγραφής δηλώνει ΠΟΥ
    ασφαλίζεται ΕΠΙΚΟΥΡΙΚΑ ο εργαζόμενος:
        09 = ξεχωριστός φορέας (ΤΕΚΑ — δική του ΑΠΔ, + No 54 = 01)
        10 = επικουρική ΜΕΣΑ στον e-ΕΦΚΑ (2η εγγραφή τύπου 3 στο ίδιο αρχείο)
        00 = καμία επικουρική
    Ο κανόνας ζει στο δηλωτικό `config.APD_SPECIAL_CASE_RULES`."""
    import dataclasses

    from misevent.rules import config

    assert config.insurance_special_case([]) == "00"
    assert config.insurance_special_case([
        {"fund": "efka_welfare"},
    ]) == "10"
    assert config.insurance_special_case([
        {"fund": "efka_welfare"}, {"fund": "teka"},
    ]) == "09"  # ρητή προτεραιότητα ΤΕΚΑ όταν συνυπάρχουν

    # ...και γράφεται στη σωστή θέση της εγγραφής τύπου 3 (bytes 18-20)
    for case, aux in (("09", "01"), ("10", ""), ("00", "")):
        r = dataclasses.replace(row("efka"), special_case=case, aux_fund_code=aux)
        b = APDFileBuilder("efka", EMP, 2, 2026, submission_date=date(2026, 3, 25))
        b.add_insured(INS, [r])
        ln = [x for x in b.render().split("\r\n") if x[:1] == "3"][0]
        assert ln[18:20] == case, (case, ln[18:20])
        assert ln[154:156] == (aux or "  "), (case, ln[154:156])
    print("OK No 36: 09 ΤΕΚΑ / 10 επικουρική ΕΦΚΑ / 00 καμία")
