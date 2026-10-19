"""
ΑΡΧΕΙΟ ΕΡΓΑΖΟΜΕΝΩΝ (JSON): εξαγωγή -> φόρτωση σε ΚΑΘΑΡΟ σύστημα -> ίδιοι.

Το αρχείο της σελίδας «Εργαζόμενοι» κουβαλά ΜΟΝΟ την **ταυτότητα** των
φυσικών προσώπων (§2): ονόματα, ΑΦΜ/ΑΜΚΑ/ΑΜΑ, τέκνα, ταυτότητα/άδεια
διαμονής. ΟΧΙ απασχολήσεις — η πρόσληψη είναι ξεχωριστή πράξη με δικούς της
όρους — και ΟΧΙ κίνηση (παρουσίες/άδειες/μισθοδοσίες: ζουν στο event log).

Κλειδώνονται τρία πράγματα:
  1. ΚΑΝΕΝΑ ΠΕΔΙΟ ΔΕΝ ΞΕΧΝΙΕΤΑΙ — η λίστα των πεδίων είναι ΠΑΡΑΓΩΓΗ του
     `constraints.PERSON_FIELDS`, οπότε ένα νέο πεδίο ταυτότητας μπαίνει
     μόνο του· εδώ ελέγχεται ότι όντως ταξιδεύει ΚΑΙ γυρίζει πίσω.
  2. ΙΔΙΟ ΑΦΜ = ΕΝΗΜΕΡΩΣΗ, όχι δεύτερο πρόσωπο (και όχι σιωπηλή αλλαγή
     ημερομηνίας γέννησης, που είναι κλειδί ταυτότητας).
  3. Οι λάθος γραμμές δεν χαλούν τις υπόλοιπες.

Τρέξιμο:  uv run pytest tests/test_persons_export.py
"""

from datetime import date


def _fresh_service():
    """Καθαρός κόσμος πυρήνα (store + projections + service)."""
    import os
    import tempfile

    from misevent.app.services import PayrollService
    from misevent.domain.projections import Projections
    from misevent.persistence.database import Database

    path = os.path.join(tempfile.mkdtemp(), "persons.db")
    store = Database(path).store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    return store, proj, PayrollService(store, proj)


def _seed(svc):
    """Δύο πρόσωπα: ένα «απλό» και ένα με ΟΛΑ τα σπάνια πεδία γεμάτα (άδεια
    διαμονής, ταυτότητα, εκπαίδευση) — αυτά ξεχνιούνται στις εξαγωγές."""
    svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ", first_name="ΓΙΩΡΓΟΣ", father_name="ΝΙΚΟΣ",
        mother_name="ΜΑΡΙΑ", birth_date=date(1990, 5, 5),
        afm="111222336", amka="05059012343", ama="123456781", children=2,
    )
    svc.register_person(
        last_name="ΤΕΣΤ", first_name="ΑΛΙ", father_name="ΧΑΣΑΝ",
        mother_name="ΦΑΤΜΑ", birth_date=date(1985, 3, 3),
        afm="222333440", amka="03038512343", ama="123456782",
        email="ali@example.com", sex="1", nationality_code="008",
        identity_type="ΔAT", identity_number="ΑΒ123456", identity_issuer="ΑΤ ΑΘΗΝΩΝ",
        identity_issue_date=date(2020, 1, 15),
        identity_expiry_date=date(2030, 1, 15),
        education_level="3",
        res_permit_ap="1", res_permit_ap_type="2",
        res_permit_ap_number="ΑΔ-999",
        res_permit_ap_expiry_date=date(2027, 6, 30),
    )


