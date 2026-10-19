"""Αλγοριθμικός έλεγχος ΑΦΜ & ΑΜΚΑ στην ΠΗΓΗ (constraints + command layer).

ΑΦΜ  — ψηφίο ελέγχου mod 11 (ΑΑΔΕ): τα 8 πρώτα ψηφία με βάρη 2^8…2^1.
ΑΜΚΑ — 11 ψηφία: ΗΗΜΜΕΕ γέννησης + ψηφίο ελέγχου κατά Luhn.

Ο έλεγχος γίνεται ΠΡΙΝ γραφτεί event (§6): register_person / correct_person /
register_company απορρίπτουν λανθασμένο μητρώο, ώστε το λάθος να μη φτάσει
μήνες αργότερα στην ΑΠΔ ή στο ΦΜΥ.
"""


def test_afm_amka():
    import random
    from datetime import date

    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from misevent.app.services import PayrollService
    from misevent.domain.projections import Projections
    from misevent.persistence.database import Database
    from misevent.rules import constraints

    fails = []


    def check(label, cond):
        print(f"  {'OK  ' if cond else 'FAIL'} {label}")
        if not cond:
            fails.append(label)


    def rejects(label, fn, *a, **kw):
        """Περιμένει ValueError από το command layer."""
        try:
            fn(*a, **kw)
        except ValueError as exc:
            print(f"  OK   {label} — {exc}")
            return
        fails.append(label)
        print(f"  FAIL {label}: έγινε δεκτό (δεν έπρεπε)")


    print("=== Ψηφίο ελέγχου ΑΦΜ (mod 11) ===")
    # Έγκυρα (το ψηφίο ελέγχου υπολογισμένο με τον κανόνα)
    for good in ("111222336", "999888771", "044555666", "100011110"):
        check(f"έγκυρο ΑΦΜ {good}", constraints.afm_checksum_ok(good))
    # Άκυρα: λάθος ψηφίο ελέγχου, λάθος μήκος, μη ψηφία, μηδενικά
    for bad in ("111222333", "11122233", "1112223360", "11122233A", "000000000"):
        check(f"άκυρο ΑΦΜ {bad}", not constraints.afm_checksum_ok(bad))

    print("\n=== Ψηφίο ελέγχου & ημ. γέννησης ΑΜΚΑ (Luhn + ΗΗΜΜΕΕ) ===")
    for good in ("01019012341", "05059012343", "02029212343", "20079533442"):
        check(f"έγκυρο ΑΜΚΑ {good}", constraints.amka_checksum_ok(good)
              and constraints.amka_birth_ok(good))
    for bad in ("01019012345", "0101901234", "0101901234A", "00000000000"):
        check(f"άκυρο ΑΜΚΑ {bad}", not constraints.amka_checksum_ok(bad))


    def _amka_reference(amka: str) -> bool:
        """ΑΝΕΞΑΡΤΗΤΗ υλοποίηση αναφοράς του Luhn για ΑΜΚΑ (γνωστή σωστή), γραμμένη
        ΔΙΑΦΟΡΕΤΙΚΑ από την `constraints.amka_checksum_ok`: το ψηφίο ελέγχου μπαίνει
        αυτούσιο, τα ψηφία σε ΠΕΡΙΤΤΗ θέση των 10 πρώτων διπλασιάζονται και
        αθροίζονται τα ΨΗΦΙΑ του γινομένου.

        ⚠️ Μια παλιότερη εκδοχή διπλασίαζε τις ΑΡΤΙΕΣ θέσεις — δηλαδή και το ΙΔΙΟ
        το ψηφίο ελέγχου — και δεχόταν/απέρριπτε λάθος ΑΜΚΑ. Γι' αυτό ο έλεγχος
        γίνεται πλέον σε ΟΛΟΚΛΗΡΟ το φάσμα, όχι σε δείγματα."""
        if len(amka) != 11 or not amka.isdigit():
            return False
        d = [int(x) for x in amka]
        total = d[10]
        for i, digit in enumerate(d[:10]):
            total += sum(int(x) for x in str(digit * 2)) if i % 2 else digit
        return total % 10 == 0


    print("  … διασταύρωση με υλοποίηση αναφοράς σε 200.000 τυχαία ΑΜΚΑ")
    random.seed(20260729)
    diffs = [
        s for s in ("".join(random.choice("0123456789") for _ in range(11))
                    for _ in range(200_000))
        if s != "0" * 11 and constraints.amka_checksum_ok(s) != _amka_reference(s)
    ]
    check(f"καμία απόκλιση από την αναφορά (βρέθηκαν {len(diffs)})", not diffs)
    # Κάθε 10άδα πρέπει να έχει ΑΚΡΙΒΩΣ ΕΝΑ έγκυρο ψηφίο ελέγχου
    prefix = "0101901234"
    valid = [d for d in "0123456789" if constraints.amka_checksum_ok(prefix + d)]
    check(f"ακριβώς ένα έγκυρο ψηφίο ελέγχου ανά πρόθεμα ({valid})", len(valid) == 1)
    # 32/13 δεν είναι ημερομηνία· η 29/02 σε δίσεκτο έτος (2000) είναι
    check("άκυρη ημ. γέννησης 320190…", not constraints.amka_birth_ok("32019012345"))
    check("άκυρος μήνας 011390…", not constraints.amka_birth_ok("01139012345"))
    check("29/02/2000 δεκτή", constraints.amka_birth_ok("29020012345"))

    print("\n=== Command layer: register_person / correct_person ===")
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)

    person = dict(
        last_name="ΠΑΠΠΑΣ", first_name="ΝΙΚΟΣ", father_name="ΓΙΩΡΓΟΣ",
        mother_name="ΜΑΡΙΑ", birth_date=date(1990, 5, 5), ama="123456789",
    )
    rejects("ΑΦΜ με λάθος ψηφίο ελέγχου", svc.register_person,
            afm="111222333", amka="05059012343", **person)
    rejects("ΑΜΚΑ με λάθος ψηφίο ελέγχου", svc.register_person,
            afm="111222336", amka="05059012345", **person)
    rejects("ΑΜΚΑ με ανύπαρκτη ημ. γέννησης", svc.register_person,
            afm="111222336", amka="32059012340", **person)
    check("κανένα event δεν γράφτηκε από τις απορρίψεις", len(store.load()) == 0)

    pid = svc.register_person(afm="111222336", amka="05059012343", **person)
    check("έγκυρα μητρώα γίνονται δεκτά", pid in proj.persons)

    rejects("διόρθωση σε άκυρο ΑΦΜ", svc.correct_person, pid, afm="111222333")
    rejects("διόρθωση σε άκυρο ΑΜΚΑ", svc.correct_person, pid, amka="05059012345")
    svc.correct_person(pid, afm="222333440")
    check("διόρθωση σε έγκυρο ΑΦΜ", proj.persons[pid].afm == "222333440")

    print("\n=== Command layer: register_company (ΑΠΔ No 9) ===")
    rejects("ΑΦΜ εργοδότη με λάθος ψηφίο ελέγχου", svc.register_company,
            ame="1234567890", afm="999888777", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    check("έγκυρο ΑΦΜ εργοδότη", proj.company is not None)

    print()
    assert not fails, "ΑΠΟΤΥΧΙΕΣ: " + "; ".join(str(x) for x in fails)
    print("OK  όλα τα tests πέρασαν.")
