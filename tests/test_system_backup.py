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


def test_first_run_restore_from_login_page(fresh_system):
    from _bootstrap import TEST_PASSWORD, ui_client

    from misevent.app.auth import BOOTSTRAP_EMAIL
    from webapp import create_app
    from webapp.system import lock, registry

    app = create_app()
    anon = app.test_client()
    assert "Επαναφορά από backup" in anon.get("/login").get_data(as_text=True)
    blank = registry.db.snapshot()  # ό,τι έχει μια νέα εγκατάσταση

    # Εγκατάσταση σε χρήση (αλλαγμένος κωδικός) -> backup της.
    backup = ui_client(app).get("/backup/download").data

    # Με δεδομένα: ούτε φόρμα ούτε restore χωρίς σύνδεση.
    anon = app.test_client()
    assert "Επαναφορά από backup" not in anon.get("/login").get_data(as_text=True)
    r = anon.post("/backup/first-run", data={"backup": (BytesIO(backup), "b.db")})
    assert "restore_error=" in r.headers["Location"]

    # «Νέα εγκατάσταση» -> restore από τη σελίδα σύνδεσης -> σύνδεση με τον
    # κωδικό του backup.
    with lock:
        registry.db.restore_snapshot(blank)
        registry.reload()
    r = anon.post("/backup/first-run", data={"backup": (BytesIO(backup), "b.db")})
    assert "restored=1" in r.headers["Location"]
    r = anon.post("/login", data={"email": BOOTSTRAP_EMAIL, "password": TEST_PASSWORD})
    assert r.status_code == 302 and r.headers["Location"] != "/password"


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
