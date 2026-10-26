"""ΣΥΣΤΗΜΑ ΕΒΔΟΜΑΔΙΑΙΑΣ ΕΡΓΑΣΙΑΣ (5 πενθήμερο / 6 εξαήμερο) = ΟΡΟΣ ΣΥΜΒΑΣΗΣ.

Δηλώνεται στην πρόσληψη (`EmploymentStarted`) και αλλάζει με «Αλλαγή σύμβασης»
(`ContractChanged`) — ΟΧΙ στο ασφαλιστικό προφίλ, όπου ζούσε παλιότερα.
Ελέγχεται ότι:
  - η τιμή ταξιδεύει σύμβαση -> projections -> read model -> ΑΠΔ (No 55),
  - η μεταβολή χωρίς ρητή τιμή ΔΕΝ την αλλάζει,
  - τιμές εκτός {5,6} απορρίπτονται ΠΡΙΝ γραφτεί event.
"""


def test_weekly_system():
    from datetime import date

    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from misevent.app.services import PayrollService
    from misevent.domain.projections import Projections
    from misevent.persistence.database import Database

    FULL_WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}
    fails = []


    def check(label, cond):
        print(f"  {'OK  ' if cond else 'FAIL'} {label}")
        if not cond:
            fails.append(label)


    def make_svc():
        store = Database().store("test")
        proj = Projections()
        store.subscribe(proj.handle)
        svc = PayrollService(store, proj)
        svc.confirm_default_parameters()
        svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
        svc.define_branch(0)
        svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
        svc.define_kpk_rates(
            "efka_main", "0101", date(2020, 1, 1), 0.1387, 0.2481,
            "main_pension=10,87/21,81|healthcare=0/0|auxiliary=3/3",
        )
        svc.define_specialty("ΠΩΛΗΤΗΣ", {
            "MAIN_EPIK": {"kad": "0110", "specialty_code": "122100", "kpk": "0101"},
        },
    ergani_specialty_code="419", ergani_specialty_description="ΛΟΙΠΟΙ ΥΠΑΛΛΗΛΟΙ ΓΡΑΦΕΙΟΥ",)
        return store, proj, svc


    def hire(svc, i=1, **kw):
        pid = svc.register_person(
            last_name=f"ΔΟΚΙΜΗ{i}", first_name="ΝΙΚΟΣ", father_name="Γ", mother_name="Μ",
            birth_date=date(1990, 5, 5),
            afm=("111222336" if i == 1 else "222333440"),
            amka=("05059012343" if i == 1 else "03039012343"),
            ama="123456789",
        )
        eid = svc.start_employment(
            pid, date(2026, 1, 1), pay_type="monthly", monthly_gross=1500.0,
            weekly_hours=40.0, weekly_pattern=FULL_WEEK, **kw,
        )
        svc.assign_specialty(eid, "ΠΩΛΗΤΗΣ")
        return eid


    print("=== Πρόσληψη: το σύστημα είναι όρος σύμβασης ===")
    store, proj, svc = make_svc()
    eid = hire(svc, weekly_system=6)
    check("εξαήμερο από την πρόσληψη", proj.employments[eid].weekly_system == 6)
    eid5 = hire(svc, i=2)  # χωρίς ρητή τιμή -> default πενθήμερο
    check("default πενθήμερο", proj.employments[eid5].weekly_system == 5)

    print("")
    print("=== Replay: η προβολή ξαναχτίζεται ίδια ===")
    rebuilt = Projections()
    rebuilt.rebuild(store)
    check("weekly_system = 6 μετά το replay", rebuilt.employments[eid].weekly_system == 6)

    print("\n=== Αλλαγή σύμβασης ===")
    svc.change_contract(eid, date(2026, 3, 1), "monthly", monthly_gross=1600.0,
                        weekly_hours=40.0, weekly_system=5)
    check("μεταβολή σε πενθήμερο", proj.employments[eid].weekly_system == 5)
    svc.change_contract(eid, date(2026, 4, 1), "monthly", monthly_gross=1700.0,
                        weekly_hours=40.0)  # χωρίς weekly_system
    check("μεταβολή ΧΩΡΙΣ τιμή δεν το αγγίζει",
          proj.employments[eid].weekly_system == 5)
    rebuilt.rebuild(store)
    check("το replay ακολουθεί τη μεταβολή",
          rebuilt.employments[eid].weekly_system == 5)

    print("\n=== Άκυρες τιμές: απορρίπτονται ΠΡΙΝ γραφτεί event ===")
    for bad in (0, 7, "x", 4.5):
        n = len(store.load())
        try:
            svc.change_contract(eid, date(2026, 5, 1), "monthly", monthly_gross=1700.0,
                                weekly_hours=40.0, weekly_system=bad)
            check(f"απόρριψη «{bad}»", False)
        except ValueError:
            check(f"απόρριψη «{bad}» χωρίς event", len(store.load()) == n)

    print("\n=== ΑΠΔ (No 55) — πηγή η ΣΥΜΒΑΣΗ, όχι το προφίλ ===")
    store3, proj3, svc3 = make_svc()
    eid3 = hire(svc3, weekly_system=6)
    svc3.submit_hire_e3(eid3)
    svc3.submit_hire_wto(eid3)
    svc3.approve_attendance(2026, 1)
    svc3.run_monthly_payroll(2026, 1)
    apd = [r for _, r in svc3.build_apd_rows(2026, 1)["efka"]]
    check("η γραμμή ΑΠΔ φέρει 6 (εξαήμερο)",
          bool(apd) and all(r.weekly_system == 6 for r in apd))
    # …και ακολουθεί τη ΜΕΤΑΒΟΛΗ σύμβασης
    svc3.change_contract(eid3, date(2026, 2, 1), "monthly", monthly_gross=1500.0,
                         weekly_hours=40.0, weekly_system=5)
    svc3.approve_attendance(2026, 2)
    svc3.run_monthly_payroll(2026, 2)
    apd2 = [r for _, r in svc3.build_apd_rows(2026, 2)["efka"]]
    check("μετά τη μεταβολή η ΑΠΔ φέρει 5 (πενθήμερο)",
          bool(apd2) and all(r.weekly_system == 5 for r in apd2))

    print()
    assert not fails, "ΑΠΟΤΥΧΙΕΣ: " + "; ".join(str(x) for x in fails)
    print("OK  όλα τα tests πέρασαν.")
