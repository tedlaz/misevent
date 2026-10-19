"""Ημέρες ΕΦΚΑ που ΠΡΟΤΕΙΝΕΙ το συγκεντρωτικό όταν η απασχόληση ΔΕΝ καλύπτει
όλο τον μήνα (πρόσληψη ή αποχώρηση στη μέση).

Ο κανόνας (§7): ο μισθωτός παίρνει **συμβατική βάση** ημερών ασφάλισης (25 σε
πλήρη απασχόληση) **ΑΝΑΛΟΓΙΚΑ** προς τις ημέρες προγράμματος του μήνα. Ο
παρονομαστής είναι ο ΠΛΗΡΗΣ μήνας — αλλιώς ο εργαζόμενος που προσλήφθηκε στις
16 θα έπαιρνε ολόκληρες τις 25 ημέρες (η αναλογία θα ήταν 1).
"""


def test_efka_days():
    import os
    from datetime import date

    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from misevent.app.services import PayrollService
    from misevent.domain.projections import Projections
    from misevent.persistence.database import Database
    from misevent.rules import constraints


    def _afm(i):
        """Συνθετικό ΑΦΜ ΜΕ σωστό ψηφίο ελέγχου (constraints.check_afm)."""
        base = f"11111{i:03d}"
        total = sum(int(d) << (8 - k) for k, d in enumerate(base))
        return base + str(total % 11 % 10)


    def _amka(i):
        """Συνθετικό ΑΜΚΑ: ΗΗΜΜΕΕ γέννησης + αύξων + ψηφίο Luhn."""
        base = f"010190{i:04d}"
        for d in "0123456789":
            if constraints.amka_checksum_ok(base + d):
                return base + d
        raise AssertionError("αδύνατο ψηφίο ελέγχου ΑΜΚΑ")

    DB = "test_efka_days.db"
    WEEK5 = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}
    WEEK5_PART = {d: "09:00-13:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}

    fails = []
    _n = [0]


    def setup():
        if os.path.exists(DB):
            os.remove(DB)
        store = Database(DB).store("test")
        proj = Projections()
        store.subscribe(proj.handle)
        svc = PayrollService(store, proj)
        svc.confirm_default_parameters()
        svc.register_company(ame="1234567890", afm="123456783", legal_name="ΔΟΚΙΜΗ")
        svc.define_branch(0)
        svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
        svc.define_kpk_rates(
            "efka_main", "0101", date(2020, 1, 1), 0.1337, 0.2229,
            "main_pension=10,37/19,29|healthcare=0/0|auxiliary=3/3",
        )
        svc.define_specialty(
            "ΥΠΑΛΛΗΛΟΣ",
            {"MAIN_EPIK": {"kad": "5510", "specialty_code": "513100",
                           "kpk": "0101"}},
        )
        return store, proj, svc


    def hire(svc, hire_date, weekly_hours=40.0, pattern=None, gross=1200.0):
        _n[0] += 1
        i = _n[0]
        pid = svc.register_person(
            last_name=f"ΔΟΚΙΜΗ{i}", first_name="ΝΙΚΟΣ", father_name="ΓΙΩΡΓΟΣ",
            mother_name="ΜΑΡΙΑ", birth_date=date(1990, 1, 1),
            afm=_afm(i), amka=_amka(i), ama=f"1111111{i:02d}",
        )
        eid = svc.start_employment(
            pid, hire_date=hire_date, pay_type="monthly", monthly_gross=gross,
            weekly_hours=weekly_hours, weekly_pattern=pattern or WEEK5,
        )
        svc.assign_specialty(eid, "ΥΠΑΛΛΗΛΟΣ")
        # Η πρόσληψη ολοκληρώνεται με την ΑΝΑΓΓΕΛΙΑ (§5.7α): χωρίς αυτήν η
        # απασχόληση είναι «εκκρεμής» και δεν μπαίνει σε παρουσίες/μισθοδοσία.
        svc.record_external_hire_submission(eid)
        return eid


    def check(desc, got, want):
        ok = got == want
        print(f"  {'OK  ' if ok else 'FAIL'} {desc}: {got} (αναμ. {want})")
        if not ok:
            fails.append(f"{desc}: {got} αντί {want}")


    store, proj, svc = setup()

    # Μάρτιος 2026: 22 ημέρες πενθημέρου προγράμματος (η 25/3 είναι Τετάρτη και
    # ΠΡΟΒΛΕΠΕΤΑΙ από το πρόγραμμα — αργία, όχι ρεπό).
    print("=== Πρόσληψη στη ΜΕΣΗ του μήνα (16/3/2026, Δευτέρα) ===")
    eid = hire(svc, date(2026, 3, 16))
    d = svc.attendance_draft(2026, 3)[eid]
    check("ημέρες προγράμματος από την πρόσληψη", d["planned"], 12)
    check("παρονομαστής = ΠΛΗΡΗΣ μήνας", d["month_planned"], 22)
    # 25 x 12/22 = 13,6 -> 14 — όσες και οι μη Κυριακές από 16 έως 31/3
    check("προτεινόμενες ημέρες ΕΦΚΑ", d["efka_days"], 14)

    print("\n=== Πρόσληψη την 1η του μήνα -> ολόκληρη η βάση ===")
    eid = hire(svc, date(2026, 3, 2))  # 1/3/2026 = Κυριακή
    d = svc.attendance_draft(2026, 3)[eid]
    check("ημέρες ΕΦΚΑ", d["efka_days"], 25)

    print("\n=== Πρόσληψη ΠΡΙΝ τον μήνα -> ολόκληρη η βάση ===")
    eid = hire(svc, date(2026, 1, 5))
    d = svc.attendance_draft(2026, 3)[eid]
    check("ημέρες ΕΦΚΑ", d["efka_days"], 25)

    print("\n=== ΑΠΟΧΩΡΗΣΗ στη μέση του μήνα ===")
    eid = hire(svc, date(2026, 1, 5))
    svc.terminate(eid, date(2026, 3, 13), reason="ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
    d = svc.attendance_draft(2026, 3)[eid]
    check("παρονομαστής = ΠΛΗΡΗΣ μήνας", d["month_planned"], 22)
    check("ημέρες ΕΦΚΑ", d["efka_days"], round(25 * d["worked"] / 22))

    print("\n=== ΜΕΡΙΚΗ απασχόληση, πρόσληψη στη μέση ===")
    # 20 ώρες/εβδομάδα -> βάση ⌈20/6,66⌉ x 4,33 = 3 x 4,33 = 13 ημέρες
    eid = hire(svc, date(2026, 3, 16), weekly_hours=20.0, pattern=WEEK5_PART, gross=600.0)
    d = svc.attendance_draft(2026, 3)[eid]
    check("παρονομαστής = ΠΛΗΡΗΣ μήνας", d["month_planned"], 22)
    check("ημέρες ΕΦΚΑ (βάση 13)", d["efka_days"], round(13 * 12 / 22))

    print("\n=== Το φύλλο ΜΕΤΑ την έγκριση οδηγεί τη μισθοδοσία ===")
    eid = hire(svc, date(2026, 3, 16))
    line = svc.attendance_draft(2026, 3)[eid]
    unpaid, efka = svc.payroll_days(proj.employments[eid], line)
    check("ημέρες ΕΦΚΑ μισθοδοσίας", efka, 14)
    check("ημέρες άνευ αποδοχών (25 - 14)", unpaid, 11)

    print()
    assert not fails, "ΑΠΟΤΥΧΙΕΣ: " + "; ".join(str(x) for x in fails)
    print("Ολα τα tests περασαν.")
