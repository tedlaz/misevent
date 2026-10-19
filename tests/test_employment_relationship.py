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
from webapp.views import employments as employment_views

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


def test_fixed_term_is_required_persisted_and_mapped_to_web_e3n():
    service, projection, person_id = _service()
    with pytest.raises(ValueError, match="απαιτούνται οι ημερομηνίες"):
        service.start_employment(
            person_id, date(2026, 1, 1), monthly_gross=1500,
            weekly_pattern=WEEK, employment_relationship="1",
        )

    employment_id = service.start_employment(
        person_id, date(2026, 1, 1), monthly_gross=1500,
        weekly_pattern=WEEK, employment_relationship="1",
        # Η τιμή «από» του client αγνοείται: πηγή αλήθειας είναι η πρόσληψη.
        fixed_term_from=date(2025, 12, 15), fixed_term_to=date(2026, 6, 30),
    )
    employment = projection.employments[employment_id]
    assert employment.employment_relationship == "1"
    assert employment.fixed_term_from == date(2026, 1, 1)
    assert employment.fixed_term_to == date(2026, 6, 30)


    service.correct_hire_details(
        employment_id, date(2026, 1, 1), monthly_gross=1500,
        weekly_pattern=WEEK, employment_relationship="1",
        fixed_term_from=date(2026, 1, 1), fixed_term_to=date(2026, 12, 31),
    )
    employment = projection.employments[employment_id]
    assert employment.fixed_term_to == date(2026, 12, 31)

    company = SimpleNamespace(ame="1", afm="2", legal_name="ΔΟΚΙΜΗ")
    profile = SimpleNamespace(branch_no=0, auxiliary_scheme="efka")
    form = ergani.build_web_e3n(company, employment, profile, WEEK)[
        "AnaggeliesE3N"
    ]["AnaggeliaE3N"][0]
    assert form["f_sxeshapasxolisis"] == "1"
    assert form["f_orismenou_apo"] == "01/01/2026"
    assert form["f_orismenou_ews"] == "31/12/2026"


def test_indefinite_relationship_clears_dates_and_date_order_is_checked():
    assert constraints.check_employment_relationship(
        "0", date(2026, 1, 1), date(2026, 6, 30)
    ) == ("0", None, None)
    with pytest.raises(ValueError, match="προηγείται"):
        constraints.check_employment_relationship(
            "1", date(2026, 7, 1), date(2026, 6, 30)
        )


def test_relationship_section_only_shows_fixed_term_dates_when_needed():
    app = create_app()
    with app.test_request_context("/"):
        indefinite = render_template(
            "partials/_general_tab.html", fv={}, ident=False, person=None,
            person_note=False, specialties=[],
        )
        fixed = render_template(
            "partials/_general_tab.html",
            fv={"employment_relationship": "1"}, ident=False, person=None,
            person_note=False, specialties=[],
        )
    assert 'name="employment_relationship"' in indefinite
    assert indefinite.index('name="hire_date"') < indefinite.index(
        "Σύμβαση &amp; αποδοχές"
    )
    assert indefinite.count('name="hire_date"') == 1
    assert 'data-fixed-term-fields\n         hidden' in indefinite
    assert 'name="fixed_term_from"' in indefinite and "disabled" in indefinite
    assert 'data-fixed-term-fields\n         >' in fixed
    assert 'name="fixed_term_from"' in fixed and "required" in fixed
    assert "data-fixed-term-from" in fixed and "readonly" in fixed


def test_fixed_term_end_must_follow_payroll_start_on_old_hire():
    """Παλαιά πρόσληψη: σύμβαση που λήγει πριν/κατά την ΑΝΑΛΗΨΗ δεν έχει
    νόημα — το πρόγραμμα δεν θα υπολόγιζε ποτέ μισθοδοσία γι' αυτήν."""
    for end in (date(2025, 12, 31), date(2026, 1, 1)):
        with pytest.raises(ValueError, match="μεταγενέστερη της έναρξης"):
            constraints.check_employment_relationship(
                "1", None, end, hire_date=date(2024, 5, 15),
                payroll_from=date(2026, 1, 1),
            )
    assert constraints.check_employment_relationship(
        "1", None, date(2026, 6, 30), hire_date=date(2024, 5, 15),
        payroll_from=date(2026, 1, 1),
    ) == ("1", date(2024, 5, 15), date(2026, 6, 30))


def test_start_employment_rejects_fixed_term_ending_before_payroll_start():
    service, _projection, person_id = _service()
    with pytest.raises(ValueError, match="μεταγενέστερη της έναρξης"):
        service.start_employment(
            person_id, date(2024, 5, 15), monthly_gross=1500,
            weekly_pattern=WEEK, payroll_from=date(2026, 1, 1),
            employment_relationship="1", fixed_term_to=date(2025, 12, 31),
        )


