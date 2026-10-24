"""
Εντολές (commands) που εκτελεί το UI. Ό,τι ΓΡΑΦΕΙ event ζει εδώ.

Κάθε συνάρτηση επιστρέφει το μήνυμα επιτυχίας ή πετά `ValueError` με
ελληνικό μήνυμα (τα views το μετατρέπουν σε toast).
"""

from datetime import date, timedelta

from misevent.rules import obligation_types as ot
from misevent.rules.calendar_gr import is_working_day

from . import clock
from .labels import ERGANI_ONLY
from .queries import blocked_reason, dep_label
from .system import proj, svc

# Πρόγραμμα πλήρους απασχόλησης (πενθήμερο) — υποχρεωτικό σε κάθε πρόσληψη
FULL_TIME = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}


# ---------------------------------------------------------------- Εντολές
# Η ΣΥΜΠΕΡΙΦΟΡΑ κάθε υποχρέωσης — δεν γίνεται δεδομένο. Το μητρώο τύπων
# (obligation_types.py) κρατά μόνο το ΟΝΟΜΑ της εντολής· εδώ είναι η
# υλοποίηση. Νέος τύπος υποχρέωσης = μία εγγραφή εκεί + μία εντολή εδώ.
def _approve_attendance(y, m):
    # Έγκριση «ως έχει» (το προσχέδιο). Χειροκίνητες διορθώσεις γίνονται
    # από τη σελίδα /attendance πριν την έγκριση.
    lines = svc.approve_attendance(y, m)
    return f"Εγκρίθηκε το συγκεντρωτικό παρουσιών {m}/{y} ({len(lines)} εργαζόμενοι)"


def _run_monthly_payroll(y, m):
    res = svc.run_monthly_payroll(y, m)
    return f"Υπολογίστηκε μισθοδοσία {m}/{y} για {len(res)} απασχολήσεις"


def _pay_salaries(y, m):
    svc.pay_salaries(y, m)
    return f"Πληρώθηκαν οι μισθοί {m}/{y}"


def _run_easter_gifts(y, m):
    svc.run_easter_gifts(y)
    return f"Υπολογίστηκε Δώρο Πάσχα {y}"


def _run_christmas_gifts(y, m):
    svc.run_christmas_gifts(y)
    return f"Υπολογίστηκε Δώρο Χριστουγέννων {y}"


def _run_leave_allowance(y, m):
    # Μόνο για όσους πήραν την πρώτη τους κανονική άδεια αυτόν τον μήνα
    n = len(svc.allowance_beneficiaries(y, m))
    svc.run_leave_allowance(y, m)
    return f"Υπολογίστηκε Επίδομα Αδείας {m}/{y} για {n} εργαζόμενους"


def _pay_easter_gift(y, m):
    total = svc.pay_gifts("easter", y)
    return f"Καταβλήθηκε το Δώρο Πάσχα {y}: {total:.2f}€"


def _pay_christmas_gift(y, m):
    total = svc.pay_gifts("christmas", y)
    return f"Καταβλήθηκε το Δώρο Χριστουγέννων {y}: {total:.2f}€"


def _pay_leave_allowance(y, m):
    total = svc.pay_leave_allowance(y, m)
    return f"Καταβλήθηκε το Επίδομα Αδείας {m}/{y}: {total:.2f}€"


def _submit_apd(y, m, fund):
    # ΜΟΝΟ το αρχείο αυτού του ταμείου — το άλλο έχει δική του υποχρέωση
    names = svc.generate_apd_files(y, m, funds=(fund,))
    if fund not in names:
        raise ValueError("Δεν παρήχθη αρχείο για το ταμείο αυτό")
    total = svc.submit_apd(y, m, fund)
    return (
        f"Παρήχθη {names[fund]} και υποβλήθηκε ΑΠΔ {fund.upper()} "
        f"{m}/{y} — εισφορές {total:.2f}€"
    )


def _submit_apd_efka(y, m):
    return _submit_apd(y, m, "efka")


def _submit_apd_teka(y, m):
    return _submit_apd(y, m, "teka")


def _pay_efka(y, m):
    total = svc.apd_fund_totals(y, m).get("efka", {}).get("contrib", 0)
    svc.pay_efka(y, m, total)
    return f"Καταβλήθηκαν εισφορές ΕΦΚΑ {m}/{y}: {total:.2f}€"


