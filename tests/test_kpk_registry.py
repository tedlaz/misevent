"""Ο.Σ.Υ.Κ. του e-ΕΦΚΑ (ΚΑΔ – Ειδικότητες – ΚΠΚ, osyk_29.zip) στις ειδικότητες."""

import re
from datetime import date

import pytest

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.integrations import kpk_registry
from misevent.persistence.database import Database


def test_registry_reads_the_zip():
    assert kpk_registry.available()
    assert kpk_registry.kad_description("0110")
    assert kpk_registry.eid_description("122100")
    codes = {r["code"] for r in kpk_registry.specialties_for("0110")}
    assert "122100" in codes
    # Τα ΚΠΚ του αρχείου (3ψήφια) έρχονται 4ψήφια, όπως στο Μητρώο ΚΠΚ· ένας
    # συνδυασμός έχει ΠΟΛΛΑ (βασικό πρώτο + προγράμματα μειώσεων).
    kpks = kpk_registry.combination_kpks("0110", "122100")
    assert kpks[0] == "0101" and len(kpks) > 1
    assert kpk_registry.combination_kpks("0110", "000000") == []
    # Πρόνοια ΤΑΠΙΤ.
    assert kpk_registry.combination_kpks("0013", "000108") == ["0026"]
    assert kpk_registry.combination_kpks("0013", "000106") == ["0024"]
    assert kpk_registry.search_kads("0110")[0]["code"] == "0110"


def test_expired_combination_counts_only_inside_its_period():
    # Ένας συνδυασμός μπορεί να αλλάξει ΚΠΚ με τον χρόνο (βλ. readme.txt του
    # zip): στην περίοδό της ισχύει η παλιά γραμμή, μετά η νεότερη ή καμία.
    kad, code, kpk, start, end = next(
        r for r in kpk_registry._lines("dn_kadeidkpk.txt") if r[4] != "999912"
    )
    inside = date(int(start[:4]), int(start[4:]), 1)
    assert kpk.zfill(4) in kpk_registry.combination_kpks(kad, code, on=inside)
    assert kpk.zfill(4) not in kpk_registry.combination_kpks(
        kad, code, on=date(2099, 1, 1))


def _service():
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
    svc.define_kpk_rates(
        "efka_main", "0101", date(2026, 1, 1), .1, .2,
        "main_pension=7/17|healthcare=0/0|auxiliary=3/3",
    )
    return svc, proj


def test_main_coverage_requires_a_valid_combination():
    svc, proj = _service()
    with pytest.raises(ValueError, match="δεν ισχύει στο μητρώο"):
        svc.define_specialty(
            "ΛΑΘΟΣ", {"MAIN_EPIK": {"kad": "0110", "specialty_code": "411100",
                                    "kpk": "0101"}})
    svc.define_specialty(
        "ΣΩΣΤΟ", {"MAIN_EPIK": {"kad": "0110", "specialty_code": "122100",
                                "kpk": "0101"}})
    assert "ΣΩΣΤΟ" in proj.specialties





def _get(client, path, trigger, **params):
    html = client.get(
        path, query_string=params,
        headers={"HX-Request": "true", "HX-Trigger-Name": trigger},
    ).get_data(as_text=True)
    return " ".join(html.split())


def _selected(html, name):
    """Η επιλεγμένη τιμή του <select name=...> (ή "")."""
    block = html.split(f'name="{name}"')[1].split("</select>")[0]
    m = re.search(r'<option value="(\w*)" selected>', block)
    return m.group(1) if m else ""


def _special(html, carrier_title):
    """Η ειδική περίπτωση που δείχνει η κάρτα ενός φορέα επικουρικής."""
    card = html.split(f"Επικουρική {carrier_title}</b>")[1]
    return re.search(r'Ειδική περίπτωση \(No 36\) <span><b class="mono">(\d+)</b>', card).group(1)


