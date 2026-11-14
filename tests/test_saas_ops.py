"""Online λειτουργίες: email (εγγραφή με επιβεβαίωση, επαναφορά, πρόσκληση),
όρια πλάνου ανά οργανισμό, οριστική διαγραφή πελάτη (GDPR)."""

import re


def test_saas_ops(fresh_system, monkeypatch):
    import _bootstrap

    from webapp import create_app, mailer
    from webapp.labels import nav_for
    from webapp.system import auth, registry

    app = create_app()
    platform = _bootstrap.ui_client(app)
    assert "orgs.index" not in [i["endpoint"] for i in nav_for(True, "")]  # desktop

    monkeypatch.setenv("MISEVENT_SAAS", "1")
    monkeypatch.setenv("MISEVENT_SMTP_HOST", "smtp.test")
    outbox = []
    monkeypatch.setattr(mailer, "send", lambda to, subj, body: outbox.append((to, body)) or True)

    def link(kind):
        return re.search(rf"(/{kind}/[\w-]+)", outbox[-1][1]).group(1)

    # Εγγραφή ΜΕ επιβεβαίωση: χωρίς κωδικό στη φόρμα — ο σύνδεσμος στο email
    c = app.test_client()
    r = c.post("/signup", data={"email": "a@example.com"})
    assert r.status_code == 200 and outbox[-1][0] == "a@example.com"
    ua = auth.by_email("a@example.com")
    assert c.post("/login", data={"email": "a@example.com", "password": "x"}).status_code == 401
    c.post(link("reset"), data={"password": "pw-A-1", "repeat": "pw-A-1"})
    assert c.post("/login", data={"email": "a@example.com",
                                  "password": "pw-A-1"}).status_code == 302
    c.post("/welcome/company", data={"name": "Α1"})

    # Ξέχασα τον κωδικό: self-service — ίδια απάντηση για άγνωστο email, χωρίς mail
    n = len(outbox)
    app.test_client().post("/forgot", data={"email": "nobody@example.com"})
    assert len(outbox) == n
    app.test_client().post("/forgot", data={"email": "a@example.com"})
    assert len(outbox) == n + 1 and "/reset/" in outbox[-1][1]

    # Πρόσκληση με email
    c.post("/users/invite", data={"email": "guest@example.com"})
    assert outbox[-1][0] == "guest@example.com" and "/invite/" in outbox[-1][1]

    # Όρια: μόνο ο διαχειριστής του συστήματος
    assert c.get("/organizations/").status_code == 403
    assert "a@example.com" in platform.get("/organizations/").get_data(as_text=True)
    platform.post(f"/organizations/{ua.org_id}/limits",
                  data={"max_companies": "1", "max_employees": "2"})
    assert auth.limits[ua.org_id] == {"max_companies": 1, "max_employees": 2}
    c.post("/companies/new", data={"name": "Α2"})
    assert len(auth.companies_for(ua)) == 1  # όριο εταιρειών

    platform.post(f"/organizations/{ua.org_id}/limits", data={"max_companies": "0",
                                                              "max_employees": "2"})
    r = c.post("/companies/demo")  # η DEMO προσλαμβάνει 5 -> σταματά στο όριο
    assert r.status_code == 400 and "έως 2" in r.get_data(as_text=True)
    assert registry.usage(ua.org_id)["employees"] <= 2

    # Διαγραφή πελάτη: λάθος επιβεβαίωση -> τίποτα· σωστή -> τα πάντα του φεύγουν
    companies = [cid for cid, ref in auth.companies.items() if ref.org_id == ua.org_id]
    platform.post(f"/organizations/{ua.org_id}/delete", data={"confirm": "λάθος"})
    assert auth.by_email("a@example.com")
    platform.post(f"/organizations/{ua.org_id}/delete", data={"confirm": "a@example.com"})
    assert auth.by_email("a@example.com") is None
    assert not set(companies) & set(auth.companies)
    assert ua.org_id not in auth.orgs() and ua.org_id not in auth.limits
    assert all(registry.db.store(f"company:{cid}").event_count() == 0 for cid in companies)
    # ο οργανισμός της εγκατάστασης δεν διαγράφεται
    assert platform.post("/organizations//delete").status_code in (404, 405)
