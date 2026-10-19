"""Backup / restore ΟΛΟΥ του συστήματος (σελίδα «Σύστημα → Backup»)."""

from io import BytesIO


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
    assert snapshot.startswith(b"SQLite format 3")

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
    bad.deserialize(snapshot)
    bad.execute("UPDATE events SET event_type = 'Άγνωστο' WHERE store = 'system'")
    bad.commit()
    r = client.post("/backup/restore",
                    data={"backup": (BytesIO(bad.serialize()), "bad.db")})
    assert "error=" in r.headers["Location"]
    assert extra in {c.company_id for c in auth.active_companies()}

    r = client.post("/backup/restore",
                    data={"backup": (BytesIO(snapshot), "b.db")})
    assert "/login?restored=1" in r.headers["Location"]
    assert {c.company_id for c in auth.active_companies()} == before
    assert list((fresh_system / "backups" / "system").glob("before-restore-*.db"))
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