def _pay_teka(y, m):
    total = svc.apd_fund_totals(y, m).get("teka", {}).get("contrib", 0)
    svc.pay_teka(y, m, total)
    return f"Καταβλήθηκαν εισφορές ΤΕΚΑ {m}/{y}: {total:.2f}€"


def _submit_fmy(y, m):
    # Όπως η ΑΠΔ παράγει CSL01, η δήλωση ΦΜΥ παράγει το αρχείο JL10
    name = svc.generate_fmy_file(y, m)
    total = svc.submit_fmy(y, m)
    return f"Παρήχθη {name} και δηλώθηκε ΦΜΥ {m}/{y}: {total:.2f}€"


def _apply_minimum_adjustments(y, m):
    # Έλεγχος/προσαρμογή στον κατώτατο (ΓΣΣΕ ή ωρίμανση τριετίας): προσαρμόζει
    # τις ενεργές συμβάσεις κατά την πολιτική «Διάφορα → keep_wage_excess»
    # (συμψηφισμός/διατήρηση) — ContractChanged + Μεταβολή ΕΡΓΑΝΗ.
    res = svc.apply_minimum_adjustments(y, m)
    if not res:
        return (
            f"Έλεγχος κατώτατου {m}/{y}: καμία σύμβαση δεν χρειάστηκε προσαρμογή"
        )
    mode = ("διατήρηση διαφοράς" if res[0].get("mode") == "keep"
            else "συμψηφισμός")
    names = ", ".join(
        f"{a['name']} {a['old']:.2f}€ → {a['new']:.2f}€ ({a.get('cause', '')})"
        for a in res
    )
    return (
        f"Προσαρμόστηκαν {len(res)} συμβάσεις ({mode}) — {m}/{y}: {names} · "
        "οι Δηλώσεις Μεταβολής εκκρεμούν στην οθόνη ΕΡΓΑΝΗ"
    )


def _pay_fmy(y, m):
    total = sum(
        p.fmy for p in proj.payrolls if p.period_year == y and p.period_month == m
    )
    svc.pay_fmy(y, m, round(total, 2))
    return f"Καταβλήθηκε ΦΜΥ {m}/{y}: {total:.2f}€"


def _submit_retro_schedule(y, m):
    res = svc.submit_retro_schedule(y, m)
    if not res["count"]:
        return f"Απολογιστικό {m}/{y}: τίποτα προς υποβολή (καμία εκκρεμής απόκλιση)"
    return (f"Υποβλήθηκε το απολογιστικό {m}/{y} στο ΕΡΓΑΝΗ: {res['count']} ημέρες "
            f"({res['employees']} εργαζόμενοι), batch {res['batch_no']}")


def _submit_leave_declaration(y, m):
    res = svc.submit_leave_declaration(y, m)
    if not res["count"]:
        return f"Δήλωση αδειών {m}/{y}: τίποτα προς δήλωση"
    return (f"Υποβλήθηκε η δήλωση αδειών {m}/{y} στο ΕΡΓΑΝΗ: {res['count']} ημέρες "
            f"({res['employees']} εργαζόμενοι)")


def _check_card_period(y, m):
    # Το αρχείο κάρτας (αν ανέβηκε στο modal) εισάγεται ΠΡΙΝ — views/obligations.run
    res = svc.check_card_period(y, m)
    return (f"Η κάρτα {m}/{y} ελέγχθηκε για όλους τους εργαζόμενους"
            + (f" · {res['no_punch']} ημέρες χωρίς χτύπημα μετρούν με το πρόγραμμα"
               if res["no_punch"] else ""))


# όνομα εντολής (obligation_types.command) -> υλοποίηση
COMMANDS = {
    "approve_attendance": _approve_attendance,
    "run_monthly_payroll": _run_monthly_payroll,
    "pay_salaries": _pay_salaries,
    "run_easter_gifts": _run_easter_gifts,
    "run_christmas_gifts": _run_christmas_gifts,
    "run_leave_allowance": _run_leave_allowance,
    "pay_easter_gift": _pay_easter_gift,
    "pay_christmas_gift": _pay_christmas_gift,
    "pay_leave_allowance": _pay_leave_allowance,
    "submit_apd_efka": _submit_apd_efka,
    "submit_apd_teka": _submit_apd_teka,
    "pay_efka": _pay_efka,
    "pay_teka": _pay_teka,
    "submit_fmy": _submit_fmy,
    "pay_fmy": _pay_fmy,
    "apply_minimum_adjustments": _apply_minimum_adjustments,
    "submit_retro_schedule": _submit_retro_schedule,
    "submit_leave_declaration": _submit_leave_declaration,
    "check_card_period": _check_card_period,
}

