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
    profile = SimpleNamespace(branch_no=0, auxiliary_scheme="efka",
                              ergani_specialty_code="4321", ergani_specialty_description="ΠΩΛΗΤΕΣ",)
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


def test_hire_kind_is_chosen_first_and_migration_has_its_own_box():
    """Είδος πρόσληψης ΠΡΩΤΟ (νέα / υπάρχων εργαζόμενος), και τα πεδία της
    μεταφοράς σε δικό τους πλαίσιο. Το «Εχει υποβληθεί στο ΕΡΓΑΝΗ» δεν είναι
    πια επιλογή: ο υπάρχων εργαζόμενος έχει ΠΑΝΤΑ ήδη αναγγελία."""
    app = create_app()
    with app.test_request_context("/"):
        html = render_template(
            "partials/_general_tab.html", fv={}, ident=False, person=None,
            person_note=False, specialties=[],
        )
    assert "Νέα πρόσληψη" in html and "Υπάρχων εργαζόμενος" in html
    assert "Εχει υποβληθεί στο ΕΡΓΑΝΗ" not in html
    assert 'name="ergani_hire_done"' not in html
    assert html.index('class="migr-sec"') < html.index('name="hire_date"')
    assert html.index('class="migr-sec"') < html.index('name="payroll_from"')
    assert html.index('name="payroll_from"') < html.index("data-fixed-term-fields")


