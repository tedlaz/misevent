"""
ΑΡΧΕΙΟ ΠΑΡΑΜΕΤΡΩΝ (JSON): εξαγωγή -> φόρτωση σε ΚΑΘΑΡΟ σύστημα -> ίδιες τιμές.

Η σελίδα «Παράμετροι» είναι ΚΟΙΝΗ για όλες τις εταιρείες και ζει σε events.
Το αρχείο εξαγωγής πρέπει να κουβαλά ΟΛΟ ό,τι δείχνει η σελίδα:

  • κάθε οικογένεια του `config.FAMILIES` με ΟΛΟ το ιστορικό ισχύος της
    (μαζί με τις καταργήσεις και τα πεδία που δεν ζητά η φόρμα, π.χ. τα
    fallback ποσοστά ΕΦΚΑ)·
  • τη φορολογική κλίμακα με το ΚΑΘΕΣΤΩΣ της και τις παραμέτρους του·
  • την αποζημίωση απόλυσης (πίνακες μηνών + κλιμάκια φόρου)·
  • τις προθεσμίες/παρατάσεις των τύπων υποχρεώσεων·
  • τις προτιμήσεις («Διάφορα») και το Μητρώο ΚΠΚ.

Ο έλεγχος είναι ΚΥΚΛΟΣ: ό,τι εξήχθη, φορτώνεται σε άδειο σύστημα και
ξαναεξάγεται — τα δύο αρχεία πρέπει να είναι ΤΑΥΤΟΣΗΜΑ. Έτσι μια νέα
οικογένεια παραμέτρων που ξεχάστηκε στην εξαγωγή σκάει εδώ, όχι στον χρήστη.

Τρέξιμο:  uv run pytest tests/test_parameters_export.py
"""


def _fresh_service():
    """Καθαρός κόσμος πυρήνα (store + projections + service)."""
    import os
    import tempfile

    from misevent.app.services import PayrollService
    from misevent.domain.projections import Projections
    from misevent.persistence.database import Database

    path = os.path.join(tempfile.mkdtemp(), "params.db")
    store = Database(path).store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    return store, proj, PayrollService(store, proj)


def _seed(svc):
    """Ένα σύστημα «με ιστορία»: επιβεβαιωμένες αρχικές τιμές + αλλαγές που
    πιάνουν ΚΑΘΕ διαδρομή (απλή οικογένεια, κατάργηση, ΚΠΚ, προθεσμία,
    προτιμήσεις)."""
    from datetime import date

    svc.confirm_default_parameters()
    svc.define_minimum_wage(date(2027, 4, 1), 950.0, 42.5)
    svc.repeal_parameters("sick_leave", date(2028, 1, 1))
    # ΚΑΙ μία «custom» οικογένεια (πίνακες): άλλη διαδρομή κατάργησης
    svc.repeal_parameters("severance", date(2028, 1, 1))
    fund = svc.define_kpk("efka", "0101", "ΜΙΚΤΑ ΤΕΣΤ", "main_auxiliary")
    svc.define_kpk_rates(fund, "0101", date(2026, 1, 1), 0.1337, 0.2229)
    svc.configure_obligation_type(
        "APD_SUBMISSION", due=("eom", 1), extension={"allowed": True, "max_days": 20}
    )
    svc.set_misc_settings(save_zip=True, show_employer_cost=False)


