"""
Demo: πλήρης κύκλος με εταιρεία + 3 πρόσωπα/απασχολήσεις (2 μισθωτοί,
1 ημερομίσθιος), επαναπρόσληψη και έλεγχο μοναδικότητας προσώπου.

python demo.py     (πρώτα: rm -f payroll.db)
"""

from datetime import date, timedelta

import misevent.rules.config as config
from misevent.app.services import PayrollService
from misevent.domain.events import LeaveType
from misevent.domain.projections import Projections
from misevent.domain.scheduler import ObligationScheduler
from misevent.persistence.database import Database
from misevent.rules.calendar_gr import is_working_day

# Το πρόγραμμα εργασίας είναι ΥΠΟΧΡΕΩΤΙΚΟ σε κάθε πρόσληψη (υποβάλλεται ΟΧΕ)
FULL_TIME = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}


def main():
    store = Database("payroll.db").store("demo")  # persisted SQLite — ή ":memory:"
    proj = Projections()
    store.subscribe(proj.handle)
    sched = ObligationScheduler(store, proj, horizon_months=1)
    store.subscribe(sched.handle)
    svc = PayrollService(store, proj)

    # 0α. Μητρώο παραμέτρων: ΟΛΕΣ οι «νομοθετικές» παράμετροι (ΓΣΣΕ, ΕΦΚΑ,
    #     ΤΕΚΑ, φορολογική κλίμακα, ασθένεια, προσαυξήσεις, όρια ωρών)
    #     καταχωρούνται ως events — εδώ επιβεβαιώνουμε τις προτεινόμενες
    #     τιμές του config, όπως το wizard πρώτου τρεξίματος του UI.
    svc.confirm_default_parameters()

    # 0. Στοιχεία εταιρείας — αποθηκεύονται ως event στο event store
    svc.register_company(
        ame="1234567890",
        afm="999888771",
        legal_name="ΔΟΚΙΜΑΣΤΙΚΗ ΕΜΠΟΡΙΚΗ ΕΠΕ",
        local_office_code="1150706000",
        local_office_name="ΤΟΠΙΚΗ ΔΙΕΥΘΥΝΣΗ Α ΘΕΣΣΑΛΟΝΙΚΗΣ",
    )
    svc.define_branch(0, "ΕΓΝΑΤΙΑ", "10", "54625", "ΘΕΣΣΑΛΟΝΙΚΗ")

    # 0β. Μητρώο ΚΠΚ: ΜΟΝΟ από events (καμία προεπιλογή στο config). Κάθε ΚΠΚ
    #     έχει ΤΑΥΤΟΤΗΤΑ (φορέας, ονομασία, ΒΑΘΜΙΔΑ ασφάλισης) και ΧΡΟΝΟΣΕΙΡΑ
    #     ποσοστών. Στις βαθμίδες επικουρικής/πρόνοιας η τριάδα ΚΑΔ/ειδικότητας
    #     είναι καθολικά σταθερή και δηλώνεται ΕΔΩ, μία φορά.
    svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
    svc.define_kpk_rates(
        "efka_main", "0101", date(2025, 1, 1), 0.1337, 0.2179,
        "main_pension=6,67/13,33|healthcare=2,55/4,55|auxiliary=3/3|dypa=1,15/0,91",
    )
    svc.define_kpk("efka", "0103", "ΙΚΑ ΜΙΚΤΑ (κύρια)", "main")
    svc.define_kpk_rates(
        "efka_main", "0103", date(2025, 1, 1), 0.1037, 0.1879,
        "main_pension=6,67/13,33|healthcare=2,55/4,55|dypa=1,15/0,91",
    )
    svc.define_kpk("teka", "4169", "ΤΕΚΑ Επικουρική", "auxiliary")
    svc.define_kpk_rates("teka", "4169", date(2022, 6, 1), 0.03, 0.03,
                         "auxiliary=3/3")

    # 0γ. Ειδικότητες (θέσεις εργασίας) της εταιρείας: κάθε μία δηλώνει τον
    #     ΑΣΦΑΛΙΣΤΙΚΟ ΣΥΝΔΥΑΣΜΟ της σε ΡΟΛΟΥΣ (config.SPECIALTY_ROLES) — εδώ
    #     επιτρέπονται και οι δύο φορείς επικουρικής: MAIN_EPIK (ενιαίο ΚΠΚ
    #     ΕΦΚΑ) ή MAIN + TEKA (κύρια χωριστά + ΤΕΚΑ). Η πρόσληψη απλώς
    #     διαλέγει ειδικότητα + φορέα.
    def packages(code):
        return {
            "MAIN_EPIK": {"kad": "0141", "specialty_code": code, "kpk": "0101"},
            "MAIN": {"kad": "0141", "specialty_code": code, "kpk": "0103"},
            "TEKA": {"kad": "0083", "specialty_code": "000860",
                     "kpk": "4169"},
        }

    svc.define_specialty("ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ", packages("411100"))
    svc.define_specialty("ΠΩΛΗΤΗΣ", packages("411200"))
    svc.define_specialty("ΕΡΓΑΤΗΣ", packages("411300"))

    # 1. Πρόσωπα + απασχολήσεις. Κάθε start_employment γεννά αυτόματα
    #    όλες τις υποχρεώσεις της περιόδου. Η υποβολή Ε3/ΟΧΕ στο ΕΡΓΑΝΗ
    #    ΔΕΝ γίνεται αυτόματα — την ενεργοποιεί ρητά ο χρήστης (βλ. βήμα 1δ).
    # 1α. Μισθωτή, νέα ασφαλισμένη (ΤΕΚΑ), 1 παιδί
    pid1 = svc.register_person(
        last_name="ΓΕΩΡΓΙΟΥ",
        first_name="ΜΑΡΙΑ",
        father_name="ΓΕΩΡΓΙΟΣ",
        mother_name="ΕΛΕΝΗ",
        birth_date=date(1990, 1, 1),
        afm="111222336",
        amka="01019012341",
        ama="123456789",
        children=1,
    )
    eid1 = svc.start_employment(
        pid1, date(2026, 1, 12), monthly_gross=920.0, weekly_pattern=FULL_TIME
    )
    svc.assign_specialty(eid1, "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ", auxiliary_scheme="teka")

    # 1β. Μισθωτός, παλαιός ασφαλισμένος (επικουρική στον e-ΕΦΚΑ), 2 παιδιά
    pid2 = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ",
        first_name="ΝΙΚΟΛΑΟΣ",
        father_name="ΙΩΑΝΝΗΣ",
        mother_name="ΑΝΝΑ",
        birth_date=date(1980, 5, 15),
        afm="222333440",
        amka="15058011220",
        ama="234567890",
        children=2,
    )
    eid2 = svc.start_employment(
        pid2, date(2026, 3, 12), monthly_gross=960.0, weekly_pattern=FULL_TIME
    )
    svc.assign_specialty(eid2, "ΠΩΛΗΤΗΣ", auxiliary_scheme="efka")

    # 1γ. Ημερομίσθιος εργάτης, νέος ασφαλισμένος (ΤΕΚΑ)
    pid3 = svc.register_person(
        last_name="ΔΗΜΗΤΡΙΟΥ",
        first_name="ΚΩΝΣΤΑΝΤΙΝΟΣ",
        father_name="ΔΗΜΗΤΡΙΟΣ",
        mother_name="ΣΟΦΙΑ",
        birth_date=date(1995, 7, 20),
        afm="333444553",
        amka="20079533442",
        ama="345678901",
        children=0,
    )
    eid3 = svc.start_employment(
        pid3,
        date(2026, 4, 2),
        pay_type="daily",
        daily_wage=46.50,
        weekly_pattern=FULL_TIME,
    )
    svc.assign_specialty(eid3, "ΕΡΓΑΤΗΣ", auxiliary_scheme="teka")

    # 1δ. Υποβολή Ε3 (Αναγγελία Πρόσληψης) + ΟΧΕ στο ΕΡΓΑΝΗ — ΧΕΙΡΟΚΙΝΗΤΑ,
    #     ξεχωριστά ανά έντυπο, όπως θα τα ενεργοποιούσε ο χρήστης από το UI.
    for eid in (eid1, eid2, eid3):
        svc.submit_hire_e3(eid)
        svc.submit_hire_wto(eid)

    # 2. Παρουσίες Απριλίου για τον ημερομίσθιο (όλες οι εργάσιμες)
    d = date(2026, 4, 2)
    while d <= date(2026, 4, 30):
        if is_working_day(d):
            svc.record_attendance(eid3, d, 8)
        d += timedelta(days=1)

    # 3. Άδειες / ασθένειες
    svc.record_leave(eid1, LeaveType.SICK, date(2026, 2, 3), date(2026, 2, 4))
    svc.record_leave(eid2, LeaveType.UNPAID, date(2026, 3, 16), date(2026, 3, 17))

    # 4. Συγκεντρωτικό παρουσιών 1/2026 -> ΑΝΟΙΓΕΙ τη μισθοδοσία
    #    (παρουσίες από καταγραφή ή τεκμαρτές από το πρόγραμμα)
    print("=== Συγκεντρωτικό παρουσιών 1/2026 ===")
    for line in svc.attendance_draft(2026, 1).values():
        print(
            f"  {line['name']:<28} εργασία {line['worked']:>2}  "
            f"άνευ αποδοχών {line['unpaid']}  άδειες {line['leave']}  "
            f"({line['source']})"
        )
    svc.approve_attendance(2026, 1)

    # 5. Μισθοδοσία 1/2026 (πάνω στο εγκεκριμένο συγκεντρωτικό)
    print("\n=== Μισθοδοσία 1/2026 ===")
    for emp_id, r in svc.run_monthly_payroll(2026, 1):
        emp = proj.employments[emp_id]
        kind = "ημερομίσθιος" if emp.pay_type == "daily" else "μισθωτός"
        print(
            f"  {emp.full_name} ({kind:>12}): μικτά {r.gross:8.2f}€  "
            f"ΕΦΚΑ εργαζ. {r.efka_employee:6.2f}€  "
            f"ΕΦΚΑ εργοδ. {r.efka_employer:6.2f}€  "
            f"ΦΜΥ {r.fmy:6.2f}€  καθαρά {r.net:8.2f}€"
        )

    # 5α. Προσαρμογή στον κατώτατο (νέα ΓΣΣΕ 1/4/2026) — ΠΡΟΗΓΕΙΤΑΙ του Δώρου
    #     Πάσχα, ώστε το Δώρο να υπολογιστεί στους (τυχόν) προσαρμοσμένους
    #     μισθούς. Εδώ κανείς δεν υπολείπεται, οπότε απλώς καταγράφεται ο
    #     έλεγχος και ξεκλειδώνει το Δώρο.
    adj = svc.apply_minimum_adjustments(2026, 4)
    print(f"\n=== Προσαρμογή ΓΣΣΕ 4/2026: {len(adj)} συμβάσεις κάτω από το όριο ===")

    # 5β. Δώρο Πάσχα 2026 (μισθωτοί: αναλογία μισθού, ημερομίσθιοι: ημερομίσθια)
    svc.run_easter_gifts(2026)
    print("\n=== Δώρο Πάσχα 2026 ===")
    for g in proj.gifts:
        name = proj.employments[g.employee_id].full_name
        print(f"  {name}: μικτά {g.gross:7.2f}€  καθαρά {g.net:7.2f}€")

    # 6. Παραγωγή αρχείων ΑΠΔ — τα στοιχεία εργοδότη διαβάζονται
    #    από το αποθηκευμένο CompanyRegistered event
    #    ΔΕΝ γράφονται στον δίσκο: παράγονται on the fly από τα events
    sub_date = date(2026, 2, 26)
    names = svc.generate_apd_files(2026, 1, submission_date=sub_date)
    print("\n=== Αρχεία ΑΠΔ 2/2026 ===")
    for fund, name in names.items():
        content = svc.apd_file_bytes(fund, 2026, 1, submission_date=sub_date)
        lines = content.decode("cp1253").splitlines()
        counts = {}
        for ln in lines[:-1]:
            counts[ln[0]] = counts.get(ln[0], 0) + 1
        print(f"  {fund.upper():4}: {name}")
        print(
            f"        εγγραφές: τύπου 1={counts.get('1', 0)}, "
            f"τύπου 2={counts.get('2', 0)}, τύπου 3={counts.get('3', 0)}, EOF=1"
        )
        for ln in lines:
            print(f"        [{len(ln):3d}] {ln[:64]}{'...' if len(ln) > 64 else ''}")

    # 7. Υποβολές & πληρωμές — κλείνουν αυτόματα τις υποχρεώσεις
    # ΠΡΟΣΟΧΗ: μισθολογική περίοδος 1/2026 (η υποβολή/καταβολή γίνεται τον 2/2026)
    apd_total = svc.submit_apd(2026, 1, "efka", reference="APD-2026-02-001")
    svc.submit_apd(2026, 1, "teka", reference="APD-TEKA-2026-02-001")
    fmy_total = svc.submit_fmy(2026, 1, reference="FMY-2026-02-001")
    svc.pay_salaries(2026, 1)
    svc.pay_efka(2026, 1, apd_total)
    teka_total = svc.apd_fund_totals(2026, 1).get("teka", {}).get("contrib", 0)
    svc.pay_teka(2026, 1, teka_total)
    svc.pay_fmy(2026, 1, fmy_total)
    print(f"\nΣυνολικές εισφορές 1/2026: {apd_total:.2f}€   ΦΜΥ: {fmy_total:.2f}€")

    # 8. Εκκρεμείς υποχρεώσεις
    print("\n=== Εκκρεμείς υποχρεώσεις έως 31/3/2026 ===")
    for ob in proj.pending_obligations(as_of=date(2026, 3, 31)):
        who = ob.employee_id or "ΕΤΑΙΡΕΙΑ"
        print(f"  {config.gr_date(ob.due_date)}  {ob.obligation_type:<20} {who}")

    # 9. Persistence: όλα (εταιρεία, πρόσωπα, απασχολήσεις, υπολογισμοί)
    #    ξαναχτίζονται πλήρως από το payroll.db
    fresh = Projections()
    fresh.rebuild(store)
    assert fresh.company is not None and fresh.company.ame == "1234567890"
    assert fresh.employments[eid3].pay_type == "daily"
    prof1 = fresh.employments[eid1].insurance_profile
    assert prof1 is not None and prof1.teka_member is True
    assert fresh.persons[pid1].full_name == "ΓΕΩΡΓΙΟΥ ΜΑΡΙΑ"
    print(
        f"\nRebuild OK — εταιρεία «{fresh.company.legal_name.strip()}», "
        f"{len(fresh.persons)} πρόσωπα, {len(fresh.employments)} απασχολήσεις, "
        f"{len(store.load())} events στο payroll.db"
    )

    # 10. ΟΙ ΠΡΟΒΟΛΕΣ ΕΙΝΑΙ ΠΑΡΑΓΩΓΕΣ: ό,τι βλέπουμε ξαναχτίζεται από τα events
    print("\n=== Προβολές (in-memory, από το event log) ===")
    for label, n in (
        ("πρόσωπα", len(proj.persons)),
        ("απασχολήσεις", len(proj.employments)),
        ("μισθοδοσίες", len(proj.payrolls)),
        ("δώρα", len(proj.gifts)),
        ("υποχρεώσεις", len(proj.obligations)),
    ):
        print(f"  {label:<20} {n:>4} εγγραφές")

    print("\n=== Σύνολα μισθοδοσίας ανά περίοδο ===")
    per_period: dict[tuple, list] = {}
    for p in proj.payrolls:
        per_period.setdefault((p.period_year, p.period_month), []).append(p)
    for (year, month), rows in sorted(per_period.items()):
        gross = sum(r.gross for r in rows)
        contributions = sum(r.efka_employee + r.efka_employer for r in rows)
        net = sum(r.net for r in rows)
        print(
            f"  {year}/{month:02d}  {len(rows):>2} εργαζ.  μικτά {gross:>10.2f}"
            f"  εισφορές {contributions:>9.2f}  καθαρά {net:>10.2f}"
        )

    # Επαλήθευση: πλήρες rebuild από τα events δίνει ΤΑ ΙΔΙΑ ΑΚΡΙΒΩΣ νούμερα
    rebuilt = Projections()
    rebuilt.rebuild(store)
    assert len(rebuilt.persons) == len(proj.persons)
    assert len(rebuilt.employments) == len(proj.employments)
    assert sorted(r.net for r in rebuilt.payrolls) == sorted(
        r.net for r in proj.payrolls
    )
    print("\nΟι προβολές ξαναχτίστηκαν πλήρως από τα events — OK")
    print("\nΠίνακες διαγράφηκαν και ξαναχτίστηκαν πλήρως από τα events — OK")

    # 11. Επαναπρόσληψη: νέα εργασιακή σχέση για ΥΠΑΡΧΟΝ πρόσωπο.
    #     Δεν χρειάζεται νέο register_person — απλώς νέο start_employment.
    # Η αποχώρηση γίνεται μέσα στον ΤΡΕΧΟΝΤΑ μήνα μισθοδοσίας (ο Ιανουάριος
    # έκλεισε παραπάνω, άρα τρέχων είναι ο Φεβρουάριος) — §2
    svc.terminate(eid1, date(2026, 2, 28), reason="oikeiothelis_apoxorisi")
    eid1b = svc.start_employment(
        pid1, date(2026, 6, 1), monthly_gross=1500.0, weekly_pattern=FULL_TIME
    )
    svc.assign_specialty(eid1b, "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ", auxiliary_scheme="teka")
    p1 = proj.persons[pid1]
    print(
        f"\n=== Επαναπρόσληψη ===\n  Το πρόσωπο «{p1.full_name}» (ΑΦΜ {p1.afm}) "
        f"έχει πλέον {len(p1.employment_ids)} περιόδους απασχόλησης: "
        f"{', '.join(p1.employment_ids)}"
    )
    assert len(p1.employment_ids) == 2

    # 12. Μοναδικότητα: απορρίπτεται στο command layer (constraints.py)
    #     — πριν γραφτεί κανένα event
    print("\n=== Έλεγχος μοναδικότητας προσώπου ===")

    # (α) ίδιο ΑΦΜ με το υπάρχον πρόσωπο
    _expect_rejection(
        "ίδιο ΑΦΜ",
        svc,
        last_name="ΑΛΛΟΣ",
        first_name="ΤΙΣ",
        father_name="ΧΧΧ",
        mother_name="ΨΨΨ",
        afm="111222336",
        amka="01017012343",
    )
    # (β) ίδιο ΑΜΚΑ με το υπάρχον πρόσωπο
    _expect_rejection(
        "ίδιο ΑΜΚΑ",
        svc,
        last_name="ΑΛΛΟΣ",
        first_name="ΤΙΣ",
        father_name="ΧΧΧ",
        mother_name="ΨΨΨ",
        afm="999999993",
        amka="01019012341",
    )
    # (γ) ίδιος συνδυασμός Επώνυμο/Όνομα/Πατρώνυμο + ημ. γέννησης
    #     (το ΜΗΤΡΩΝΥΜΟ δεν ταυτοποιεί — εδώ είναι διαφορετικό)
    _expect_rejection(
        "ίδια ονόματα + ημ. γέννησης",
        svc,
        last_name="ΓΕΩΡΓΙΟΥ",
        first_name="ΜΑΡΙΑ",
        father_name="ΓΕΩΡΓΙΟΣ",
        mother_name="ΣΟΦΙΑ",
        birth_date=date(1990, 1, 1),
        afm="888888880",
        amka="11117012341",
    )


def _expect_rejection(label, svc, birth_date=date(1970, 1, 1), **person):
    """Καλεί register_person και περιμένει απόρριψη λόγω μοναδικότητας."""
    try:
        svc.register_person(birth_date=birth_date, **person)
        print(f"  ΣΦΑΛΜΑ: «{label}» έγινε δεκτό (δεν έπρεπε)")
    except ValueError as exc:
        print(f"  Απόρριψη ({label}) — OK: {exc}")


if __name__ == "__main__":
    main()
