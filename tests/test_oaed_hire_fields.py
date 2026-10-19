from datetime import date
from types import SimpleNamespace

import pytest
from flask import render_template

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.integrations import ergani
from misevent.persistence.database import Database
from misevent.rules import constraints
from webapp import create_app

WEEK = {day: "09:00-17:00" for day in ("MON", "TUE", "WED", "THU", "FRI")}


def _service():
    store = Database().store("test")
    projection = Projections()
    store.subscribe(projection.handle)
    service = PayrollService(store, projection)
    person_id = service.register_person(
        last_name="ΔΟΚΙΜΗ", first_name="ΜΑΡΙΑ", father_name="ΙΩΑΝΝΗΣ",
        mother_name="ΕΛΕΝΗ", birth_date=date(1990, 5, 5),
        afm="111222336", amka="05059012343",
    )
    return service, projection, person_id


def test_oaed_and_trial_fields_persist_and_map_to_web_e3n():
    service, projection, person_id = _service()
    employment_id = service.start_employment(
        person_id, date(2026, 1, 1), monthly_gross=1500,
        weekly_pattern=WEEK, oaed_placement="1", oaed_program="12-345",
        oaed_replacement="1", oaed_replaced_afm="123456789",
        oaed_replaced_amka="01019012345", trial_period="1",
        trial_date_to=date(2026, 3, 31),
    )
    employment = projection.employments[employment_id]
    assert employment.oaed_program == "12-345"
    assert employment.oaed_replaced_afm == "123456789"
    assert employment.trial_date_to == date(2026, 3, 31)


    company = SimpleNamespace(ame="1", afm="2", legal_name="ΔΟΚΙΜΗ")
    profile = SimpleNamespace(
        branch_no=0, kad="1234", specialty_code="123456", kpk="0101",
        auxiliary_scheme="efka", specialty_name="ΠΩΛΗΤΗΣ",
        ergani_specialty_code="4321", ergani_specialty_description="ΠΩΛΗΤΕΣ",
    )
    values = ergani.build_web_e3n(company, employment, profile, WEEK)[
        "AnaggeliesE3N"
    ]["AnaggeliaE3N"][0]
    assert values["f_topothetisioaed"] == "1"
    assert values["f_programaoaed"] == "12-345"
    assert values["f_replaceprograma"] == "1"
    assert values["f_replaceprograma_afm"] == "123456789"
    assert values["f_replaceprograma_amka"] == "01019012345"
    assert values["f_trial_period"] == "1"
    assert values["f_trial_date_to"] == "31/03/2026"


def test_hidden_oaed_values_are_cleared_and_dependencies_are_validated():
    assert constraints.check_oaed_hire_fields(
        "0", "INVALID", "1", "123456789", "01019012345", "0",
        date(2026, 3, 31), date(2026, 1, 1),
    ) == ("0", "", "", "", "", "0", None)

    with pytest.raises(ValueError, match="κωδικός προγράμματος"):
        constraints.check_oaed_hire_fields("1", "ABC")
    with pytest.raises(ValueError, match="ΑΦΜ"):
        constraints.check_oaed_hire_fields("1", "12-3", "1", "123")
    with pytest.raises(ValueError, match="ημερομηνία λήξης"):
        constraints.check_oaed_hire_fields(trial_period="1")
    with pytest.raises(ValueError, match="προηγείται"):
        constraints.check_oaed_hire_fields(
            trial_period="1", trial_date_to=date(2025, 12, 31),
            hire_date=date(2026, 1, 1),
        )


def test_oaed_tab_contains_exact_web_e3n_fields_and_conditional_groups():
    app = create_app()
    with app.test_request_context("/"):
        hidden = render_template("partials/_oaed_tab.html", fv={})
        visible = render_template("partials/_oaed_tab.html", fv={
            "f_topothetisioaed": "1", "f_replaceprograma": "1",
            "f_trial_period": "1", "f_trial_date_to": "2026-03-31",
        })
    for field in (
        "f_topothetisioaed", "f_programaoaed", "f_replaceprograma",
        "f_replaceprograma_afm", "f_replaceprograma_amka",
        "f_trial_period", "f_trial_date_to",
    ):
        assert f'name="{field}"' in hidden
    assert "data-oaed-program-fields hidden" in hidden
    assert "data-trial-date-field hidden" in hidden
    assert "data-oaed-program-fields >" in visible
    assert "data-oaed-replacement-fields\n           " in visible
    assert 'name="f_trial_date_to"' in visible and "required" in visible
