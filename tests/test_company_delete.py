"""
ΟΡΙΣΤΙΚΗ ΔΙΑΓΡΑΦΗ ΕΤΑΙΡΕΙΑΣ — από ΠΑΝΤΟΥ, χωρίς να αγγίξει τις άλλες.

Ελέγχει ότι: μόνο διαχειριστής· χωρίς σωστή πληκτρολόγηση της επωνυμίας
τίποτα δεν γίνεται· σβήνονται events, γραμμή `stores`, έγγραφα, κωδικοί
ΕΡΓΑΝΗ, safety backups και η εγγραφή στο μητρώο· η άλλη εταιρεία και οι
κοινές παράμετροι μένουν ανέπαφες· η τελευταία δεν διαγράφεται· μια
αποσυρμένη επαναφέρεται ή διαγράφεται.
"""


def test_company_purge(fresh_system):
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from misevent.persistence.database import company_store  # noqa: E402
    from webapp import create_app  # noqa: E402
    from webapp.system import auth, registry  # noqa: E402

    app = create_app()
    admin = app.test_client()
    admin.post("/setup", data={"email": "boss@example.com", "password": "admin-2026",
                               "repeat": "admin-2026"})
    (boss,) = auth.users.values()
    db = registry.db.conn

    a_id = registry.create_company("ΑΛΦΑ ΑΕ", created_by=boss.user_id)
    b_id = registry.create_company("ΒΗΤΑ ΕΠΕ", created_by=boss.user_id)
    b_store = company_store(b_id)
    a_events = registry.tenant(a_id).event_count
    shared_events = registry.shared.event_count()
    registry.tenant(b_id).svc.register_company(
        ame="1234567890", afm="999888771", legal_name="ΒΗΤΑ ΕΤΑΙΡΕΙΑ",
        local_office_code="1150706000", local_office_name="ΤΟΠΙΚΗ Α")
    registry.documents.scope(b_store).put("p1", "foreign_file", "x.pdf", b"%PDF")
    registry.set_ergani(b_id, "simulate", "")
    (fresh_system / "backups" / b_id).mkdir(parents=True)
    (fresh_system / "backups" / b_id / "before-restore-x.json").write_text("{}")
    admin.post(f"/companies/select/{b_id}")

    # λάθος επιβεβαίωση: τίποτα
    r = admin.post(f"/companies/delete/{b_id}", data={"confirm": "ΒΗΤΑ"})
    assert r.status_code == 400 and b_id in auth.companies

    # απλός χρήστης: 403
    svc = registry.auth_svc
    svc.register_user("user@example.com", "user-pass-1", password_repeat="user-pass-1")
    user = auth.by_email("user@example.com")
    svc.set_access(user.user_id, all_companies=True)
    guest = app.test_client()
    guest.post("/login", data={"email": "user@example.com", "password": "user-pass-1"})
    guest.post(f"/companies/select/{b_id}")
    assert guest.post(f"/companies/delete/{b_id}",
                      data={"confirm": "ΒΗΤΑ ΕΤΑΙΡΕΙΑ"}).status_code == 403

    # σωστή επιβεβαίωση (η επωνυμία): σβήνονται ΟΛΑ
    r = admin.post(f"/companies/delete/{b_id}", data={"confirm": "ΒΗΤΑ ΕΤΑΙΡΕΙΑ"})
    assert r.status_code == 302 and b_id not in auth.companies
    assert not db.execute("SELECT 1 FROM stores WHERE name = ?", (b_store,)).fetchone()
    assert not db.execute(
        "SELECT 1 FROM events WHERE aggregate_id = ? OR payload LIKE ?",
        (b_id, f'%"company_id":"{b_id}"%')).fetchone()
    assert not registry.documents.conn.execute(
        "SELECT 1 FROM documents WHERE store = ?", (b_store,)).fetchone()
    assert not registry.secrets.conn.execute(
        "SELECT 1 FROM ergani_credentials WHERE store = ?", (b_store,)).fetchone()
    assert not (fresh_system / "backups" / b_id).exists()
    assert b_id not in registry._tenants

    # οι άλλοι ανέπαφοι — και η κοινή παράμετρος δεν φτάνει στη σβησμένη
    assert registry.tenant(a_id).event_count == a_events
    assert registry.shared.event_count() == shared_events
    assert len(registry.shared._subscribers) == 2 * len(registry._tenants)

    # ανοιχτό session πάνω στη σβησμένη: ο guard αλλάζει εταιρεία
    assert guest.get("/persons/").status_code == 200
    with guest.session_transaction() as s:
        assert s["company_id"] != b_id

    # η τελευταία ενεργή δεν διαγράφεται
    for c in auth.active_companies():
        if c.company_id != a_id:
            svc.archive_company(c.company_id)
    r = admin.post(f"/companies/delete/{a_id}", data={"confirm": "ΑΛΦΑ ΑΕ"})
    assert r.status_code == 400 and a_id in auth.companies

    # αποσυρμένη: επαναφέρεται, και διαγράφεται οριστικά
    (archived,) = [c for c in auth.companies.values() if c.archived]
    assert admin.post(f"/companies/unarchive/{archived.company_id}").status_code == 302
    assert not auth.companies[archived.company_id].archived
    svc.archive_company(archived.company_id)
    r = admin.post(f"/companies/delete/{archived.company_id}",
                   data={"confirm": archived.name})
    assert r.status_code == 302 and archived.company_id not in auth.companies
