from datetime import date
from pathlib import Path
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


def test_digital_card_fields_persist_and_map_to_web_e3n():
    service, projection, person_id = _service()
    employment_id = service.start_employment(
        person_id, date(2026, 1, 1), monthly_gross=1500,
        weekly_pattern=WEEK, working_card="1", flexible_arrival_minutes="30",
        break_within_schedule="1", break_minutes="20",
    )
    employment = projection.employments[employment_id]
    assert employment.working_card == "1"
    assert employment.flexible_arrival_minutes == "30"
    assert employment.break_within_schedule == "1"
    assert employment.break_minutes == "20"


    profile = SimpleNamespace(
        branch_no=0, kad="1234", specialty_code="123456", kpk="0101",
        auxiliary_scheme="efka", specialty_name="ΠΩΛΗΤΗΣ",
    )
    values = ergani.build_web_e3n(
        SimpleNamespace(ame="1", afm="2", legal_name="ΔΟΚΙΜΗ"),
        employment, profile, WEEK,
    )["AnaggeliesE3N"]["AnaggeliaE3N"][0]
    assert values["f_working_card"] == "1"
    assert values["f_euelikto_wrario_minutes"] == "30"
    assert values["f_dialeimma_entos_wrariou"] == "1"
    assert values["f_dialeimma_minutes"] == "20"


def test_digital_card_constraints_follow_web_e3n_patterns():
    assert constraints.check_digital_card_fields("1", "030", "0", "000") == (
        "1", "30", "0", "0"
    )
    with pytest.raises(ValueError, match="κάρτα εργασίας"):
        constraints.check_digital_card_fields("2")
    with pytest.raises(ValueError, match="0 έως 999"):
        constraints.check_digital_card_fields("0", "1000")
    with pytest.raises(ValueError, match="εντός ωραρίου"):
        constraints.check_digital_card_fields("0", "0", "2")


def test_schedule_tab_contains_digital_card_section_and_oaed_is_last():
    app = create_app()
    with app.test_request_context("/"):
        html = render_template("partials/_schedule_tab.html", fv={})
    assert "Ψηφιακή κάρτα" in html
    for field in (
        "f_working_card", "f_euelikto_wrario_minutes",
        "f_dialeimma_entos_wrariou", "f_dialeimma_minutes",
    ):
        assert f'name="{field}"' in html
    assert '<option value="1" selected>1 — ΝΑΙ</option>' in html
    assert 'name="f_euelikto_wrario_minutes"' in html
    assert 'value="120"' in html
    assert 'name="f_dialeimma_minutes"' in html
    assert 'value="15"' in html

    templates = Path("webapp/templates/partials")
    for filename in (
        "_hire_form.html", "_person_employ_modal.html",
        "_emp_hire_details_modal.html",
    ):
        source = (templates / filename).read_text(encoding="utf-8")
        labels = source[source.index('<div class="tabbar">'):]
        labels = labels[:labels.index("</div>")]
        assert labels.rfind('for="tab-oaed"') > labels.rfind('for="tab-schedule"')
        assert labels.rfind('for="tab-oaed"') > labels.rfind('for="tab-json"')
