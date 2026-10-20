"""
Στοιχεία σύνδεσης ΕΡΓΑΝΗ ανά εταιρεία: κρυπτογραφημένα σε `secrets.db`,
ΕΚΤΟΣ events, μόνο διαχειριστές, μέσα στο backup όλου του συστήματος.

Εκτέλεση: uv run pytest tests/test_ergani_credentials.py
"""

import io
import json

import _bootstrap  # noqa: F401
import pytest

from misevent.integrations import ergani
from webapp.ergani_credentials import SecretStore

PASSWORD = "Πολύ-μυστικό-42"


def test_secret_store_encrypts_keeps_and_builds_client(tmp_path):
    s = SecretStore(str(tmp_path / "secrets.db"), key_path=tmp_path / "secrets.key")
    assert s.get("company:a")["environment"] == "simulate"
    assert s.client("company:a").simulate

    with pytest.raises(ValueError, match="χρειάζονται"):
        s.set("company:a", "trial", "user", "")
    s.set("company:a", "trial", " user ", PASSWORD)
    assert s.get("company:a") == {**s.get("company:a"), "environment": "trial",
                                  "username": "user", "has_password": True}
    assert PASSWORD.encode() not in (tmp_path / "secrets.db").read_bytes()
    assert PASSWORD.encode("utf-8") not in s.conn.execute(
        "SELECT password FROM ergani_credentials").fetchone()[0]

    s.set("company:a", "production", "user")  # κενός κωδικός = κρατά τον υπάρχοντα
    c = s.client("company:a")
    assert (c.simulate, c.environment, c.username, c.password) == (
        False, "production", "user", PASSWORD)
    assert c.base == "https://eservices.yeka.gr/WebservicesAPI/Api"
    assert s.client("company:b").simulate  # άλλη εταιρεία: τίποτα

    # Άλλο κλειδί (π.χ. χαμένο secrets.key): ΟΧΙ σιωπηλή προσομοίωση
    (tmp_path / "secrets.key").unlink()
    other = SecretStore(str(tmp_path / "secrets.db"), key_path=tmp_path / "secrets.key")
    c = other.client("company:a")
    assert not c.simulate and c.password == ""


def test_ui_admin_only_live_client_and_backup(fresh_system, monkeypatch):
    from _bootstrap import TEST_PASSWORD, ui_client

    from webapp import create_app
    from webapp.system import auth_svc, registry

    app = create_app()
    admin = ui_client(app)
    company_id = next(iter(registry.auth.companies))
    tenant = registry.tenant(company_id)
    assert "Σύνδεση ΕΡΓΑΝΗ" in admin.get("/company/").get_data(as_text=True)

    # Αποθήκευση -> ο client της εταιρείας αλλάζει ΑΜΕΣΩΣ (χωρίς restart)
    r = admin.post("/company/ergani", data={
        "environment": "trial", "username": "erg-user", "password": PASSWORD})
    assert not json.loads(r.headers["HX-Trigger"])["flash"]["err"]
    assert PASSWORD not in r.get_data(as_text=True)  # δεν ξαναεμφανίζεται ποτέ
    assert not tenant.svc.ergani.simulate and tenant.svc.ergani.environment == "trial"
    assert all(PASSWORD not in json.dumps(e) for e in
               registry.db.conn.execute("SELECT payload FROM events").fetchall())

    # Δοκιμή σύνδεσης: μόνο authentication (εδώ ψεύτικο δίκτυο)
    calls = []
    monkeypatch.setattr(ergani.ErganiClient, "_post", lambda self, path, payload, auth=True:
                        calls.append((self.base + path, payload)) or {"accessToken": "T"})
    r = admin.post("/company/ergani/test")
    assert "Επιτυχής σύνδεση" in json.loads(r.headers["HX-Trigger"])["flash"]["msg"]
    assert calls == [("https://trialv2eservices.yeka.gr/WebservicesAPI/Api/Authentication",
                      {"UserName": "erg-user", "Password": PASSWORD, "UserType": "01"})]

    # Μη διαχειριστής: βλέπει την κατάσταση, δεν αλλάζει τίποτα
    uid = auth_svc.register_user("user@example.com", TEST_PASSWORD)
    auth_svc.grant_company(uid, company_id)
    user = app.test_client()
    user.post("/login", data={"email": "user@example.com", "password": TEST_PASSWORD})
    page = user.get("/company/").get_data(as_text=True)
    assert "Δοκιμαστικό ΕΡΓΑΝΗ" in page and "ergani/test" not in page
    assert user.post("/company/ergani", data={"environment": "simulate"}).status_code == 403
    assert user.post("/company/ergani/test").status_code == 403

    # Backup -> αλλαγή -> restore: οι κωδικοί επανέρχονται ΚΑΙ αποκρυπτογραφούνται
    backup = admin.get("/backup/download").data
    registry.set_ergani(company_id, "simulate", "")
    admin.post("/backup/restore", data={"backup": (io.BytesIO(backup), "b.zip")})
    c = registry.tenant(company_id).svc.ergani
    assert (c.simulate, c.username, c.password) == (False, "erg-user", PASSWORD)
