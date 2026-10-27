"""
Εταιρεία DEMO: πέντε εργαζόμενοι, με ιστορικό από τον Ιανουάριο του τρέχοντος
έτους ως τον μήνα εγκατάστασης.

  1. μισθωτός πλήρους, βασικός μισθός
  2. ημερομίσθιος πλήρους, βασικό ημερομίσθιο   — ΗΜΕΡΗΣΙΟ πρόγραμμα
  3. μισθωτός μερικής (20ω)
  4. ημερομίσθιος μερικής (20ω, 4ωρο)           — ΗΜΕΡΗΣΙΟ πρόγραμμα
  5. μισθωτός πλήρους, κάτω των 25

Όλα γράφονται με τις ΙΔΙΕΣ εντολές του UI (κανένα event «με το χέρι»), και η
αλυσίδα υποχρεώσεων (παρουσίες → μισθοδοσία → ΑΠΔ/ΦΜΥ → πληρωμές, Δώρα,
προσαρμογή ΓΣΣΕ…) τρέχει όπως θα την έτρεχε ο χρήστης, για κάθε περίοδο πριν
από τον τρέχοντα μήνα. Ο τρέχων μήνας μένει ανοιχτός.

Τρέχει πάνω στην ΕΝΕΡΓΗ εταιρεία (proxies `svc`/`proj`).
"""

from datetime import date, timedelta

from misevent.rules import config
from misevent.rules.constraints import afm_checksum_ok, amka_checksum_ok

from . import clock
from .actions import trigger
from .queries import blocked_reason
from .system import proj, svc

WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI")
DAY_CODES = (*WEEKDAYS, "SAT", "SUN")  # date.weekday() -> κωδικός ημέρας
FULL = {d: "09:00-17:00" for d in WEEKDAYS}  # 40ω
PART = {d: "09:00-13:00" for d in WEEKDAYS}  # 20ω


def _afm(prefix8: str) -> str:
    return next(a for a in (prefix8 + str(d) for d in range(10)) if afm_checksum_ok(a))


def _amka(birth: date, seq: str) -> str:
    head = birth.strftime("%d%m%y") + seq
    return next(a for a in (head + str(d) for d in range(10)) if amka_checksum_ok(a))


def _packages(code):
    return {"MAIN_EPIK": {"kad": "0141", "specialty_code": code, "kpk": "0101"}}


