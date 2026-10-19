from datetime import date
from types import SimpleNamespace

from misevent.app.services import PayrollService
from misevent.domain.events import ErganiSubmissionRecorded
from misevent.domain.projections import Projections
from misevent.integrations import ergani, ergani_parameters
from misevent.persistence.database import Database
from misevent.rules import constraints


def test_web_e3n_payload_uses_documented_schema_and_formats():
    company = SimpleNamespace(ame="1234567890", afm="123456789", legal_name="ACME")
    person = SimpleNamespace(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΥ",
        first_name="ΜΑΡΙΑ",
        father_name="ΙΩΑΝΝΗΣ",
        mother_name="ΕΛΕΝΗ",
        birth_date=date(1990, 2, 3),
        afm="123456789",
        amka="03029012345",
        ama="123456789",
        children=2,
        sex="1",
        nationality_code="300",
        identity_type="ΑΔΤ",
        identity_number="ΑΒ123456",
        identity_issuer="ΑΤ ΑΘΗΝΩΝ",
        identity_issue_date=date(2020, 1, 2),
        identity_expiry_date=date(2030, 1, 2),
        marital_status="1",
        education_level="12",
        res_permit_inst="1",
        res_permit_inst_type="123",
        res_permit_inst_number="RP-100",
        res_permit_inst_expiry_date=date(2029, 12, 31),
        res_permit_ap="0",
        res_permit_ap_type="",
        res_permit_ap_number="",
        res_permit_ap_expiry_date=None,
        res_permit_visa="1",
        res_permit_visa_number="VISA-1",
        res_permit_visa_from=date(2026, 7, 1),
        res_permit_visa_to=date(2026, 10, 31),
    )
    emp = SimpleNamespace(
        **vars(person),
        hire_date=date(2026, 7, 31),  # Παρασκευή
        pay_type="monthly",
        monthly_gross=1200,
        daily_wage=0,
        weekly_hours=40,
        weekly_system=5,
        service_years=3,
        married=True,
        schedule_mode="fixed",
    )
    profile = SimpleNamespace(
        branch_no=0,
        kad="1234",
        specialty_code="123456",
        specialty_name="ΥΠΑΛΛΗΛΟΣ",
        kpk="101",
        auxiliary_scheme="efka",
    )

    payload = ergani.build_web_e3n(
        company, emp, profile, {"FRI": ["08:30-16:30"]}
    )

    assert set(payload) == {"AnaggeliesE3N"}
    announcements = payload["AnaggeliesE3N"]["AnaggeliaE3N"]
    assert len(announcements) == 1
    form = announcements[0]
    assert form["f_proslipsidate"] == "31/07/2026"
    assert form["f_birthdate"] == "03/02/1990"
    assert form["f_proslipsitime"] == "08:30"
    assert form["f_apoxwrisitime"] == "16:30"
    assert form["f_week_hours"] == "40,0"
    assert form["f_apodoxes"] == "1200,00"
    assert form["f_hour_apodoxes"] == "7,20"
    assert form["f_marital_status"] == "1"
    assert form["f_epipedo_morfosis"] == "12"
    assert form["f_arithmos_teknon"] == "2"
    assert form["f_sex"] == "1"
    assert form["f_yphkoothta"] == "300"
    assert form["f_typos_taytothtas"] == "ΑΔΤ"
    assert form["f_ar_taytothtas"] == "ΑΒ123456"
    assert form["f_ekdousa_arxh"] == "ΑΤ ΑΘΗΝΩΝ"
    assert form["f_date_ekdosis"] == "02/01/2020"
    assert form["f_date_ekdosis_lixi"] == "02/01/2030"
    assert form["f_res_permit_inst"] == "1"
    assert form["f_res_permit_inst_type"] == "123"
    assert form["f_res_permit_inst_ar"] == "RP-100"
    assert form["f_res_permit_inst_lixi"] == "31/12/2029"
    assert form["f_res_permit_ap"] == "0"
    assert form["f_res_permit_visa"] == "1"
    assert form["f_res_permit_visa_ar"] == "VISA-1"
    assert form["f_res_permit_visa_from"] == "01/07/2026"
    assert form["f_res_permit_visa_to"] == "31/10/2026"
    assert form["f_kad_pararthmatos"] == "1234"
    assert form["f_eidikothta"] == "123456"
    # >=40 ώρες υπερισχύουν του μονοήμερου template: πλήρης απασχόληση.
    assert form["f_kathestosapasxolisis"] == "0"
    assert form["f_working_time_digital_organization"] == "1"
    assert form["f_file"] == ""
    assert form["f_file_symbash"] == ""
    assert form["f_foreign_file"] == ""
    assert form["f_young_file"] == ""
    assert form["f_kyria_asfalisi"] == "001"
    assert form["EpikourikiSelections"] == {
        "EpikourikiSelectionsE3N": [{"f_kod_epikourikis": "001"}]
    }
    insurance_fields = [
        "f_kyria_asfalisi",
        "EpikourikiSelections",
        "f_prosthetes_asfalistikes",
        "f_mh_provlepsimo_programma",
    ]
    keys = list(form)
    start = keys.index("f_kyria_asfalisi")
    assert keys[start : start + len(insurance_fields)] == insurance_fields

    profile.auxiliary_scheme = "teka"
    teka_form = ergani.build_web_e3n(
        company, emp, profile, {"FRI": ["08:30-16:30"]}
    )["AnaggeliesE3N"]["AnaggeliaE3N"][0]
    assert teka_form["f_kyria_asfalisi"] == "001"
    assert teka_form["EpikourikiSelections"] == {
        "EpikourikiSelectionsE3N": [{"f_kod_epikourikis": "002"}]
    }

    profile.auxiliary_scheme = "none"
    default_form = ergani.build_web_e3n(
        company, emp, profile, {"FRI": ["08:30-16:30"]}
    )["AnaggeliesE3N"]["AnaggeliaE3N"][0]
    assert default_form["EpikourikiSelections"] == {
        "EpikourikiSelectionsE3N": [{"f_kod_epikourikis": "001"}]
    }


