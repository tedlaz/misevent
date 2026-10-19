"""Επαναπρόσληψη: η προϋπηρεσία συνεχίζει από την προηγούμενη περίοδο."""

from datetime import date

import _bootstrap  # noqa: F401
from _bootstrap import ui_client

from misevent.app.services import PayrollService
from misevent.domain.events import EmployeeTerminated
from misevent.domain.projections import Projections
from misevent.persistence.database import Database
from webapp.helpers import frozen_service_years_fields, service_years_fields

WEEK = {day: "09:00-17:00" for day in ("MON", "TUE", "WED", "THU", "FRI")}


def _svc():
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    pid = svc.register_person(
        last_name="ΔΟΚΙΜΗ",
        first_name="ΜΑΡΙΑ",
        father_name="ΙΩΑΝΝΗΣ",
        mother_name="ΕΛΕΝΗ",
        birth_date=date(1990, 5, 5),
        afm="111222336",
        amka="05059012343",
    )
    return svc, proj, pid


def test_previous_service_years_adds_the_time_worked():
    svc, proj, pid = _svc()
    assert svc.previous_service_years(pid) == 0.0  # καμία προηγούμενη

    eid = svc.start_employment(
        pid,
        date(2024, 5, 1),
        monthly_gross=1500,
        weekly_pattern=WEEK,
        service_years=2.0,  # δηλωθείσα προϋπηρεσία
    )
    proj.employments[eid].hire_submitted = True
    # Ενεργή απασχόληση δεν μετράει ως «προηγούμενη»
    assert svc.previous_service_years(pid) == 0.0

    # Σκέτη λήξη της σχέσης — η εκκαθάριση αποχώρησης δεν αφορά τον έλεγχο
    svc.store.append(
        eid, EmployeeTerminated(eid, date(2025, 11, 1), "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
    )
    total = svc.previous_service_years(pid)
    # 2 έτη δηλωθείσα + ~1,5 έτος στον εργοδότη (1/5/2024 -> 1/11/2025)
    assert abs(total - 3.5031) < 0.01, total
    assert service_years_fields(total) == {"service_years": 3, "service_months": 6}


def test_service_years_fields_is_the_inverse_of_the_form():
    from webapp.helpers import form_frozen_service_years, form_service_years

    for years, months in ((0, 0), (2, 11), (5, 1)):
        value = form_service_years(
            {"service_years": str(years), "service_months": str(months)}
        )
        assert service_years_fields(value) == {
            "service_years": years,
            "service_months": months,
        }
    # 11,96 μήνες -> στρογγυλοποίηση σε πλήρες έτος, όχι «0 έτη 12 μήνες»
    assert service_years_fields(0.9967) == {"service_years": 1, "service_months": 0}
    assert frozen_service_years_fields(1.5) == {
        "frozen_service_years": 1,
        "frozen_service_months": 6,
    }
    assert (
        form_frozen_service_years(
            {"frozen_service_years": "1", "frozen_service_months": "6"}
        )
        == 1.5
    )


def test_rehire_modal_prefills_the_service_fields(monkeypatch):
    """Το modal επαναπρόσληψης ανοίγει με τα δύο πεδία ήδη γεμάτα."""
    from webapp import create_app
    from webapp.views import employments as employment_views
    from webapp.views import persons as person_views

    svc, proj, pid = _svc()
    eid = svc.start_employment(
        pid,
        date(2024, 5, 1),
        monthly_gross=1500,
        weekly_pattern=WEEK,
        service_years=2.0,
    )
    svc.store.append(
        eid, EmployeeTerminated(eid, date(2025, 11, 1), "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
    )
    monkeypatch.setattr(person_views, "proj", proj)
    monkeypatch.setattr(person_views, "svc", svc)
    monkeypatch.setattr(employment_views, "proj", proj)
    monkeypatch.setattr(employment_views, "svc", svc)

    html = (
        ui_client(create_app()).get(f"/persons/{pid}/employ").get_data(as_text=True)
    )

    import re

    def field(name):
        m = re.search(rf'name="{name}"[^>]*value="(\d+)"', html, re.DOTALL)
        assert m, f"δεν βρέθηκε το πεδίο {name}"
        return m.group(1)

    assert field("service_years") == "3"
    assert field("service_months") == "6"


def test_hire_details_form_keeps_the_rehire_mode(monkeypatch):
    """«Επεξεργασία φόρμας πρόσληψης» σε επαναπρόσληψη: ίδιο mode με τη φόρμα
    που τη δημιούργησε — τίτλος «Επαναπρόσληψη», καμία «Παλαιά πρόσληψη»."""
    from webapp import create_app
    from webapp.views import employments as employment_views

    svc, proj, pid = _svc()
    first = svc.start_employment(
        pid,
        date(2024, 5, 1),
        monthly_gross=1500,
        weekly_pattern=WEEK,
    )
    svc.store.append(
        first, EmployeeTerminated(first, date(2025, 11, 1), "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
    )
    second = svc.start_employment(
        pid,
        date(2026, 3, 1),
        monthly_gross=1500,
        weekly_pattern=WEEK,
    )
    assert proj.employments[first].is_rehire is False
    assert proj.employments[second].is_rehire is True

    monkeypatch.setattr(employment_views, "proj", proj)
    monkeypatch.setattr(employment_views, "svc", svc)
    client = ui_client(create_app())

    rehired = client.get(f"/employments/{second}/hire-form").get_data(as_text=True)
    original = client.get(f"/employments/{first}/hire-form").get_data(as_text=True)

    assert "Επαναπρόσληψη" in rehired
    assert "Παλαιά πρόσληψη" not in rehired
    assert "Επαναπρόσληψη" not in original
    assert "Παλαιά πρόσληψη" in original


test_previous_service_years_adds_the_time_worked()
test_service_years_fields_is_the_inverse_of_the_form()
print("OK επαναπροσληψη: προϋπηρεσία = δηλωθείσα + χρόνος προηγούμενης περιόδου")