def test_persons_export_roundtrip(fresh_system):
    import json

    from misevent.app.services.company import PERSON_EXPORT_FIELDS
    from misevent.rules import constraints

    fails = []

    def check(desc, cond):
        print(("OK   " if cond else "ΛΑΘΟΣ ") + desc)
        if not cond:
            fails.append(desc)

    # ------------------------------------------------- 1. ΕΞΑΓΩΓΗ (σύστημα Α)
    _s, _p, svc_a = _fresh_service()
    _seed(svc_a)
    raw = svc_a.export_persons_bytes()
    doc = json.loads(raw.decode("utf-8"))

    check("το αρχείο δηλώνει τον τύπο του", doc["schema"] == "misevent.persons")
    check("εξήχθησαν και τα δύο πρόσωπα", len(doc["persons"]) == 2)
    check("ταξινομημένα με το ονοματεπώνυμο",
          [p["last_name"] for p in doc["persons"]] == ["ΠΑΠΑΔΟΠΟΥΛΟΣ", "ΤΕΣΤ"])
    ali = next(p for p in doc["persons"] if p["last_name"] == "ΤΕΣΤ")
    check("οι ημερομηνίες γράφονται ISO",
          ali["birth_date"] == "1985-03-03"
          and ali["res_permit_ap_expiry_date"] == "2027-06-30")
    check("η άδεια διαμονής ταξιδεύει", ali["res_permit_ap_number"] == "ΑΔ-999")
    check("τα ΚΕΝΑ πεδία παραλείπονται (αναγνώσιμο αρχείο)",
          "res_permit_visa_number" not in ali)
    # ΤΟ ΔΙΧΤΥ: κάθε πεδίο του μητρώου ταυτότητας ΠΡΕΠΕΙ να μπορεί να ταξιδέψει
    check("η λίστα πεδίων καλύπτει ΟΛΟ το constraints.PERSON_FIELDS",
          set(constraints.PERSON_FIELDS) <= set(PERSON_EXPORT_FIELDS))
    check("και την ημ. γέννησης (λείπει από το PERSON_FIELDS επίτηδες)",
          "birth_date" in PERSON_EXPORT_FIELDS)
    covered = set(ali) | set(doc["persons"][0])
    missing = [
        f for f in PERSON_EXPORT_FIELDS
        if f not in covered and f not in ("marital_status",)
    ]
    check(f"το δείγμα γεμίζει σχεδόν όλα τα πεδία (κενά: {missing})",
          len(missing) <= 8)

    # ------------------------------- 2. ΦΟΡΤΩΣΗ ΣΕ ΚΑΘΑΡΟ ΣΥΣΤΗΜΑ (σύστημα Β)
    _s2, _p2, svc_b = _fresh_service()
    res = svc_b.import_persons(raw)
    check(f"η εισαγωγή δεν έβγαλε σφάλματα ({'; '.join(res['errors'][:3])})",
          not res["errors"])
    check("δύο νέα πρόσωπα", res["counts"] == {"created": 2, "updated": 0})
    check("ίδιες τιμές μετά τον κύκλο εξαγωγή -> εισαγωγή",
          svc_b.export_persons()["persons"] == doc["persons"])

    # --------------------------------- 3. ΙΔΙΟ ΑΦΜ = ΕΝΗΜΕΡΩΣΗ, ΟΧΙ ΔΙΠΛΟ
    changed = json.loads(raw.decode("utf-8"))
    changed["persons"][0]["children"] = 3
    changed["persons"][0]["email"] = "giorgos@example.com"
    res = svc_b.import_persons(json.dumps(changed).encode("utf-8"))
    check("καμία νέα καταχώρηση, μόνο ενημερώσεις",
          res["counts"] == {"created": 0, "updated": 2} and not res["errors"])
    check("δεν δημιουργήθηκε δεύτερο πρόσωπο", len(svc_b.proj.persons) == 2)
    giorgos = svc_b.proj.person_by_afm("111222336")
    check("τα στοιχεία ενημερώθηκαν",
          giorgos.children == 3 and giorgos.email == "giorgos@example.com")

    # ------------------- 4. ΑΛΛΗ ΗΜ. ΓΕΝΝΗΣΗΣ ΣΤΟ ΙΔΙΟ ΑΦΜ: ΣΦΑΛΜΑ, ΟΧΙ ΣΙΩΠΗ
    clash = json.loads(raw.decode("utf-8"))
    clash["persons"][0]["birth_date"] = "1991-01-01"
    res = svc_b.import_persons(json.dumps(clash).encode("utf-8"))
    check("η σύγκρουση ημ. γέννησης αναφέρεται",
          any("γέννησης" in e for e in res["errors"]))
    check("το πρόσωπο έμεινε ΑΘΙΚΤΟ",
          svc_b.proj.person_by_afm("111222336").birth_date == date(1990, 5, 5))
    check("οι υπόλοιπες γραμμές πέρασαν κανονικά", res["counts"]["updated"] == 1)

    # -------------------------------------------- 5. ΛΑΘΟΣ/ΑΓΝΩΣΤΟ ΑΡΧΕΙΟ
    for bad, why in (
        (b"{", "μη έγκυρο JSON"),
        (b'{"schema": "misevent.company"}', "αρχείο άλλου τύπου"),
        (b'{"schema": "misevent.persons"}', "χωρίς λίστα persons"),
    ):
        try:
            svc_b.import_persons(bad)
            fails.append(f"το «{why}» πέρασε χωρίς σφάλμα")
        except ValueError:
            print(f"OK   {why}: ValueError με ελληνικό μήνυμα")

    # Γραμμή χωρίς ΑΦΜ/ονόματα: αναφέρεται, δεν ρίχνει την εισαγωγή
    res = svc_b.import_persons(
        json.dumps({"schema": "misevent.persons",
                    "persons": [{"last_name": "ΧΩΡΙΣ", "first_name": "ΑΦΜ"}]}
                   ).encode("utf-8")
    )
    check("η ελλιπής γραμμή επιστρέφεται ως σφάλμα",
          res["errors"] and res["counts"]["created"] == 0)

    assert not fails, "ΑΠΟΤΥΧΙΕΣ: " + "; ".join(fails)