def test_old_hire_fields_live_in_their_own_box():
    """«Παλαιά πρόσληψη»: δικό της πλαίσιο, και τα πεδία ορισμένου χρόνου
    ΠΑΝΩ από αυτό (αμέσως κάτω από τον επιλογέα της σχέσης)."""
    app = create_app()
    with app.test_request_context("/"):
        html = render_template(
            "partials/_general_tab.html", fv={}, ident=False, person=None,
            person_note=False, specialties=[],
        )
    assert "Παλαιά πρόσληψη" in html
    assert "Μεταφορά από άλλο πρόγραμμα" not in html
    assert "Εχει υποβληθεί στο ΕΡΓΑΝΗ" in html
    assert html.index("data-fixed-term-fields") < html.index('class="migr-sec"')
    assert html.index('class="migr-sec"') < html.index('name="payroll_from"')


def test_rehire_form_hides_the_old_hire_box_and_saves_as_draft():
    """Επαναπρόσληψη: καμία «Παλαιά πρόσληψη» (νέα περίοδος που ξεκινά τώρα)
    και το κουμπί αποθηκεύει πρόχειρα — δεν υποβάλλει στο ΕΡΓΑΝΗ."""
    person = SimpleNamespace(
        person_id="P1", full_name="ΔΟΚΙΜΗ ΜΑΡΙΑ", afm="111222336",
        amka="05059012343", children=0, employment_ids=["E1"],
    )
    app = create_app()
    with app.test_request_context("/"):
        rehire = render_template(
            "partials/_person_employ_modal.html", p=person, form={},
            specialties=[],
        )
        first = render_template(
            "partials/_person_employ_modal.html",
            p=SimpleNamespace(**{**person.__dict__, "employment_ids": []}),
            form={}, specialties=[],
        )
    assert "Παλαιά πρόσληψη" not in rehire
    assert 'name="payroll_from"' not in rehire
    assert "Παλαιά πρόσληψη" in first          # 1η πρόσληψη: παραμένει
    for html in (rehire, first):
        assert "Προσωρινή αποθήκευση" in html
        assert "→ ΕΡΓΑΝΗ" not in html


def test_fixed_term_start_is_always_canonicalized_to_hire_date():
    assert constraints.check_employment_relationship(
        "1", date(2025, 12, 1), date(2026, 8, 31),
        hire_date=date(2026, 2, 1),
    ) == ("1", date(2026, 2, 1), date(2026, 8, 31))


def test_fixed_term_end_is_informational_marker_in_annual_grids(monkeypatch):
    from webapp.views import leaves as leave_views
    from webapp.views import work_schedules as schedule_views

    service, projection, person_id = _service()
    employment_id = service.start_employment(
        person_id, date(2026, 1, 1), monthly_gross=1500,
        weekly_pattern=WEEK, employment_relationship="1",
        fixed_term_to=date(2026, 6, 30),
    )
    projection.employments[employment_id].hire_submitted = True
    monkeypatch.setattr(schedule_views, "proj", projection)
    monkeypatch.setattr(schedule_views, "svc", service)
    monkeypatch.setattr(schedule_views, "today", lambda: date(2026, 2, 1))
    monkeypatch.setattr(leave_views, "proj", projection)
    monkeypatch.setattr(leave_views, "svc", service)
    monkeypatch.setattr(leave_views, "today", lambda: date(2026, 2, 1))

    schedule_context = schedule_views._planner_context(2026)
    schedule_row = schedule_context["rows"][0]
    end_cell = next(
        cell for cell in schedule_row["cells"]
        if cell["date"] == date(2026, 6, 30)
    )
    next_cell = next(
        cell for cell in schedule_row["cells"]
        if cell["date"] == date(2026, 7, 1)
    )
    assert end_cell["contract_end"] is True
    assert "Προβλεπόμενη λήξη" in end_cell["title"]
    assert next_cell["active"] is True  # η λήξη δεν λειτουργεί ως αποχώρηση

    leave_context = leave_views._planner_context(2026)
    marker = leave_context["rows"][0]["contract_end"]
    assert marker["date"] == date(2026, 6, 30)

    app = create_app()
    with app.test_request_context("/"):
        schedule_html = render_template(
            "partials/_work_schedule_panel.html", **schedule_context
        )
        leave_html = render_template(
            "partials/_leave_planner_panel.html", **leave_context
        )
    assert "work-day-cell state-work" in schedule_html
    assert "contract-end" in schedule_html
    assert "leave-contract-end" in leave_html
    assert "πληροφοριακή ένδειξη, επιτρέπεται παράταση" in leave_html


