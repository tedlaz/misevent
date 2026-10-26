"""
Οδηγός «Εισαγωγή από ΑΠΔ»: ανάγνωση ΑΠΔ, draft, δοκιμαστικός έλεγχος που
ΔΕΝ αγγίζει το σύστημα, και πλήρης ροή οθονών σε νέα εταιρεία.

⚠️ Ζητά `fresh_system`: ορίζει ΚΠΚ (κοινά) και δημιουργεί εταιρεία.
"""

import dataclasses
from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd
import pytest
from test_apd_files import EMP, INS, row

from misevent.integrations.apd_files import APDFileBuilder, APDFormatError, parse_apd

EMPLOYER = dataclasses.replace(EMP, afm="999888771")
PERSON = dataclasses.replace(INS, afm="111222336", amka="01019012341")
WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}


def apd(fund, aux=""):
    r = row(fund)
    if fund == "efka":
        r = dataclasses.replace(r, kad="0141", kpk="0103" if aux else "0101",
                                aux_fund_code=aux)
    else:
        r = dataclasses.replace(r, kad="0083", specialty_code="000860", kpk="4169")
    b = APDFileBuilder(fund, EMPLOYER, 2, 2026, submission_date=date(2026, 3, 25))
    b.add_insured(PERSON, [r])
    return b, r


def test_parse_round_trip():
    for fund in ("efka", "teka"):
        b, r = apd(fund)
        for data in (b.encoded(), b.zip_bytes()):
            a = parse_apd(data)
            assert (a.fund, a.employer, a.year, a.month) == (fund, EMPLOYER, 2026, 2)
            assert a.insured == [(PERSON, [r])]


def test_parse_rejects_broken_files():
    b, _ = apd("efka")
    good = b.encoded()
    with pytest.raises(APDFormatError, match="EOF"):
        parse_apd(good.replace(b"EOF", b""))
    with pytest.raises(APDFormatError, match="μήκους"):
        parse_apd(good.replace(b"\r\n2", b"\r\n2X", 1))
    with pytest.raises(APDFormatError, match="Καταβλητέων"):
        lines = good.split(b"\r\n")
        lines[0] = lines[0][:343] + b"000000000001" + lines[0][355:]
        parse_apd(b"\r\n".join(lines))
    with pytest.raises(APDFormatError, match="τύπο 1|τύπου 1"):
        parse_apd(b"hello")


def _kpk(svc):
    svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
    svc.define_kpk_rates("efka_main", "0101", date(2025, 1, 1), 0.1337, 0.2179,
                         "main_pension=6,67/13,33|healthcare=2,55/4,55|auxiliary=3/3|dypa=1,15/0,91")
    svc.define_kpk("efka", "0103", "ΙΚΑ ΜΙΚΤΑ (κύρια)", "main")
    svc.define_kpk_rates("efka_main", "0103", date(2025, 1, 1), 0.1037, 0.1879,
                         "main_pension=6,67/13,33|healthcare=2,55/4,55|dypa=1,15/0,91")
    svc.define_kpk("teka", "4169", "ΤΕΚΑ Επικουρική", "auxiliary")
    svc.define_kpk_rates("teka", "4169", date(2022, 6, 1), 0.03, 0.03, "auxiliary=3/3")


def test_draft_and_dry_run(fresh_system):
    from misevent.app import apd_import as core
    from webapp.system import registry, store, svc

    _kpk(svc)
    efka = parse_apd(apd("efka", aux="01")[0].encoded())
    assert core.check_teka(efka, None) == [PERSON.amka]  # ζητά ΑΠΔ ΤΕΚΑ
    assert core.check_teka(efka, parse_apd(apd("teka")[0].encoded()))

    draft = core.draft_from_apd(efka, svc)
    sp, emp = draft["specialties"][0], draft["employees"][0]
    assert set(sp["packages"]) == {"MAIN", "TEKA"}
    assert emp["auxiliary_scheme"] == "teka"
    assert emp["hire"]["payroll_from"] == "2026-03-01"

    before = store.event_count()
    errors = core.dry_run(registry.shared, store, draft, date(2026, 3, 10))
    assert {e[0] for e in errors} == {"company", "employees"}  # ονομασία, φύλο
    assert any("κωδικός ειδικότητας ΕΡΓΑΝΗ" in e[2] for e in errors)
    assert any("δραστηριότητα" in e[2] for e in errors)  # υποχρεωτική
    assert store.event_count() == before  # ΤΙΠΟΤΑ στο πραγματικό σύστημα

    draft["company"]["activity"] = "ΕΜΠΟΡΙΟ"
    sp.update(name="ΥΠΑΛΛΗΛΟΣ", ergani_specialty_code="419",
              ergani_specialty_description="ΛΟΙΠΟΙ ΥΠΑΛΛΗΛΟΙ ΓΡΑΦΕΙΟΥ")
    emp["hire"]["hire_date"] = "2020-05-15"
    emp["schedule"]["days"] = WEEK
    errors = core.dry_run(registry.shared, store, draft, date(2026, 3, 10))
    assert [e[2] for e in errors] == ["Λείπει: φύλο"]  # υποχρεωτικό
    emp["person"].update(sex="1", identity_type="ΔAT", identity_number="ΑΚ123456")
    assert core.dry_run(registry.shared, store, draft, date(2026, 3, 10)) == []
    assert store.event_count() == before