def test_parameters_export_roundtrip(fresh_system):
    import json

    from misevent.rules import config
    from misevent.rules import obligation_types as ot

    fails = []

    def check(desc, cond):
        print(("OK   " if cond else "ΛΑΘΟΣ ") + desc)
        if not cond:
            fails.append(desc)

    # ------------------------------------------------- 1. ΕΞΑΓΩΓΗ (σύστημα Α)
    _store_a, _proj_a, svc_a = _fresh_service()
    _seed(svc_a)
    raw = svc_a.export_parameters_bytes()
    doc = json.loads(raw.decode("utf-8"))

    check("το αρχείο δηλώνει τον τύπο του",
          doc["schema"] == "misevent.parameters")
    check("εξάγονται ΟΛΕΣ οι οικογένειες του μητρώου",
          set(doc["families"]) == set(config.FAMILIES))
    check("εξάγονται ΟΛΟΙ οι τύποι υποχρεώσεων",
          {t["code"] for t in doc["obligation_types"]} == set(ot.TYPES))
    check("η ΓΣΣΕ κουβαλά όλο το ιστορικό της",
          any(r["valid_from"] == "2027-04-01"
              and r["values"]["monthly"] == 950.0
              for r in doc["families"]["minimum_wage"]["entries"]))
    check("η κατάργηση ταξιδεύει ως κατάργηση (απλή οικογένεια)",
          any(r["repealed"] for r in doc["families"]["sick_leave"]["entries"]))
    check("η κατάργηση ταξιδεύει ως κατάργηση (οικογένεια με πίνακες)",
          any(r["repealed"] for r in doc["families"]["severance"]["entries"]))
    check("το αρχείο ΔΕΝ γράφει τα fallback ποσοστά ΕΦΚΑ (έρχονται από ΚΠΚ)",
          all(set(r["values"]) == {"monthly_cap"}
              for r in doc["families"]["efka"]["entries"]))
    check("δεν υπάρχει πια οικογένεια «teka»",
          "teka" not in doc["families"] and "teka" not in config.FAMILIES)
    check("η κλίμακα ΦΜΥ κουβαλά το ΚΑΘΕΣΤΩΣ της",
          all("model" in r for r in doc["families"]["tax"]["entries"]))
    check("το τελευταίο κλιμάκιο ΦΜΥ μένει ανοιχτό (None, όχι άπειρο)",
          all(r["brackets"][-1]["up_to"] is None
              for r in doc["families"]["tax"]["entries"]))
    # Το μητρώο έχει και τα ΚΠΚ της αρχικοποίησης (data/init_parameters.json)
    # και το χειροκίνητο του `_seed` — όλα ταξιδεύουν.
    check("το Μητρώο ΚΠΚ ταξιδεύει μαζί",
          "0101" in [k["kpk"] for k in doc["kpks"]] and len(doc["kpks"]) > 1)
    check("η εξαγωγή του χρήστη ΔΕΝ κουβαλά ανάλυση κλάδων",
          all("components" not in r for k in doc["kpks"] for r in k["rates"]))
    check("οι προτιμήσεις («Διάφορα») ταξιδεύουν μαζί",
          doc["misc"]["save_zip"] is True
          and doc["misc"]["show_employer_cost"] is False)
    check("η αλλαγμένη προθεσμία ταξιδεύει μαζί",
          any(t["code"] == "APD_SUBMISSION" and t["due"] == ["eom", 1]
              and t["extension"]["max_days"] == 20
              for t in doc["obligation_types"]))

    # ------------------------------- 2. ΦΟΡΤΩΣΗ ΣΕ ΚΑΘΑΡΟ ΣΥΣΤΗΜΑ (σύστημα Β)
    # ⚠️ Τα overrides είναι κατάσταση ΔΙΕΡΓΑΣΙΑΣ (ARCH_REF.md §1α): το σύστημα Β
    # ξεκινά μόνο αφού καθαριστούν, αλλιώς θα «έβλεπε» τις τιμές του Α.
    config.clear_overrides()
    ot.clear_overrides()
    _store_b, _proj_b, svc_b = _fresh_service()
    check("το άδειο σύστημα Β δεν έχει επιβεβαιωμένες παραμέτρους",
          not svc_b.parameters_confirmed())

    res = svc_b.import_parameters(raw)
    check(f"η εισαγωγή δεν έβγαλε σφάλματα ({'; '.join(res['errors'][:3])})",
          not res["errors"])
    check("εισήχθησαν γραμμές παραμέτρων", res["counts"]["parameters"] > 0)
    check("εισήχθη ΟΛΟ το Μητρώο ΚΠΚ",
          res["counts"]["kpks"] == len(doc["kpks"]))
    # Όχι `== 1`: εισάγεται ΚΑΘΕ τύπος που διαφέρει από τον κώδικα — και το
    # data/init_parameters.json έχει ήδη δικές του προθεσμίες.
    check("εισήχθη η αλλαγμένη προθεσμία",
          res["counts"]["obligation_types"] >= 1
          and tuple(ot.spec("APD_SUBMISSION").due) == ("eom", 1)
          and ot.spec("APD_SUBMISSION").extension.get("max_days") == 20)
    check("το σύστημα Β έχει πλέον επιβεβαιωμένες παραμέτρους",
          svc_b.parameters_confirmed())

    # ---------------------------------- 3. ΙΔΙΕΣ ΤΙΜΕΣ (ο κύκλος κλείνει)
    again = svc_b.export_parameters()
    expected = json.loads(raw.decode("utf-8"))
    for section in ("families", "obligation_types", "misc", "kpks"):
        same = json.dumps(again[section], sort_keys=True, ensure_ascii=False) == (
            json.dumps(expected[section], sort_keys=True, ensure_ascii=False)
        )
        check(f"«{section}»: ίδιες τιμές μετά τον κύκλο εξαγωγή -> εισαγωγή",
              same)

    # -------------------------------------------- 4. ΛΑΘΟΣ/ΑΓΝΩΣΤΟ ΑΡΧΕΙΟ
    for bad, why in (
        (b"{", "μη έγκυρο JSON"),
        (b'{"schema": "misevent.company"}', "αρχείο άλλου τύπου"),
    ):
        try:
            svc_b.import_parameters(bad)
            fails.append(f"το «{why}» πέρασε χωρίς σφάλμα")
        except ValueError:
            print(f"OK   {why}: ValueError με ελληνικό μήνυμα")

    # Μια λάθος γραμμή ΔΕΝ ρίχνει όλη την εισαγωγή — επιστρέφεται ως σφάλμα
    partial = json.loads(raw.decode("utf-8"))
    partial["families"]["minimum_wage"]["entries"][0]["values"]["monthly"] = -5
    res = svc_b.import_parameters(json.dumps(partial).encode("utf-8"))
    check("η λάθος γραμμή επιστρέφεται ως σφάλμα, δεν χάνεται σιωπηλά",
          any("ΓΣΣΕ" in e for e in res["errors"]))
    check("οι υπόλοιπες γραμμές μπήκαν κανονικά",
          res["counts"]["parameters"] > 0)

    config.clear_overrides()
    ot.clear_overrides()
    assert not fails, "ΑΠΟΤΥΧΙΕΣ: " + "; ".join(fails)