# Δίχτυ ασφαλείας: κάθε τύπος του μητρώου πρέπει να έχει υλοποιημένη εντολή
_missing = {t.command for t in ot.TYPES.values()} - set(COMMANDS)
if _missing:
    raise RuntimeError(f"Λείπουν εντολές για: {sorted(_missing)}")


def trigger(ob_id: str, via_ergani: bool = False) -> str:
    """Ενεργοποίηση υποχρέωσης — εκτελεί την εντολή του τύπου της."""
    ob = proj.obligations.get(ob_id)
    if ob is None:
        raise ValueError("Άγνωστη υποχρέωση")
    if ob.obligation_type in ERGANI_ONLY and not via_ergani:
        raise ValueError("Υποβάλλεται ΜΟΝΟ από την οθόνη ΕΡΓΑΝΗ")
    if ob.fulfilled:
        raise ValueError("Η υποχρέωση έχει ήδη ολοκληρωθεί")
    reason = blocked_reason(ob)
    if reason:
        raise ValueError(f"Μπλοκαρισμένη: {reason}")

    spec = ot.TYPES.get(ob.obligation_type)
    if spec is None:
        raise ValueError(f"Μη υποστηριζόμενος τύπος: {ob.obligation_type}")
    return COMMANDS[spec.command](ob.period_year, ob.period_month)


def auto_settle_payments() -> list[str]:
    """Διάφορα → `auto_payments`: εκτελεί κάθε ανεκπλήρωτη πληρωμή (στήλη
    «Πληρωμές») που δεν είναι πλέον μπλοκαρισμένη. Καλείται μετά από κάθε
    επιτυχημένη εντολή (`helpers.run_command`). Οι πληρωμές δεν είναι
    προαπαιτούμενα άλλων, άρα αρκεί ένα πέρασμα. Επιστρέφει τις ετικέτες
    όσων εξοφλήθηκαν."""
    if not proj.misc_settings.get("auto_payments"):
        return []
    done = []
    for ob in sorted(
        proj.obligations.values(), key=lambda o: (o.due_date, o.obligation_type)
    ):
        if ob.fulfilled or ot.TRACK_OF.get(ob.obligation_type) != "payment":
            continue
        if blocked_reason(ob):
            continue
        try:
            trigger(ob.obligation_id)
        except ValueError:
            continue  # π.χ. «τίποτα προς πληρωμή» — μένει ανοιχτή
        done.append(dep_label(ob))
    return done