def test_wizard_creates_company_and_employees(fresh_system):
    from webapp import create_app
    from webapp.system import registry, svc

    _kpk(svc)
    app = create_app()
    c = _bootstrap.ui_client(app)
    companies = len(registry.auth.companies)

    def post(url, data):
        r = c.post(url, data=data, content_type="multipart/form-data")
        assert r.status_code == 302, r.get_data(as_text=True)[:2000]
        return r

    import io
    post("/apd-import/", {"efka": (io.BytesIO(apd("efka")[0].encoded()), "CSL01"),
                          "teka": (io.BytesIO(apd("teka")[0].encoded()), "CSL01")})
    page = c.get("/apd-import/company").get_data(as_text=True)
    assert "Νέα εταιρεία" in page and "0141 / 411100" in page
    assert 'class="locked"' in page and "Προς συμπλήρωση" in page
    # τα πεδία της ΑΠΔ είναι κλειδωμένα: ακόμη κι αν σταλούν, αγνοούνται
    post("/apd-import/company", {"c_activity": "ΕΜΠΟΡΙΟ", "c_afm": "000000000",
                                  "c_legal_name": "ΑΛΛΗ", "b_street": "ΑΛΛΗ",
                                  "sp0_name": "ΥΠΑΛΛΗΛΟΣ",
                                  "sp0_ergani_specialty_code": "419"})
    page = c.get("/apd-import/employees").get_data(as_text=True)
    assert "επόμενη περίοδος μετά την ΑΠΔ (03/2026)" in page  # προεπιλογή
    bad = c.post("/apd-import/employees", data={"payroll_mode": "custom"})
    assert "error=" in bad.location  # «Άλλος μήνας» χωρίς μήνα
    post("/apd-import/employees", {
        "payroll_mode": "custom", "payroll_month": "2026-06",
        "e0_hire_date": "2020-05-15", "e0_pay_type": "daily", "e0_amount": "1288",
        "e0_weekly_hours": "40", "e0_weekly_system": "6",
        "e0_employment_relationship": "0", "e0_children": "1",
        "e0_sex": "1", "e0_nationality_code": "048", "e0_identity_type": "ΔAT",
        "e0_identity_number": "ΑΚ123456",
    })
    post("/apd-import/schedule", {**{f"e0_day_{d}": v for d, v in WEEK.items()},
                                  "e0_mode": "fixed"})
    assert "Όλα εντάξει" in c.get("/apd-import/check").get_data(as_text=True)
    assert len(registry.auth.companies) == companies  # ακόμα τίποτα

    assert post("/apd-import/commit", {}).location.endswith("/persons/")
    # νέα εγκατάσταση: ο ΚΕΝΟΣ χώρος γίνεται η εταιρεία της ΑΠΔ
    assert len(registry.auth.companies) == companies
    cid = next(i for i in registry.auth.companies
               if (registry.tenant(i).proj.company or EMP).afm == "999888771")
    proj = registry.tenant(cid).proj
    assert [p.afm for p in proj.persons.values()] == ["111222336"]
    assert (proj.company.afm, proj.company.legal_name) == ("999888771", "ΔΟΚΙΜΗ ΑΕ")
    (emp,) = proj.employments.values()
    assert emp.hire_date == date(2020, 5, 15) and emp.migrated
    assert emp.payroll_start == date(2026, 6, 1)  # ο μήνας του χρήστη, όχι 03/2026
    assert (emp.pay_type, emp.weekly_system) == ("monthly", 5)  # της ΑΠΔ, όχι του POST