def test_parameters_page_is_system_wide(fresh_system):
    """Η σελίδα ανήκει στο ΣΥΣΤΗΜΑ: μενού «Σύστημα», χωρίς απαίτηση ενεργής
    εταιρείας — και τα κουμπιά εξαγωγής/εισαγωγής δουλεύουν από το UI."""
    import json

    import _bootstrap

    import webapp
    from webapp.labels import nav_for

    fails = []

    def check(desc, cond):
        print(("OK   " if cond else "ΛΑΘΟΣ ") + desc)
        if not cond:
            fails.append(desc)

    groups = {g["key"]: [i["endpoint"] for i in g["items"]]
              for g in nav_for(True, "settings.index")}
    check("οι «Παράμετροι» είναι στο μενού «Σύστημα»",
          "settings.index" in groups.get("system", []))
    check("δεν έμειναν και στις «Ρυθμίσεις» της εταιρείας",
          "settings.index" not in groups.get("setup", []))
    check("η σελίδα δεν απαιτεί ΕΝΕΡΓΗ εταιρεία",
          "settings." in webapp.SYSTEM_ENDPOINTS)
    check("μένει ΜΟΝΟ για διαχειριστές",
          "settings." in webapp.ADMIN_ENDPOINTS
          and "settings.index" not in
          [i["endpoint"] for g in nav_for(False, "") for i in g["items"]])

    app = webapp.create_app()
    client = _bootstrap.ui_client(app)

    page = client.get("/settings/")
    check("η σελίδα ανοίγει", page.status_code == 200)
    check("δείχνει την κάρτα του αρχείου παραμέτρων",
          "Αρχείο παραμέτρων" in page.get_data(as_text=True))

    dump = client.get("/settings/export")
    check("η εξαγωγή κατεβάζει αρχείο JSON",
          dump.status_code == 200 and dump.mimetype == "application/json")
    doc = json.loads(dump.get_data(as_text=True))
    check("το κατεβασμένο αρχείο είναι αρχείο παραμέτρων",
          doc["schema"] == "misevent.parameters")

    load = client.post(
        "/settings/import",
        data={"file": (__import__("io").BytesIO(dump.get_data()), "params.json")},
        content_type="multipart/form-data",
    )
    check("η εισαγωγή απαντά με ανανεωμένο panel",
          load.status_code == 200 and "set-panel" in load.get_data(as_text=True))
    flash = json.loads(load.headers.get("HX-Trigger", "{}")).get("flash", {})
    check(f"η εισαγωγή πέτυχε ({flash.get('msg', '')})", not flash.get("err"))

    assert not fails, "ΑΠΟΤΥΧΙΕΣ: " + "; ".join(fails)


