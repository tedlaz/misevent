"""Smoke test του web UI: όλες οι σελίδες + ροή επαναπρόσληψης.
Αυτόνομο — στήνει τα δεδομένα μέσα από το ίδιο το UI (POST /seed)."""


def test_webapp(fresh_system):
    import io
    import json
    import os
    import re
    import zipfile
    from calendar import monthrange
    from datetime import date as _date

    # ΠΟΛΛΕΣ ΕΤΑΙΡΕΙΕΣ: χρήστες/εταιρείες πάνε σε ΠΡΟΣΩΡΙΝΟ φάκελο δεδομένων —
    # το στήνει το `_bootstrap` για ΟΛΑ τα tests (μία πηγή αλήθειας).
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from misevent.integrations import apd_files, fmy_files
    from webapp import clock, create_app
    from webapp import queries as q
    from webapp.system import proj, svc

    app = create_app()
    fails = []

    # ΚΑΘΕ σελίδα απαιτεί συνδεδεμένο χρήστη. ΜΙΑ πηγή αλήθειας για τη σύνδεση:
    # ο `_bootstrap.ui_client` — είναι idempotent, οπότε δουλεύει είτε ο
    # διαχειριστής των tests έχει ήδη δημιουργηθεί από άλλο test αρχείο της
    # ίδιας διεργασίας είτε όχι (τότε κάνει την «Αρχική ρύθμιση»).
    c = _bootstrap.ui_client(app)


    _ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
    _TAGS = re.compile(r"<[^>]*>", re.S)
    _SCRIPTS = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
    _VISIBLE_ATTR = re.compile(r'(?:title|aria-label|placeholder)="([^"]*)"')


    def _iso_dates(body: str) -> list:
        """ΟΡΑΤΕΣ ημερομηνίες σε μορφή ISO (κείμενο + tooltips) — ΑΠΑΓΟΡΕΥΟΝΤΑΙ:
        παντού γράφουμε ΗΗ/ΜΜ/ΕΕΕΕ (ARCH_REF.md §9). Τα `value` των `<input
        type="date">`, τα `data-*` και τα query params ΕΙΝΑΙ ISO από προδιαγραφή,
        γι' αυτό αφαιρούνται τα tags πριν τον έλεγχο."""
        text = _TAGS.sub(" ", _SCRIPTS.sub(" ", body))
        hits = set(_ISO_DATE.findall(text))
        for value in _VISIBLE_ATTR.findall(body):
            hits.update(_ISO_DATE.findall(value))
        return sorted(hits)


    def check(desc, resp, must_contain=None, iso_ok=False):
        """`iso_ok=True` ΜΟΝΟ όπου εμφανίζεται ΩΜΟ payload ΕΡΓΑΝΗ (το έγγραφο που
        στάλθηκε — δεν το ξαναμορφοποιούμε)."""
        ok = resp.status_code == 200
        body = resp.get_data(as_text=True)
        if ok and must_contain and must_contain not in body:
            ok, desc = False, f"{desc} (λείπει «{must_contain}»)"
        if ok and not iso_ok and "html" in resp.headers.get("Content-Type", ""):
            iso = _iso_dates(body)
            if iso:
                ok, desc = False, f"{desc} (ISO ημερομηνίες: {iso[:3]})"
        print(f"  {'OK  ' if ok else 'FAIL'} {resp.status_code} {desc}")
        if not ok:
            fails.append(desc)
        return body


    # Σταθερό «σήμερα»: το seed προσλαμβάνει 45 ημέρες πριν την 1η του μήνα
    # (εδώ 17/06), ενώ τα σενάρια έχουν σταθερές ημερομηνίες 2026 — π.χ. η
    # λήξη 30/06 πρέπει να πέφτει στον τρέχοντα μήνα μισθοδοσίας.
    c.set_cookie(clock.COOKIE, "2026-08-26")

    print("=== Seed μέσω UI ===")
    r = _bootstrap.seed(c)
    print(
        f"  {'OK  ' if r.status_code == 200 else 'FAIL'} POST /seed -> "
        f"{len(proj.persons)} πρόσωπα, {len(proj.employments)} απασχολήσεις"
    )

    print("\n=== Σελίδες (GET) ===")
    check("/            Επισκόπηση", c.get("/"), "Επισκόπηση")
    check("/  υποχρεώσεις (κάρτα)", c.get("/"), 'data-fold="obligations"')
    check("/  υποχρεώσεις: μετρητές", c.get("/"), 'class="ob-sum"')
    from types import SimpleNamespace as _NS
    _ob = lambda d, f=False: _NS(due_date=_date(2026, 11, d), fulfilled=f)  # noqa: E731
    _r = _date(2026, 11, 10)
    ok = ([q.due_countdown(_ob(d), _r) for d in (13, 11, 10, 7)]
          == ["σε 3 ημ.", "λήγει αύριο", "λήγει σήμερα", "εκπρόθεσμη 3 ημ."]
          and q.due_countdown(_ob(1, True), _r) == "✓ έγινε")
    print(f"  {'OK  ' if ok else 'FAIL'} αντίστροφη μέτρηση προθεσμίας")
    if not ok:
        fails.append("λάθος αντίστροφη μέτρηση στο pill υποχρεώσεων")
    # Modal: τα προαπαιτούμενα ανοίγουν το modal ΕΚΕΙΝΗΣ της υποχρέωσης
    _with_dep = next((d for d in (q.detail_payload(i, _r) for i in proj.obligations)
                      if d["deps"]), None)
    if _with_dep:
        body = check("modal υποχρέωσης με προαπαιτούμενο",
                     c.get(f"/obligations/modal?id={_with_dep['id']}"))
        if f"/obligations/modal?id={_with_dep['deps'][0]['id']}" not in body:
            fails.append("το προαπαιτούμενο στο modal δεν είναι σύνδεσμος")
    r = c.get("/obligations/?view=all")
    assert r.status_code == 302 and r.headers["Location"].endswith("/?view=all")
    check("/persons     Εργαζόμενοι", c.get("/persons/"), "Εργαζόμενοι")
    check("/persons     ταξινόμηση", c.get("/persons/"), "data-sortable")
    check("/  Ζωντανά (κάρτα)", c.get("/"), 'id="lboard"')
    check("/live/rows   γραμμές (htmx)", c.get("/live/rows"))
    check("/live/rows   αργία 28/10", c.get("/live/rows?base=2026-10-27&days=3"),
          "28η Οκτωβρίου")
    # Αργία χωρίς παρουσία = κλειστά: ούτε πρόγραμμα «τώρα» (άρα ούτε
    # ασυμφωνία με κάρτα), μπάρες σημειωμένες ως αργία
    hb = q.live_board(_date(2026, 10, 28))
    if not any(r["hol"] and not r["ref"]["segs"] for r in hb["rows"]) or not any(
        s["hol"] for r in hb["rows"] for s in r["segs"]
    ):
        fails.append("Ζωντανά: η αργία χωρίς παρουσία δεν εμφανίζεται κλειστή")
    check("/ergani      εκκρεμείς υποβολές", c.get("/ergani/"), "Εκκρεμείς υποβολές")
    # Ιστορικό: και οι εβδομαδιαίες υποβολές (π.χ. το αρχικό πρόγραμμα)
    check("/ergani      ιστορικό εβδομαδιαίων", c.get("/ergani/"), "Εβδομαδιαίο")
    check("/work-schedules/day Ημέρα",
          c.get("/work-schedules/day?date=2026-07-14"), "Πρόγραμμα ημέρας")
    check("/attendance  Παρουσίες", c.get("/attendance/"), "Παρουσίες")
    check("/payroll     Μισθοδοσία", c.get("/payroll/"), "Σύνολα ανά περίοδο")
    check("/payments    Πληρωμές", c.get("/payments/"), "Πληρωμές περιόδου")
    check("/eapd        Αρχεία ΑΠΔ", c.get("/eapd/"), "Παραχθέντα αρχεία")
    check("/fmy         Αρχεία ΦΜΥ", c.get("/fmy/"), "Παραχθέντα αρχεία")
    check("/eapd/app    eΑΠΔ viewer", c.get("/eapd/app/"), "Διάβασε αρχείο ΑΠΔ")
    check("/eapd/app/*  assets", c.get("/eapd/app/bridge.js"), "parseAPDText")
    check("/eapd/app    ΤΕΚΑ mode", c.get("/eapd/app/fund.js"), "apdSetFund")
    # Το /eapd/raw παράγει ΜΟΝΟ περιόδους που προέρχονται από APDFileGenerated
    _r = c.get("/eapd/raw?fund=efka&year=1999&month=1")
    print(f"  {'OK  ' if _r.status_code == 404 else 'FAIL'} {_r.status_code} "
          "/eapd/raw άγνωστη περίοδος -> 404")
    if _r.status_code != 404:
        fails.append("το /eapd/raw σέρβιρε περίοδο εκτός των events")
    check("/ergani      ΕΡΓΑΝΗ", c.get("/ergani/"), "Ιστορικό υποβολών",
          iso_ok=True)  # ωμό JSON payload ΕΡΓΑΝΗ
    # Ημερομηνία υποβολής: από το occurred_at κάθε υποβολής (σε τοπική ώρα)
    import re as _re

    from webapp.system import proj as _proj
    _erg = c.get("/ergani/").get_data(as_text=True)
    if _proj.ergani_submissions and not _re.search(
            r"<td[^>]*>\d{2}/\d{2}/\d{4} \d{2}:\d{2}</td>", _erg):
        fails.append("/ergani: λείπει η ημερομηνία υποβολής")
    check("/company     Εταιρεία", c.get("/company/"), "Στοιχεία εργοδότη")
    # Σύστημα δήλωσης αλλαγών ωραρίου (ανά εταιρεία, από τον επόμενο μήνα)
    check("/company σύστημα δήλωσης", c.post("/company/schedule-system", data={
        "month": "2026-09-01", "system": "retro"}), "09/2026")
    if proj.schedule_system_on(_date(2026, 9, 1)) != "retro":
        fails.append("Σύστημα δήλωσης: δεν καταγράφηκε το απολογιστικό")
    check("/settings    Παράμετροι", c.get("/settings/"), "Αλυσίδα υποχρεώσεων")
    check("/audit       Audit log", c.get("/audit/", headers={"HX-Request": "true"}), "Event log",
          iso_ok=True)  # ωμό JSON payload ΕΡΓΑΝΗ στα events

    print("\n=== Lookup Τοπικής Δ/νσης e-ΕΦΚΑ (ΑΠΔ No 3/No 4) ===")
    from misevent.rules.constraints import norm_name  # noqa: E402
    from webapp import osyk  # noqa: E402

    _n = len(osyk.topiks())
    print(f"  {'OK  ' if _n > 100 else 'FAIL'} μητρώο ΤΟΠΙΚ από το topic.js "
          f"του viewer ({_n} εγγραφές)")
    if _n <= 100:
        fails.append("δεν φορτώθηκε το μητρώο ΤΟΠΙΚ (webapp/eapd/topic.js)")
    # Αναζήτηση ΧΩΡΙΣ τόνους, όπως το lookup του eΑΠΔ viewer
    _hits = osyk.search_topiks("λαρισ")
    ok = _hits and all("ΛΑΡΙΣ" in norm_name(h["per"]) for h in _hits)
    print(f"  {'OK  ' if ok else 'FAIL'} «λαρισ» (πεζά, χωρίς τόνο) -> "
          f"{len(_hits)} τοπικές διευθύνσεις")
    if not ok:
        fails.append("η αναζήτηση ΤΟΠΙΚ δεν αγνοεί τόνους/πεζά-κεφαλαία")
    # Η επιλογή γυρίζει ΚΑΙ ΤΑ ΔΥΟ πεδία ως OOB (κλείνει το modal με κενό υπόλοιπο)
    _code = _hits[0]["code"] if _hits else "1151002000"
    body = check("modal αναζήτησης", c.get("/company/topik/modal"), "topik-results")
    body = check("επιλογή γεμίζει No3+No4",
                 c.get(f"/company/topik/pick?code={_code}"), 'hx-swap-oob="true"')
    ok = f'value="{_code}"' in body and "local_office_name" in body
    print(f"  {'OK  ' if ok else 'FAIL'} το OOB φέρνει τον κωδικό {_code} "
          "και την ονομασία")
    if not ok:
        fails.append("η επιλογή ΤΟΠΙΚ δεν συμπληρώνει και τα δύο πεδία")
    # Η ονομασία γράφεται όπως θα μπει στο αρχείο ΑΠΔ: ΚΕΦΑΛΑΙΑ, χωρίς τόνους
    _per = next(h["per"] for h in _hits) if _hits else ""
    if _per and _per.upper() != _per and norm_name(_per) not in body:
        fails.append("η ονομασία ΤΟΠΙΚ δεν κανονικοποιήθηκε σε κεφαλαία/άτονα")
    else:
        print("  OK   ονομασία σε ΚΕΦΑΛΑΙΑ χωρίς τόνους (όπως στην ΑΠΔ)")

    print("\n=== Ασφαλιστικοί συνδυασμοί: Μητρώο ΚΠΚ + Ειδικότητες + Πρόσληψη ===")
    # ① Το μητρώο ΚΠΚ είναι ΘΕΣΜΙΚΟ (κοινό για όλες τις εταιρείες), οπότε ζει
    #    στις Παραμέτρους — και δείχνει ΤΑΥΤΟΤΗΤΑ (ονομασία/βαθμίδα), όχι μόνο
    #    ποσοστά. Στη σελίδα «Εταιρεία» ΔΕΝ πρέπει να εμφανίζεται πια.
    body = check("/settings πίνακας ΚΠΚ", c.get("/settings/"), "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ")
    if "Κύρια + Επικουρική" not in body:
        fails.append("ο πίνακας ΚΠΚ δεν δείχνει βαθμίδα ασφάλισης")
    if "Μητρώο ΚΠΚ" in check("/company χωρίς ΚΠΚ", c.get("/company/")):
        fails.append("το Μητρώο ΚΠΚ εμφανίζεται ακόμη στη σελίδα Εταιρεία")
    else:
        print("  OK   το Μητρώο ΚΠΚ έφυγε από τη σελίδα Εταιρεία")
    # Το ΤΕΚΑ επιτρέπει ΜΟΝΟ επικουρική -> η βαθμίδα κλειδώνει στο modal
    check("modal ΚΠΚ ΤΕΚΑ (κλειδωμένη βαθμίδα)",
          c.get("/settings/kpk/modal?fund=teka&kpk=4169"), "επιτρέπει μόνο")
    # ΧΡΟΝΟΣΕΙΡΑ: δύο γραμμές ισχύος σε μία υποβολή
    r = c.post("/settings/kpk", data={
        "carrier": "efka", "kpk": "0026", "name": "Πρόνοια παλαιών",
        "tier": "welfare",
        "rate0_valid_from": "2013-12-01", "rate0_employee": "4",
        "rate0_employer": "4", "rate0_components": "welfare=4/4",
        "rate1_valid_from": "2026-01-01", "rate1_employee": "5",
        "rate1_employer": "5", "rate1_components": "welfare=5/5",
    })
    check("νέο ΚΠΚ πρόνοιας με 2 γραμμές ισχύος", r, "Πρόνοια παλαιών")
    _flash = json.loads(r.headers.get("HX-Trigger", "{}")).get("flash", {})
    ok = "2 γραμμές ισχύος" in _flash.get("msg", "")
    print(f"  {'OK  ' if ok else 'FAIL'} καταχωρήθηκε ολόκληρη η χρονοσειρά")
    if not ok:
        fails.append("η χρονοσειρά ποσοστών δεν αποθηκεύτηκε ολόκληρη")

    # ② Ειδικότητα με ΡΟΛΟΥΣ + προαιρετική διαφοροποιημένη πρόνοια
    check("modal ειδικότητας (ρόλοι)",
          c.get("/company/specialties/modal?name=ΠΩΛΗΤΗΣ"), "PRON_NEW_kpk")
    r = c.post("/company/specialties", data={
        "name": "ΠΩΛΗΤΗΣ", "epik_carriers": ["efka", "teka"],
        # ΕΝΑ ΚΑΔ/ΕΙΔ κύριας για τις δύο εναλλακτικές· το ΤΕΚΑ είναι σταθερό
        "main_kad": "0141", "main_code": "411200",
        "MAIN_EPIK_kpk": "0101", "MAIN_kpk": "0103",
        "pron_enabled": "on", "pron_name": "ΤΑΠΙΤ", "pron_differentiated": "on",
        # Πρόνοια: ΕΝΑ ΚΑΔ, ΕΙΔ + ΚΠΚ ανά νέους/παλαιούς (Ο.Σ.Υ.Κ.)
        "pron_kad": "0013", "PRON_NEW_code": "000106", "PRON_OLD_code": "000108",
        "PRON_NEW_kpk": "0024", "PRON_OLD_kpk": "0026",
    })
    check("ειδικότητα με πρόνοια", r, "ΤΑΠΙΤ")
    # Σύνοψη wizard: 2 φορείς × νέοι/παλαιοί = 4 σενάρια· ΤΕΚΑ -> 4169 και No 36 = 09
    body = check("σύνοψη wizard ειδικότητας", c.get("/company/specialties/summary", query_string={
        "epik_carriers": ["efka", "teka"], "main_kad": "0141", "main_code": "411200",
        "MAIN_EPIK_kpk": "0101", "MAIN_kpk": "0103",
        "pron_enabled": "on", "pron_mandatory": "on", "pron_differentiated": "on",
        "pron_kad": "0013", "PRON_NEW_kpk": "0024", "PRON_OLD_kpk": "0026",
    }), "4169")
    ok = body.count("wz-scn") == 4 and ">09<" in body
    print(f"  {'OK  ' if ok else 'FAIL'} σύνοψη: 4 σενάρια, γραμμή ΤΕΚΑ με ειδ. περίπτωση 09")
    if not ok:
        fails.append("η σύνοψη του wizard δεν δείχνει τα σενάρια πρόσληψης")
    # Κλικ σε κουτάκι πακέτου -> popup με περιγραφές + ιστορικό ποσοστών (0026: 2 γραμμές)
    body = check("popup πακέτου πρόνοιας", c.get("/company/specialties/package",
                 query_string={"name": "ΠΩΛΗΤΗΣ", "role": "PRON_OLD"}), "Τρέχοντα ποσοστά")
    # Τα ποσοστά ζουν ως ΚΛΑΣΜΑΤΑ (0.05) -> το popup δείχνει 5.00%, όχι 0.05%
    ok = ("0026" in body and "Ιστορικό ποσοστών" in body and "Πρόνοια παλαιών" in body
          and "5.00%" in body and "0.05%" not in body)
    print(f"  {'OK  ' if ok else 'FAIL'} popup πακέτου: ΚΠΚ, ονομασία, χρονοσειρά")
    if not ok:
        fails.append("το popup πακέτου δεν δείχνει ΚΠΚ/ποσοστά")
    # Ο ρόλος δέχεται ΜΟΝΟ ΚΠΚ της βαθμίδας του (0103 = κύρια, όχι κύρια+επικ.)
    r = c.post("/company/specialties", data={
        "name": "ΛΑΘΟΣ ΒΑΘΜΙΔΑ", "epik_carriers": ["efka"],
        "main_kad": "0141", "main_code": "411200", "MAIN_EPIK_kpk": "0103",
    })
    _msg = json.loads(r.headers.get("HX-Trigger", "{}")).get("flash", {}).get("msg", "")
    ok = "βαθμίδας" in _msg and "ΛΑΘΟΣ ΒΑΘΜΙΔΑ" not in proj.specialties
    print(f"  {'OK  ' if ok else 'FAIL'} απόρριψη ΚΠΚ λάθος βαθμίδας σε ρόλο")
    if not ok:
        fails.append("έγινε δεκτό ΚΠΚ λάθος βαθμίδας σε ρόλο ειδικότητας")

    # ③ Πρόσληψη: οι επιλογές παράγονται από την ειδικότητα + ζωντανή προεπισκόπηση
    body = check("/employments/hire", c.get("/employments/hire"), 'id="ins-fields"')
    if "Σύνολο κρατήσεων" not in body:
        fails.append("η φόρμα πρόσληψης δεν δείχνει προεπισκόπηση γραμμών ΑΠΔ")
    body = check("htmx ΤΕΚΑ + πρόνοια παλαιών", c.post(
        "/employments/insurance-fields",
        data={"specialty_name": "ΠΩΛΗΤΗΣ", "auxiliary_scheme": "teka",
              "pron_choice": "old", "hire_date": "2026-03-01"}), "4169")
    ok = "0103" in body and "0026" in body and "0101" not in body
    print(f"  {'OK  ' if ok else 'FAIL'} ΤΕΚΑ -> 0103 + 4169 (+ πρόνοια), όχι 0101")
    if not ok:
        fails.append("η προεπισκόπηση ΑΠΔ δεν ακολουθεί τον φορέα επικουρικής")
    body = check("htmx ΕΦΚΑ χωρίς πρόνοια", c.post(
        "/employments/insurance-fields",
        data={"specialty_name": "ΠΩΛΗΤΗΣ", "auxiliary_scheme": "efka",
              "pron_choice": "no", "hire_date": "2026-03-01"}), "0101")
    ok = "0026" not in body and "4169" not in body
    print(f"  {'OK  ' if ok else 'FAIL'} ΕΦΚΑ χωρίς πρόνοια -> μόνο το ενιαίο 0101")
    if not ok:
        fails.append("η επιλογή «χωρίς πρόνοια» δεν αφαιρεί το προνοιακό πακέτο")
    # Ειδικότητα ΧΩΡΙΣ πρόνοια δεν ρωτά καν
    body = c.post("/employments/insurance-fields", data={
        "specialty_name": "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ", "hire_date": "2026-03-01",
    }).get_data(as_text=True)
    ok = "pron_choice" not in body
    print(f"  {'OK  ' if ok else 'FAIL'} ειδικότητα χωρίς πρόνοια δεν εμφανίζει πεδίο")
    if not ok:
        fails.append("εμφανίστηκε επιλογή πρόνοιας σε ειδικότητα χωρίς πρόνοια")

    print("\n=== Καρτέλες ===")
    pid = next(
        p.person_id for p in proj.persons.values() if p.full_name == "ΓΕΩΡΓΙΟΥ ΜΑΡΙΑ"
    )
    # Η καρτέλα προσώπου είναι πλέον modal στη λίστα (δεν υπάρχει ξεχωριστή σελίδα)
    body = check(
        f"/persons/{pid}/info", c.get(f"/persons/{pid}/info"), "Περίοδοι απασχόλησης"
    )
    # Φίλτρα λίστας: κατάσταση + κανονικοποιημένο κλειδί αναζήτησης ανά γραμμή
    check("/persons φίλτρα", c.get("/persons/"), 'data-search="ΓΕΩΡΓΙΟΥ ΜΑΡΙΑ')
    # Βεβαίωση ΑΜΚΑ: λάθος αρχείο -> μήνυμα (όχι 500), η φόρμα κρατά τα στοιχεία
    modal = check("/persons/new από αρχείο", c.get("/persons/new"), "Από αρχείο")
    check("/persons/new είδος άδειας = λίστα", c.get("/persons/new"),
          '<select name="res_permit_ap_type">')
    # Αίτηση υποψηφίου (.html): αυτόνομη, και ΚΑΘΕ πεδίο της υπάρχει στο modal
    form = check("/persons/form.html", c.get("/persons/form.html"), "misevent.candidate/1")
    # εκτός: <meta name=viewport> και τα συνημμένα (στο modal γίνονται doc_<είδος>)
    names = set(re.findall(r'name="(\w+)"', form)) - {"viewport", "foreign_file",
                                                        "young_file"}
    assert names and names <= set(re.findall(r'name="(\w+)"', modal)), names
    assert not re.search(r'(src|href)="(https?:)?//', form), "εξωτερική αναφορά"
    r = c.post("/persons/new/amka", content_type="multipart/form-data", data={
        "ama": "987654321", "amka_pdf": (io.BytesIO(b"not a pdf"), "x.pdf"),
    })
    ok = (r.status_code == 200 and json.loads(r.headers["HX-Trigger"])["flash"]["err"]
          and 'value="987654321"' in r.get_data(as_text=True))
    print(f"  {'OK  ' if ok else 'FAIL'} βεβαίωση ΑΜΚΑ: λάθος αρχείο")
    if not ok:
        fails.append(f"βεβαίωση ΑΜΚΑ: {r.status_code}")
    # Μεταβολή στοιχείων: modal -> POST -> ιστορικό στο info modal. Μακριά στο
    # μέλλον, ώστε να μην αγγίξει τις μισθοδοσίες των επόμενων ελέγχων.
    check(f"/persons/{pid}/change", c.get(f"/persons/{pid}/change"), "Ισχύει από")
    kids = proj.persons[pid].children
    marital = proj.persons[pid].marital_status
    flipped = "0" if marital == "1" else "1"
    r = c.post(f"/persons/{pid}/change", data={
        "effective_from": "2099-01-01", "children": kids + 1,
        "marital_status": flipped,
    })
    trig = r.headers.get("HX-Trigger", "")
    active = next(proj.employments[e] for e in proj.persons[pid].employment_ids
                  if proj.employments[e].active)
    warn = "επίδομα γάμου" in json.loads(trig)["flash"]["msg"]
    ok = (r.status_code == 200 and proj.persons[pid].children_on(_date(2099, 1, 1))
          == kids + 1 and warn == (active.married != (flipped == "1")))
    print(f"  {'OK  ' if ok else 'FAIL'} μεταβολή τέκνων/οικογ. κατάστασης")
    if not ok:
        fails.append(f"μεταβολή στοιχείων: {r.status_code} {trig}")
    check(f"/persons/{pid}/info ιστορικό", c.get(f"/persons/{pid}/info"),
          "Ιστορικό μεταβολών")
    c.post(f"/persons/{pid}/change", data={  # επαναφορά για τους επόμενους
        "effective_from": "2099-01-02", "children": kids, "marital_status": marital,
    })
    eid = proj.persons[pid].employment_ids[0]
    check(f"/employments/{eid}",c.get(f"/employments/{eid}"), "Ψηφιακή κάρτα εργασίας")
    svc.record_card_punch(eid, _date(2026, 8, 20), "08:00", "in")
    svc.record_card_punch(eid, _date(2026, 8, 20), "19:00", "out")
    cal = c.get(f"/employments/{eid}/calendar?m=2026-08")
    check(f"/employments/{eid}/calendar", cal, "Αύγουστος 2026")
    check(f"/employments/{eid}/calendar κάρτα", cal, "08:00-19:00")
    check(f"/employments/{eid}/calendar υπερωρία", cal, "+2:00")
    # σύγκριση πρόγραμμα ↔ κάρτα + τρόπος υπολογισμού του μήνα
    check(f"/employments/{eid}/calendar κουμπί σύγκρισης", cal, "Σύγκριση με κάρτα")
    check(f"/employments/{eid}/calendar/compare",
          c.get(f"/employments/{eid}/calendar/compare?m=2026-08"), "Πρόγραμμα ↔ Κάρτα")
    c.post(f"/employments/{eid}/calendar/compare?m=2026-08", data={"basis": "schedule"})
    if proj.employments[eid].pay_basis.get((2026, 8)) != ("schedule", False):
        fails.append("τρόπος υπολογισμού μήνα: δεν καταγράφηκε")
    # ιδανική κάρτα: το xlsx ξαναδιαβάζεται από την εισαγωγή, ίδιο με το πρόγραμμα
    from misevent.integrations import karta_import
    emp = proj.employments[eid]
    xlsx = c.get(f"/employments/{eid}/calendar/karta.xlsx?m=2026-08").data
    kp = karta_import.parse_karta(xlsx)
    import io
    sheet = zipfile.ZipFile(io.BytesIO(xlsx)).read("xl/worksheets/sheet1.xml").decode()
    if f'r="A2" t="inlineStr"><is><t>{emp.insurance_profile.branch_no}</t>' not in sheet:
        fails.append("ιδανική κάρτα xlsx: λάθος ΑΑ παραρτήματος")
    want = [d for d in (_date(2026, 8, n) for n in range(1, 32))
            if emp.day_schedule(d) and emp.active_on(d) and not emp.holiday_off(d)
            and not any(lv.date_from <= d <= lv.date_to for lv in emp.active_leaves())]
    if any(p.day == _date(2026, 8, 15) for p in kp):
        fails.append("ιδανική κάρτα xlsx: χτυπήματα σε αργία (15/8)")
    # ιδανική κάρτα ΟΛΟΥ του προσωπικού (Παρουσίες): περιέχει και αυτόν τον εργαζόμενο
    check("/attendance κουμπί ιδανικής κάρτας", c.get("/attendance/?y=2026&m=8"),
          "Ιδανική κάρτα xlsx")
    # κλικ στο όνομα -> ημερολόγιο του εργαζομένου (modal) στον μήνα της περιόδου
    check("/attendance όνομα -> ημερολόγιο", c.get("/attendance/?y=2026&m=8"),
          f"/employments/{eid}/calendar/modal?m=2026-08")
    check("ημερολόγιο σε modal", c.get(f"/employments/{eid}/calendar/modal?m=2026-08"),
          f"/employments/{eid}/calendar?m=2026-08")
    resp = c.get("/attendance/karta.xlsx?y=2026&m=8")
    # το όνομα αρχείου έχει και την επωνυμία (ελληνικά: filename* κατά RFC 5987)
    from urllib.parse import quote
    disp = resp.headers["Content-Disposition"]
    want_name = quote("karta_ΔΟΚΙΜΑΣΤΙΚΗ_ΕΜΠΟΡΙΚΗ_ΕΠΕ_2026-08.xlsx")
    if f"filename*=UTF-8''{want_name}" not in disp:
        fails.append(f"ιδανική κάρτα: όνομα αρχείου χωρίς επωνυμία ({disp})")
    allp = karta_import.parse_karta(resp.data)
    if {(p.day, p.time_in) for p in kp} - {(p.day, p.time_in) for p in allp
                                         if p.afm == emp.afm}:
        fails.append("ιδανική κάρτα προσωπικού: λείπουν χτυπήματα του εργαζομένου")
    if not kp or {p.day for p in kp} != set(want) or any(p.afm != emp.afm for p in kp) \
            or f"{kp[0].time_in}-{kp[0].time_out}" not in emp.day_schedule(kp[0].day):
        fails.append(f"ιδανική κάρτα xlsx: {kp[:2]}")
    # αργία: χρωματισμός + όνομα δίπλα στην ημέρα (15/8 Δεκαπενταύγουστος)
    check(f"/employments/{eid}/calendar αργία", cal, 'ecal-d hol')
    check(f"/employments/{eid}/calendar όνομα αργίας", cal, "<i>Δεκαπενταύγουστος")
    # έλεγχος αρτιότητας κάρτας (Διάφορα → card_required): εκκρεμότητες + ολοκλήρωση
    svc.set_misc_settings(card_required=True)
    cal = c.get(f"/employments/{eid}/calendar?m=2026-08")
    check(f"/employments/{eid}/calendar έλεγχος κάρτας", cal, "Χωρίς χτύπημα")
    check(f"/employments/{eid}/calendar ημέρα εκκρεμότητας", cal, " issue")
    r = c.post(f"/employments/{eid}/calendar/check?m=2026-08")
    check(f"/employments/{eid}/calendar/check", r, "ολοκληρώθηκε")
    svc.set_misc_settings(card_required=False)
    # Διακοπτόμενη βάρδια (χτυπήματα κάρτας) -> φαίνεται στο Ημερολόγιο
    svc.record_attendance(eid, _date(2026, 8, 21), ["08:00-12:00", "17:00-22:00"])
    if proj.employments[eid].attendance_shifts.get(_date(2026, 8, 21)) != [
            "08:00-12:00", "17:00-22:00"]:
        fails.append("Καταγραφή παρουσιών: δεν γράφτηκαν οι βάρδιες εισόδου/εξόδου")
    check("calendar παρουσία", c.get(f"/employments/{eid}/calendar?m=2026-08"),
          "17:00-22:00")
    # η παλιά φόρμα «Καταγραφή παρουσιών» της καρτέλας καταργήθηκε
    if c.get(f"/employments/{eid}/modal/attendance").status_code != 404:
        fails.append("modal παρουσιών: έπρεπε να έχει καταργηθεί (404)")
    # Άδειες: κατάσταση ως προς την ημ. αναφοράς (26/08/2026)
    from misevent.domain.events import LeaveType
    svc.record_leave(eid, LeaveType.UNPAID, _date(2026, 8, 10), _date(2026, 8, 10))
    svc.record_leave(eid, LeaveType.UNPAID, _date(2026, 9, 8), _date(2026, 9, 8))
    _card = check("καρτέλα: ληφθείσα άδεια", c.get(f"/employments/{eid}"), "ληφθείσα")
    if "προγραμματισμένη" not in _card:
        fails.append("Καρτέλα: η μελλοντική άδεια δεν φαίνεται προγραμματισμένη")
    # Ιστορικό ημέρας (κλικ στο ημερολόγιο): πρόγραμμα, κάρτα, άδεια
    _h = check("ιστορικό ημέρας", c.get(f"/employments/{eid}/calendar/day?d=2026-08-20"),
               "Κάρτα: είσοδος 08:00")
    if "Αρχικό πρόγραμμα (εβδομαδιαίο)" not in _h:
        fails.append("Ιστορικό ημέρας: λείπει το αρχικό πρόγραμμα")
    check("ιστορικό ημέρας άδειας", c.get(f"/employments/{eid}/calendar/day?d=2026-08-10"),
          "Άδεια: ")
    # Από το modal ημέρας: χτύπημα κάρτας (μένει ανοιχτό) και πρόγραμμα ημέρας
    check("modal ημέρας: φόρμες", c.get(f"/employments/{eid}/calendar/day?d=2026-08-24"),
          "Προσθήκη χτυπήματος")
    check("modal ημέρας: πρόγραμμα", c.get(f"/employments/{eid}/calendar/day?d=2026-09-10"),
          "πακέτο υποβολής")
    _start, _end = proj.employments[eid].day_schedule(_date(2026, 8, 24)).split("-")
    check("modal ημέρας: πρόταση εισόδου", c.get(
        f"/employments/{eid}/calendar/day?d=2026-08-24"), f'value="{_start}"')
    _r = c.post(f"/employments/{eid}/calendar/day/punch", data={
        "d": "2026-08-24", "time": "08:55", "kind": "in"})
    _p = check("modal ημέρας: χτύπημα", _r, "Κάρτα: είσοδος 08:55")
    # htmx: μόνο το modal + ανανέωση ημερολογίου (όχι ολόκληρη η καρτέλα)
    if "ecal-refresh" not in _r.headers.get("HX-Trigger", "") or "employment-card" in _p:
        fails.append("Χτύπημα: έπρεπε να ανανεωθεί μόνο το modal και το ημερολόγιο")
    if f'value="{_end}"' not in _p or '<option value="out" selected' not in _p:
        fails.append("Χτύπημα: μετά την είσοδο δεν προτείνεται η έξοδος του προγράμματος")
    c.post(f"/employments/{eid}/calendar/day/punch", data={
        "d": "2026-09-24", "time": "08:55", "kind": "in"})
    if proj.employments[eid].punches_on(_date(2026, 9, 24)):
        fails.append("Χτύπημα κάρτας σε μελλοντική ημέρα")
    # Ημερήσιο ανά εβδομάδα: διαφορετικό ωράριο ανά ημέρα, ◀ ▶ εβδομάδες
    check("modal ημερήσιου (εβδομάδα)",
          c.get(f"/employments/{eid}/modal/daily?week=2026-09-09"), "Δευτέρα 07/09")
    _e = proj.employments[eid]
    check("ημερήσιο → πακέτο υποβολής", c.post(f"/employments/{eid}/schedule/daily", data={
        "week": "2026-09-07", "hours_2026-09-07": _e.day_schedule(_date(2026, 9, 7)),
        "hours_2026-09-08": "10:00-18:00", "hours_2026-09-09": "08:00-16:00"}),
        "Πακέτο υποβολής")
    if _date(2026, 9, 7) in _e.daily_schedule:
        fails.append("Ημερήσιο: γράφτηκε ημέρα χωρίς αλλαγή")
    if (_e.day_schedule(_date(2026, 9, 8)), _e.day_schedule(_date(2026, 9, 9))) != (
            "10:00-18:00", "08:00-16:00"):
        fails.append("Ημερήσιο ανά εβδομάδα: δεν αποθηκεύτηκαν οι ημέρες")
    # Η υποβολή γίνεται ΜΟΝΟ από την οθόνη ΕΡΓΑΝΗ (+ ειδοποίηση στις Υποχρεώσεις)
    check("υποχρεώσεις: εκκρεμείς υποβολές", c.get("/"),
          "εκκρεμείς υποβολές στο")
    check("ΕΡΓΑΝΗ: ημερήσια ομάδα", c.get("/ergani/"),
          "📆 Ημερήσιο πρόγραμμα")
    c.post("/ergani/schedules", data={"week": f"{eid}|2026-09-07"})
    # Εβδομαδιαίο: καταγράφεται (εκκρεμεί), υποβάλλεται από την ομάδα «Εβδομαδιαίο»
    c.post(f"/employments/{eid}/schedule", data={
        "effective_from": "2026-09-14",
        **{f"day_{k}": v for k, v in _e.schedule.items()}})
    if not _e.pending_weekly:
        fails.append("Εβδομαδιαίο: δεν έμεινε εκκρεμές για υποβολή")
    check("ΕΡΓΑΝΗ: εβδομαδιαία ομάδα", c.get("/ergani/"),
          "🗓️ Εβδομαδιαίο πρόγραμμα")
    c.post("/ergani/schedules", data={"weekly": f"{eid}|2026-09-14"})
    if _e.pending_weekly:
        fails.append("Εβδομαδιαίο: δεν υποβλήθηκε από την οθόνη ΕΡΓΑΝΗ")
    # Όρια 11ω/3ω και στο «Ημερήσιο»· το σφάλμα ανανεώνει μόνο το περιεχόμενο
    _next = _e.day_schedule(_date(2026, 9, 16))
    _r = c.post(f"/employments/{eid}/schedule/daily", data={
        "week": "2026-09-14", "hours_2026-09-15": "14:00-23:00",
        "hours_2026-09-16": _next})
    if _next and ("#daily-modal" not in _r.headers.get("HX-Retarget", "")
                  or "11" not in _r.headers.get("HX-Trigger", "")):
        fails.append("Ημερήσιο: η παραβίαση 11ωρης ανάπαυσης δεν απορρίφθηκε σωστά")
    check("Ημερήσιο: ◀ ▶ χωρίς νέο .modal", c.get(
        f"/employments/{eid}/modal/daily?week=2026-09-21"), 'id="daily-modal"')
    if _e.day_status(_date(2026, 9, 8)) != "submitted":
        fails.append("ΕΡΓΑΝΗ: η υποβολή δεν δήλωσε τις ημέρες")
    c.post(f"/employments/{eid}/schedule/daily", data={"hours_2026-08-20": ""})
    if not _e.day_schedule(_date(2026, 8, 20)):
        fails.append("Ημερήσιο: άλλαξε ημέρα του παρελθόντος")
    check(f"/employments/{eid} επιδότηση", c.get(f"/employments/{eid}"),
          "Επιδότηση εισφοράς νέων")
    check(f"/employments/{eid}/modal/childbirth",
          c.get(f"/employments/{eid}/modal/childbirth"), "Ημερομηνία τοκετού")
    check(f"/employments/{eid}/modal/childcare",
          c.get(f"/employments/{eid}/modal/childcare"), "30 μήνες")
    check(f"/employments/{eid}/childcare",
          c.post(f"/employments/{eid}/childcare",
                 data={"date_from": "2026-07-01", "option": "classic"}),
          "1 ώρ./ημέρα")
    c.post(f"/employments/{eid}/childcare",
           data={"date_from": "2026-07-01", "option": ""})  # κατάργηση
    # Το info modal (badge «απασχολήσεις») δείχνει τις περιόδους με link στην καρτέλα
    if f"/employments/{eid}" in body:
        print("  OK   info modal συνδέει με τις καρτέλες απασχόλησης")
    else:
        fails.append("το info modal δεν συνδέει με τις καρτέλες απασχόλησης")

    print("\n=== Ροή: ΕΠΑΝΑΠΡΟΣΛΗΨΗ μέσω UI ===")
    n_before = len(proj.persons[pid].employment_ids)
    # 1. λήξη τρέχουσας απασχόλησης
    # Δήλωση αποχώρησης ως ΦΩΤΟΓΡΑΦΙΑ -> PDF -> WebE5N (f_file)
    from PIL import Image

    img = io.BytesIO()
    Image.new("RGB", (200, 300), "white").save(img, "JPEG")
    rt = c.post(
        f"/employments/{eid}/terminate", content_type="multipart/form-data",
        data={"termination_date": "2026-06-30", "reason": "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ",
              "resignation": (io.BytesIO(img.getvalue()), "dilosi.jpg")},
    )
    # Η αποχώρηση ΔΕΝ στέλνει: η Αναγγελία Λήξης εκκρεμεί στην οθόνη ΕΡΓΑΝΗ
    if any(s.employee_id == eid and s.doc_type == "E5_LIXI"
           for s in proj.ergani_submissions):
        fails.append("η αποχώρηση έστειλε μόνη της Αναγγελία Λήξης")
    check("ΕΡΓΑΝΗ: εκκρεμής Αναγγελία Λήξης", c.get("/ergani/"), "δήλωση ✓")
    c.post(f"/ergani/e5/{eid}")
    lixi = [s for s in proj.ergani_submissions
            if s.employee_id == eid and s.doc_type == "E5_LIXI"]
    ok = bool(lixi) and '"AnaggeliesE5N"' in lixi[-1].payload and \
        '"f_file": "PDF ' in lixi[-1].payload
    print(f"  {'OK  ' if ok else 'FAIL'} οικειοθελής -> WebE5N με τη δήλωση (PDF)")
    if not ok:
        fails.append("η αποχώρηση δεν έστειλε WebE5N με τη δήλωση")
    # Η καρτέλα εκκαθάρισης δείχνει τα αναλογούντα ποσά + καταβλητέο της ημέρας
    check("εκκαθάριση αποχώρησης: καταβλητέο στην αποχώρηση",
          rt, "Καταβλητέο στην αποχώρηση")
    # ΟΛΕΣ οι αποδείξεις της εκκαθάρισης σε ΕΝΑ PDF (συγκεντρωτικό + αποδείξεις)
    rp = c.get(f"/payroll/print/exit-settlement/{eid}")
    ok = rp.status_code == 200 and rp.data[:4] == b"%PDF"
    print(f"  {'OK  ' if ok else 'FAIL'} PDF εκκαθάρισης αποχώρησης "
          f"({len(rp.data)} bytes)")
    if not ok:
        fails.append("δεν παράχθηκε το PDF εκκαθάρισης αποχώρησης")
    # Η γραμμή «Αποζημίωση Αδείας» του καθολικού έχει τις αποδείξεις του μήνα
    if any(lc.employee_id == eid for lc in proj.leave_compensations):
        check("καθολικό: εκτυπώσεις αποζημίωσης αδείας",
              c.get("/payroll/?gy=2026"), "/payroll/print/settlements/leave/zip")
        check("αποζημίωση αδείας: ανάλυση", c.get(
            "/payroll/modal/settlement-analysis/leave?y=2026&m=6"), "αποζημίωσης")
        for part, magic in (("statement", b"%PDF"), ("slips", b"%PDF"),
                            ("zip", b"PK")):
            rp = c.get(f"/payroll/print/settlements/leave/{part}?y=2026&m=6")
            if not (rp.status_code == 200 and rp.data[:len(magic)] == magic):
                fails.append(f"αποζημίωση αδείας: απέτυχε το {part}")
        # Εξοφλείται ΜΑΖΙ με τους μισθούς — ΜΙΑ φορά, όσες κι αν γίνουν πληρωμές
        svc.pay_exit_compensations(2026, 6)
        svc.pay_exit_compensations(2026, 7)
        lc_net = next(lc.net for lc in proj.leave_compensations
                      if lc.employee_id == eid)
        lc_paid = [p.amount for p in proj.payments
                   if p.payment_type == "leave_compensation" and p.beneficiary == eid]
        if lc_paid != [lc_net]:
            fails.append(f"αποζημίωση αδείας: πληρωμές {lc_paid} αντί [{lc_net}]")
    # Η μισθοδοσία του μήνα αποχώρησης υπολογίστηκε ΜΟΝΟ γι' αυτόν
    june = [p for p in proj.payrolls if (p.period_year, p.period_month) == (2026, 6)]
    ok = len(june) == 1 and june[0].employee_id == eid and june[0].exit_settlement
    print(f"  {'OK  ' if ok else 'FAIL'} μισθοδοσία 6/2026 μόνο για τον αποχωρούντα")
    if not ok:
        fails.append("η μισθοδοσία αποχώρησης δεν αφορά μόνο τον αποχωρούντα")
    # Η καρτέλα του αποχωρήσαντος ΠΡΕΠΕΙ να ανοίγει (ιστορικό, εκκαθάριση)
    check("καρτέλα αποχωρήσαντος", c.get(f"/employments/{eid}"))
    check("εργαζόμενοι: link στην καρτέλα αποχωρήσαντος", c.get("/persons/"),
          f"/employments/{eid}\"")
    # 2. νέα περίοδος στο ΙΔΙΟ πρόσωπο
    SCHEDULE = {f"day_{d}": "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}
    r = c.post(
        f"/persons/{pid}/employ",
        data={
            "hire_date": "2026-09-01",
            "pay_type": "monthly",
            "amount": "1600",
            "weekly_hours": "40",
            # Η ασφαλιστική τριάδα έρχεται από τη δηλωμένη ειδικότητα εταιρείας
            "specialty_name": "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ",
            "teka": "1",
            **SCHEDULE,  # ΥΠΟΧΡΕΩΤΙΚΟ πρόγραμμα εργασίας (ΟΧΕ)
        },
    )
    n_after = len(proj.persons[pid].employment_ids)
    ok = n_after == n_before + 1
    print(f"  {'OK  ' if ok else 'FAIL'} περίοδοι απασχόλησης: {n_before} -> {n_after}")
    print(f"       toast: {r.headers.get('HX-Trigger', '')}")
    if not ok:
        fails.append("η επαναπρόσληψη δεν πρόσθεσε νέα περίοδο")
    # Αποδοχές & πληρωμές: ΟΛΕΣ οι προσλήψεις, αριθμημένες (#1, #2…)
    check("αποδοχές εργαζομένου: πολλαπλές προσλήψεις",
          c.get(f"/persons/{pid}/pay"), f"#{n_after}")
    if len(proj.persons) != 3:
        fails.append("η επαναπρόσληψη δημιούργησε νέο ΠΡΟΣΩΠΟ (δεν έπρεπε)")
    else:
        print("  OK   δεν δημιουργήθηκε νέο πρόσωπο (παραμένουν 3)")

    print("\n=== Επαναπρόσληψη ΕΝΩ είναι ενεργός (πρέπει να απορριφθεί) ===")
    # Το πρόσωπο έχει τώρα ενεργή περίοδο (από την προηγούμενη ροή)
    r = c.post(
        f"/persons/{pid}/employ",
        data={
            "hire_date": "2026-11-01",
            "pay_type": "monthly",
            "amount": "1700",
            "specialty_name": "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ",
        },
    )
    trig = r.headers.get("HX-Trigger", "")
    n_now = len(proj.persons[pid].employment_ids)
    ok = r.status_code == 200 and n_now == n_after and '"err": true' in trig
    print(f"  {'OK  ' if ok else 'FAIL'} απορρίφθηκε — περίοδοι παραμένουν {n_now}")
    if not ok:
        fails.append("επιτράπηκε επαναπρόσληψη ενώ υπάρχει ενεργή απασχόληση")

    print("\n=== Αναζήτηση προσώπου στη ροή πρόσληψης ===")
    body = c.get("/employments/search?q=111222336").get_data(as_text=True)
    # Η (επανα)πρόσληψη χωρίς αναγγελία Ε3 είναι ΕΚΚΡΕΜΗΣ — ποτέ «Ενεργή» (§5.7α)
    ok = "ΓΕΩΡΓΙΟΥ ΜΑΡΙΑ" in body and "Εκκρεμής πρόσληψη" in body
    print(f"  {'OK  ' if ok else 'FAIL'} αναζήτηση με ΑΦΜ βρίσκει το πρόσωπο σε πρόσληψη")
    if not ok:
        fails.append("η αναζήτηση με ΑΦΜ δεν βρήκε το πρόσωπο")
    body = c.get("/employments/search?q=ΖΖΖΖΖ").get_data(as_text=True)
    ok = "Νέος εργαζόμενος" in body
    print(f"  {'OK  ' if ok else 'FAIL'} χωρίς αποτέλεσμα -> προτείνεται νέο πρόσωπο")
    if not ok:
        fails.append("η κενή αναζήτηση δεν προτείνει νέο πρόσωπο")

    print("\n=== Πρόσληψη ΧΩΡΙΣ πρόγραμμα εργασίας (πρέπει να απορριφθεί) ===")
    n_persons = len(proj.persons)
    r = c.post(
        "/employments/",
        data={
            "last_name": "ΧΩΡΙΣ",
            "first_name": "ΠΡΟΓΡΑΜΜΑ",
            "father_name": "ΧΧΧ",
            "mother_name": "ΨΨΨ",
            "afm": "444555667",
            "amka": "12129012345",
            "birth_date": "1985-01-01",
            "hire_date": "2026-06-01",
            "pay_type": "monthly",
            "amount": "1500",
            "weekly_hours": "40",
            "specialty_name": "ΠΩΛΗΤΗΣ",
            # καμία ημέρα προγράμματος
        },
    )
    trig = r.headers.get("HX-Trigger", "")
    ok = r.status_code == 200 and '"err": true' in trig and len(proj.persons) == n_persons
    print(f"  {'OK  ' if ok else 'FAIL'} απορρίφθηκε — δεν δημιουργήθηκε ούτε πρόσωπο")
    if not ok:
        fails.append("επιτράπηκε πρόσληψη χωρίς πρόγραμμα εργασίας")

    print("\n=== Μοναδικότητα μέσω UI (πρέπει να απορριφθεί, χωρίς 500) ===")
    r = c.post(
        "/persons/",
        data={
            "last_name": "ΑΛΛΟΣ",
            "first_name": "ΤΙΣ",
            "father_name": "ΧΧΧ",
            "mother_name": "ΨΨΨ",
            "afm": "111222336",  # ΑΦΜ που υπάρχει ήδη
            "amka": "01017012343",
            "birth_date": "1970-01-01",
            "children": "0",
        },
    )
    trig = r.headers.get("HX-Trigger", "")
    ok = r.status_code == 200 and "\\u03ae\\u03b4\\u03b7" in trig or "ήδη" in trig
    print(f"  {'OK  ' if ok else 'FAIL'} διπλό ΑΦΜ απορρίφθηκε ({r.status_code})")
    print(f"       toast: {trig}")
    if not ok:
        fails.append("το διπλό ΑΦΜ δεν απορρίφθηκε σωστά")

    print("\n=== Αλυσίδα: παρουσίες -> μισθοδοσία ===")
    period = q.next_payroll_period()
    assert period is not None, "αναμενόταν ανοικτή περίοδος μισθοδοσίας μετά το seed"
    y, m = period
    # 1. η μισθοδοσία ΔΕΝ τρέχει πριν εγκριθεί το συγκεντρωτικό
    # ⚠️ Στην περίοδο μπορεί να υπάρχει ΗΔΗ μισθοδοσία ΑΠΟΧΩΡΗΣΗΣ (ενός εργαζόμενου
    # που αποχώρησε μέσα στον μήνα — υπολογίζεται στην αποχώρηση, §2): το κριτήριο
    # είναι ότι ο ΕΤΑΙΡΙΚΟΣ κύκλος δεν πρόσθεσε κανέναν άλλον.
    before = {p.employee_id for p in proj.payrolls
              if (p.period_year, p.period_month) == (y, m)}
    r = c.post("/payroll/run", data={"y": y, "m": m})
    trig = r.headers.get("HX-Trigger", "")
    ok = '"err": true' in trig and {
        p.employee_id for p in proj.payrolls
        if (p.period_year, p.period_month) == (y, m)
    } == before
    print(f"  {'OK  ' if ok else 'FAIL'} μισθοδοσία {m}/{y} μπλοκαρισμένη χωρίς έγκριση")
    if not ok:
        fails.append("η μισθοδοσία έτρεξε χωρίς εγκεκριμένο συγκεντρωτικό")

    # 2. έγκριση συγκεντρωτικού (με χειροκίνητη διόρθωση σε μία γραμμή)
    with app.test_request_context("/"):
        sheet = q.attendance_sheet(y, m)
    eids = [r_["eid"] for r_ in sheet["rows"]]
    data = {"eid": eids}
    for r_ in sheet["rows"]:
        data[f"worked_{r_['eid']}"] = r_["worked"]
        data[f"unpaid_{r_['eid']}"] = r_["unpaid"]
        data[f"leave_{r_['eid']}"] = r_["leave"]
        data[f"efka_{r_['eid']}"] = r_["efka_days"]  # ← οδηγεί τη μισθοδοσία
    # Ήρθαμε από την Επισκόπηση (Referer) -> η σελίδα κρατά `back`, και η
    # έγκριση γυρίζει στην Επισκόπηση (ολόκληρη σελίδα + toast μετά το settle)
    body = c.get(f"/attendance/?y={y}&m={m}",
                 headers={"Referer": "http://localhost/"}).get_data(as_text=True)
    if 'name="back" value="dashboard"' not in body:
        fails.append("οι Παρουσίες δεν θυμούνται ότι ήρθαμε από την Επισκόπηση")
    r = c.post(f"/attendance/approve?y={y}&m={m}", data={**data, "back": "dashboard"})
    ok = (y, m) in proj.attendance_sheets
    print(f"  {'OK  ' if ok else 'FAIL'} εγκρίθηκε το συγκεντρωτικό {m}/{y}")
    if not ok:
        fails.append("η έγκριση παρουσιών απέτυχε")
    ok = (r.headers.get("HX-Retarget") == "body" and r.headers.get("HX-Push-Url") == "/"
          and "Εγκρίθηκε" in json.loads(r.headers.get("HX-Trigger-After-Settle", "{}"))
          .get("flash", {}).get("msg", ""))
    print(f"  {'OK  ' if ok else 'FAIL'} μετά την έγκριση -> πίσω στην Επισκόπηση")
    # Χάρτης κανόνων: ο κόμβος της έγκρισης δείχνει «έγινε» για την περίοδο
    # και ανοίγει το modal της υποχρέωσης
    _appr = next(o for o in proj.obligations.values()
                 if o.obligation_type == "ATTENDANCE_APPROVAL"
                 and (o.period_year, o.period_month) == (y, m))
    body = c.get(f"/rules/flow?y={y}&m={m}").get_data(as_text=True)
    if f"/obligations/modal?id={_appr.obligation_id}" not in body or "gs-done" not in body:
        fails.append("ο Χάρτης κανόνων δεν δείχνει την κατάσταση της περιόδου")
    if not ok:
        fails.append("η έγκριση από Επισκόπηση δεν επιστρέφει στην Επισκόπηση")

    # 3. τώρα τρέχει η μισθοδοσία
    r = c.post("/payroll/run", data={"y": y, "m": m})
    ok = any(p.period_year == y and p.period_month == m for p in proj.payrolls)
    print(f"  {'OK  ' if ok else 'FAIL'} υπολογίστηκε η μισθοδοσία {m}/{y}")
    print(f"       toast: {r.headers.get('HX-Trigger', '')}")
    if not ok:
        fails.append("η μισθοδοσία δεν έτρεξε μετά την έγκριση")

    # 3β. ΟΙ ΑΠΟΔΟΧΕΣ ΤΟΥ ΜΗΝΑ ΜΕΝΟΥΝ ΣΤΑ ΚΑΤΑΒΛΗΤΕΑ ΤΗΣ ΑΠΟΧΩΡΗΣΗΣ ακόμη και
    # αφού ο ΕΤΑΙΡΙΚΟΣ κύκλος ξαναγράψει τη μισθοδοσία της περιόδου (το
    # `exit_settlement` του event αφορά τον scheduler, όχι το τι πληρώνεται)
    with app.test_request_context("/"):
        settlement = q.exit_settlement(eid)
    pay = next((i for i in settlement["now_lines"] if i["kind"] == "payroll"), None)
    ok = (
        pay is not None
        and not settlement["payroll_missing"]
        and settlement["now_net"] == round(
            sum(i["net"] for i in settlement["now_lines"]), 2)
    )
    print(f"  {'OK  ' if ok else 'FAIL'} οι αποδοχές του μήνα στα καταβλητέα "
          f"αποχώρησης ({pay['net'] if pay else '—'} καθαρά)")
    if not ok:
        fails.append("οι αποδοχές του μήνα λείπουν από τα καταβλητέα αποχώρησης")

    # 4. εκτυπώσεις PDF: κατάσταση + αποδείξεις για υπολογισμένη περίοδο
    for path in (f"/payroll/print/statement?y={y}&m={m}",
                 f"/payroll/print/slips?y={y}&m={m}"):
        r = c.get(path)
        ok = r.status_code == 200 and r.data.startswith(b"%PDF")
        print(f"  {'OK  ' if ok else 'FAIL'} PDF {path} ({r.status_code})")
        if not ok:
            fails.append(f"απέτυχε η εκτύπωση {path}")
    r = c.get("/payroll/print/statement?y=1999&m=1")
    ok = r.status_code == 404
    print(f"  {'OK  ' if ok else 'FAIL'} PDF ανύπαρκτης περιόδου -> 404")
    if not ok:
        fails.append("η εκτύπωση ανύπαρκτης περιόδου δεν γύρισε 404")

    # 5. εκτυπώσεις της ΕΠΙΣΚΟΠΗΣΗΣ: μηνιαία εξέλιξη (και στις τρεις οπτικές)
    #    + πραγματικό κόστος εργατοώρας
    for path in (f"/print/cost-report?year={y}",
                 f"/print/cost-report?year={y}&measure=gross",
                 f"/print/cost-report?year={y}&measure=net",
                 f"/print/hourly-cost?year={y}"):
        r = c.get(path)
        ok = r.status_code == 200 and r.data.startswith(b"%PDF")
        print(f"  {'OK  ' if ok else 'FAIL'} PDF {path} ({r.status_code})")
        if not ok:
            fails.append(f"απέτυχε η εκτύπωση {path}")
    # Έτος χωρίς κόστος και άγνωστη οπτική -> 404, ΠΟΤΕ 500
    for path, why in ((f"/print/cost-report?year={y}&measure=xxx", "οπτική"),
                      ("/print/cost-report?year=1999", "έτος χωρίς κόστος")):
        r = c.get(path)
        ok = r.status_code == 404
        print(f"  {'OK  ' if ok else 'FAIL'} {why} -> 404 ({r.status_code})")
        if not ok:
            fails.append(f"η εκτύπωση με άκυρο/κενό {why} δεν γύρισε 404")

    print("\n=== ΑΠΔ: διάστημα απασχόλησης = ΠΛΗΡΗΣ μήνας, σύνολα κεφαλίδας ===")
    _last = _date(y, m, monthrange(y, m)[1])
    _terms = {e.termination_date for e in proj.employments.values() if e.termination_date}
    _pairs = svc.build_apd_rows(y, m).get("efka", [])
    _reg = [r for _, r in _pairs if r.earnings_type == "001"]
    # No 41: τελευταία ημέρα ΤΟΥ ΜΗΝΑ (όχι 28), εκτός αν αποχώρησε νωρίτερα
    _bad = [r.date_to for r in _reg if r.date_to != _last and r.date_to not in _terms]
    ok = bool(_reg) and not _bad
    print(f"  {'OK  ' if ok else 'FAIL'} No41 = {_last.strftime('%d/%m/%Y')} "
          f"({len(_reg)} εγγραφές τύπου 001)")
    if not ok:
        fails.append(f"το No41 της ΑΠΔ {m}/{y} δεν είναι το τέλος του μήνα: {_bad}")
    # No 40: αρχή μήνα ή ημ. πρόσληψης — ποτέ εκτός μήνα
    _first = _date(y, m, 1)
    if any(r.date_from < _first or r.date_from > _last for r in _reg):
        fails.append("το No40 της ΑΠΔ βγήκε εκτός του μήνα")
    else:
        print("  OK   No40 εντός του μήνα (αρχή μήνα ή ημ. πρόσληψης)")
    # Σύνολα κεφαλίδας: οι αποδοχές μετρούν ΜΙΑ φορά ανά πραγματική αμοιβή
    _once = round(sum(r.gross for _, r in _pairs if not r.repeats_earnings), 2)
    _allrows = round(sum(r.gross for _, r in _pairs), 2)
    ok = svc.apd_fund_totals(y, m).get("efka", {}).get("gross") == _once
    print(f"  {'OK  ' if ok else 'FAIL'} No17 = {_once} (άθροισμα όλων των "
          f"γραμμών: {_allrows})")
    if not ok:
        fails.append("το No17 δεν ακολουθεί τον κανόνα της κεφαλίδας")

    print("\n=== Υποβολή ΑΠΔ: κάθε ταμείο παράγει ΜΟΝΟ το δικό του αρχείο ===")
    # Οι υποβολές/καταβολές εκτελούνται ΜΟΝΟ από τις Υποχρεώσεις (η σελίδα
    # «Πληρωμές» είναι πλέον ΠΡΟΒΟΛΗ ΜΟΝΟ).
    def run_ob(ob_type, py=None, pm=None):
        """Ενεργοποιεί την υποχρέωση τύπου `ob_type` της περιόδου."""
        for ob in proj.obligations.values():
            if (ob.obligation_type == ob_type
                    and ob.period_year == (py if py is not None else y)
                    and ob.period_month == (pm if pm is not None else m)):
                return c.post(f"/obligations/{ob.obligation_id}/run")
        fails.append(f"δεν βρέθηκε υποχρέωση {ob_type} {pm or m}/{py or y}")
        return None


    _n = lambda f: sum(1 for x in proj.apd_files                       # noqa: E731
                       if x.fund == f and x.period_year == y and x.period_month == m)
    run_ob("APD_SUBMISSION")
    ok = _n("efka") == 1 and _n("teka") == 0
    print(f"  {'OK  ' if ok else 'FAIL'} «Υποβολή ΑΠΔ e-ΕΦΚΑ» -> αρχεία "
          f"efka={_n('efka')} teka={_n('teka')}")
    if not ok:
        fails.append("η υποβολή ΕΦΚΑ παρήγαγε και αρχείο ΤΕΚΑ (χωριστή υποχρέωση)")
    run_ob("APD_TEKA_SUBMISSION")
    ok = _n("teka") == 1
    print(f"  {'OK  ' if ok else 'FAIL'} «Υποβολή ΑΠΔ ΤΕΚΑ» -> αρχείο "
          f"teka={_n('teka')}")
    if not ok:
        fails.append("η υποβολή ΤΕΚΑ δεν παρήγαγε το αρχείο της")

    print("\n=== Αρχεία ΑΠΔ: on the fly, ZIP στη λήψη (τίποτα στον δίσκο) ===")
    _q = f"fund=efka&year={y}&month={m}"
    _raw = c.get(f"/eapd/raw?{_q}")
    _txt = _raw.get_data().decode(apd_files.ENCODING)
    _recs = [ln for ln in _txt.split("\r\n") if ln]
    ok = (_raw.status_code == 200 and _recs[0][0] == "1" and _recs[-1] == "EOF")
    print(f"  {'OK  ' if ok else 'FAIL'} /eapd/raw -> CSL01 ({len(_recs)} εγγραφές)")
    if not ok:
        fails.append("το /eapd/raw δεν παρήγαγε το CSL01 της περιόδου")
    _dl = c.get(f"/eapd/download?{_q}")
    _name = apd_files.zip_name("efka", y, m)
    ok = (_dl.status_code == 200 and _name in _dl.headers["Content-Disposition"])
    print(f"  {'OK  ' if ok else 'FAIL'} /eapd/download -> {_name}")
    if not ok:
        fails.append("η λήψη ΑΠΔ δεν κατέβασε το ZIP της περιόδου")
    else:
        with zipfile.ZipFile(io.BytesIO(_dl.get_data())) as _z:
            _names = _z.namelist()
            _zraw = _z.read(apd_files.FILE_NAME)
        ok = _names == [apd_files.FILE_NAME] and _zraw == _raw.get_data()
        print(f"  {'OK  ' if ok else 'FAIL'} περιεχόμενο ZIP: {_names}")
        if not ok:
            fails.append("το ZIP της ΑΠΔ δεν περιέχει ακριβώς το CSL01")
    # Κανένα αρχείο ΑΠΔ δεν γράφεται στον δίσκο
    ok = not os.path.exists("apd_out")
    print(f"  {'OK  ' if ok else 'FAIL'} δεν δημιουργήθηκε φάκελος apd_out/")
    if not ok:
        fails.append("η ΑΠΔ γράφτηκε στον δίσκο (apd_out/)")

    print("\n=== Αντικείμενο δραστηριότητας: φόρμα Εταιρείας -> JL10 (2.6) ===")
    _c = proj.company
    _body = c.post("/company/", data={
        "ame": _c.ame, "afm": _c.afm, "legal_name": _c.legal_name,
        "local_office_code": _c.local_office_code,
        "local_office_name": _c.local_office_name,
        "activity": "ΕΜΠΟΡΙΟ ΞΥΛΕΙΑΣ",
    }).get_data(as_text=True)
    ok = (proj.company.activity == "ΕΜΠΟΡΙΟ ΞΥΛΕΙΑΣ"
          and 'name="activity"' in _body)
    print(f"  {'OK  ' if ok else 'FAIL'} αποθηκεύτηκε στο CompanyRegistered: "
          f"{proj.company.activity!r}")
    if not ok:
        fails.append("το «αντικείμενο δραστηριότητας» δεν αποθηκεύτηκε")

    print("\n=== Δήλωση ΦΜΥ: το JL10 παράγεται on the fly, ZIP στη λήψη ===")
    run_ob("FMY_SUBMISSION")
    _f = [x for x in proj.fmy_files if x.period_year == y and x.period_month == m]
    # Το event καταγράφει ότι το αρχείο παράχθηκε· το όνομα προκύπτει από την περίοδο.
    ok = len(_f) == 1
    print(f"  {'OK  ' if ok else 'FAIL'} όνομα λήψης {m}/{y}: "
          f"{fmy_files.zip_name(y, m) if _f else '—'}")
    if not ok:
        fails.append("η δήλωση ΦΜΥ δεν κατέγραψε το FMY_<έτος>_<μήνας>.zip")
    else:
        _fq = f"year={y}&month={m}"
        _dl = c.get(f"/fmy/download?{_fq}")
        ok = (_dl.status_code == 200
              and fmy_files.zip_name(y, m) in _dl.headers["Content-Disposition"])
        print(f"  {'OK  ' if ok else 'FAIL'} /fmy/download -> {fmy_files.zip_name(y, m)}")
        if not ok:
            fails.append("η λήψη ΦΜΥ δεν κατέβασε το ZIP της περιόδου")
        with zipfile.ZipFile(io.BytesIO(_dl.get_data())) as _z:
            _names = _z.namelist()
            _raw = _z.read(fmy_files.FILE_NAME)
        ok = _raw == c.get(f"/fmy/raw?{_fq}").get_data()
        print(f"  {'OK  ' if ok else 'FAIL'} το ZIP περιέχει ακριβώς το /fmy/raw")
        if not ok:
            fails.append("το ZIP του ΦΜΥ διαφέρει από το ωμό JL10")
        ok = _names == [fmy_files.FILE_NAME]
        print(f"  {'OK  ' if ok else 'FAIL'} περιεχόμενο ZIP: {_names}")
        if not ok:
            fails.append("το ZIP του ΦΜΥ δεν περιέχει ακριβώς ένα αρχείο JL10")
        _lines = [ln for ln in _raw.decode(fmy_files.ENCODING).split("\r\n") if ln]
        _sub = [s for s in proj.fmy_submissions
                if s.period_year == y and s.period_month == m][-1]
        ok = (all(len(ln) == 148 for ln in _lines)
              and "".join(ln[0] for ln in _lines[:3]) == "012"
              and abs(int(_lines[2][64:79]) / 100 - _sub.total_fmy) < 0.01)
        print(f"  {'OK  ' if ok else 'FAIL'} {len(_lines)} εγγραφές x 148, "
              f"σύνολο φόρου = δηλωθείς ({_sub.total_fmy:.2f} €)")
        if not ok:
            fails.append("το JL10 δεν συμφωνεί με τη γραμμογράφηση/το δηλωθέν σύνολο")
        # Πεδίο 2.6 [46-61]: το αντικείμενο δραστηριότητας της εταιρείας
        _a26 = _lines[1][45:61]
        ok = _a26 == "ΕΜΠΟΡΙΟ ΞΥΛΕΙΑΣ".ljust(16)
        print(f"  {'OK  ' if ok else 'FAIL'} πεδίο 2.6 [46-61] = {_a26!r}")
        if not ok:
            fails.append("το «αντικείμενο δραστηριότητας» δεν έφτασε στο JL10")
        # Η ΟΘΟΝΗ «Αρχεία ΦΜΥ»: λίστα + modal ανάλυσης, κανένα αρχείο στον δίσκο
        _p = c.get("/fmy/")
        ok = _p.status_code == 200 and fmy_files.zip_name(y, m) in _p.get_data(as_text=True)
        print(f"  {'OK  ' if ok else 'FAIL'} /fmy Αρχεία ΦΜΥ ({_p.status_code})")
        if not ok:
            fails.append("η σελίδα «Αρχεία ΦΜΥ» δεν δείχνει το αρχείο της περιόδου")
        _md = c.get(f"/fmy/modal?{_fq}").get_data(as_text=True)
        ok = "Ανάλυση αρχείου ΦΜΥ" in _md and "Αναλυτικές αμοιβές" in _md
        print(f"  {'OK  ' if ok else 'FAIL'} modal ανάλυσης JL10")
        if not ok:
            fails.append("το modal ανάλυσης ΦΜΥ δεν αποδόθηκε")
        ok = c.get("/fmy/raw?year=1999&month=1").status_code == 404
        print(f"  {'OK  ' if ok else 'FAIL'} /fmy/raw άγνωστη περίοδος -> 404")
        if not ok:
            fails.append("το /fmy/raw σέρβιρε περίοδο εκτός των events")
        ok = not os.path.exists("fmy_out")
        print(f"  {'OK  ' if ok else 'FAIL'} δεν δημιουργήθηκε φάκελος fmy_out/")
        if not ok:
            fails.append("το ΦΜΥ γράφτηκε στον δίσκο (fmy_out/)")

    print("\n=== Πληρωμές: γραμμή = ΜΗΝΑΣ ΚΑΤΑΒΟΛΗΣ, στήλες με προέλευση ===")
    # Καταβολές της περιόδου (μισθοί/ΕΦΚΑ/ΤΕΚΑ/ΦΜΥ) — από τις Υποχρεώσεις
    for _t in ("SALARY_PAYMENT", "EFKA_PAYMENT", "TEKA_PAYMENT", "FMY_PAYMENT"):
        run_ob(_t)
    _rows, _tot = q.payments_by_period()
    _at = lambda mm: next(                                             # noqa: E731
        (r for r in _rows if r["year"] == y and r["month"] == mm), None)
    ok = len({(r["year"], r["month"]) for r in _rows}) == len(_rows)
    print(f"  {'OK  ' if ok else 'FAIL'} {len(_rows)} γραμμές, μία ανά μήνα πληρωμής")
    if not ok:
        fails.append("η σελίδα Πληρωμές δεν δίνει μία γραμμή ανά μήνα")
    # Οι μισθοί πληρώνονται στον ΙΔΙΟ μήνα, οι εισφορές τον ΕΠΟΜΕΝΟ (eom+1),
    # ο ΦΜΥ δύο μήνες μετά (eom+2) — προθεσμίες από το μητρώο τύπων.
    _r0, _r1, _r2 = _at(m), _at(m + 1), _at(m + 2)
    ok = (_r0 and _r0["cells"]["salary"]["amount"] > 0
          and _r1 and _r1["cells"]["efka"]["amount"] > 0
          and _r1["cells"]["teka"]["amount"] > 0
          and _r2 and _r2["cells"]["fmy"]["amount"] >= 0)
    print(f"  {'OK  ' if ok else 'FAIL'} μισθοί -> {m}/{y} · εισφορές -> {m + 1}/{y} "
          f"· ΦΜΥ -> {m + 2}/{y}")
    if not ok:
        fails.append("οι πληρωμές δεν μπήκαν στον μήνα που προβλέπεται να πληρωθούν")
    else:
        ok = all(abs(r["total"] - sum(c["amount"] for c in r["cells"].values())) < 0.01
                 for r in _rows)
        print(f"  {'OK  ' if ok else 'FAIL'} σύνολο γραμμής = άθροισμα στηλών")
        if not ok:
            fails.append("το σύνολο της γραμμής πληρωμών δεν βγαίνει")
        # Το tooltip λέει ΑΠΟ ΠΟΥ (ΜΙΣΘΟΛΟΓΙΚΗ περίοδος) προέρχεται το ποσό
        ok = ("Μισθοδοσία" in _r2["cells"]["fmy"]["tip"]
              and "Μισθοδοσία" in _r0["cells"]["salary"]["tip"]
              and "ΑΠΔ" in _r1["cells"]["efka"]["tip"])
        print(f"  {'OK  ' if ok else 'FAIL'} tooltip ΦΜΥ ({m + 2}/{y}): "
              f"{_r2['cells']['fmy']['tip'].splitlines()[-1]}")
        if not ok:
            fails.append("τα tooltip προέλευσης δεν συμπληρώθηκαν")
        ok = abs(sum(r["total"] for r in _rows) - _tot["total"]) < 0.01
        print(f"  {'OK  ' if ok else 'FAIL'} γενικό σύνολο {_tot['total']:.2f} €")
        if not ok:
            fails.append("τα γενικά σύνολα δεν συμφωνούν με τις γραμμές")
    _pg = c.get("/payments/").get_data(as_text=True)
    ok = all(x in _pg for x in ("Μισθοί", "ΕΦΚΑ", "ΤΕΚΑ", "ΦΜΥ", "Σύνολο", "data-tip"))
    print(f"  {'OK  ' if ok else 'FAIL'} η σελίδα δείχνει τις στήλες + data-tip")
    if not ok:
        fails.append("η σελίδα Πληρωμές δεν απέδωσε τον πίνακα")

    print("\n=== Δώρα: υπολογισμός + εκτυπώσεις PDF ===")
    r = c.post("/payroll/gift/christmas", data={"gy": y})
    ok = any(g.gift_type == "christmas" and g.year == y for g in proj.gifts)
    print(f"  {'OK  ' if ok else 'FAIL'} υπολογίστηκε Δώρο Χριστουγέννων {y}")
    print(f"       toast: {r.headers.get('HX-Trigger', '')}")
    if not ok:
        fails.append("δεν υπολογίστηκε το Δώρο Χριστουγέννων")
    for path in (f"/payroll/print/gift/christmas/statement?gy={y}",
                 f"/payroll/print/gift/christmas/slips?gy={y}"):
        r = c.get(path)
        ok = r.status_code == 200 and r.data.startswith(b"%PDF")
        print(f"  {'OK  ' if ok else 'FAIL'} PDF {path} ({r.status_code})")
        if not ok:
            fails.append(f"απέτυχε η εκτύπωση {path}")
    r = c.get("/payroll/print/gift/easter/statement?gy=1999")
    ok = r.status_code == 404
    print(f"  {'OK  ' if ok else 'FAIL'} PDF μη υπολογισμένου Δώρου -> 404")
    if not ok:
        fails.append("η εκτύπωση μη υπολογισμένου Δώρου δεν γύρισε 404")
    # Η ΚΑΤΑΒΟΛΗ του Δώρου (period_month == 0) μπαίνει στη γραμμή του μήνα που
    # υπολογίστηκε/φορολογήθηκε — ΜΙΑ πηγή αλήθειας με το ΦΜΥ (svc.gift_fmy_period)
    _gob = next((o for o in proj.obligations.values()
                 if o.obligation_type == "CHRISTMAS_GIFT_PAYMENT" and o.period_year == y),
                None)
    if _gob is None:
        fails.append("δεν γεννήθηκε υποχρέωση καταβολής Δώρου Χριστουγέννων")
    else:
        c.post(f"/obligations/{_gob.obligation_id}/run")
        _rows, _ = q.payments_by_period()
        _dec = next((r for r in _rows if r["year"] == y and r["month"] == 12), None)
        ok = _dec is not None and "Δώρο Χριστουγέννων" in _dec["cells"]["salary"]["tip"]
        print(f"  {'OK  ' if ok else 'FAIL'} η καταβολή Δώρου μπήκε στη γραμμή 12/{y}")
        if not ok:
            fails.append("η καταβολή Δώρου δεν εντάχθηκε στη σωστή περίοδο")

    print("\n=== Παράμετροι: μητρώο από events + νέα ΓΣΣΕ -> WAGE_ADJUSTMENT ===")
    from webapp.system import svc  # noqa: E402

    ok = svc.parameters_confirmed()
    print(f"  {'OK  ' if ok else 'FAIL'} το seed επιβεβαίωσε το μητρώο παραμέτρων")
    if not ok:
        fails.append("το μητρώο παραμέτρων δεν επιβεβαιώθηκε από το seed")

    body = c.get("/settings/").get_data(as_text=True)
    ok = "Πρώτο τρέξιμο" not in body and "ΓΣΣΕ / Κατώτατος" in body
    print(f"  {'OK  ' if ok else 'FAIL'} η σελίδα δεν δείχνει wizard μετά το seed")
    if not ok:
        fails.append("η σελίδα Παραμέτρων δείχνει λάθος κατάσταση wizard")

    # Νέα ΓΣΣΕ με ισχύ σε ΑΝΟΙΧΤΗ περίοδο -> γεννιέται υποχρέωση WAGE_ADJUSTMENT
    open_periods = {
        (o.period_year, o.period_month)
        for o in proj.obligations.values()
        if o.obligation_type == "MONTHLY_PAYROLL"
    }
    wy, wm = max(open_periods)
    had = any(
        o.obligation_type == "WAGE_ADJUSTMENT"
        and (o.period_year, o.period_month) == (wy, wm)
        for o in proj.obligations.values()
    )
    r = c.post(
        "/settings/param/minimum_wage",
        data={"valid_from": f"{wy}-{wm:02d}-01", "monthly": "990", "daily": "44.20"},
    )
    has = any(
        o.obligation_type == "WAGE_ADJUSTMENT"
        and (o.period_year, o.period_month) == (wy, wm)
        for o in proj.obligations.values()
    )
    ok = r.status_code == 200 and not had and has
    print(f"  {'OK  ' if ok else 'FAIL'} νέα ΓΣΣΕ {wm}/{wy} γέννησε WAGE_ADJUSTMENT")
    if not ok:
        fails.append("η νέα ΓΣΣΕ δεν γέννησε υποχρέωση προσαρμογής")

    print("\n=== Παραμετροποίηση τύπου + παράταση προθεσμίας ===")
    # Ο τύπος FMY_SUBMISSION δεν επιτρέπει παράταση -> τη δηλώνουμε από το UI
    r = c.post(
        "/settings/obtype/FMY_SUBMISSION",
        data={"due_kind": "eom", "due_k": "2", "ext_allowed": "1",
              "ext_max_days": "15"},
    )
    from misevent.rules import obligation_types as ot  # noqa: E402

    ok = r.status_code == 200 and ot.spec("FMY_SUBMISSION").extension.get("allowed")
    print(f"  {'OK  ' if ok else 'FAIL'} δηλώθηκε παράταση για FMY_SUBMISSION")
    if not ok:
        fails.append("δεν αποθηκεύτηκε η παραμετροποίηση τύπου")

    fmy_ob = next(
        o for o in proj.obligations.values()
        if o.obligation_type == "FMY_SUBMISSION" and not o.fulfilled
    )
    new_due = fmy_ob.due_date.fromordinal(fmy_ob.due_date.toordinal() + 5)
    r = c.post(
        f"/obligations/{fmy_ob.obligation_id}/extend",
        data={"new_due": new_due.isoformat(), "reason": "δοκιμή"},
    )
    ok = r.status_code == 200 and fmy_ob.due_date == new_due and fmy_ob.extended
    print(f"  {'OK  ' if ok else 'FAIL'} παρατάθηκε η προθεσμία ΦΜΥ έως {new_due}")
    if not ok:
        fails.append("η παράταση προθεσμίας δεν εφαρμόστηκε")

    # Πέρα από το όριο max_days -> απορρίπτεται
    too_far = new_due.fromordinal(new_due.toordinal() + 60)
    r = c.post(
        f"/obligations/{fmy_ob.obligation_id}/extend",
        data={"new_due": too_far.isoformat()},
    )
    trig = r.headers.get("HX-Trigger", "")
    ok = '"err": true' in trig and fmy_ob.due_date == new_due
    print(f"  {'OK  ' if ok else 'FAIL'} παράταση πέρα από το όριο απορρίφθηκε")
    if not ok:
        fails.append("επιτράπηκε παράταση πέρα από το όριο του τύπου")

    print("\n=== Rebuild: οι παράμετροι ξαναχτίζονται από τα events ===")
    from misevent.rules import config  # noqa: E402
    from webapp.system import rebuild_all  # noqa: E402

    before = config.minimum_wage_for(new_due).monthly
    rebuild_all()
    after = config.minimum_wage_for(new_due).monthly
    ok = before == after and svc.parameters_confirmed()
    print(f"  {'OK  ' if ok else 'FAIL'} rebuild διατηρεί το μητρώο ({after} €)")
    if not ok:
        fails.append("το rebuild έχασε το μητρώο παραμέτρων")

    assert not fails, "ΑΠΟΤΥΧΙΕΣ: " + "; ".join(str(x) for x in fails)