def test_persons_page_has_export_and_import(fresh_system):
    """Τα κουμπιά δουλεύουν από την ίδια την οθόνη «Εργαζόμενοι»."""
    import io
    import json

    import _bootstrap

    import webapp

    fails = []

    def check(desc, cond):
        print(("OK   " if cond else "ΛΑΘΟΣ ") + desc)
        if not cond:
            fails.append(desc)

    app = webapp.create_app()
    client = _bootstrap.ui_client(app)

    page = client.get("/persons/")
    check("η σελίδα ανοίγει", page.status_code == 200)
    check("δείχνει τα κουμπιά αρχείου",
          "Εξαγωγή αρχείου" in page.get_data(as_text=True)
          and "Εισαγωγή αρχείου" in page.get_data(as_text=True))

    dump = client.get("/persons/export")
    check("η εξαγωγή κατεβάζει αρχείο JSON",
          dump.status_code == 200 and dump.mimetype == "application/json")
    doc = json.loads(dump.get_data(as_text=True))
    check("το κατεβασμένο αρχείο είναι αρχείο προσώπων",
          doc["schema"] == "misevent.persons")

    # Ένα νέο πρόσωπο μέσα από το αρχείο — η εισαγωγή είναι ΓΡΑΦΗ, όχι προβολή
    doc["persons"].append({
        "last_name": "ΕΙΣΑΓΩΓΗΣ", "first_name": "ΔΟΚΙΜΗ",
        "father_name": "Π", "mother_name": "Μ", "birth_date": "1992-02-02",
        # ΑΦΜ/ΑΜΚΑ με σωστά ψηφία ελέγχου (η εισαγωγή τα ελέγχει κανονικά)
        "afm": "090000008", "amka": "02029200009",
    })
    load = client.post(
        "/persons/import",
        data={"file": (io.BytesIO(json.dumps(doc).encode("utf-8")), "p.json")},
        content_type="multipart/form-data",
    )
    check("η εισαγωγή απαντά με ανανεωμένο panel",
          load.status_code == 200
          and "persons-panel" in load.get_data(as_text=True))
    flash = json.loads(load.headers.get("HX-Trigger", "{}")).get("flash", {})
    check(f"η εισαγωγή πέτυχε ({flash.get('msg', '')})", not flash.get("err"))
    check("ο νέος εργαζόμενος φαίνεται στη λίστα",
          "ΕΙΣΑΓΩΓΗΣ" in client.get("/persons/").get_data(as_text=True))

    assert not fails, "ΑΠΟΤΥΧΙΕΣ: " + "; ".join(fails)