def seed_demo_company() -> str:
    if proj.persons:
        raise ValueError("Η εταιρεία έχει ήδη πρόσωπα")
    today = clock.today()
    year = today.year
    # Ήδη εργαζόμενοι από πέρυσι· το σύστημα ΑΝΑΛΑΜΒΑΝΕΙ τη μισθοδοσία από 1/1
    # («μεταφορά από άλλο πρόγραμμα», §2) — πλήρης Ιανουάριος, χωρίς πρόσληψη
    # σε αργία, και η αναγγελία πρόσληψης θεωρείται ήδη υποβληθείσα.
    hire, start = date(year - 1, 9, 1), date(year, 1, 1)
    mw = config.minimum_wage_for(start)

    svc.confirm_default_parameters()  # κοινές παράμετροι + μητρώο ΚΠΚ, αν λείπουν
    svc.set_misc_settings(digital_card=False)  # το demo δεν έχει αρχείο κάρτας
    svc.register_company(
        ame="1234567890", afm=_afm("99988877"), legal_name="DEMO ΕΜΠΟΡΙΚΗ ΕΠΕ",
        local_office_code="1150706000",
        local_office_name="ΤΟΠΙΚΗ ΔΙΕΥΘΥΝΣΗ Α ΘΕΣΣΑΛΟΝΙΚΗΣ",
        activity="ΕΜΠΟΡΙΟ",
    )
    svc.define_branch(0, "ΕΓΝΑΤΙΑ", "10", "54625", "ΘΕΣΣΑΛΟΝΙΚΗ")
    svc.define_specialty("ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ", _packages("411100"),
                         "419", "ΛΟΙΠΟΙ ΥΠΑΛΛΗΛΟΙ ΓΡΑΦΕΙΟΥ")
    svc.define_specialty("ΕΡΓΑΤΗΣ", _packages("411300"),
                         "932", "ΑΝΕΙΔΙΚΕΥΤΟΙ ΕΡΓΑΤΕΣ ΜΕΤΑΠΟΙΗΤΙΚΩΝ ΒΙΟΜΗΧΑΝΙΩΝ")

    # (επώνυμο, όνομα, πατρώνυμο, γέννηση, ειδικότητα, όροι σύμβασης)
    staff = [
        ("ΑΛΕΞΙΟΥ", "ΑΝΝΑ", "ΓΕΩΡΓΙΟΣ", date(1985, 3, 14), "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ",
         dict(monthly_gross=mw.monthly, weekly_pattern=FULL)),
        ("ΒΑΣΙΛΕΙΟΥ", "ΠΕΤΡΟΣ", "ΝΙΚΟΛΑΟΣ", date(1979, 6, 2), "ΕΡΓΑΤΗΣ",
         dict(pay_type="daily", daily_wage=mw.daily, weekly_pattern=FULL,
              schedule_mode="variable", schedule_weeks=1)),
        ("ΓΕΩΡΓΙΑΔΟΥ", "ΕΛΕΝΗ", "ΙΩΑΝΝΗΣ", date(1992, 11, 23), "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ",
         dict(monthly_gross=round(mw.monthly / 2, 2), weekly_hours=20.0,
              weekly_pattern=PART)),
        ("ΔΗΜΟΥ", "ΣΤΑΥΡΟΣ", "ΑΘΑΝΑΣΙΟΣ", date(1988, 8, 9), "ΕΡΓΑΤΗΣ",
         dict(pay_type="daily", daily_wage=mw.daily, weekly_hours=20.0,
              weekly_pattern=PART, schedule_mode="variable", schedule_weeks=1)),
        ("ΕΥΑΓΓΕΛΟΥ", "ΜΑΡΙΟΣ", "ΚΩΝΣΤΑΝΤΙΝΟΣ", date(year - 22, 5, 17),
         "ΥΠΑΛΛΗΛΟΣ ΓΡΑΦΕΙΟΥ", dict(monthly_gross=mw.monthly, weekly_pattern=FULL)),
    ]
    # ΗΜΕΡΗΣΙΟ πρόγραμμα: σχεδιάζεται ως το τέλος του τρέχοντος μήνα
    # (κενό ωράριο = ρεπό) και δηλώνεται στο ΕΡΓΑΝΗ όπως από το «Πρόγραμμα».
    month_end = date(year + today.month // 12, today.month % 12 + 1, 1) - timedelta(1)
    days = [start + timedelta(n) for n in range((month_end - start).days + 1)]
    for i, (last, first, father, born, spec, terms) in enumerate(staff, 1):
        pid = svc.register_person(
            last_name=last, first_name=first, father_name=father, mother_name="ΜΑΡΙΑ",
            birth_date=born, afm=_afm(f"1{i:02d}22233"), amka=_amka(born, f"{i:04d}"),
            ama=f"10000000{i}",
        )
        eid = svc.start_employment(pid, hire, payroll_from=start,
                                   ergani_hire_done=True, **terms)
        svc.assign_specialty(eid, spec, auxiliary_scheme="efka")
        if terms.get("schedule_mode") == "variable":
            pattern = terms["weekly_pattern"]
            svc.set_daily_schedule(eid, {
                d: pattern.get(DAY_CODES[d.weekday()], "")
                for d in days
            })
            svc.submit_hire_wto_variable(eid)
        else:
            svc.submit_hire_wto(eid)

    done = _run_chain(today)
    return f"Δημιουργήθηκε η DEMO: 5 εργαζόμενοι, {done} υποχρεώσεις ολοκληρώθηκαν"


def _run_chain(today: date) -> int:
    """Ενεργοποιεί κάθε υποχρέωση περιόδου ΠΡΙΝ από τον τρέχοντα μήνα (και τις
    ετήσιες με προθεσμία ως σήμερα), με τη σειρά των εξαρτήσεων: σε κάθε
    πέρασμα ό,τι δεν είναι πια μπλοκαρισμένο, ώσπου να μη μένει τίποτα."""
    current = (today.year, today.month)

    def due(ob):
        if ob.period_month:
            return (ob.period_year, ob.period_month) < current
        return ob.due_date <= today

    done, progress = 0, True
    while progress:
        progress = False
        for ob in sorted(proj.obligations.values(),
                         key=lambda o: (o.due_date, o.obligation_type)):
            if ob.fulfilled or not due(ob) or blocked_reason(ob):
                continue
            try:
                trigger(ob.obligation_id, via_ergani=True)
            except ValueError:
                continue  # π.χ. «τίποτα προς πληρωμή»
            done += 1
            progress = True
    return done
