"""Backup / restore ΟΛΟΥ του συστήματος (σελίδα «Σύστημα → Backup»)."""

import zipfile
from io import BytesIO


def _events_db(backup: bytes) -> bytes:
    """Η βάση events μέσα σε ένα backup .zip."""
    with zipfile.ZipFile(BytesIO(backup)) as z:
        return z.read("misevent.db")


def test_system_backup_roundtrip(fresh_system):
    from _bootstrap import ui_client

    from webapp import create_app
    from webapp.system import auth, registry

    app = create_app()
    client = ui_client(app)
    assert client.get("/backup/").status_code == 200

    before = {c.company_id for c in auth.active_companies()}
    r = client.get("/backup/download")
    assert r.status_code == 200
    snapshot = r.data
    with zipfile.ZipFile(BytesIO(snapshot)) as z:
        assert sorted(z.namelist()) == ["documents.db", "misevent.db",
                                        "secrets.db", "secrets.key"]
    assert _events_db(snapshot).startswith(b"SQLite format 3")

    extra = registry.create_company("Μετά το backup")
    assert extra in {c.company_id for c in auth.active_companies()}

    # Σκουπίδια: απορρίπτονται, τίποτα δεν αλλάζει.
    r = client.post("/backup/restore",
                    data={"backup": (BytesIO(b"not a db"), "x.db")})
    assert "error=" in r.headers["Location"]
    assert extra in {c.company_id for c in auth.active_companies()}

    # Έγκυρη βάση, αλλά τα events δεν ξαναπαίζονται: επιστροφή στην τρέχουσα.
    import sqlite3

    bad = sqlite3.connect(":memory:")
    bad.deserialize(_events_db(snapshot))
    bad.execute("UPDATE events SET event_type = 'Άγνωστο' "
                "WHERE store = (SELECT id FROM stores WHERE name = 'system')")
    bad.commit()
    r = client.post("/backup/restore",
                    data={"backup": (BytesIO(bad.serialize()), "bad.db")})
    assert "error=" in r.headers["Location"]
    assert extra in {c.company_id for c in auth.active_companies()}

    r = client.post("/backup/restore",
                    data={"backup": (BytesIO(snapshot), "b.db")})
    assert "/login?restored=1" in r.headers["Location"]
    assert {c.company_id for c in auth.active_companies()} == before
    assert list((fresh_system / "backups" / "system").glob("before-restore-*.zip"))
    # Μετά το restore: αποσύνδεση -> νέα σύνδεση με τα στοιχεία του backup.
    assert client.get("/persons/").status_code == 302
    client = ui_client(app)
    assert client.get("/persons/").status_code == 200


