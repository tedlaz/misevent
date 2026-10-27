"""
ΠΟΛΛΕΣ ΕΤΑΙΡΕΙΕΣ: απομόνωση δεδομένων + ΚΟΙΝΕΣ νομοθετικές παράμετροι.

Ελέγχει ότι:
  • δύο εταιρείες έχουν ΞΕΧΩΡΙΣΤΑ δεδομένα (πρόσωπα/εταιρεία/read model)·
  • μια νέα ΓΣΣΕ που γράφεται από τη ΜΙΑ ισχύει ΑΜΕΣΩΣ και στην ΑΛΛΗ·
  • το κοινό event ΔΕΝ γράφεται στο τοπικό log της εταιρείας·
  • το rebuild κάθε εταιρείας δίνει το ΙΔΙΟ αποτέλεσμα (κανόνας #3).

Τρέξιμο:  uv run tests/test_tenancy.py
"""


def test_tenancy():
    from datetime import date

    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    # Ο ΠΡΟΣΩΡΙΝΟΣ φάκελος δεδομένων στήνεται από το `_bootstrap`.
    from misevent.rules import config  # noqa: E402
    from webapp.tenancy import Registry  # noqa: E402

    fails = []


    def check(desc, cond):
        print(("OK   " if cond else "ΛΑΘΟΣ ") + desc)
        if not cond:
            fails.append(desc)


    def person(svc, afm, amka, last):
        return svc.register_person(
            last_name=last,
            first_name="ΤΕΣΤ",
            father_name="ΠΑΤΗΡ",
            mother_name="ΜΗΤΗΡ",
            birth_date=date(1990, 5, 5),
            afm=afm,
            amka=amka,
        )


    reg = Registry()
    a_id = reg.create_company("ΔΕΛΤΑ ΑΕ")
    b_id = reg.create_company("ΕΨΙΛΟΝ ΕΠΕ")
    check("δύο ξεχωριστοί χώροι εταιρειών", a_id != b_id)

    a, b = reg.tenant(a_id), reg.tenant(b_id)
    check("ξεχωριστό store ανά εταιρεία", a.store_name != b.store_name)

    # -- 1. ΑΠΟΜΟΝΩΣΗ ΔΕΔΟΜΕΝΩΝ ---------------------------------------
    a.svc.register_company(
        ame="1234567890",
        afm="999888771",
        legal_name="ΔΕΛΤΑ ΑΝΩΝΥΜΗ ΕΤΑΙΡΕΙΑ",
        local_office_code="1150706000",
        local_office_name="ΤΟΠΙΚΗ Α",
    )
    b.svc.register_company(
        ame="1234567891",
        afm="094014201",
        legal_name="ΕΨΙΛΟΝ ΕΠΕ",
        local_office_code="1150706000",
        local_office_name="ΤΟΠΙΚΗ Α",
    )
    person(a.svc, "999888771", "05059010008", "ΑΛΦΑΙΟΣ")

    check("η Α έχει 1 πρόσωπο", len(a.proj.persons) == 1)
    check("η Β έχει 0 πρόσωπα (απομόνωση)", len(b.proj.persons) == 0)
    check("κάθε εταιρεία βλέπει ΤΗ ΔΙΚΗ ΤΗΣ επωνυμία",
          a.proj.company.legal_name == "ΔΕΛΤΑ ΑΝΩΝΥΜΗ ΕΤΑΙΡΕΙΑ"
          and b.proj.company.legal_name == "ΕΨΙΛΟΝ ΕΠΕ")
    # Η απομόνωση είναι ΛΟΓΙΚΗ πλέον: ΙΔΙΑ βάση, ίδιο connection, ξεχωριστό
    # store — γι' αυτό ελέγχουμε ότι κανένα store δεν βλέπει τα events του
    # άλλου, όχι απλώς ότι τα αρχεία διαφέρουν.
    check("μία βάση για όλες τις εταιρείες",
          a.store.conn is b.store.conn and a.store.db is reg.db)
    check("η απομόνωση κρατά: κάθε store βλέπει ΜΟΝΟ τα δικά του events",
          a.store.count_by_type("CompanyRegistered") == 1
          and b.store.count_by_type("CompanyRegistered") == 1
          and a.store.event_count() != b.store.event_count())

    # -- 2. ΚΟΙΝΕΣ ΠΑΡΑΜΕΤΡΟΙ -----------------------------------------
    a.svc.confirm_default_parameters()
    on = date(2031, 3, 1)
    a.svc.define_minimum_wage(on, 1234.0, 55.0)

    check("η νέα ΓΣΣΕ φαίνεται στην εταιρεία που τη δήλωσε",
          any(p.valid_from == on for p in a.proj.parameters["minimum_wage"].values()))
    check("η νέα ΓΣΣΕ φαίνεται ΑΜΕΣΩΣ και στην ΑΛΛΗ εταιρεία",
          any(p.valid_from == on for p in b.proj.parameters["minimum_wage"].values()))
    check("και ισχύει στους υπολογισμούς (config overrides)",
          config.minimum_wage_for(on).monthly == 1234.0)

    local = a.store.count_by_type("ParametersDefined")
    shared = reg.shared.count_by_type("ParametersDefined")
    check("το κοινό event ΔΕΝ γράφτηκε στο τοπικό log", local == 0)
    check("γράφτηκε ΜΙΑ φορά στο κοινό log", shared >= 1)

    # -- 2β. ΜΗΤΡΩΟ ΚΠΚ: ΘΕΣΜΙΚΟ, ΟΧΙ ΕΤΑΙΡΙΚΟ -----------------------
    # Τα ΚΠΚ ορίζονται από ΕΦΚΑ/ΤΕΚΑ και ισχύουν για ΟΛΟΥΣ τους εργοδότες.
    a.svc.define_kpk("efka", "0999", "ΔΟΚΙΜΑΣΤΙΚΟ ΚΠΚ", "main")
    a.svc.define_kpk_rates("efka_main", "0999", date(2030, 1, 1), 0.10, 0.20)

    check("το ΚΠΚ φαίνεται στην εταιρεία που το δήλωσε",
          a.svc.kpk_identity("efka_main", "0999") is not None)
    check("το ΚΠΚ φαίνεται ΑΜΕΣΩΣ και στην ΑΛΛΗ εταιρεία",
          b.svc.kpk_identity("efka_main", "0999") is not None)
    check("τα ποσοστά του ΚΠΚ ισχύουν και στην ΑΛΛΗ εταιρεία",
          "0999" in b.svc.known_kpks("efka_main"))
    check("το ΚΠΚ ΔΕΝ γράφτηκε στο τοπικό log της εταιρείας",
          a.store.count_by_type("KPKDefined") == 0
          and a.store.count_by_type("KPKRatesDefined") == 0)
    check("γράφτηκε στο ΚΟΙΝΟ log",
          reg.shared.count_by_type("KPKDefined") >= 1
          and reg.shared.count_by_type("KPKRatesDefined") >= 1)

    # -- 3. ΝΕΑ ΕΤΑΙΡΕΙΑ ΚΛΗΡΟΝΟΜΕΙ ΤΙΣ ΠΑΡΑΜΕΤΡΟΥΣ -------------------
    c = reg.tenant(reg.create_company("ΖΗΤΑ ΟΕ"))
    check("νεοσύστατη εταιρεία βρίσκει τις κοινές παραμέτρους",
          any(p.valid_from == on for p in c.proj.parameters["minimum_wage"].values()))
    check("νεοσύστατη εταιρεία βρίσκει και το μητρώο ΚΠΚ",
          c.svc.kpk_identity("efka_main", "0999") is not None)
    check("και ξεκινά χωρίς δικά της δεδομένα",
          c.proj.company is None and not c.proj.persons)

    # -- 4. REBUILD ΔΙΝΕΙ ΤΟ ΙΔΙΟ -------------------------------------
    before = (len(a.proj.persons), a.proj.company.legal_name,
              len(a.proj.parameters["minimum_wage"]))
    a.rebuild_all()
    after = (len(a.proj.persons), a.proj.company.legal_name,
             len(a.proj.parameters["minimum_wage"]))
    check("rebuild δίνει το ίδιο", before == after)
    check("και δεν «μολύνει» την άλλη εταιρεία", len(b.proj.persons) == 0)


    print()
    assert not fails, "ΑΠΟΤΥΧΙΕΣ: " + "; ".join(str(x) for x in fails)
    print("OK - πολυ-εταιρική απομόνωση + κοινές παράμετροι")