def test_specialty_form_derives_both_alternatives(fresh_system):
    from _bootstrap import ui_client

    from webapp import create_app
    from webapp.system import lock, proj, svc

    client = ui_client(create_app())
    with lock:
        svc.confirm_default_parameters()  # ΚΠΚ 0101, 0103, 4169, 0024, 0026

    modal = client.get("/company/specialties/modal").get_data(as_text=True)
    assert 'id="spec-main"' in modal and 'name="main_kad"' in modal
    assert 'name="pron_kad"' in modal and '<option value="0110">' in modal

    # ΕΝΑ ΚΑΔ + ΜΙΑ ειδικότητα κύριας -> οι δύο εναλλακτικές από τον Ο.Σ.Υ.Κ.
    # 5242/532030: βασικό 103 (χωρίς επικουρική).
    main = dict(main_kad="5242", main_code="532030", epik_carriers=["efka", "teka"])
    html = _get(client, "/company/specialties/main", "main_code", **main)
    assert '<option value="532030">' in html  # η λίστα ειδικοτήτων του ΚΑΔ
    assert _special(html, "ΕΦΚΑ") == "10" and _selected(html, "MAIN_EPIK_kpk") == "0101"
    assert _special(html, "ΤΕΚΑ") == "09" and _selected(html, "MAIN_kpk") == "0103"
    assert "Γραμμή ΤΕΚΑ" in html

    # 0110/122100: βασικό 101 (έχει ήδη επικουρική) -> 00· με πρόνοια -> 10.
    other = dict(main, main_kad="0110", main_code="122100")
    html = _get(client, "/company/specialties/main", "main_code", **other)
    assert _special(html, "ΕΦΚΑ") == "00" and _selected(html, "MAIN_EPIK_kpk") == "0101"
    html = _get(client, "/company/specialties/main", "pron_enabled", **other,
                pron_enabled="on")
    assert _special(html, "ΕΦΚΑ") == "10"

    html = _get(client, "/company/specialties/main", "main_kad", **dict(main, main_kad="0110"))
    assert "Η ειδικότητα δεν ισχύει για αυτό το ΚΑΔ" in html

    # Πρόνοια: ΕΝΑ ΚΑΔ, ΕΙΔ + ΚΠΚ ανά νέους/παλαιούς.
    html = _get(client, "/company/specialties/pron", "pron_kad", pron_kad="0013",
                PRON_NEW_code="000106", PRON_OLD_code="000108")
    assert _selected(html, "PRON_NEW_kpk") == "0024"
    assert _selected(html, "PRON_OLD_kpk") == "0026"

    # Αποθήκευση: τα κοινά ΚΑΔ/ΕΙΔ πάνε σε κάθε ρόλο, το ΤΕΚΑ είναι σταθερό.
    r = client.post("/company/specialties", data={
        "name": "ΠΩΛΗΤΗΣ", **main, "MAIN_EPIK_kpk": "0101", "MAIN_kpk": "0103",
        "pron_enabled": "on", "pron_name": "ΤΑΠΙΤ", "pron_differentiated": "on",
        "pron_kad": "0013", "PRON_NEW_code": "000106", "PRON_NEW_kpk": "0024",
        "PRON_OLD_code": "000108", "PRON_OLD_kpk": "0026",
    }, headers={"HX-Request": "true"})
    assert r.status_code == 200, r.get_data(as_text=True)
    sp = proj.specialties["ΠΩΛΗΤΗΣ"]
    got = {role: (p["kad"], p["specialty_code"], p["kpk"], p.get("special_case"))
           for role in config_roles() if (p := sp.package(role))}
    assert got == {
        "MAIN_EPIK": ("5242", "532030", "0101", "10"),
        "MAIN": ("5242", "532030", "0103", "09"),
        "TEKA": ("0083", "000860", "4169", None),
        "PRON_NEW": ("0013", "000106", "0024", None),
        "PRON_OLD": ("0013", "000108", "0026", None),
    }


def config_roles():
    from misevent.rules import config

    return config.SPECIALTY_ROLES


def test_special_case_kpk():
    assert kpk_registry.special_case_kpk("00", "0101") == "0101"
    assert kpk_registry.special_case_kpk("09", "0101") == "0103"
    assert kpk_registry.special_case_kpk("10", "0103") == "0101"
    assert kpk_registry.suggested_kpks("0110", "122100", "09")[0] == "0103"


