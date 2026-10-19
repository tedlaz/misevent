from types import SimpleNamespace

import _bootstrap  # noqa: F401

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.persistence.database import Database
from webapp import create_app
from webapp.views import company as company_views


def test_company_branches_persist_and_rebuild_cleanly():
    store = Database().store("test")
    projection = Projections()
    store.subscribe(projection.handle)
    service = PayrollService(store, projection)

    service.register_company("1234567890", "999888771", "ΔΟΚΙΜΗ ΑΕ")
    assert projection.branches == {}

    service.define_branch(
        0, "ΝΕΑ", "10", "54625", "ΘΕΣΣΑΛΟΝΙΚΗ",
        "11010", "101201", "0111", "90010101",
    )
    service.define_branch(2, "ΤΣΙΜΙΣΚΗ", "20", "54624", "ΘΕΣΣΑΛΟΝΙΚΗ")

    assert projection.branches[2].display_name == "2 — ΤΣΙΜΙΣΚΗ 20"
    assert projection.branches[0].employment_label == "0 Κεντρικό"
    assert projection.branches[2].employment_label == "2 — ΤΣΙΜΙΣΚΗ 20"
    assert service.employer_info().street == "ΝΕΑ"
    assert projection.branches[0].kad_code == "0111"

    rebuilt = Projections()
    rebuilt.rebuild(store)
    assert rebuilt.branches[0].street == "ΝΕΑ"
    assert rebuilt.branches[2].street_no == "20"


def test_invalid_company_form_keeps_every_submitted_value(monkeypatch):
    """Validation error must not replace the user's input with stored values."""
    stored = SimpleNamespace(
        ame="0000000000", afm="000000000", legal_name="ΠΑΛΙΑ",
        first_name="", father_name="", activity="ΠΑΛΙΟ",
        local_office_code="0000000000", local_office_name="ΠΑΛΙΑ ΥΠΗΡΕΣΙΑ",
    )

    def reject(**_kwargs):
        raise ValueError("Μη έγκυρο ΑΦΜ")

    monkeypatch.setattr(company_views, "proj", SimpleNamespace(company=stored))
    monkeypatch.setattr(
        company_views, "svc", SimpleNamespace(register_company=reject)
    )
    submitted = {
        "ame": "1234567890", "afm": "123", "legal_name": "Νέα εταιρεία",
        "first_name": "Μαρία", "father_name": "Νίκος",
        "activity": "Νέα δράση", "local_office_code": "1156602000",
        "local_office_name": "ΤΟΠΙΚΗ ΛΑΡΙΣΑΣ",
    }
    app = create_app()
    with app.test_request_context("/company/", method="POST", data=submitted):
        response = company_views.save()

    html = response.get_data(as_text=True)
    for field, value in submitted.items():
        assert f'name="{field}"' in html
        assert f'value="{value}"' in html
    assert "ΠΑΛΙΑ ΥΠΗΡΕΣΙΑ" not in html
    assert '"err": true' in response.headers["HX-Trigger"]
