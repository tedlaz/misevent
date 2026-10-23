from datetime import date
from types import SimpleNamespace

from _bootstrap import ui_client

from misevent.domain.events import (
    ContractChanged,
    EmploymentHireDetailsCorrected,
    EmploymentStarted,
    PersonRegistered,
)
from misevent.domain.projections import Projections
from misevent.persistence.database import Database
from webapp import create_app
from webapp.views import employments as employment_views


def _history_system():
    store = Database().store("test")
    projection = Projections()
    store.subscribe(projection.handle)
    store.append("P1", PersonRegistered(
        person_id="P1", last_name="ΔΟΚΙΜΗ", first_name="ΜΑΡΙΑ",
        father_name="ΙΩΑΝΝΗΣ", mother_name="ΕΛΕΝΗ",
        birth_date=date(1990, 1, 1), afm="111222336", amka="01019012345",
    ))
    store.append("E1", EmploymentStarted(
        employment_id="E1", person_id="P1", hire_date=date(2026, 1, 1),
        pay_type="monthly", monthly_gross=1000, weekly_hours=40,
        weekly_system=5, service_years=3, married=True,
    ))
    # Η διόρθωση ανήκει στην αρχική σύμβαση, δεν είναι ξεχωριστή μεταβολή.
    store.append("E1", EmploymentHireDetailsCorrected(
        employment_id="E1", hire_date=date(2026, 1, 2),
        pay_type="monthly", monthly_gross=1100, weekly_hours=38,
        weekly_system=5, service_years=3, married=True,
    ))
    store.append("E1", ContractChanged(
        employee_id="E1", effective_from=date(2026, 3, 1),
        pay_type="monthly", monthly_gross=1250, weekly_hours=40,
        weekly_system=6,
    ))
    store.append("E1", ContractChanged(
        employee_id="E1", effective_from=date(2026, 6, 1),
        pay_type="daily", daily_wage=60, weekly_hours=40,
        weekly_system=None,
    ))
    return store, projection


def _adjust_system(reason, reason_date=None, *, migrated=False):
    """Απασχόληση με ΜΙΑ αυτόματη προσαρμογή αποδοχών (ΓΣΣΕ/τριετία)."""
    store = Database().store("test")
    projection = Projections()
    store.subscribe(projection.handle)
    store.append("P1", PersonRegistered(
        person_id="P1", last_name="ΔΟΚΙΜΗ", first_name="ΜΑΡΙΑ",
        father_name="ΙΩΑΝΝΗΣ", mother_name="ΕΛΕΝΗ",
        birth_date=date(1990, 1, 1), afm="111222336", amka="01019012345",
    ))
    store.append("E1", EmploymentStarted(
        employment_id="E1", person_id="P1", hire_date=date(2024, 5, 15),
        pay_type="monthly", monthly_gross=1000, weekly_hours=40,
        weekly_system=5,
        payroll_from=date(2026, 1, 1) if migrated else None,
    ))
    store.append("E1", ContractChanged(
        employee_id="E1", effective_from=date(2026, 4, 1),
        pay_type="monthly", monthly_gross=1100, weekly_hours=40,
        reason=reason, reason_date=reason_date,
    ))
    return store, projection


def test_contract_history_contains_corrected_initial_and_all_changes(monkeypatch):
    store, projection = _history_system()
    monkeypatch.setattr(employment_views, "store", store)
    monkeypatch.setattr(employment_views, "proj", projection)

    rows = employment_views._contract_history("E1")

    assert [row["effective_from"] for row in rows] == [
        date(2026, 1, 2), date(2026, 3, 1), date(2026, 6, 1)
    ]
    assert [row["kind"] for row in rows] == [
        "Αρχική σύμβαση", "Μεταβολή 1", "Μεταβολή 2"
    ]
    assert rows[0]["monthly_gross"] == 1100
    assert rows[2]["daily_wage"] == 60
    assert rows[2]["weekly_system"] == 6
    assert rows[2]["is_current"] is True


def test_contract_history_notes_describe_each_change(monkeypatch):
    """Η στήλη «Σχόλια»: διαφορά όρων στις χειροκίνητες μεταβολές."""
    store, projection = _history_system()
    monkeypatch.setattr(employment_views, "store", store)
    monkeypatch.setattr(employment_views, "proj", projection)

    notes = [row["note"] for row in employment_views._contract_history("E1")]

    assert notes[0] == "Αρχική σύμβαση πρόσληψης"
    assert "1.100,00€ → 1.250,00€" in notes[1]
    assert "Αύξηση αποδοχών" in notes[1]
    assert "38 → 40 ώρες" in notes[1] and "εξαήμερο" in notes[1]
    assert "ημερομίσθιο" in notes[2]


def test_contract_history_notes_from_event_reason(monkeypatch):
    """ΓΣΣΕ/τριετία/μεταφορά: η αιτιολογία έρχεται από το ίδιο το event."""
    cases = {
        ("gsse", date(2026, 4, 1)): "Προσαρμογή αποδοχών σε ΓΣΣΕ της 01/04/2026",
        ("triennium", None): "Προσαρμογή αποδοχών λόγω τριετίας",
    }
    for (reason, when), expected in cases.items():
        store, projection = _adjust_system(reason, when)
        monkeypatch.setattr(employment_views, "store", store)
        monkeypatch.setattr(employment_views, "proj", projection)
        assert employment_views._contract_history("E1")[1]["note"] == expected

    # Μεταφορά από άλλο πρόγραμμα μισθοδοσίας -> η ΑΝΑΛΗΨΗ στην αρχική σύμβαση
    store, projection = _adjust_system("triennium", migrated=True)
    monkeypatch.setattr(employment_views, "store", store)
    monkeypatch.setattr(employment_views, "proj", projection)
    assert (employment_views._contract_history("E1")[0]["note"]
            == "Μεταφορά και ανάληψη από 01/01/2026")


def test_contract_history_modal_route_renders_table(monkeypatch):
    contracts = [{
        "effective_from": date(2026, 1, 1), "kind": "Αρχική σύμβαση",
        "pay_type": "monthly", "monthly_gross": 1200, "daily_wage": 0,
        "weekly_hours": 40, "weekly_system": 5, "service_years": 0,
        "married": False, "extra_allowances_pct": 0, "is_current": True,
    }]
    employee = SimpleNamespace(employment_id="E1", full_name="ΔΟΚΙΜΗ ΜΑΡΙΑ")
    monkeypatch.setattr(employment_views, "_card", lambda _eid: {"e": employee})
    monkeypatch.setattr(
        employment_views, "_contract_history", lambda _eid: contracts
    )

    response = ui_client(create_app()).get(
        "/employments/E1/modal/contract_history"
    )
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Ιστορικό συμβάσεων" in body
    assert "Αρχική σύμβαση" in body
    assert "01/01/2026" in body
    assert "Τρέχουσα" in body


def test_migrated_employment_has_no_schedule_weeks_before_payroll_start():
    """Μεταφορά (πρόσληψη 15/5/2024, ανάληψη 1/1/2026): οι εβδομάδες πριν την
    ανάληψη ανήκουν στο προηγούμενο πρόγραμμα — ούτε εκκρεμή προγράμματα."""
    _store, projection = _adjust_system(None, migrated=True)
    e = projection.employments["E1"]
    assert e.week_status(date(2025, 12, 22)) == "off"
    assert e.active_days_in_week(date(2025, 12, 29)) == [
        date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4)]
    assert e.active_on(date(2026, 1, 1))
