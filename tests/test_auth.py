"""
ΧΡΗΣΤΕΣ & ΔΙΚΑΙΩΜΑΤΑ — μέσα από το ΙΔΙΟ το UI (test client).

  • bootstrap admin@admin.app με ΠΡΟΣΩΡΙΝΟ κωδικό → υποχρεωτική αλλαγή
  • πρόσκληση με token → ο προσκεκλημένος ορίζει email + κωδικό
  • χωρίς δικαιώματα δεν βλέπει καμία εταιρεία· ο admin τα δίνει
  • πρόσβαση ΜΟΝΟ στις εταιρείες που του δόθηκαν
  • δικαίωμα δημιουργίας δικών του εταιρειών
  • προαγωγή σε διαχειριστή
  • αίτημα + επαναφορά κωδικού με σύνδεσμο

Τρέξιμο:  uv run tests/test_auth.py
"""


def test_auth(fresh_system):
    import re

    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    # Ο ΠΡΟΣΩΡΙΝΟΣ φάκελος δεδομένων (χρήστες/εταιρείες) στήνεται από το
    # `_bootstrap` — εδώ απλώς φορτώνουμε το UI πάνω σε καθαρό σύστημα.
    from webapp import create_app  # noqa: E402
    from webapp.system import auth, registry  # noqa: E402

    fails = []


    def check(desc, cond):
        print(("OK   " if cond else "ΛΑΘΟΣ ") + desc)
        if not cond:
            fails.append(desc)


    def token_from(body: str, kind: str) -> str:
        """Το ΩΜΟ token φαίνεται ΜΙΑ φορά, μέσα στον σύνδεσμο της απάντησης."""
        m = re.search(rf"/{kind}/([A-Za-z0-9_\-]+)", body)
        return m.group(1) if m else ""


    def login(client, email, password):
        return client.post("/login", data={"email": email, "password": password})


    app = create_app()
    admin = app.test_client()

    # -- 1. BOOTSTRAP + ΥΠΟΧΡΕΩΤΙΚΗ ΑΛΛΑΓΗ ΚΩΔΙΚΟΥ --------------------
    r = login(admin, "admin@admin.app", "λάθος")
    check("λάθος κωδικός δεν συνδέει", r.status_code == 401)

    r = login(admin, "admin@admin.app", "changeme")
    check("ο αρχικός admin συνδέεται", r.headers.get("Location") == "/password")
    r = admin.get("/persons/")
    check("με προσωρινό κωδικό ΚΑΜΙΑ σελίδα δεν ανοίγει",
          r.status_code == 302 and r.headers["Location"] == "/password")
    r = admin.post("/password", data={"current_password": "changeme",
                                      "new_password": "changeme", "repeat": "changeme"})
    check("ο νέος κωδικός δεν επιτρέπεται ίδιος με τον παλιό", r.status_code == 400)
    r = admin.post("/password", data={"current_password": "changeme",
                                      "new_password": "admin-2026", "repeat": "admin-2026"})
    check("η αλλαγή κωδικού ξεκλειδώνει την εφαρμογή", r.headers.get("Location") == "/")
    check("η εφαρμογή ανοίγει", admin.get("/persons/").status_code == 200)

    # -- 2. ΔΥΟ ΕΤΑΙΡΕΙΕΣ ----------------------------------------------
    a_id = registry.create_company("ΑΛΦΑ ΑΕ")
    b_id = registry.create_company("ΒΗΤΑ ΕΠΕ")
    r = admin.get("/companies/")
    body = r.get_data(as_text=True)
    check("ο admin βλέπει όλες τις εταιρείες",
          "ΑΛΦΑ ΑΕ" in body and "ΒΗΤΑ ΕΠΕ" in body)
    check("και μπορεί να ενεργοποιήσει όποια θέλει",
          admin.post(f"/companies/select/{b_id}").status_code == 302)

    # -- 3. ΠΡΟΣΚΛΗΣΗ ---------------------------------------------------
    r = admin.post("/users/invite", data={"note": "Λογιστήριο"})
    invite_token = token_from(r.get_data(as_text=True), "invite")
    check("η πρόσκληση δίνει σύνδεσμο με token", bool(invite_token))

    guest = app.test_client()
    check("ο σύνδεσμος πρόσκλησης ανοίγει χωρίς σύνδεση",
          guest.get(f"/invite/{invite_token}").status_code == 200)
    r = guest.post(f"/invite/{invite_token}",
                   data={"email": "kostas@example.com", "password": "kostas-123",
                         "repeat": "kostas-123"})
    check("ο προσκεκλημένος ορίζει ΜΟΝΟΣ ΤΟΥ email/κωδικό",
          r.headers.get("Location") == "/companies/")
    check("το token δεν ξαναχρησιμοποιείται",
          guest.get(f"/invite/{invite_token}").status_code == 400)

    guest_user = auth.by_email("kostas@example.com")
    check("ο νέος χρήστης δεν είναι διαχειριστής", not guest_user.is_admin)
    check("και ξεκινά ΧΩΡΙΣ καμία εταιρεία",
          not auth.companies_for(guest_user))
    body = guest.get("/companies/").get_data(as_text=True)
    check("βλέπει ότι εκκρεμούν δικαιώματα", "Καμία εταιρεία" in body)
    check("και δεν φτάνει σε δεδομένα εταιρείας",
          guest.get("/persons/").headers["Location"].endswith("/companies/"))
    check("ούτε στη διαχείριση χρηστών", guest.get("/users/").status_code == 403)
    check("ούτε στις ΚΟΙΝΕΣ παραμέτρους", guest.get("/settings/").status_code == 403)

    # -- 4. ΔΙΚΑΙΩΜΑΤΑ ΣΕ ΣΥΓΚΕΚΡΙΜΕΝΗ ΕΤΑΙΡΕΙΑ -----------------------
    admin.post(f"/users/{guest_user.user_id}/access",
               data={"scope": "some", "companies": [a_id]})
    check("ο admin δίνει πρόσβαση σε ΜΙΑ εταιρεία",
          auth.users[guest_user.user_id].companies == [a_id])
    check("ο χρήστης μπαίνει πλέον στην εφαρμογή",
          guest.get("/persons/").status_code == 200)
    check("αλλά ΔΕΝ μπορεί να ενεργοποιήσει την άλλη",
          guest.post(f"/companies/select/{b_id}").status_code == 403)
    body = guest.get("/companies/").get_data(as_text=True)
    check("και δεν τη βλέπει καν στη λίστα του",
          "ΑΛΦΑ ΑΕ" in body and "ΒΗΤΑ ΕΠΕ" not in body)

    # -- 5. ΔΗΜΙΟΥΡΓΙΑ ΔΙΚΩΝ ΤΟΥ ΕΤΑΙΡΕΙΩΝ ----------------------------
    check("χωρίς δικαίωμα δεν δημιουργεί εταιρεία",
          guest.post("/companies/new", data={"name": "ΔΙΚΗ ΜΟΥ"}).status_code == 403)
    admin.post(f"/users/{guest_user.user_id}/access",
               data={"scope": "some", "companies": [a_id], "can_create": "1"})
    r = guest.post("/companies/new", data={"name": "ΔΙΚΗ ΜΟΥ ΟΕ"})
    check("με δικαίωμα δημιουργεί δική του", r.status_code == 302)
    check("και αποκτά αυτόματα πρόσβαση σε αυτήν",
          len(auth.companies_for(auth.users[guest_user.user_id])) == 2)

    # -- 6. ΠΡΟΣΒΑΣΗ ΣΕ ΟΛΕΣ + ΠΡΟΑΓΩΓΗ ΣΕ ADMIN ----------------------
    admin.post(f"/users/{guest_user.user_id}/access", data={"scope": "all"})
    check("πρόσβαση σε ΟΛΕΣ τις εταιρείες",
          auth.users[guest_user.user_id].all_companies
          and guest.post(f"/companies/select/{b_id}").status_code == 302)

    admin.post(f"/users/{guest_user.user_id}/admin", data={"is_admin": "1"})
    check("ο admin κάνει άλλον χρήστη διαχειριστή",
          auth.users[guest_user.user_id].is_admin)
    check("ο νέος διαχειριστής βλέπει τους χρήστες",
          guest.get("/users/").status_code == 200)

    # -- 7. ΕΠΑΝΑΦΟΡΑ ΚΩΔΙΚΟΥ ------------------------------------------
    anon = app.test_client()
    anon.post("/forgot", data={"email": "kostas@example.com"})
    check("το αίτημα επαναφοράς καταγράφεται",
          "kostas@example.com" in auth.reset_requests)
    body = admin.get("/users/").get_data(as_text=True)
    check("ο admin βλέπει το εκκρεμές αίτημα", "αίτημα επαναφοράς" in body)

    r = admin.post(f"/users/{guest_user.user_id}/reset")
    reset_token = token_from(r.get_data(as_text=True), "reset")
    check("ο admin εκδίδει σύνδεσμο επαναφοράς", bool(reset_token))
    r = anon.post(f"/reset/{reset_token}",
                  data={"password": "neos-kodikos", "repeat": "neos-kodikos"})
    check("ο χρήστης ορίζει νέο κωδικό", r.headers.get("Location") == "/login")
    check("ο σύνδεσμος δεν ξαναχρησιμοποιείται",
          anon.get(f"/reset/{reset_token}").status_code == 400)
    check("το αίτημα έκλεισε", "kostas@example.com" not in auth.reset_requests)

    fresh = app.test_client()
    check("συνδέεται με τον νέο κωδικό",
          login(fresh, "kostas@example.com", "neos-kodikos").headers.get("Location") == "/")
    check("ο παλιός κωδικός δεν δουλεύει πια",
          login(app.test_client(), "kostas@example.com", "kostas-123").status_code == 401)

    # -- 8. ΑΠΕΝΕΡΓΟΠΟΙΗΣΗ ---------------------------------------------
    admin.post(f"/users/{guest_user.user_id}/active")
    check("απενεργοποιημένος λογαριασμός δεν συνδέεται",
          login(app.test_client(), "kostas@example.com", "neos-kodikos").status_code == 401)
    check("και η ανοιχτή του συνεδρία κόβεται",
          fresh.get("/persons/").headers["Location"].startswith("/login"))


    print()
    assert not fails, "ΑΠΟΤΥΧΙΕΣ: " + "; ".join(str(x) for x in fails)
    print("OK - χρήστες, προσκλήσεις, δικαιώματα, επαναφορά κωδικού")
