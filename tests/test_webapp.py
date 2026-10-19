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
    # προσωρινός κωδικός έχει ήδη αλλάξει από άλλο test αρχείο της ίδιας
    # διεργασίας είτε όχι (με hardcoded «changeme» έπαιρνε 302 παντού).
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
    r = c.post("/seed")
    print(
        f"  {'OK  ' if r.status_code == 200 else 'FAIL'} POST /seed -> "
        f"{len(proj.persons)} πρόσωπα, {len(proj.employments)} απασχολήσεις"
    )

    print("\n=== Σελίδες (GET) ===")
    check("/            Επισκόπηση", c.get("/"), "Επισκόπηση")
    check("/obligations Υποχρεώσεις", c.get("/obligations/"), "Χρονολόγιο")
    check("/persons     Εργαζόμενοι", c.get("/persons/"), "Εργαζόμενοι")
    check("/live        Ζωντανά", c.get("/live/"), "Ζωντανή εικόνα ημέρας")
    check("/live/rows   γραμμές (htmx)", c.get("/live/rows"))
    check("/work-schedules Πρόγραμμα", c.get("/work-schedules/?year=2026"), "Ετήσιο πρόγραμμα")
    check("/work-schedules/day Ημέρα",
          c.get("/work-schedules/day?date=2026-07-14"), "Πρόγραμμα ημέρας")
    check("/attendance  Παρουσίες", c.get("/attendance/"), "Παρουσίες")
    check("/leaves      Άδειες", c.get("/leaves/?year=2026"), "Ετήσιο πλάνο")
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
    check("/ergani      ΕΡΓΑΝΗ", c.get("/ergani/"), "Υποβολές εντύπων",
          iso_ok=True)  # ωμό JSON payload ΕΡΓΑΝΗ
    check("/company     Εταιρεία", c.get("/company/"), "Στοιχεία εργοδότη")
    check("/settings    Παράμετροι", c.get("/settings/"), "Αλυσίδα υποχρεώσεων")
    check("/audit       Audit log", c.get("/audit/"), "Event log",
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
        "MAIN_EPIK_kad": "5510", "MAIN_EPIK_code": "411200", "MAIN_EPIK_kpk": "0101",
        "MAIN_kad": "5510", "MAIN_code": "411200", "MAIN_kpk": "0103",
        # ΚΑΔ/ειδικότητα σε ΚΑΘΕ ρόλο — το ΚΠΚ δεν κουβαλά πια κωδικούς
        "TEKA_kad": "0083", "TEKA_code": "000860", "TEKA_kpk": "4169",
        "pron_enabled": "on", "pron_name": "ΤΑΠΙΤ", "pron_differentiated": "on",
        "PRON_NEW_kad": "0013", "PRON_NEW_code": "000108",
        "PRON_OLD_kad": "0013", "PRON_OLD_code": "000108",
        "PRON_NEW_kpk": "0026", "PRON_OLD_kpk": "0026",
    })
    check("ειδικότητα με πρόνοια", r, "ΤΑΠΙΤ")
    # Ο ρόλος δέχεται ΜΟΝΟ ΚΠΚ της βαθμίδας του (0103 = κύρια, όχι κύρια+επικ.)
    r = c.post("/company/specialties", data={
        "name": "ΛΑΘΟΣ ΒΑΘΜΙΔΑ", "epik_carriers": ["efka"],
        "MAIN_EPIK_kad": "5510", "MAIN_EPIK_code": "411200", "MAIN_EPIK_kpk": "0103",
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
    eid = proj.persons[pid].employment_ids[0]
    check(f"/employments/{eid}", c.get(f"/employments/{eid}"), "Ασφαλιστικό προφίλ")
    # Το info modal (badge «απασχολήσεις») δείχνει τις περιόδους με link στην καρτέλα
    if f"/employments/{eid}" in body:
        print("  OK   info modal συνδέει με τις καρτέλες απασχόλησης")
    else:
        fails.append("το info modal δεν συνδέει με τις καρτέλες απασχόλησης")

    print("\n=== Ροή: ΕΠΑΝΑΠΡΟΣΛΗΨΗ μέσω UI ===")
    n_before = len(proj.persons[pid].employment_ids)
    # 1. λήξη τρέχουσας απασχόλησης
    rt = c.post(
        f"/employments/{eid}/terminate",
        data={"termination_date": "2026-06-30", "reason": "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ"},
    )
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
    # Η μισθοδοσία του μήνα αποχώρησης υπολογίστηκε ΜΟΝΟ γι' αυτόν
    june = [p for p in proj.payrolls if (p.period_year, p.period_month) == (2026, 6)]
    ok = len(june) == 1 and june[0].employee_id == eid and june[0].exit_settlement
    print(f"  {'OK  ' if ok else 'FAIL'} μισθοδοσία 6/2026 μόνο για τον αποχωρούντα")
    if not ok:
        fails.append("η μισθοδοσία αποχώρησης δεν αφορά μόνο τον αποχωρούντα")
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
    r = c.post(f"/attendance/approve?y={y}&m={m}", data=data)
    ok = (y, m) in proj.attendance_sheets
    print(f"  {'OK  ' if ok else 'FAIL'} εγκρίθηκε το συγκεντρωτικό {m}/{y}")
    if not ok:
        fails.append("η έγκριση παρουσιών απέτυχε")

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
