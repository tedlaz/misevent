"""ΟΡΓΑΝΙΣΜΟΙ (online έκδοση): κάθε πελάτης βλέπει ΜΟΝΟ τα δικά του — εταιρείες,
χρήστες, προσκλήσεις, backup — και κανείς πελάτης δεν αγγίζει τις κοινές
παραμέτρους. Στη desktop έκδοση δεν υπάρχει εγγραφή."""


def test_organizations(fresh_system, monkeypatch):
    import _bootstrap

    from webapp import create_app
    from webapp.system import auth, registry

    app = create_app()
    platform = _bootstrap.ui_client(app)  # ο διαχειριστής της εγκατάστασης (org "")
    platform_co = auth.active_companies("")[0].company_id

    # Desktop: καμία εγγραφή
    assert app.test_client().get("/signup").status_code == 404

    monkeypatch.setenv("MISEVENT_SAAS", "1")

    def signup(email):
        c = app.test_client()
        r = c.post("/signup", data={"email": email, "password": "pw-123456",
                                    "repeat": "pw-123456"})
        assert r.status_code == 302, r.get_data(as_text=True)
        c.post("/welcome/company", data={"name": f"Εταιρεία {email}"})
        return c, auth.by_email(email)

    a, ua = signup("a@example.com")
    b, ub = signup("b@example.com")
    assert ua.org_id and ub.org_id and ua.org_id != ub.org_id
    assert ua.is_admin and not ua.is_platform_admin
    co_a = auth.companies_for(ua)[0].company_id
    co_b = auth.companies_for(ub)[0].company_id
    assert [c.company_id for c in auth.companies_for(ua)] == [co_a]

    # ΙΔΙΟ όνομα εταιρείας επιτρέπεται σε διαφορετικούς οργανισμούς
    a.post("/companies/new", data={"name": "Κοινό όνομα"})
    b.post("/companies/new", data={"name": "Κοινό όνομα"})
    assert len(auth.companies_for(ua)) == len(auth.companies_for(ub)) == 2

    # Ο A δεν επιλέγει / δεν κατεβάζει / δεν σβήνει εταιρεία του B ή της πλατφόρμας
    assert a.post(f"/companies/select/{co_b}").status_code == 403
    assert a.get(f"/backup/company/{co_b}/download").status_code == 404
    assert a.get(f"/backup/company/{platform_co}/download").status_code == 404
    assert a.get(f"/backup/company/{co_a}/download").status_code == 200
    a.post(f"/companies/delete/{co_b}", data={"confirm": registry.display_name(co_b)})
    assert co_b in auth.companies

    # Κοινές παράμετροι & backup συστήματος: μόνο ο διαχειριστής της εγκατάστασης
    assert a.get("/settings/").status_code == 403
    assert a.get("/backup/download").status_code == 403
    assert a.get("/backup/").status_code == 200
    assert platform.get("/backup/download").status_code == 200

    # Χρήστες: ο A βλέπει μόνο τον οργανισμό του και δεν αγγίζει τον B
    page = a.get("/users/").get_data(as_text=True)
    assert "a@example.com" in page and "b@example.com" not in page
    assert "admin@example.com" not in page
    a.post(f"/users/{ub.user_id}/active", data={})
    assert auth.users[ub.user_id].active
    a.post(f"/users/{ub.user_id}/admin", data={})
    assert auth.users[ub.user_id].is_admin
    try:
        registry.auth_svc.set_access(ua.user_id, companies=[co_b], by=ua.user_id)
        raise AssertionError("δόθηκε πρόσβαση σε εταιρεία άλλου οργανισμού")
    except ValueError:
        pass

    # Πρόσκληση από τον A -> ο νέος χρήστης μπαίνει στον οργανισμό του A
    _, token = registry.auth_svc.issue_invitation(ua.user_id)
    guest = registry.auth_svc.accept_invitation(token, "g@example.com", "pw-1", "pw-1")
    assert auth.users[guest].org_id == ua.org_id
    assert auth.companies_for(auth.users[guest]) == []  # χωρίς δικαιώματα ακόμη

    # «≥1 διαχειριστής» ΑΝΑ οργανισμό: ο A είναι ο μόνος του
    try:
        registry.auth_svc.set_admin(ua.user_id, False, by=ua.user_id)
        raise AssertionError("έμεινε οργανισμός χωρίς διαχειριστή")
    except ValueError:
        pass

    # Ανοιχτό redirect μετά τη σύνδεση: αγνοείται
    r = app.test_client().post("/login", data={"email": "a@example.com",
                                               "password": "pw-123456",
                                               "next": "https://evil.example/"})
    assert "evil" not in r.headers["Location"]