def test_web_e3n_uses_selected_branch_ergani_fields_not_insurance_kad():
    company = SimpleNamespace(ame="1", afm="2", legal_name="ACME")
    emp = SimpleNamespace(
        last_name="Α", first_name="Β", father_name="Γ", mother_name="Δ",
        birth_date=date(1990, 1, 1), afm="", amka="", ama="", children=0,
        hire_date=date(2026, 1, 2), pay_type="monthly", monthly_gross=1000,
        daily_wage=0, weekly_hours=40, weekly_system=5, service_years=0,
        married=False, schedule_mode="fixed",
    )
    profile = SimpleNamespace(
        branch_no=2, kad="9999", specialty_code="123456", kpk="101",
        auxiliary_scheme="efka",
    )
    branch = SimpleNamespace(
        sepe_code="11010", oaed_code="101201", kad_code="0111",
        kallikratis_code="90010101",
    )
    form = ergani.build_web_e3n(
        company, emp, profile, {"FRI": "09:00-17:00"}, branch=branch
    )["AnaggeliesE3N"]["AnaggeliaE3N"][0]

    assert form["f_aa_pararthmatos"] == "2"
    assert form["f_ypiresia_sepe"] == "11010"
    assert form["f_ypiresia_oaed"] == "101201"
    assert form["f_kad_pararthmatos"] == "0111"
    assert form["f_kallikratis_pararthmatos"] == "90010101"


def test_web_e3n_employment_status_uses_hours_and_template_days():
    five_days = {
        day: "09:00-15:00" for day in ("MON", "TUE", "WED", "THU", "FRI")
    }
    four_days = {
        day: ["09:00-16:00"] for day in ("MON", "TUE", "WED", "THU")
    }
    four_days["FRI"] = ""  # κενή ημέρα/ρεπό δεν προσμετράται

    assert ergani.employment_status(40, four_days) == "0"  # πλήρης
    assert ergani.employment_status(39, five_days) == "1"  # μερική
    assert ergani.employment_status(39, four_days) == "2"  # εκ περιτροπής


def test_web_e3n_client_posts_to_document_entry_point(monkeypatch):
    client = ergani.ErganiClient(simulate=False)
    client._token = "TOKEN"
    called = {}

    def fake_post(path, payload, auth=True):
        called.update(path=path, payload=payload, auth=auth)
        return {"protocol": "P-1"}

    monkeypatch.setattr(client, "_post", fake_post)
    payload = {"AnaggeliesE3N": {"AnaggeliaE3N": []}}

    result = client.submit("WebE3N", payload)

    assert called["path"] == "/Documents/WebE3N"
    assert called["payload"] == payload
    assert result["reference"] == "P-1"


