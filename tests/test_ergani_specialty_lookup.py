from datetime import date
from types import SimpleNamespace

import pytest
from _bootstrap import ui_client

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.integrations import ergani, ergani_parameters
from misevent.persistence.database import Database
from webapp import create_app


def _registry_row():
    rows = ergani_parameters.search_specialties("πωλη")
    assert rows
    return rows[0]


def test_specialty_search_is_accent_insensitive_and_limited():
    rows = ergani_parameters.search_specialties("πωλη", limit=5)
    assert 0 < len(rows) <= 5
    assert all({"code", "description"} <= row.keys() for row in rows)
    assert ergani_parameters.search_specialties("") == ()


def test_htmx_lookup_returns_results_and_picks_both_fields():
    client = ui_client(create_app())
    row = _registry_row()
    results = client.get(
        "/company/specialties/ergani/search",
        query_string={"ergani_specialty_description": "πωλη"},
    ).get_data(as_text=True)
    assert row["code"] in results

    by_code = client.get(
        "/company/specialties/ergani/search",
        query_string={"ergani_specialty_code": row["code"]},
    ).get_data(as_text=True)
    assert row["code"] in by_code
    assert row["description"] in by_code

    picked = client.get(
        "/company/specialties/ergani/pick", query_string={"code": row["code"]}
    ).get_data(as_text=True)
    assert 'name="ergani_specialty_code"' in picked
    assert 'name="ergani_specialty_description"' in picked
    assert picked.count('hx-swap-oob="outerHTML"') == 2
    assert row["description"] in picked
    assert picked.count("company.ergani_specialty_search") == 0
    assert picked.count("/company/specialties/ergani/search") == 2


def test_company_specialty_persists_verified_ergani_pair():
    row = _registry_row()
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    service = PayrollService(store, proj)
    service.register_company("1234567890", "999888771", "ΔΟΚΙΜΗ ΑΕ")
    service.define_branch(0)
    service.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
    service.define_kpk_rates(
        "efka_main", "0101", date(2026, 1, 1), .1, .2,
        "main_pension=7/17|healthcare=0/0|auxiliary=3/3",
    )
    coverages = {
        "MAIN_EPIK": {"kad": "0141", "specialty_code": "411100", "kpk": "0101"},
    }
    service.define_specialty(
        "ΠΩΛΗΤΗΣ", coverages, row["code"], row["description"]
    )
    specialty = proj.specialties["ΠΩΛΗΤΗΣ"]
    assert specialty.ergani_specialty_code == row["code"]
    assert specialty.ergani_specialty_description == row["description"]

    person_id = service.register_person(
        last_name="ΔΟΚΙΜΗ", first_name="ΜΑΡΙΑ", father_name="ΙΩΑΝΝΗΣ",
        mother_name="ΕΛΕΝΗ", birth_date=date(1990, 5, 5),
        afm="111222336", amka="05059012343", ama="123456789",
    )
    employment_id = service.start_employment(
        person_id, date(2026, 1, 1), monthly_gross=1500,
        weekly_pattern={
            day: "09:00-17:00" for day in ("MON", "TUE", "WED", "THU", "FRI")
        },
    )
    service.assign_specialty(employment_id, "ΠΩΛΗΤΗΣ")
    profile = proj.employments[employment_id].insurance_profile
    assert profile.ergani_specialty_code == row["code"]
    assert profile.ergani_specialty_description == row["description"]

    with pytest.raises(ValueError, match="δεν αντιστοιχεί"):
        service.define_specialty("ΑΛΛΗ", coverages, row["code"], "ΛΑΘΟΣ")


def test_web_e3n_prefers_the_separate_ergani_specialty_fields():
    company = SimpleNamespace(ame="1", afm="2", legal_name="ACME")
    emp = SimpleNamespace(
        last_name="Α", first_name="Β", father_name="Γ", mother_name="Δ",
        birth_date=None, afm="", ama="", amka="", children=0,
        hire_date=date(2026, 1, 1), weekly_hours=40, weekly_system=5,
        service_years=0, married=False, extra_allowances_pct=0,
        pay_type="monthly", monthly_gross=1000, daily_wage=0,
        schedule_mode="fixed",
    )
    profile = SimpleNamespace(
        branch_no=0, kad="1234", specialty_code="654321",
        specialty_name="ΕΣΩΤΕΡΙΚΗ ΘΕΣΗ", kpk="0101", auxiliary_scheme="efka",
        ergani_specialty_code="1233",
        ergani_specialty_description="ΔΙΕΥΘΥΝΤΕΣ ΠΩΛΗΣΕΩΝ",
    )
    form = ergani.build_web_e3n(company, emp, profile, {})[
        "AnaggeliesE3N"
    ]["AnaggeliaE3N"][0]
    assert form["f_eidikothta"] == "1233"
    assert form["f_eidikothta_anal"] == "ΔΙΕΥΘΥΝΤΕΣ ΠΩΛΗΣΕΩΝ"