def seed_demo() -> str:
    """Δοκιμαστικά δεδομένα: εταιρεία + 3 πρόσωπα/απασχολήσεις."""
    if proj.persons:
        raise ValueError("Υπάρχουν ήδη καταχωρημένα πρόσωπα")

    # Μητρώο παραμέτρων: επιβεβαίωση των προτεινόμενων τιμών ως events
    # (ό,τι κάνει το wizard της σελίδας Παραμέτρων στο πρώτο τρέξιμο)
    svc.confirm_default_parameters()

    svc.register_company(
        ame="1234567890",
        afm="999888771",
        legal_name="ΔΟΚΙΜΑΣΤΙΚΗ ΕΜΠΟΡΙΚΗ ΕΠΕ",
        local_office_code="1150706000",
        local_office_name="ΤΟΠΙΚΗ ΔΙΕΥΘΥΝΣΗ Α ΘΕΣΣΑΛΟΝΙΚΗΣ",
    )
    svc.define_branch(0, "ΕΓΝΑΤΙΑ", "10", "54625", "ΘΕΣΣΑΛΟΝΙΚΗ")
    # Μητρώο ΚΠΚ: ΜΟΝΟ από events (καμία προεπιλογή στο config) — πρώτα η
    # ταυτότητα (φορέας/ονομασία/βαθμίδα) και τα ποσοστά, μετά οι ειδικότητες.
    svc.define_kpk("efka", "0101", "ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ", "main_auxiliary")
    svc.define_kpk_rates("efka_main", "0101", date(2025, 1, 1),
                         0.1337, 0.2179,
                         "main_pension=6,67/13,33|healthcare=2,55/4,55|auxiliary=3/3|dypa=1,15/0,91")
    svc.define_kpk("efka", "0103", "ΙΚΑ ΜΙΚΤΑ (κύρια)", "main")
    svc.define_kpk_rates("efka_main", "0103", date(2025, 1, 1),
                         0.1037, 0.1879,
                         "main_pension=6,67/13,33|healthcare=2,55/4,55|dypa=1,15/0,91")
    svc.define_kpk("teka", "4169", "ΤΕΚΑ Επικουρική", "auxiliary")
    svc.define_kpk_rates("teka", "4169", date(2022, 6, 1), 0.03, 0.03,
                         "auxiliary=3/3")

    # Ειδικότητες (θέσεις εργασίας): ο ασφαλιστικός συνδυασμός σε ΡΟΛΟΥΣ —
    # και οι δύο φορείς επικουρικής επιτρεπτοί. Η πρόσληψη διαλέγει από εδώ.
    def _packages(code):
        return {
            "MAIN_EPIK": {"kad": "0141", "specialty_code": code, "kpk": "0101"},
            "MAIN": {"kad": "0141", "specialty_code": code, "kpk": "0103"},
            "TEKA": {"kad": "0083", "specialty_code": "000860",
                     "kpk": "4169"},
        }

    svc.define_specialty("ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ", _packages("411100"))
    svc.define_specialty("ΠΩΛΗΤΗΣ", _packages("411200"))
    svc.define_specialty("ΕΡΓΑΤΗΣ", _packages("411300"))

    # Τα δοκιμαστικά δεδομένα στήνονται γύρω από την ΗΜΕΡΟΜΗΝΙΑ ΑΝΑΦΟΡΑΣ,
    # ώστε το seed να είναι συνεπές και όταν «ταξιδεύουμε στον χρόνο».
    today = clock.today()
    start = date(today.year, today.month, 1) - timedelta(days=45)

    pid1 = svc.register_person(
        last_name="ΓΕΩΡΓΙΟΥ", first_name="ΜΑΡΙΑ",
        father_name="ΓΕΩΡΓΙΟΣ", mother_name="ΕΛΕΝΗ",
        birth_date=date(1990, 1, 1),
        afm="111222336", amka="01019012341", ama="123456789", children=1,
    )
    eid1 = svc.start_employment(pid1, start, monthly_gross=1400.0,
                                weekly_pattern=FULL_TIME)
    svc.assign_specialty(eid1, "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ", auxiliary_scheme="teka")

    pid2 = svc.register_person(
        last_name="ΠΑΠΑΔΟΠΟΥΛΟΣ", first_name="ΝΙΚΟΛΑΟΣ",
        father_name="ΙΩΑΝΝΗΣ", mother_name="ΑΝΝΑ",
        birth_date=date(1980, 5, 15),
        afm="222333440", amka="15058011220", ama="234567890", children=2,
    )
    eid2 = svc.start_employment(pid2, start, monthly_gross=1950.0,
                                weekly_pattern=FULL_TIME)
    svc.assign_specialty(eid2, "ΠΩΛΗΤΗΣ", auxiliary_scheme="efka")

    pid3 = svc.register_person(
        last_name="ΔΗΜΗΤΡΙΟΥ", first_name="ΚΩΝΣΤΑΝΤΙΝΟΣ",
        father_name="ΔΗΜΗΤΡΙΟΣ", mother_name="ΣΟΦΙΑ",
        birth_date=date(1995, 7, 20),
        afm="333444553", amka="20079533442", ama="345678901",
    )
    eid3 = svc.start_employment(pid3, start, pay_type="daily", daily_wage=46.50,
                                weekly_pattern=FULL_TIME)
    svc.assign_specialty(eid3, "ΕΡΓΑΤΗΣ", auxiliary_scheme="teka")

    # Υποβολή Ε3/ΟΧΕ στο ΕΡΓΑΝΗ — προαπαιτούμενο των παρουσιών (§5.7)
    for eid in (eid1, eid2, eid3):
        svc.submit_hire_e3(eid)
        svc.submit_hire_wto(eid)

    d = start
    while d <= today:
        if is_working_day(d):
            svc.record_attendance(eid3, d, ["09:00-17:00"])
        d += timedelta(days=1)
    return "Δημιουργήθηκαν δοκιμαστικά δεδομένα (3 πρόσωπα)"
