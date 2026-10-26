"""
Οδηγός «Εισαγωγή από ΑΠΔ»: αρχείο ΑΠΔ → εταιρεία, ειδικότητες, εργαζόμενοι.

Η ΑΠΔ δίνει ταυτότητες (εργοδότη/ασφαλισμένων) και ασφαλιστικά στοιχεία·
ΔΕΝ δίνει ημ. πρόσληψης, ωράριο, πρόγραμμα. Γι' αυτό:

1. `draft_from_apd` → ένα DRAFT (απλό, JSON-able dict) προσυμπληρωμένο
   από την ΑΠΔ — ο χρήστης το συμπληρώνει βήμα-βήμα στο UI.
2. `dry_run` → τρέχει το draft σε ΔΟΚΙΜΑΣΤΙΚΟ αντίγραφο (SQLite στη μνήμη)
   και μαζεύει ΟΛΑ τα σφάλματα. Το πραγματικό σύστημα δεν αγγίζεται.
3. `apply` → τα ΙΔΙΑ commands στο πραγματικό σύστημα — μόνο αφού το
   `dry_run` βγει καθαρό. Κανένας νέος κανόνας εδώ: ό,τι ελέγχει η
   φόρμα πρόσληψης ελέγχεται και εδώ, γιατί περνά από τα ίδια commands.
"""

from datetime import date

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.domain.scheduler import ObligationScheduler
from misevent.integrations.apd_files import ApdFile, APDFormatError
from misevent.persistence.database import Database
from misevent.persistence.event_store import transaction
from misevent.rules import config

DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
DATE_FIELDS = ("birth_date", "identity_issue_date", "identity_expiry_date")
REGULAR = "001"  # Τακτικές αποδοχές — τα Δώρα/επιδόματα δεν λένε τίποτα για τη σύμβαση