def test_teka_package_is_fixed():
    svc, proj = _service()
    svc.define_kpk("efka", "0103", "ΙΚΑ ΚΥΡΙΑ", "main")
    svc.define_kpk_rates("efka_main", "0103", date(2026, 1, 1), .07, .13,
                         "main_pension=7/13")
    svc.define_kpk("teka", "4169", "ΤΕΚΑ Επικουρική", "auxiliary")
    svc.define_kpk_rates("teka", "4169", date(2022, 6, 1), .03, .03,
                         "auxiliary=3/3")
    # Χωρίς πακέτο ΤΕΚΑ (ή με άλλους κωδικούς): μπαίνει το σταθερό.
    svc.define_specialty(
        "ΣΕΡΒΙΤΟΡΟΣ",
        {"MAIN": {"kad": "0110", "specialty_code": "122100", "kpk": "0103"},
         "TEKA": {"kad": "1111", "specialty_code": "222222", "kpk": "4169"}},
        epik_carriers=["teka"],
    )
    teka = proj.specialties["ΣΕΡΒΙΤΟΡΟΣ"].package("TEKA")
    assert (teka["kad"], teka["specialty_code"], teka["kpk"]) == ("0083", "000860", "4169")


def test_welfare_coverage_requires_a_valid_combination():
    svc, proj = _service()
    svc.define_kpk("efka", "0026", "ΤΑΠΙΤ ΠΑΛΑΙΟΙ", "welfare")
    svc.define_kpk_rates("efka_welfare", "0026", date(2026, 1, 1), .04, .01,
                         "welfare=4/1")
    main = {"kad": "0110", "specialty_code": "122100", "kpk": "0101"}
    pron = {"enabled": True, "name": "ΤΑΠΙΤ", "differentiated": False}
    with pytest.raises(ValueError, match="δεν ισχύει στο μητρώο"):
        svc.define_specialty("ΛΑΘΟΣ", {
            "MAIN_EPIK": main,
            "PRON_UNIFORM": {"kad": "0013", "specialty_code": "999999", "kpk": "0026"},
        }, pron=pron)
    svc.define_specialty("ΣΩΣΤΟ", {
        "MAIN_EPIK": main,
        "PRON_UNIFORM": {"kad": "0013", "specialty_code": "000108", "kpk": "0026"},
    }, pron=pron)
    assert "ΣΩΣΤΟ" in proj.specialties


def test_apd_special_case_follows_the_auxiliary_carrier():
    """5242/532030 (βασικό 103): ΕΦΚΑ -> 10 με 0101, ΤΕΚΑ -> 09 με 0103 + ΤΕΚΑ."""
    from test_insurance_packages import WEEK, service

    _store, _proj, svc = service()
    combo = {"kad": "5242", "specialty_code": "532030"}
    svc.define_specialty("ΠΩΛΗΤΡΙΑ", {"MAIN_EPIK": {**combo, "kpk": "0101"},
                                      "MAIN": {**combo, "kpk": "0103"}},
                         epik_carriers=["efka", "teka"])
    people = (("111222336", "05059012343"), ("222333440", "03039012343"))
    for i, ((afm, amka), carrier) in enumerate(zip(people, ("efka", "teka"))):
        pid = svc.register_person(
            last_name=f"ΔΟΚΙΜΗ{i}", first_name="ΝΙΚΟΣ", father_name="Γ",
            mother_name="Μ", birth_date=date(1990, 5, 5), afm=afm, amka=amka,
            ama=f"12345678{i}")
        eid = svc.start_employment(pid, date(2026, 1, 1), pay_type="monthly",
                                   monthly_gross=1500, weekly_hours=40,
                                   weekly_pattern=WEEK)
        svc.assign_specialty(eid, "ΠΩΛΗΤΡΙΑ", auxiliary_scheme=carrier)
        svc.submit_hire_e3(eid)
        svc.submit_hire_wto(eid)
    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)
    apd = svc.build_apd_rows(2026, 1)
    efka = sorted((r.kpk, r.special_case) for _i, r in apd["efka"] if r.earnings_type == "001")
    assert efka == [("0101", "10"), ("0103", "09")]
    assert [r.kpk for _i, r in apd["teka"] if r.earnings_type == "001"] == ["4169"]


def test_main_special_case():
    assert kpk_registry.main_special_case("teka", "5242", "532030") == "09"
    assert kpk_registry.main_special_case("efka", "5242", "532030") == "10"
    assert kpk_registry.main_special_case("efka", "0110", "122100") == "00"
    assert kpk_registry.main_special_case("efka", "0110", "122100", welfare=True) == "10"