def test_initial_values_come_from_the_data_file(fresh_system, tmp_path):
    """ΑΡΧΙΚΟΠΟΙΗΣΗ ΑΠΟ ΑΡΧΕΙΟ: το πρώτο τρέξιμο διαβάζει το
    `data/init_parameters.json`, ΟΧΙ hardcoded λίστες. Νέα ΓΣΣΕ στις αρχικές
    τιμές = αλλαγή ενός JSON.

    Ελέγχεται και ότι το ΠΡΑΓΜΑΤΙΚΟ αρχείο του repo φορτώνεται καθαρά — αν
    κάποιος το χαλάσει, σκάει εδώ και όχι στον πρώτο χρήστη."""
    import json
    import os
    from datetime import date

    from misevent.app.services.parameters import INIT_PARAMETERS_FILE
    from misevent.rules import config
    from misevent.rules import obligation_types as ot

    fails = []

    def check(desc, cond):
        print(("OK   " if cond else "ΛΑΘΟΣ ") + desc)
        if not cond:
            fails.append(desc)

    # ------------------------------- 1. ΤΟ ΑΡΧΕΙΟ ΤΟΥ REPO ΕΙΝΑΙ Η ΠΗΓΗ
    check("το data/init_parameters.json υπάρχει", INIT_PARAMETERS_FILE.exists())
    _s, _p, svc = _fresh_service()
    written = svc.confirm_default_parameters()
    check(f"το πρώτο τρέξιμο έγραψε γραμμές ({written})", written > 0)
    check("κάθε ΚΡΙΣΙΜΗ οικογένεια πήρε τιμές", svc.parameters_confirmed())
    doc = json.loads(INIT_PARAMETERS_FILE.read_text(encoding="utf-8"))
    check("το πρώτο τρέξιμο γέμισε ΚΑΙ το Μητρώο ΚΠΚ",
          [k["kpk"] for k in svc.kpk_entries()]
          == [k["kpk"] for k in doc["kpks"]])
    check("τα ΚΠΚ ήρθαν με ανάλυση κλάδων (έλεγχος διπλοχρέωσης)",
          any(r["components"] for k in svc.kpk_entries() for r in k["series"]))
    rows = doc["families"]["minimum_wage"]["entries"]
    newest = max(rows, key=lambda r: r["valid_from"])
    check("η ΓΣΣΕ ήρθε από το αρχείο, όχι από το config",
          config.minimum_wage_for(date(2099, 1, 1)).monthly
          == newest["values"]["monthly"])

    # ------------------------- 2. ΑΛΛΑΖΩ ΤΟ ΑΡΧΕΙΟ -> ΑΛΛΑΖΟΥΝ ΟΙ ΤΙΜΕΣ
    # Αν το αρχείο αγνοούνταν σιωπηλά, ΕΔΩ θα φαινόταν: η τιμή θα έμενε αυτή
    # του config.py.
    custom = dict(doc)
    custom["families"] = dict(doc["families"])
    custom["families"]["minimum_wage"] = {
        "entries": [{"valid_from": "2030-01-01", "repealed": False,
                     "values": {"monthly": 1234.56, "daily": 55.55}}]
    }
    custom["obligation_types"] = [
        {"code": "APD_SUBMISSION", "due": ["eom", 2],
         "extension": {"allowed": True, "max_days": 45}}
    ]
    custom["kpks"] = [{"carrier": "efka", "kpk": "0101", "name": "ΔΟΚΙΜΗ",
                       "tier": "main_auxiliary",
                       "rates": [{"valid_from": "2025-01-01",
                                  "employee": 0.1, "employer": 0.2}]}]
    path = tmp_path / "init_parameters.json"
    path.write_text(json.dumps(custom, ensure_ascii=False), encoding="utf-8")

    config.clear_overrides()
    ot.clear_overrides()
    os.environ["MISEVENT_INIT_PARAMETERS"] = str(path)
    try:
        _s2, _p2, svc2 = _fresh_service()
        svc2.confirm_default_parameters()
        check("η ΓΣΣΕ του αρχείου ισχύει",
              config.minimum_wage_for(date(2030, 6, 1)).monthly == 1234.56)
        check("οι υπόλοιπες οικογένειες ήρθαν κανονικά",
              svc2.parameters_confirmed())
        check("η προθεσμία του αρχείου ισχύει",
              tuple(ot.spec("APD_SUBMISSION").due) == ("eom", 2))
        check("το Μητρώο ΚΠΚ του αρχείου ισχύει",
              [k["kpk"] for k in svc2.kpk_entries()] == ["0101"])

        # 3. ΧΑΛΑΣΜΕΝΟ ΑΡΧΕΙΟ: ΣΚΑΕΙ, δεν υποχωρεί σιωπηλά στο config
        path.write_text("{ όχι json", encoding="utf-8")
        config.clear_overrides()
        _s3, _p3, svc3 = _fresh_service()
        try:
            svc3.confirm_default_parameters()
            fails.append("το χαλασμένο αρχείο αρχικών τιμών πέρασε αθόρυβα")
        except ValueError as exc:
            check(f"χαλασμένο αρχείο -> ValueError ({exc})", True)
    finally:
        os.environ.pop("MISEVENT_INIT_PARAMETERS", None)

    # ----------------- 4. ΑΝ ΛΕΙΠΕΙ ΤΟ ΑΡΧΕΙΟ: οι τιμές του config.py
    config.clear_overrides()
    os.environ["MISEVENT_INIT_PARAMETERS"] = str(tmp_path / "δεν-υπάρχει.json")
    try:
        _s4, _p4, svc4 = _fresh_service()
        svc4.confirm_default_parameters()
        expected = max(config.MINIMUM_WAGE_HISTORY, key=lambda e: e.valid_from)
        check("χωρίς αρχείο, εφεδρεία οι προτεινόμενες τιμές του config",
              config.minimum_wage_for(date(2099, 1, 1)).monthly == expected.monthly)
    finally:
        os.environ.pop("MISEVENT_INIT_PARAMETERS", None)

    config.clear_overrides()
    ot.clear_overrides()
    assert not fails, "ΑΠΟΤΥΧΙΕΣ: " + "; ".join(fails)
