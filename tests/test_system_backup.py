"""Backup / restore ΟΛΟΥ του συστήματος (σελίδα «Σύστημα → Backup»)."""

from io import BytesIO


def test_system_backup_roundtrip(fresh_system):
    from _bootstrap import ui_client

    from webapp import create_app
    from webapp.system import auth, registry

    client = ui_client(create_app())
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
    assert "restored=" in r.headers["Location"]
    assert {c.company_id for c in auth.active_companies()} == before
    assert list((fresh_system / "backups" / "system").glob("before-restore-*.db"))
    # Η εφαρμογή δουλεύει μετά το restore (ο ίδιος χρήστης υπάρχει στο backup).
    assert client.get("/persons/").status_code == 200