def _first_of_next_month(year: int, month: int) -> date:
    return date(year + month // 12, month % 12 + 1, 1)


def _main_rows(rows):
    """Οι εγγραφές τακτικών αποδοχών (αλλιώς ό,τι υπάρχει)."""
    return [r for r in rows if r.earnings_type == REGULAR] or list(rows)


def check_teka(efka: ApdFile, teka: ApdFile | None) -> list[str]:
    """Ασφαλισμένοι της ΑΠΔ ΕΦΚΑ με επικουρική ΤΕΚΑ (No 54 = 01): η ΑΠΔ ΤΕΚΑ
    της ίδιας περιόδου πρέπει να τους έχει, με το (σταθερό) πακέτο ΤΕΚΑ.
    Επιστρέφει τα ΑΜΚΑ τους — κενό = δεν χρειάζεται ΑΠΔ ΤΕΚΑ."""
    need = [
        p.amka for p, rows in efka.insured if any(r.aux_fund_code == "01" for r in rows)
    ]
    if teka is None:
        return need
    if teka.fund != "teka":
        raise APDFormatError("Το δεύτερο αρχείο δεν είναι ΑΠΔ ΤΕΚΑ")
    if (teka.employer.afm, teka.year, teka.month) != (
        efka.employer.afm, efka.year, efka.month
    ):
        raise APDFormatError(
            "Η ΑΠΔ ΤΕΚΑ είναι άλλου εργοδότη ή άλλης περιόδου "
            f"(ΑΦΜ {teka.employer.afm}, {teka.month:02d}/{teka.year})"
        )
    by_amka = {p.amka: rows for p, rows in teka.insured}
    pkg = config.TEKA_PACKAGE
    for p, rows in efka.insured:
        if p.amka not in need:
            continue
        who = f"{p.last_name} {p.first_name}"
        if p.amka not in by_amka:
            raise APDFormatError(f"{who}: δηλώνει επικουρική ΤΕΚΑ αλλά λείπει από την ΑΠΔ ΤΕΚΑ")
        for r in by_amka[p.amka]:
            got = (r.kad, r.specialty_code, r.kpk)
            want = (pkg["kad"], pkg["specialty_code"], pkg["kpk"])
            if got != want:
                raise APDFormatError(
                    f"{who}: η ΑΠΔ ΤΕΚΑ έχει ΚΑΔ/ειδικότητα/ΚΠΚ {'/'.join(got)}, "
                    f"ενώ το πακέτο ΤΕΚΑ είναι {'/'.join(want)}"
                )
    return need


def draft_from_apd(efka: ApdFile, svc) -> dict:
    """Το αρχικό draft. `svc`: για τη βαθμίδα των ΚΠΚ (Μητρώο ΚΠΚ) και τα
    πρόσωπα που υπάρχουν ήδη (αν η εταιρεία υπάρχει)."""
    e = efka.employer
    payroll_from = _first_of_next_month(efka.year, efka.month)

    def tier(kpk):
        for fund in ("efka_main", "efka_welfare"):
            ident = svc.kpk_identity(fund, kpk)
            if ident is not None:
                return ident.tier
        return None

    specialties: dict[tuple, dict] = {}
    employees = []
    for p, rows in efka.insured:
        rows = _main_rows(rows)
        main = next((r for r in rows if tier(r.kpk) in ("main", "main_auxiliary")), rows[0])
        teka = main.aux_fund_code == "01" or tier(main.kpk) == "main"
        welfare = next((r for r in rows if r is not main and tier(r.kpk) == "welfare"), None)
        # Μία ειδικότητα ανά ΚΑΔ+κωδ. ειδικότητας· οι ρόλοι (ΕΦΚΑ/ΤΕΚΑ/πρόνοια)
        # μαζεύονται από όλους τους ασφαλισμένους της.
        # ponytail: ίδιος ρόλος με ΔΙΑΦΟΡΕΤΙΚΟ ΚΠΚ σε δύο ασφαλισμένους — κρατά
        # τον πρώτο· ο χρήστης το βλέπει και το διορθώνει στο βήμα 2.
        key = f"{main.kad}-{main.specialty_code}"
        sp = specialties.setdefault(key, {
            "key": key, "name": "", "kad": main.kad, "specialty_code": main.specialty_code,
            "ergani_specialty_code": "", "ergani_specialty_description": "",
            "packages": {}, "pron_name": "",
        })
        code = {"kad": main.kad, "specialty_code": main.specialty_code, "kpk": main.kpk}
        sp["packages"].setdefault("MAIN" if teka else "MAIN_EPIK", code)
        if teka:
            sp["packages"].setdefault("TEKA", dict(config.TEKA_PACKAGE))
        if welfare:
            sp["packages"].setdefault("PRON_UNIFORM", {
                "kad": welfare.kad, "specialty_code": welfare.specialty_code, "kpk": welfare.kpk,
            })
        hire_from = main.date_from if main.date_from and main.date_from.day != 1 else None
        employees.append({
            "afm": p.afm,
            "exists": svc.proj.person_by_afm(p.afm) is not None,
            "person": {
                "last_name": p.last_name, "first_name": p.first_name,
                "father_name": p.father_name, "mother_name": p.mother_name,
                "birth_date": p.birth_date.isoformat() if p.birth_date else "",
                "afm": p.afm, "amka": p.amka, "ama": p.ama,
                "children": "0", "sex": "", "email": "", "phone": "",
                # Ομάδα ταυτότητας WebE3N: ή όλη ή καθόλου (Φύλο => όλη)
                "nationality_code": "048", "identity_type": "", "identity_number": "",
                "identity_issuer": "", "identity_issue_date": "", "identity_expiry_date": "",
            },
            "hire": {
                # Πρόσληψη ΜΕΣΑ στον μήνα της ΑΠΔ: η «Από» (No 40) είναι η πρόσληψη.
                "hire_date": hire_from.isoformat() if hire_from else "",
                "payroll_from": payroll_from.isoformat(),
                "pay_type": "daily" if main.is_daily_paid else "monthly",
                "amount": f"{main.daily_wage if main.is_daily_paid else main.gross:.2f}",
                "weekly_hours": "40" if main.full_schedule else "",
                "weekly_system": str(main.weekly_system or 5),
                "employment_relationship": "0", "fixed_term_to": "",
                "service_years": "0", "frozen_service_years": "0",
                "married": "", "extra_allowances_pct": "0",
            },
            "specialty_key": key,
            "auxiliary_scheme": "teka" if teka else "efka",
            "pron_choice": "yes" if welfare else "",
            "branch_no": main.branch_no,
            "apd": {  # μόνο για ενημέρωση του χρήστη
                "days": main.insurance_days, "gross": main.gross,
                "kpk": main.kpk, "full_schedule": main.full_schedule,
            },
            "schedule": {"mode": "fixed", "weeks": "4", "days": {}},
        })
    for sp in specialties.values():
        if "PRON_UNIFORM" in sp["packages"]:
            sp["pron_name"] = "Πρόνοια"
    return {
        "period": [efka.year, efka.month],
        "company": {
            "ame": e.ame, "afm": e.afm, "legal_name": e.legal_name,
            "first_name": e.first_name, "father_name": e.father_name,
            "local_office_code": e.local_office_code,
            "local_office_name": e.local_office_name, "activity": "",
        },
        "branch": {
            "street": e.street, "street_no": e.street_no,
            "postal_code": e.postal_code, "city": e.city,
            "sepe_code": "", "oaed_code": "", "kad_code": "", "kallikratis_code": "",
        },
        "specialties": list(specialties.values()),
        "employees": employees,
    }


# ---------------------------------------------------------------- εφαρμογή
def _d(v):
    return date.fromisoformat(v) if v else None


def _f(v, default=0.0):
    return float(str(v).replace(",", ".")) if str(v or "").strip() else default


def apply(svc, draft: dict, today: date) -> list[tuple[str, str, str]]:
    """Εκτελεί το draft με τα κανονικά commands. Δεν σταματά στο πρώτο
    σφάλμα: επιστρέφει ΟΛΑ ως (βήμα, ποιος, μήνυμα) — κενό = επιτυχία."""
    errors = []

    def attempt(step, who, fn):
        try:
            with transaction():
                return fn()
        except (ValueError, KeyError) as exc:
            errors.append((step, who, str(exc)))
            return None

    c, b = draft["company"], draft["branch"]
    if svc.proj.company is None:
        if not c["activity"].strip():
            errors.append(("company", c["legal_name"], "Λείπει η δραστηριότητα της εταιρείας"))
        attempt("company", c["legal_name"], lambda: svc.register_company(**c))
        attempt("company", "Έδρα (παράρτημα 0)", lambda: svc.define_branch(0, **b))
    elif svc.proj.company.afm != c["afm"]:
        errors.append(("company", c["legal_name"],
                       f"Η εταιρεία έχει ΑΦΜ {svc.proj.company.afm}, η ΑΠΔ {c['afm']}"))
        return errors

    names = {}
    for sp in draft["specialties"]:
        names[sp["key"]] = sp["name"]
        label = f"Ειδικότητα «{sp['name'] or sp['key']}»"
        # ΥΠΟΧΡΕΩΤΙΚΟΣ: χωρίς αυτόν δεν βγαίνει κανένα έντυπο ΕΡΓΑΝΗ (E3N/E5N/E7N)
        if not sp["ergani_specialty_code"]:
            errors.append(("company", label, "Λείπει ο κωδικός ειδικότητας ΕΡΓΑΝΗ"))
            continue
        try:
            old = svc.get_specialty(sp["name"])
        except ValueError:
            old = None
        if old is not None:
            # Υπάρχει ήδη: καλύψεις ΩΣ ΕΧΟΥΝ — συμπληρώνεται μόνο ο κωδ. ΕΡΓΑΝΗ
            # αν της λείπει (αλλιώς κρατά τον δικό της).
            if not old.ergani_specialty_code:
                attempt("company", label, lambda old=old, sp=sp: svc.define_specialty(
                    old.name, list(old.coverages),
                    sp["ergani_specialty_code"], sp["ergani_specialty_description"],
                    epik_carriers=old.epik_carriers or None, pron=old.pron or None))
            continue
        pron = ({"enabled": True, "name": sp["pron_name"] or "Πρόνοια",
                 "differentiated": any(r in sp["packages"] for r in ("PRON_NEW", "PRON_OLD"))}
                if any(r.startswith("PRON") for r in sp["packages"]) else None)
        attempt("company", label, lambda sp=sp, pron=pron:
                svc.define_specialty(
                    sp["name"], sp["packages"],
                    ergani_specialty_code=sp["ergani_specialty_code"],
                    ergani_specialty_description=sp["ergani_specialty_description"],
                    pron=pron))

    for emp in draft["employees"]:
        if emp["exists"]:
            continue
        p, h, s = emp["person"], emp["hire"], emp["schedule"]
        who = f"{p['last_name']} {p['first_name']}"

        def person(p=p):
            if not p["sex"]:
                _missing("φύλο")
            return svc.register_person(**{
                **p, "children": int(p["children"] or 0),
                **{k: _d(p[k]) for k in DATE_FIELDS},
            })

        pid = attempt("employees", who, person)
        if pid is None:
            continue
        pattern = {d: v for d, v in s["days"].items() if str(v).strip()}
        if not pattern:
            errors.append(("schedule", who, "Δεν δηλώθηκε πρόγραμμα εργασίας"))
            continue
        daily = h["pay_type"] == "daily"
        amount = _f(h["amount"])
        fixed = h["employment_relationship"] == "1"

        def hire(pid=pid, h=h, s=s, pattern=pattern, daily=daily, amount=amount, fixed=fixed):
            eid = svc.start_employment(
                pid, _d(h["hire_date"]) or _missing("ημερομηνία πρόσληψης"),
                payroll_from=_d(h["payroll_from"]), ergani_hire_done=True,
                ergani_hire_reference="ΑΠΔ",
                pay_type=h["pay_type"],
                monthly_gross=0.0 if daily else amount,
                daily_wage=amount if daily else 0.0,
                weekly_hours=_f(h["weekly_hours"]) or _missing("ώρες εβδομάδας"),
                weekly_system=int(h["weekly_system"] or 5),
                weekly_pattern=pattern,
                service_years=_f(h["service_years"]),
                frozen_service_years=_f(h["frozen_service_years"]),
                married=bool(h["married"]),
                extra_allowances_pct=_f(h["extra_allowances_pct"]),
                employment_relationship=h["employment_relationship"],
                fixed_term_from=_d(h["hire_date"]) if fixed else None,
                fixed_term_to=_d(h["fixed_term_to"]) if fixed else None,
                schedule_mode=s["mode"],
                schedule_weeks=int(s["weeks"] or 0),
                draft=True,
                today=today,
            )
            svc.assign_specialty(eid, names[emp["specialty_key"]],
                                 auxiliary_scheme=emp["auxiliary_scheme"],
                                 pron_choice=emp["pron_choice"] or None,
                                 branch_no=emp["branch_no"])
            return eid

        # Πρόσληψη + ειδικότητα: ένα βήμα «εργαζόμενος» για τον χρήστη
        attempt("employees", who, hire)
    return errors


def _missing(what):
    raise ValueError(f"Λείπει: {what}")


def dry_run(shared_store, company_store, draft: dict, today: date):
    """Το `apply` σε ΑΝΤΙΓΡΑΦΟ στη μνήμη: κοινές παράμετροι + (αν υπάρχει)
    το log της εταιρείας. Επιστρέφει τη λίστα σφαλμάτων του `apply`."""
    db = Database()
    shared = db.store("shared")
    shared.restore_backup(shared_store.export_backup())
    store = db.store("dry-run", shared=shared)
    if company_store is not None:
        store.restore_backup(company_store.export_backup())
    proj = Projections()
    proj.rebuild(store)
    store.subscribe(proj.handle)
    sched = ObligationScheduler(store, proj, horizon_months=1)
    store.subscribe(sched.handle)
    shared.subscribe(proj.handle)
    shared.subscribe(sched.handle)
    try:
        return apply(PayrollService(store, proj), draft, today)
    finally:
        db.close()

