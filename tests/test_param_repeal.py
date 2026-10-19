"""
Tests ΚΑΤΑΡΓΗΣΗΣ κανόνα παραμέτρων (`repealed`) + του μητρώου `config.FAMILIES`.

Ο χρήστης θέλει «διαγραφή παλιών κανόνων». Σε event sourcing δεν υπάρχει
διαγραφή (κανόνας #2) — υπάρχουν ΤΡΙΑ διαφορετικά πράγματα:

  1. ΑΝΤΙΚΑΤΑΣΤΑΣΗ  — ίδιο (family, valid_from) = νέο event υπερισχύει
  2. ΚΑΤΑΡΓΗΣΗ      — `repealed=True` από μια ημερομηνία και μετά
  3. ΑΦΑΙΡΕΣΗ ΑΠΟ ΤΟ LOG — δεν υπάρχει (μόνο διαχειριστικό rollback)

Εδώ κλειδώνονται τα (1) και (2), και ότι το replay τα δίνει ίδια.
"""


def test_param_repeal():
    import os
    import tempfile
    from datetime import date

    import _bootstrap  # noqa: F401

    from misevent.app.services import PayrollService
    from misevent.domain.projections import Projections
    from misevent.persistence.database import Database
    from misevent.rules import config

    fails = []


    def check(cond, msg):
        if not cond:
            fails.append(msg)


    def fresh():
        path = os.path.join(tempfile.mkdtemp(), "repeal.db")
        store = Database(path).store("test")
        proj = Projections()
        store.subscribe(proj.handle)
        return path, store, proj, PayrollService(store, proj)


    # ------------------------------------------------------- 1. ΑΝΤΙΚΑΤΑΣΤΑΣΗ
    path, store, proj, svc = fresh()
    svc.define_minimum_wage(date(2026, 4, 1), 920.0, 41.09)
    svc.define_minimum_wage(date(2026, 4, 1), 950.0, 42.0)  # ΙΔΙΟ valid_from
    check(
        config.minimum_wage_for(date(2026, 6, 1)).monthly == 950.0,
        "Η αντικατάσταση με ίδιο valid_from δεν υπερίσχυσε",
    )
    entries, _src = config.effective_entries("minimum_wage")
    check(len(entries) == 1, f"Η αντικατάσταση άφησε {len(entries)} εγγραφές αντί για 1")

    # ------------------------------------------------------------ 2. ΚΑΤΑΡΓΗΣΗ
    svc.define_minimum_wage(date(2027, 1, 1), 1000.0, 44.0)
    check(config.minimum_wage_for(date(2027, 6, 1)).monthly == 1000.0, "Λάθος ΓΣΣΕ 2027")

    svc.repeal_parameters("minimum_wage", date(2028, 1, 1))

    # πριν την κατάργηση: ισχύει κανονικά
    check(
        config.minimum_wage_for(date(2027, 12, 31)).monthly == 1000.0,
        "Η κατάργηση επηρέασε ΠΡΟΓΕΝΕΣΤΕΡΗ ημερομηνία",
    )
    # μετά: ΣΚΑΕΙ — ΔΕΝ υποχωρεί σιωπηλά στην προηγούμενη γραμμή
    try:
        config.minimum_wage_for(date(2028, 6, 1))
        fails.append("Μετά την κατάργηση ΔΕΝ έσκασε — υποχώρησε σιωπηλά")
    except ValueError as exc:
        check(
            "καταργήθηκε" in str(exc),
            f"Το μήνυμα κατάργησης δεν εξηγεί τι έγινε: {exc}",
        )

    # η κατάργηση ΔΕΝ μετράει ως νέα ΓΣΣΕ (δεν γεννά WAGE_ADJUSTMENT)
    active = config.minimum_wage_entries()
    check(
        all(not e.repealed for e in active),
        "Το minimum_wage_entries() επιστρέφει και καταργημένες γραμμές",
    )
    check(
        len(active) == 2,
        f"Περίμενα 2 ενεργές γραμμές ΓΣΣΕ, βρήκα {len(active)}",
    )

    # διπλή κατάργηση απορρίπτεται
    try:
        svc.repeal_parameters("minimum_wage", date(2029, 1, 1))
        fails.append("Η διπλή κατάργηση έγινε δεκτή")
    except ValueError as exc:
        check("ήδη καταργηθεί" in str(exc), f"Λάθος μήνυμα διπλής κατάργησης: {exc}")

    # άγνωστη οικογένεια
    try:
        svc.repeal_parameters("δεν_υπάρχει", date(2028, 1, 1))
        fails.append("Δεκτή κατάργηση άγνωστης οικογένειας")
    except ValueError:
        pass

    # ------------------------------------------------- 3. ΤΟ REPLAY ΔΙΝΕΙ ΤΟ ΙΔΙΟ
    store2 = Database(path).store("test")
    proj2 = Projections()
    # ⚠️ Το store.load() ΕΠΙΣΤΡΕΦΕΙ events, δεν τα στέλνει στους subscribers.
    # Το replay γίνεται ρητά με proj.rebuild(store) — όπως στο webapp/tenancy.py.
    proj2.rebuild(store2)
    store2.subscribe(proj2.handle)
    try:
        config.minimum_wage_for(date(2028, 6, 1))
        fails.append("Μετά το replay η κατάργηση χάθηκε")
    except ValueError:
        pass
    check(
        config.minimum_wage_for(date(2027, 12, 31)).monthly == 1000.0,
        "Μετά το replay άλλαξε η ΓΣΣΕ του 2027",
    )

    # -------------------------- 4. REBUILD: το μητρώο ξαναχτίζεται πλήρως
    rebuilt = Projections()
    rebuilt.rebuild(store2)
    entries = rebuilt.parameters["minimum_wage"]
    check(len(entries) == 3, f"Περίμενα 3 εγγραφές ΓΣΣΕ, βρήκα {len(entries)}")
    newest = entries[max(entries)]
    check(
        getattr(newest, "repealed", False) is True,
        "Η κατάργηση δεν επιβίωσε του rebuild",
    )

    # ------------------------------------- 5. ΚΑΘΕ ΟΙΚΟΓΕΝΕΙΑ ΜΠΟΡΕΙ ΝΑ ΚΑΤΑΡΓΗΘΕΙ
    path2, store3, proj3, svc3 = fresh()
    svc3.confirm_default_parameters()
    for family in config.FAMILIES:
        try:
            svc3.repeal_parameters(family, date(2030, 1, 1))
        except ValueError as exc:
            fails.append(f"{family}: η κατάργηση απέτυχε — {exc}")
    for family, spec in config.FAMILIES.items():
        entries, _s = config.effective_entries(family)
        last = max(entries, key=lambda e: e.valid_from)
        check(
            getattr(last, "repealed", False),
            f"{family}: η τελευταία εγγραφή δεν είναι καταργημένη",
        )

    config.clear_overrides()

    assert not fails, "ΑΠΟΤΥΧΙΕΣ: " + "; ".join(str(x) for x in fails)
    print("OK - κατάργηση κανόνα: αντικατάσταση, κατάργηση, replay, rebuild, 7 οικογένειες")