def test_new_install_wizard_restore(fresh_system):
    """Νέα εγκατάσταση: αρχική ρύθμιση → οδηγός «Ξεκινώντας» → restore →
    σύνδεση με τους χρήστες του backup (ο προσωρινός διαχειριστής χάνεται)."""
    from _bootstrap import TEST_EMAIL, TEST_PASSWORD, ui_client

    from webapp import create_app
    from webapp.system import lock, registry

    app = create_app()
    backup = ui_client(app).get("/backup/download").data  # εγκατάσταση σε χρήση
    blank = None
    with lock:  # «νέα εγκατάσταση»: κανένας χρήστης
        from misevent.persistence.database import Database
        blank = Database().snapshot()
    anon = app.test_client()
    with lock:
        registry.db.conn.execute("DELETE FROM events")
        registry.db.conn.commit()
        registry.reload()
    assert blank and registry.needs_setup()
    assert anon.get("/login").headers["Location"] == "/setup"

    new = app.test_client()
    r = new.post("/setup", data={"email": "new@example.com", "password": "new-2026-xx",
                                 "repeat": "new-2026-xx"})
    assert r.headers["Location"] == "/welcome/"
    page = new.get("/welcome/").get_data(as_text=True)
    assert "Δημιουργία νέας εταιρείας" in page and "Επαναφορά από backup" in page
    assert "Εισαγωγή εταιρείας από ΑΠΔ" in page

    r = new.post("/backup/restore", data={"backup": (BytesIO(backup), "b.db")})
    assert "restored=1" in r.headers["Location"]
    r = anon.post("/login", data={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert r.status_code == 302
    assert anon.post("/login", data={"email": "new@example.com",
                                     "password": "new-2026-xx"}).status_code == 401


def test_new_install_wizard_new_company_reuses_the_empty_space(fresh_system):
    from _bootstrap import ui_client

    from webapp import create_app
    from webapp.system import registry

    app = create_app()
    client = ui_client(app)  # αρχική ρύθμιση
    spaces = len(registry.auth.companies)
    r = client.post("/welcome/company", data={"name": "ΑΛΦΑ ΑΕ"})
    assert r.headers["Location"] == "/company/"
    assert len(registry.auth.companies) == spaces  # όχι δεύτερος, άδειος χώρος
    assert [c.name for c in registry.auth.active_companies()] == ["ΑΛΦΑ ΑΕ"]


def test_safety_backups_page(fresh_system):
    from _bootstrap import ui_client

    from webapp import create_app

    app = create_app()
    client = ui_client(app)
    assert "Κανένα αυτόματο αντίγραφο" in client.get("/backup/").get_data(as_text=True)

    snapshot = client.get("/backup/download").data
    client.post("/backup/restore", data={"backup": (BytesIO(snapshot), "b.db")})
    client = ui_client(app)  # το restore αποσυνδέει

    (safety,) = (fresh_system / "backups" / "system").glob("before-restore-*.zip")
    base = f"/backup/safety/system/{safety.name}"
    assert safety.name in client.get("/backup/").get_data(as_text=True)

    r = client.get(base)
    assert r.status_code == 200 and r.data.startswith(b"PK")

    r = client.post(base + "/restore")
    assert "/login?restored=1" in r.headers["Location"]
    client = ui_client(app)
    # Η επαναφορά κράτησε ΝΕΟ αντίγραφο· το αρχικό μένει.
    assert len(list(safety.parent.glob("before-restore-*.zip"))) == 2

    r = client.post(base + "/delete")
    assert r.status_code == 302 and not safety.exists()
    assert client.get(base).status_code == 404

    # Μόνο αρχεία αντιγράφων, μόνο μέσα στο backups/.
    assert client.get("/backup/safety/system/..%2F..%2Fsecret.key").status_code == 404
    assert client.get("/backup/safety/..%2F..%2Fsecret.key").status_code == 404
    assert client.get("/backup/safety/nope/before-restore-x.db").status_code == 404
    assert client.post("/backup/safety/system/secret.key/delete").status_code == 404


def test_restore_snapshot_remaps_store_ids():
    """Τα ids του `stores` στο backup ΔΕΝ είναι τα δικά μας: η επαναφορά
    πηγαίνει με το όνομα του store, αλλιώς τα events αλλάζουν εταιρεία."""
    from misevent.domain.events import MiscSettingsChanged
    from misevent.persistence.database import SYSTEM_STORE, Database

    src = Database()
    src.store(SYSTEM_STORE).append("MISC", MiscSettingsChanged({"who": "system"}))
    src.store("company:a").append("MISC", MiscSettingsChanged({"who": "a"}))

    dst = Database()  # άλλη σειρά δημιουργίας -> άλλα ids
    a, system = dst.store("company:a"), dst.store(SYSTEM_STORE)
    assert dst.restore_snapshot(src.snapshot()) == 2
    assert [e.fields for _, e in a.load()] == [{"who": "a"}]
    assert [e.fields for _, e in system.load()] == [{"who": "system"}]


def test_company_backup_restore(fresh_system):
    """Backup/restore ΜΙΑΣ εταιρείας: events + έγγραφα, οι άλλες ανέγγιχτες."""
    from _bootstrap import ui_client

    from webapp import create_app
    from webapp.system import registry

    app = create_app()
    client = ui_client(app)
    a = registry.create_company("ΑΛΦΑ")
    b = registry.create_company("ΒΗΤΑ")
    ta, tb = registry.tenant(a), registry.tenant(b)
    ta.svc.register_company(ame="1234567890", afm="999888771", legal_name="ΑΛΦΑ ΑΕ")
    docs_a = registry.documents.scope(ta.store_name)
    docs_b = registry.documents.scope(tb.store_name)
    docs_a.put("P1", "other", "a.pdf", b"%PDF-a")
    docs_b.put("P1", "other", "b.pdf", b"%PDF-b")
    assert "ΑΛΦΑ ΑΕ" in client.get("/backup/").get_data(as_text=True)

    r = client.get(f"/backup/company/{a}/download")
    assert r.status_code == 200
    assert "misevent-%CE%91%CE%9B%CE%A6%CE%91_%CE%91%CE%95-" in r.headers["Content-Disposition"]
    with zipfile.ZipFile(BytesIO(r.data)) as z:
        assert sorted(z.namelist()) == ["documents.db", "events.json"]
    snapshot = r.data
    count = ta.event_count

    # Αλλαγές μετά το backup, σε ΑΛΦΑ και ΒΗΤΑ.
    ta.svc.register_company(ame="1234567890", afm="999888771", legal_name="ΑΛΛΟ ΑΕ")
    docs_a.delete("P1", "other")
    docs_b.put("P2", "other", "b2.pdf", b"%PDF-b2")

    r = client.post(f"/backup/company/{a}/restore",
                    data={"backup": (BytesIO(b"junk"), "x.zip")})
    assert "error=" in r.headers["Location"]
    assert ta.proj.company.legal_name == "ΑΛΛΟ ΑΕ"

    r = client.post(f"/backup/company/{a}/restore",
                    data={"backup": (BytesIO(snapshot), "a.zip")})
    assert r.headers["Location"].endswith(f"restored={a}")
    assert ta.event_count == count and ta.proj.company.legal_name == "ΑΛΦΑ ΑΕ"
    assert docs_a.get("P1", "other")["content"] == b"%PDF-a"
    assert [d["kind"] for d in docs_b.list("P2")] == ["other"]  # η ΒΗΤΑ ανέγγιχτη
    assert client.get("/persons/").status_code == 200  # δεν αποσυνδέει

    # Το αυτόματο αντίγραφο της ΑΛΦΑ επαναφέρεται από τη σελίδα.
    (safety,) = (fresh_system / "backups" / a).glob("before-restore-*.zip")
    r = client.post(f"/backup/safety/{a}/{safety.name}/restore")
    assert ta.proj.company.legal_name == "ΑΛΛΟ ΑΕ"
    assert docs_a.get("P1", "other") is None
    assert client.get("/backup/company/nope/download").status_code == 404


def test_company_restore_as_new(fresh_system):
    """Restore εταιρείας που ΔΕΝ υπάρχει: νέος χώρος με την επωνυμία της."""
    from _bootstrap import ui_client

    from webapp import create_app
    from webapp.system import auth, registry

    app = create_app()
    client = ui_client(app)
    a = registry.create_company("ΑΛΦΑ")
    ta = registry.tenant(a)
    ta.svc.register_company(ame="1234567890", afm="999888771", legal_name="ΑΛΦΑ ΑΕ")
    registry.documents.scope(ta.store_name).put("P1", "other", "a.pdf", b"%PDF-a")
    snapshot = client.get(f"/backup/company/{a}/download").data
    count = ta.event_count
    registry.purge_company(a)
    companies = {c.company_id for c in auth.active_companies()}

    r = client.post("/backup/company/new", data={"backup": (BytesIO(b"junk"), "x.zip")})
    assert "error=" in r.headers["Location"]
    assert {c.company_id for c in auth.active_companies()} == companies  # τίποτα δεν έμεινε

    r = client.post("/backup/company/new", data={"backup": (BytesIO(snapshot), "a.zip")})
    (new,) = {c.company_id for c in auth.active_companies()} - companies
    assert r.headers["Location"].endswith(f"restored={new}")
    tn = registry.tenant(new)
    assert tn.event_count == count and auth.companies[new].name == "ΑΛΦΑ ΑΕ"
    assert registry.documents.scope(tn.store_name).get("P1", "other")["content"] == b"%PDF-a"