def test_rehire_form_hides_the_old_hire_box_and_saves_as_draft():
    """Επαναπρόσληψη: κανένα «Υπάρχων εργαζόμενος» (νέα περίοδος που ξεκινά τώρα)
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
    assert "Υπάρχων εργαζόμενος" not in rehire
    assert 'name="payroll_from"' not in rehire
    assert "Υπάρχων εργαζόμενος" in first      # 1η πρόσληψη: παραμένει
    for html in (rehire, first):
        assert "Προσωρινή αποθήκευση" in html
        assert "→ ΕΡΓΑΝΗ" not in html


def test_fixed_term_start_is_always_canonicalized_to_hire_date():
    assert constraints.check_employment_relationship(
        "1", date(2025, 12, 1), date(2026, 8, 31),
        hire_date=date(2026, 2, 1),
    ) == ("1", date(2026, 2, 1), date(2026, 8, 31))


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
        ergani_specialty_code="419",
        ergani_specialty_description="ΛΟΙΠΟΙ ΥΠΑΛΛΗΛΟΙ ΓΡΑΦΕΙΟΥ",
        coverages=({"fund": "efka_main", "kad": "0000",
                    "specialty_code": "000000", "kpk": "0101"},),
        auxiliary_scheme="efka",
    )

    result = service.submit_hire_e3(employment_id)

    assert result["status"] == "simulated"
    assert service.ergani_hire_submitted(employment_id, "WebE3N")


def test_form_migration_existing_employee_needs_payroll_start_and_is_announced():
    """Υπάρχων εργαζόμενος: η αναγγελία έχει ΠΑΝΤΑ ήδη γίνει (όχι επιλογή) και
    χωρίς ανάληψη δεν περνά σιωπηλά ως νέα πρόσληψη."""
    from webapp.helpers import form_migration

    assert form_migration({"migrated": ""})["ergani_hire_done"] is False
    with pytest.raises(ValueError, match="Έναρξη μισθοδοσίας"):
        form_migration({"migrated": "1", "payroll_from": ""})
    got = form_migration({"migrated": "1", "payroll_from": "2026-01-01",
                          "ergani_hire_reference": "123"})
    assert got == {"payroll_from": date(2026, 1, 1), "ergani_hire_done": True,
                   "ergani_hire_reference": "123"}


def _migrated(schedule_mode="fixed"):
    """Υπάρχων εργαζόμενος: πρόσληψη 2024, ανάληψη 1/1/2026, «σήμερα» 21/1."""
    service, projection, person_id = _service()
    service.confirm_default_parameters()
    service.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    service.define_branch(0)
    eid = service.start_employment(
        person_id, date(2024, 5, 15), monthly_gross=1500, weekly_pattern=WEEK,
        payroll_from=date(2026, 1, 1), ergani_hire_done=True,
        schedule_mode=schedule_mode, schedule_weeks=4, today=date(2026, 1, 21),
    )
    return service, projection.employments[eid]


def test_migrated_fixed_schedule_before_today_is_covered_and_ocx_pends_from_today():
    service, emp = _migrated()
    assert emp.hire_submitted
    assert all(emp.day_status(date(2026, 1, d)) != "pending" for d in range(1, 21))
    assert emp.day_status(date(2026, 1, 21)) == "pending"
    # ΜΙΑ εκκρεμής ΟΧΕ, από «σήμερα» — όχι αναδρομικά από την ανάληψη
    assert [p[0] for p in emp.pending_weekly] == [date(2026, 1, 21)]
    service.submit_weekly_schedules({emp.employment_id: [date(2026, 1, 21)]})
    assert not emp.pending_weekly
    assert not service.month_schedule_pending(emp, 2026, 1)


def test_migrated_variable_schedule_before_today_is_covered():
    _service_, emp = _migrated("variable")
    assert all(emp.day_status(date(2026, 1, d)) != "pending" for d in range(1, 21))
    assert emp.day_status(date(2026, 1, 21)) == "pending"
    assert not emp.days_declared_here   # η κάλυψη ΔΕΝ είναι δική μας ΟΧΕ


def test_existing_employee_cannot_be_imported_after_the_first_payroll():
    """Η 1η μισθοδοσία έπρεπε να περιλαμβάνει ΟΛΟΥΣ τους ενεργούς — μετά από
    αυτήν υπάρχων εργαζόμενος (μεταφορά) απορρίπτεται· νέα πρόσληψη περνά."""
    from misevent.domain.events import MonthlyPayrollCalculated

    service, projection, person_id = _service()
    assert service.migration_closed_by() is None
    service.store.append("E0", MonthlyPayrollCalculated("E0", 2026, 2, 0, 0, 0, 0, 0, 0))
    assert service.migration_closed_by() == (2026, 2)
    for kwargs in ({"payroll_from": date(2026, 3, 1)}, {"ergani_hire_done": True}):
        with pytest.raises(ValueError, match="πριν από την πρώτη μισθοδοσία"):
            service.start_employment(person_id, date(2024, 5, 15), monthly_gross=1500,
                                     weekly_pattern=WEEK, **kwargs)
    assert service.start_employment(person_id, date(2026, 3, 2), monthly_gross=1500,
                                    weekly_pattern=WEEK)


def test_hire_form_hides_existing_employee_after_the_first_payroll():
    app = create_app()
    with app.test_request_context("/"):
        closed = render_template(
            "partials/_general_tab.html", fv={}, ident=False, person=None,
            person_note=False, specialties=[], migration_closed=(2026, 2),
        )
        shown = render_template(   # ήδη μεταφερμένη: η προβολή τη δείχνει
            "partials/_general_tab.html", fv={"migrated": "1"}, ident=False,
            person=None, person_note=False, specialties=[],
            migration_closed=(2026, 2),
        )
    assert 'name="payroll_from"' not in closed and "02/2026" in closed
    assert "Υπάρχων εργαζόμενος" in shown


def test_hire_form_has_minor_booklet_and_unemployment_card_fields():
    """Το βιβλιάριο είναι κρυμμένο ώσπου το JS να κρίνει ανήλικο (από την ημ.
    γέννησης — του προσώπου σε επαναπρόσληψη)· το δελτίο ζει δίπλα στο
    «Ήταν άνεργος»."""
    app = create_app()
    person = SimpleNamespace(birth_date=date(2009, 6, 1), full_name="Χ", afm="",
                             amka="", children=0)
    with app.test_request_context("/"):
        html = render_template(
            "partials/_general_tab.html", fv={}, ident=False, person=person,
            person_note=False, specialties=[],
        )
    assert 'name="f_ar_vivliou_anilikou"' in html and "data-minor-field hidden" in html
    assert 'data-birth-date="2009-06-01"' in html
    assert 'name="was_unemployed"' in html and 'name="f_code_anergias"' in html