def test_live_json_preview_uses_unsaved_form_values_without_mutation(monkeypatch):
    service, projection, person_id = _service()
    employment_id = service.start_employment(
        person_id, date(2026, 1, 1), monthly_gross=1500,
        weekly_pattern=WEEK, draft=True,
    )
    projection.company = SimpleNamespace(
        ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ"
    )
    specialty = SimpleNamespace(
        name="ΠΩΛΗΤΗΣ",
        coverages=({
            "role": "MAIN_EPIK", "fund": "efka_main", "kad": "1234",
            "specialty_code": "123456", "kpk": "0101",
        },),
        ergani_specialty_code="4321",
        ergani_specialty_description="ΠΩΛΗΤΕΣ",
    )
    # Η προεπισκόπηση περνά από την ΙΔΙΑ επίλυση με την ανάθεση
    # (svc.specialty_lines) — εδώ την προσποιούμαστε με ένα μόνο πακέτο.
    lines = {
        "specialty": specialty, "carrier": "efka", "pron_choice": "no",
        "lines": [{"coverage": specialty.coverages[0], "fund": "efka_main",
                   "kpk": "0101"}],
        "employee": 0.0, "employer": 0.0, "incomplete": False,
    }
    monkeypatch.setattr(employment_views, "proj", projection)
    monkeypatch.setattr(
        employment_views, "svc",
        SimpleNamespace(
            get_specialty=lambda name: specialty,
            specialty_lines=lambda *a, **kw: lines,
            get_branch=lambda code: SimpleNamespace(
                code=int(code), sepe_code="", oaed_code="", kad_code="",
                kallikratis_code="",
            ),
        ),
    )
    form = {
        "hire_date": "2026-02-01", "pay_type": "monthly", "amount": "1800",
        "weekly_hours": "40", "weekly_system": "5",
        "employment_relationship": "1", "fixed_term_from": "2025-12-01",
        "fixed_term_to": "2026-08-31", "specialty_name": "ΠΩΛΗΤΗΣ",
        "auxiliary_scheme": "efka", "welfare_scheme": "none",
        "insurance_class": "new", "service_years": "0",
        "service_months": "0", "extra_allowances_pct": "0",
        **{f"day_{day}": hours for day, hours in WEEK.items()},
    }

    payload = employment_views._hire_json_preview_from_form(employment_id, form)
    values = payload["AnaggeliesE3N"]["AnaggeliaE3N"][0]

    assert values["f_proslipsidate"] == "01/02/2026"
    assert values["f_orismenou_apo"] == "01/02/2026"
    assert values["f_orismenou_ews"] == "31/08/2026"
    assert values["f_apodoxes"] == "1800,00"
    assert values["f_eidikothta"] == "4321"
    assert projection.employments[employment_id].hire_date == date(2026, 1, 1)
    assert projection.employments[employment_id].monthly_gross == 1500


def test_json_preview_fragment_listens_to_the_whole_form():
    app = create_app()
    with app.test_request_context("/"):
        html = render_template(
            "partials/_hire_json_preview.html",
            e=SimpleNamespace(employment_id="E1"), readonly=False,
            payload_json="{}", json_error="",
        )
    assert "/employments/E1/hire-form/json-preview" in html
    assert "input from:closest form" in html
    assert 'hx-include="closest form"' in html

def test_fixed_term_correction_does_not_block_next_web_e3n_event(tmp_path):
    """Διόρθωση με κενό daily_schedule δεν εμποδίζει την επόμενη υποβολή ΕΡΓΑΝΗ.

    Ήταν regression του SQL read model (άφηνε ανοιχτό transaction)· ο read
    model έφυγε, αλλά η ροή διόρθωση -> προφίλ -> submit_hire_e3 αξίζει να
    παραμείνει καλυμμένη."""
    db_path = str(tmp_path / "shared.db")
    store = Database(db_path).store("test")
    projection = Projections()
    store.subscribe(projection.handle)
    service = PayrollService(store, projection)
    service.confirm_default_parameters()
    service.register_company(
        ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ"
    )
    service.define_branch(0)
    person_id = service.register_person(
        last_name="ΔΟΚΙΜΗ", first_name="ΜΑΡΙΑ", father_name="ΙΩΑΝΝΗΣ",
        mother_name="ΕΛΕΝΗ", birth_date=date(1990, 5, 5),
        afm="111222336", amka="05059012343",
    )
    employment_id = service.start_employment(
        person_id, date(2026, 1, 1), monthly_gross=1500,
        weekly_pattern=WEEK, draft=True,
    )
    service.correct_hire_details(
        employment_id, date(2026, 1, 1), monthly_gross=1500,
        weekly_pattern=WEEK, schedule_mode="fixed",
        employment_relationship="1", fixed_term_from=date(2026, 1, 1),
        fixed_term_to=date(2026, 6, 30),
    )
    service.assign_insurance_profile(
        employment_id,
        coverages=({"fund": "efka_main", "kad": "0000",
                    "specialty_code": "000000", "kpk": "0101"},),
        auxiliary_scheme="efka",
    )

    result = service.submit_hire_e3(employment_id)

    assert result["status"] == "simulated"
    assert service.ergani_hire_submitted(employment_id, "WebE3N")