def test_web_e3n_identity_fields_are_persisted_and_correctable():
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    service = PayrollService(store, proj)
    nationality = ergani_parameters.nationalities()[0]["code"]
    identity_type = ergani_parameters.identity_types()[0]["code"]
    education_levels = ergani_parameters.education_levels()

    person_id = service.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ",
        first_name="ΙΩΑΝΝΗΣ",
        father_name="ΝΙΚΟΛΑΟΣ",
        mother_name="ΜΑΡΙΑ",
        birth_date=date(1990, 5, 5),
        afm="111222336",
        amka="05059012343",
        ama="123456789",
        sex="0",
        nationality_code=nationality,
        identity_type=identity_type,
        identity_number="ΑΒ123456",
        identity_issuer="ΑΤ ΑΘΗΝΩΝ",
        identity_issue_date=date(2020, 1, 2),
        identity_expiry_date=date(2030, 1, 2),
        marital_status="2",
        education_level=education_levels[0]["code"],
        res_permit_inst="1",
        res_permit_inst_type="123",
        res_permit_inst_number="RP-100",
        res_permit_inst_expiry_date=date(2029, 12, 31),
    )

    person = proj.persons[person_id]
    assert person.sex == "0"
    assert person.nationality_code == nationality
    assert person.identity_type == identity_type
    assert person.identity_issue_date == date(2020, 1, 2)
    assert person.identity_expiry_date == date(2030, 1, 2)
    assert person.marital_status == "2"
    assert person.education_level == education_levels[0]["code"]
    assert person.res_permit_inst_number == "RP-100"


    service.correct_person(
        person_id,
        identity_number="ΧΖ987654",
        identity_expiry_date=date(2032, 3, 4),
        education_level=education_levels[1]["code"],
    )
    assert proj.persons[person_id].identity_number == "ΧΖ987654"
    assert proj.persons[person_id].education_level == education_levels[1]["code"]

    replayed = Projections()
    replayed.rebuild(store)
    assert replayed.persons[person_id].identity_number == "ΧΖ987654"
    assert replayed.persons[person_id].identity_issue_date == date(2020, 1, 2)
    assert replayed.persons[person_id].identity_expiry_date == date(2032, 3, 4)
    assert replayed.persons[person_id].education_level == education_levels[1]["code"]

    service.correct_person(
        person_id,
        res_permit_inst="",
        res_permit_inst_type="",
        res_permit_inst_number="",
        res_permit_inst_expiry_date=None,
    )
    assert proj.persons[person_id].res_permit_inst == ""
    assert proj.persons[person_id].res_permit_inst_number == ""
    assert proj.persons[person_id].res_permit_inst_expiry_date is None


def test_hire_form_is_correctable_only_before_web_e3n_submission():
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    service = PayrollService(store, proj)
    person_id = service.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ",
        first_name="ΙΩΑΝΝΗΣ",
        father_name="ΝΙΚΟΛΑΟΣ",
        mother_name="ΜΑΡΙΑ",
        birth_date=date(1990, 5, 5),
        afm="111222336",
        amka="05059012343",
    )
    initial_schedule = {
        code: "09:00-17:00" for code in ("MON", "TUE", "WED", "THU", "FRI")
    }
    employment_id = service.start_employment(
        person_id,
        date(2026, 7, 1),
        monthly_gross=1200,
        weekly_pattern=initial_schedule,
        draft=True,
    )
    assert proj.persons[person_id].employment_ids == [employment_id]
    assert not proj.employments[employment_id].hire_submitted
    assert employment_id not in service.attendance_draft(2026, 7)
    corrected_schedule = {
        code: "08:00-16:00" for code in ("MON", "TUE", "WED", "THU", "FRI")
    }

    service.correct_hire_details(
        employment_id,
        date(2026, 7, 2),
        monthly_gross=1300,
        weekly_pattern=corrected_schedule,
        service_years=2,
        married=True,
        extra_allowances_pct=5,
    )

    employment = proj.employments[employment_id]
    assert employment.hire_date == date(2026, 7, 2)
    assert employment.monthly_gross == 1300
    assert employment.schedule == corrected_schedule
    assert employment.married is True
    assert employment.service_years == 2

    store.append(
        employment_id,
        ErganiSubmissionRecorded(
            "WebE3N", employment_id, "{}", "simulated", "SIM-1", "trial"
        ),
    )
    assert proj.employments[employment_id].hire_submitted
    assert employment_id in service.attendance_draft(2026, 7)
    try:
        service.correct_hire_details(
            employment_id,
            date(2026, 7, 3),
            monthly_gross=1400,
            weekly_pattern=corrected_schedule,
        )
    except ValueError as exc:
        assert "μόνο για προβολή" in str(exc)
    else:
        raise AssertionError("Submitted hire form remained editable")


def test_ergani_parameter_lists_are_loaded_from_sqlite():
    identity_types = ergani_parameters.identity_types()
    nationalities = ergani_parameters.nationalities()
    education_levels = ergani_parameters.education_levels()

    assert len(identity_types) == 12
    assert len(nationalities) == 196
    assert len(education_levels) == 17
    assert [int(row["code"]) for row in education_levels] == sorted(
        int(row["code"]) for row in education_levels
    )
    assert all(set(row) == {"code", "description"} for row in identity_types)
    assert all(len(row["code"]) == 3 for row in nationalities)
    assert all(set(row) == {"code", "description"} for row in education_levels)
    assert ergani_parameters.contains(
        "education_levels", education_levels[0]["code"]
    )
    assert ergani_parameters.contains("identity_types", identity_types[0]["code"])
    assert not ergani_parameters.contains("nationalities", "300")
    assert ergani_parameters.is_eu_nationality("048")
    assert not ergani_parameters.is_eu_nationality("999")


def test_third_country_details_are_rejected_for_eu_nationality():
    try:
        constraints.check_web_e3n_person_details(
            marital_status="0", nationality_code="048", res_permit_inst="1"
        )
    except ValueError as exc:
        assert "υπηκοότητα ΕΕ" in str(exc)
    else:
        raise AssertionError("EU nationality accepted third-country permit details")

    constraints.check_web_e3n_person_details(
        marital_status="0", nationality_code="999", res_permit_inst="1"
    )
