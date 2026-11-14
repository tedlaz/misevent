"""Σκλήρυνση για online χρήση: CSRF (cross-site POST), κλείδωμα σύνδεσης, /health,
και διακόπτης SaaS (χωρίς ταξίδι στον χρόνο)."""


def test_saas_hardening(fresh_system, monkeypatch):
    import _bootstrap

    from webapp import clock, create_app

    app = create_app()
    client = _bootstrap.ui_client(app)

    assert app.test_client().get("/health").status_code == 200

    # CSRF: POST από άλλο site κόβεται — από το ίδιο site / χωρίς browser περνά
    assert client.post("/logout", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/logout", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 302

    # 5 λάθος κωδικοί -> κλείδωμα, ακόμη και με ΣΩΣΤΟ κωδικό
    from webapp.views import auth as auth_view

    auth_view._fails.clear()  # το ui_client μέτρησε ήδη μία αποτυχία (πριν το /setup)
    anon = app.test_client()
    for _ in range(5):
        r = anon.post("/login", data={"email": _bootstrap.TEST_EMAIL, "password": "λάθος"})
        assert r.status_code == 401
    r = anon.post("/login", data={"email": _bootstrap.TEST_EMAIL,
                                  "password": _bootstrap.TEST_PASSWORD})
    assert r.status_code == 429

    # SaaS: το «σήμερα» είναι πάντα το πραγματικό — εκτός αν instance δοκιμών
    auth_view._fails.clear()
    client = _bootstrap.ui_client(app)  # ο προηγούμενος αποσυνδέθηκε (/logout)
    assert 'class="clock' in client.get("/").get_data(as_text=True)  # desktop
    monkeypatch.setenv("MISEVENT_SAAS", "1")
    with app.test_request_context("/?ref=2001-01-01"):
        assert clock.today() == clock.real_today()
    assert 'class="clock' not in client.get("/").get_data(as_text=True)
    # ...εκτός από εταιρεία με τη ρύθμιση «Δοκιμές → Αλλαγή ημερομηνίας»
    page = client.get("/company/").get_data(as_text=True)
    assert 'name="time_travel"' in page
    client.post("/company/settings", data={"time_travel": "1", "digital_card": "1"})
    assert 'class="clock' in client.get("/").get_data(as_text=True)
    assert 'value="2025-01-15"' in client.get("/?ref=2025-01-15").get_data(as_text=True)
    client.post("/company/settings", data={"digital_card": "1"})
    assert 'class="clock' not in client.get("/").get_data(as_text=True)
    monkeypatch.delenv("MISEVENT_SAAS")
    with app.test_request_context("/?ref=2001-01-01"):
        assert clock.today().year == 2001
