"""
Read-only ερωτήματα προς τα projections. Καμία παρενέργεια — τίποτα εδώ
δεν γράφει event. Οι εντολές ζουν στο `actions.py`.

Οι συναρτήσεις επιστρέφουν έτοιμα view models για τα templates.
"""

import json
from calendar import monthrange
from datetime import date, datetime, timedelta

from misevent.app.services.apd import apd_missing_ama
from misevent.domain import dependencies as deps
from misevent.domain import payroll_engine as pe
from misevent.domain import work_hours
from misevent.integrations import apd_files
from misevent.integrations import card_punches as cp
from misevent.rules import config, constraints
from misevent.rules import obligation_types as ot
from misevent.rules.calendar_gr import (
    holiday_names,
    last_working_day_of_month,
    public_holidays,
)

from .filters import money
from .labels import (
    DAY_NAMES,
    DUE_SOON_DAYS,
    ERGANI_ONLY,
    ET_LABELS,
    LEAVE_LABELS,
    LEAVE_NOW,
    MARITAL_LABELS,
    MONTHS_GEN,
    MONTHS_NOM,
    MONTHS_SHORT,
    OB_ICONS,
    OB_LABELS,
    OB_TRACKS,
    PERSON_FIELD_LABELS,
)
from .system import proj, store, svc


# ------------------------------------------------------------ Υποχρεώσεις
def dep_label(o) -> str:
    per = f"{o.period_month}/{o.period_year}" if o.period_month else str(o.period_year)
    return f"{OB_LABELS.get(o.obligation_type, o.obligation_type)} {per}"


def blocked_reason(ob):
    """Λόγος μπλοκαρίσματος: ανεκπλήρωτα προαπαιτούμενα ή παράβαση
    κατώτατου μισθού (ΓΣΣΕ/ΚΥΑ) της περιόδου."""
    index = deps.build_index(proj.obligations.values())
    unmet = deps.unmet_prerequisites(ob, index)
    if unmet:
        return "Προαπαιτείται: " + ", ".join(dep_label(u) for u in unmet)
    if ob.obligation_type == "MONTHLY_PAYROLL" and not ob.fulfilled:
        v = svc.wage_violations(ob.period_year, ob.period_month)
        if v:
            return "Κατώτατος μισθός: " + " | ".join(v)
    if ob.obligation_type == "ATTENDANCE_APPROVAL" and not ob.fulfilled and ob.period_month:
        missing = _schedule_pending_days(ob.period_year, ob.period_month)
        if missing:
            return ("Αδήλωτο πρόγραμμα στο ΕΡΓΑΝΗ (οθόνη ΕΡΓΑΝΗ): "
                    + " · ".join(missing))
    return None


def _schedule_pending_days(y: int, m: int) -> list[str]:
    """«Όνομα: ΗΗ/ΜΜ–ΗΗ/ΜΜ» για όσους έχουν ενεργή ημέρα του μήνα ΧΩΡΙΣ
    δηλωμένο πρόγραμμα στο ΕΡΓΑΝΗ (ίδιο κριτήριο με `svc.month_schedule_pending`)."""
    out = []
    for emp in proj.employments.values():
        if not emp.hire_submitted or not svc.month_schedule_pending(emp, y, m):
            continue
        days = [d for d in (date(y, m, n) for n in range(1, monthrange(y, m)[1] + 1))
                if emp.active_on(d) and emp.day_status(d) == "pending"]
        span = f"{days[0]:%d/%m}" + (f"–{days[-1]:%d/%m}" if len(days) > 1 else "")
        out.append(f"{emp.full_name}: {span} ({len(days)} ημέρες)")
    return out


def days_late(ob, ref: date) -> int:
    """Ημέρες καθυστέρησης (0 = εμπρόθεσμη ή ολοκληρωμένη)."""
    if ob.fulfilled or ob.due_date >= ref:
        return 0
    return (ref - ob.due_date).days


def obligation_status(ob, ref: date):
    """(status, reason). `reason` ΜΟΝΟ όταν η υποχρέωση είναι πραγματικά
    μπλοκαρισμένη (ανεκπλήρωτο προαπαιτούμενο / κατώτατος μισθός).

    Η ΕΚΠΡΟΘΕΣΜΗ ΔΕΝ είναι reason: βάφεται κόκκινη, αλλά ΕΠΙΤΡΕΠΕΤΑΙ να
    υποβληθεί εκπρόθεσμα — με προειδοποίηση (βλ. `days_late`)."""
    if ob.fulfilled:
        return "done", None
    reason = blocked_reason(ob)
    if reason and ob.due_date <= ref + timedelta(days=DUE_SOON_DAYS):
        return "problem", reason
    if ob.due_date < ref:
        return "problem", None
    if ob.due_date <= ref + timedelta(days=DUE_SOON_DAYS):
        return "warning", None
    return "ready", None


def period_has_employment(y: int, m: int) -> bool:
    """True αν έστω μία απασχόληση ήταν ΕΝΕΡΓΗ οποιαδήποτε στιγμή του μήνα
    (y, m): πρόσληψη έως το τέλος του μήνα ΚΑΙ (χωρίς λήξη Ή λήξη από την 1η
    του μήνα και μετά). Χρησιμοποιείται για να κρύβονται οι υποχρεώσεις
    περιόδου σε μήνες χωρίς κανέναν εργαζόμενο (π.χ. μετά την αποχώρηση του
    τελευταίου). Παράγωγο — η επαναπρόσληψη τις ξαναεμφανίζει."""
    month_start = date(y, m, 1)
    month_end = last_working_day_of_month(y, m)
    for e in proj.employments.values():
        if not e.hire_submitted:
            continue
        if e.hire_date is None or e.hire_date > month_end:
            continue
        if e.termination_date is not None and e.termination_date < month_start:
            continue
        return True
    return False


def hidden_payment(ob) -> bool:
    """Πληρωμή κρυμμένη από το χρονολόγιο (Διάφορα → `auto_payments`):
    εξοφλείται αυτόματα, άρα δεν περιμένει ενέργεια του χρήστη."""
    return bool(proj.misc_settings.get("auto_payments")) and (
        OB_TRACKS.get(ob.obligation_type) == "payment"
    )


def is_locked(ob, index) -> bool:
    """Κλειδωμένη = έχει ανεκπλήρωτο ΠΡΟΑΠΑΙΤΟΥΜΕΝΟ event.

    Δεν εμφανίζεται στο χρονολόγιο: θα «ξεκλειδώσει» μόνη της μόλις
    ενεργοποιηθεί η προαπαιτούμενη υποχρέωση. (Άλλα προβλήματα — π.χ.
    παράβαση κατώτατου μισθού — ΔΕΝ κλειδώνουν: είναι ενεργά προς επίλυση.)

    ➜ Αδήλωτο πρόγραμμα ΕΡΓΑΝΗ του μήνα (§5.7α) ΔΕΝ κρύβει την «Έγκριση
    Παρουσιών»: εμφανίζεται ΜΠΛΟΚΑΡΙΣΜΕΝΗ με τις ημέρες που λείπουν
    (`blocked_reason`) — αλλιώς ο χρήστης δεν βλέπει τι περιμένει."""
    if ob.fulfilled:
        return False
    return bool(deps.unmet_prerequisites(ob, index))


def obligation_list(ref: date, show_locked: bool = False, view: str = "active"):
    """Οι υποχρεώσεις με κατάσταση/εξαρτήσεις, ταξινομημένες.

    `view`: "active" (εκκρεμείς) | "done" (ολοκληρωμένες) | "all" (και τα δύο).
    Οι κλειδωμένες (με ανεκπλήρωτο προαπαιτούμενο) παραλείπονται, εκτός αν
    ζητηθούν ρητά με `show_locked`. Στις εκκρεμείς, οι μελλοντικές φτάνουν
    έως το τέλος του μήνα `ref` + ορίζοντα (Παράμετροι → Διάφορα)."""
    index = deps.build_index(proj.obligations.values())
    months = ref.month + int(proj.misc_settings.get("obligations_horizon_months", 1))
    y, m = ref.year + (months - 1) // 12, (months - 1) % 12 + 1
    horizon = date(y, m, monthrange(y, m)[1])
    out = []
    for o in sorted(
        proj.obligations.values(), key=ot.sort_key
    ):
        if view == "active" and (o.fulfilled or o.due_date > horizon):
            continue
        if view == "done" and not o.fulfilled:
            continue
        if hidden_payment(o):
            continue
        if not show_locked and is_locked(o, index):
            continue
        # Μήνες χωρίς κανέναν ενεργό εργαζόμενο δεν έχουν υποχρεώσεις
        # περιόδου (π.χ. «Έγκριση Παρουσιών» μετά την αποχώρηση του τελευταίου).
        if not o.fulfilled and o.period_month and not period_has_employment(
            o.period_year, o.period_month
        ):
            continue
        st, reason = obligation_status(o, ref)
        out.append(
            {
                "id": o.obligation_id,
                "type": o.obligation_type,
                "label": OB_LABELS.get(o.obligation_type, o.obligation_type),
                "icon": OB_ICONS.get(o.obligation_type, "📌"),
                # Στήλη χρονολογίου — ΔΗΛΩΤΙΚΗ, από το μητρώο τύπων
                "track": OB_TRACKS.get(o.obligation_type, "payroll"),
                "due": o.due_date,
                "period": (
                    f"{o.period_month}/{o.period_year}"
                    if o.period_month
                    else str(o.period_year or "")
                ),
                "status": st,
                "reason": reason,
                "late": days_late(o, ref),
                "deps": [
                    {"label": dep_label(p), "done": p.fulfilled}
                    for p in deps.prerequisites(o, index)
                ],
            }
        )
    return out


def locked_count() -> int:
    """Πόσες υποχρεώσεις είναι κρυμμένες γιατί περιμένουν προαπαιτούμενο."""
    index = deps.build_index(proj.obligations.values())
    return sum(
        1 for o in proj.obligations.values()
        if not hidden_payment(o) and is_locked(o, index)
    )


def status_counts(obs) -> dict:
    c = {"done": 0, "ready": 0, "warning": 0, "problem": 0}
    for o in obs:
        c[o["status"]] += 1
    return c


# --------------------------------------------------------------- Πρόσωπα
def person_rows():
    """Πρόσωπα με τις περιόδους απασχόλησής τους.

    `active`: πλήθος ενεργών περιόδων. `rehirable`: έχει ιστορικό αλλά καμία
    ενεργή -> επιτρέπεται επαναπρόσληψη (βλ. constraints.check_rehire)."""
    out = []
    for p in proj.persons.values():
        emps = sorted(
            (proj.employments[e] for e in p.employment_ids if e in proj.employments),
            key=lambda e: e.hire_date or date.min,
        )
        active = sum(1 for e in emps if e.active)
        active_emp = next((e for e in emps if e.active), None)
        # Η ειδικότητα ανήκει στην ΠΡΟΣΛΗΨΗ, όχι στο πρόσωπο — εδώ όλες όσες
        # είχε (για το φίλτρο της οθόνης Εργαζόμενοι).
        specialties = sorted({e.insurance_profile.specialty_name for e in emps
                              if e.insurance_profile
                              and e.insurance_profile.specialty_name})
        out.append(
            {
                "person": p,
                "employments": emps,
                "count": len(emps),
                "last": emps[-1] if emps else None,
                "active": active,
                "active_emp": active_emp,
                "specialties": specialties,
                "rehirable": active == 0,
                # Για τα φίλτρα της οθόνης Εργαζόμενοι (persons-filter.js)
                "status": (active_emp.lifecycle if active_emp
                           else "terminated" if emps else "none"),
                "search": " ".join(
                    [constraints.norm_name(f"{p.full_name} {p.father_name or ''}"),
                     p.afm or "", p.amka or "", p.ama or ""]
                ),
            }
        )
    return sorted(out, key=lambda r: r["person"].full_name)


def search_persons(term: str):
    """Αναζήτηση στο μητρώο με ΑΦΜ / ΑΜΚΑ / ΑΜΑ ή οποιοδήποτε όνομα.

    Τα ψηφία ταιριάζουν ως πρόθεμα, τα ονόματα ως υποσυμβολοσειρά (χωρίς
    τόνους, κεφαλαία — βλ. constraints.norm_name)."""
    t = (term or "").strip()
    if not t:
        return []
    digits = constraints.norm_digits(t)
    name = constraints.norm_name(t)
    out = []
    for r in person_rows():
        p = r["person"]
        by_id = bool(digits) and any(
            (v or "").startswith(digits) for v in (p.afm, p.amka, p.ama)
        )
        by_name = bool(name) and any(
            name in constraints.norm_name(v or "")
            for v in (p.last_name, p.first_name, p.father_name, p.mother_name)
        )
        if by_id or by_name:
            out.append(r)
    return out


def employment_rows(person_id: str | None = None):
    out = []
    for e in proj.employments.values():
        if not e.hire_submitted:
            continue
        if person_id and e.person_id != person_id:
            continue
        out.append(e)
    return sorted(out, key=lambda e: (e.hire_date or date.min), reverse=True)


def emp_name(eid: str) -> str:
    e = proj.employments.get(eid)
    return e.full_name if e else eid


GIFT_LABELS = {"easter": "Δώρο Πάσχα", "christmas": "Δώρο Χριστουγέννων"}


def _exit_settled(employment_id: str):
    """Τι εκκαθαρίστηκε ΚΑΤΑ την αποχώρηση: (σύνολο gift_types, allowance;).
    Κριτήριο: το event γράφτηκε ΜΕΤΑ το EmployeeTerminated της απασχόλησης
    (seq μεγαλύτερο). Έτσι Δώρο/Επίδομα που είχαν ΗΔΗ υπολογιστεί στον τακτικό
    κύκλο (π.χ. Δώρο Χριστουγέννων πριν αποχώρηση 22-31/12) ΔΕΝ εμφανίζονται
    στην εκκαθάριση αποχώρησης."""
    term_seq = store.max_seq(employment_id, "EmployeeTerminated")
    if term_seq is None:
        return set(), False
    gift_types, allowance = set(), False
    for etype, payload in store.events_after(
        employment_id,
        ("GiftCalculated", "LeaveAllowanceCalculated"),
        term_seq,
    ):
        if etype == "GiftCalculated":
            gift_types.add(json.loads(payload)["gift_type"])
        else:
            allowance = True
    return gift_types, allowance


def exit_settlement(employment_id: str):
    """Πλήρης ανάλυση εκκαθάρισης αποχώρησης: ΟΛΑ τα ποσά που οφείλονται
    στον αποχωρούντα εργαζόμενο, με σύνολο καθαρών προς καταβολή. None αν
    η απασχόληση δεν έχει λήξει.

    Περιλαμβάνει: τη ΜΙΣΘΟΔΟΣΙΑ ΤΟΥ ΜΗΝΑ ΑΠΟΧΩΡΗΣΗΣ (αν υπολογίστηκε στην
    αποχώρηση, μόνο γι' αυτόν), Δώρο (Πάσχα/Χριστουγέννων) & Επίδομα Αδείας
    που ΑΝΑΛΟΓΟΥΝ, αποζημίωση μη ληφθείσας αδείας, αποζημίωση απόλυσης.

    ➜ Κάθε γραμμή δηλώνει ΠΟΤΕ καταβάλλεται (`now`): **ΟΛΑ καταβάλλονται την
    ημέρα της αποχώρησης ΕΚΤΟΣ του Δώρου**, που πληρώνεται στη δική του
    ημερομηνία — γι' αυτό τα σύνολα δίνονται και χωριστά (`now_*`/`later_*`)."""
    e = proj.employments.get(employment_id)
    if e is None or e.termination_date is None:
        return None
    year = e.termination_date.year
    # ΜΟΝΟ ό,τι εκκαθαρίστηκε ΚΑΤΑ την αποχώρηση (όχι ό,τι είχε ήδη υπολογιστεί
    # στον τακτικό κύκλο πριν την αποχώρηση)
    gift_types, allowance_settled = _exit_settled(employment_id)
    items = []
    # ΟΙ ΑΠΟΔΟΧΕΣ ΤΟΥ ΜΗΝΑ ΟΦΕΙΛΟΝΤΑΙ ΠΑΝΤΑ στην αποχώρηση — ΑΝΕΞΑΡΤΗΤΑ από το
    # ποιος τις υπολόγισε (εκκαθάριση αποχώρησης ή τακτικός κύκλος). Το
    # `exit_settlement` του event αφορά ΜΟΝΟ τον scheduler (τι υποχρέωση
    # κλείνει), ΟΧΙ το τι πληρώνεται — γι' αυτό ΔΕΝ φιλτράρεται εδώ.
    p = next(
        (
            x
            for x in proj.payrolls
            if x.employee_id == employment_id
            and (x.period_year, x.period_month)
            == (year, e.termination_date.month)
        ),
        None,
    )
    payroll_missing = ""
    if p:
        items.append({
            "kind": "payroll",
            "label": f"Μισθοδοσία {MONTHS_GEN[p.period_month]} {p.period_year}",
            "note": f"{p.insurance_days} ημέρες ασφάλισης έως την αποχώρηση",
            "gross": p.gross, "efka": p.efka_employee, "fmy": p.fmy,
            "net": p.net, "now": True,
        })
    else:
        # Δεν υπολογίστηκε (π.χ. εκκρεμούσε προηγούμενος μήνας): ΔΕΝ σβήνεται
        # σιωπηλά — η κάρτα/απόδειξη το δηλώνει ρητά, με τον λόγο.
        payroll_missing = (
            svc.exit_payroll_preview(employment_id)["blocked"]
            or "δεν έχει υπολογιστεί ακόμη"
        )
    for g in proj.gifts:
        if (g.employee_id == employment_id and g.year == year
                and g.gift_type in gift_types):
            items.append({
                "kind": "gift",
                "label": GIFT_LABELS.get(g.gift_type, "Δώρο"),
                "note": "καταβάλλεται στην ημερομηνία του Δώρου, όχι τώρα",
                "gross": g.gross, "efka": g.efka_employee, "fmy": g.fmy,
                "net": g.net, "now": False,
                "pay_date": pe.gift_payment_date(g.gift_type, g.year),
            })
    a = (next((x for x in proj.allowances
               if x.employee_id == employment_id and x.year == year), None)
         if allowance_settled else None)
    if a:
        items.append({
            "kind": "allowance",
            "label": "Επίδομα Αδείας", "note": "pro-rata στην αποχώρηση",
            "gross": a.gross, "efka": a.efka_employee, "fmy": a.fmy,
            "net": a.net, "now": True,
        })
    lc = svc.leave_compensation(employment_id)
    if lc:
        items.append({
            "kind": "leave_comp",
            "label": "Αποζημίωση μη ληφθείσας αδείας",
            "note": "μόνο ΦΜΥ — καμία ασφαλιστική κράτηση",
            "gross": lc.gross, "efka": 0.0, "fmy": lc.fmy, "net": lc.net,
            "now": True,
        })
    sev = svc.severance(employment_id)
    if sev:
        items.append({
            "kind": "severance",
            "label": "Αποζημίωση απόλυσης",
            "note": "αυτοτελής φόρος — καμία ασφαλιστική κράτηση",
            "gross": sev.gross, "efka": 0.0, "fmy": sev.fmy, "net": sev.net,
            "now": True,
        })
    if not items:
        return None

    def tot(rows, field):
        return round(sum(i[field] for i in rows), 2)

    now_rows = [i for i in items if i["now"]]
    later_rows = [i for i in items if not i["now"]]
    return {
        "lines": items,
        "now_lines": now_rows,
        "later_lines": later_rows,
        "termination_date": e.termination_date,
        # Λόγος για τον οποίο ΛΕΙΠΟΥΝ οι αποδοχές του μήνα («» αν υπάρχουν)
        "payroll_missing": payroll_missing,
        "payroll_period": (
            f"{MONTHS_GEN[e.termination_date.month]} {e.termination_date.year}"
        ),
        "total_gross": tot(items, "gross"),
        "total_efka": tot(items, "efka"),
        "total_fmy": tot(items, "fmy"),
        "total_net": tot(items, "net"),
        # Καταβλητέο ΤΩΡΑ (ημέρα αποχώρησης) — ΟΛΑ εκτός Δώρου
        "now_gross": tot(now_rows, "gross"),
        "now_efka": tot(now_rows, "efka"),
        "now_fmy": tot(now_rows, "fmy"),
        "now_net": tot(now_rows, "net"),
        "later_net": tot(later_rows, "net"),
    }


def person_info(person_id: str, year: int):
    """Σύνοψη προσώπου για το info modal: περίοδοι απασχόλησης, υπόλοιπο
    κανονικής άδειας (τρέχον έτος αναφοράς) και τελευταία ασθένεια."""
    from misevent.domain.events import LeaveType

    p = proj.persons.get(person_id)
    if p is None:
        return None
    emps = employment_rows(person_id)  # νεότερη πρώτη

    # Υπόλοιπο αδείας: για την ενεργή (ή πιο πρόσφατη) περίοδο
    leave = None
    target = next((e for e in emps if e.active), emps[0] if emps else None)
    if target is not None:
        ent = svc.annual_leave_entitlement(target.employment_id, year)
        taken = svc.annual_leave_taken(target.employment_id, year)
        leave = {
            "employment": target,
            "entitlement": ent,
            "taken": taken,
            "remaining": ent - taken,
            "year": year,
        }

    # Τελευταία ασθένεια σε όλες τις περιόδους
    last_sick = None
    for e in emps:
        for lv in e.active_leaves():
            if lv.leave_type != LeaveType.SICK:
                continue
            if last_sick is None or lv.date_from > last_sick["date_from"]:
                last_sick = {"date_from": lv.date_from, "date_to": lv.date_to}

    # Ιστορικό μεταβολών (PersonDetailsChanged) — νεότερη ισχύς πρώτη
    def shown(k, v):
        if k == "marital_status":
            return MARITAL_LABELS.get(v, v)
        if isinstance(v, str) and k.endswith(("_date", "_from", "_to")) and v:
            v = date.fromisoformat(v)
        return config.gr_date(v) if isinstance(v, date) else (v if v != "" else "—")

    changes = [
        {
            "effective_from": c["effective_from"],
            "rows": [
                (PERSON_FIELD_LABELS.get(k, k), shown(k, old), shown(k, new))
                for k, (old, new) in c["fields"].items()
            ],
        }
        for c in reversed(p.changes)
    ]
    return {"person": p, "employments": emps, "leave": leave,
            "last_sick": last_sick, "changes": changes}


# ------------------------------------------------------------ Μισθοδοσία
def payroll_rows(y: int, m: int, preview: bool):
    """Γραμμές μισθοδοσίας: πραγματικές (done) ή προβλεπόμενες (preview)."""
    rows = []
    if not preview:
        for p in proj.payrolls:
            if p.period_year == y and p.period_month == m:
                rows.append(
                    {
                        "Εργαζόμενος": emp_name(p.employee_id),
                        "Ημέρες": p.insurance_days,
                        "Προσαυξ.": p.premiums,
                        "Μικτά": p.gross,
                        "ΕΦΚΑ εργαζ.": p.efka_employee,
                        "ΕΦΚΑ εργοδ.": p.efka_employer,
                        "ΦΜΥ": p.fmy,
                        "Καθαρά": p.net,
                    }
                )
        return rows

    # Preview πάνω στο ΙΔΙΟ συγκεντρωτικό που θα χρησιμοποιήσει η μισθοδοσία
    # (εγκεκριμένο αν υπάρχει, αλλιώς το προσχέδιο) — ίδιοι αριθμοί, όχι
    # άλλοι: ασθένεια και προσαυξήσεις ωρών όπως στη run_monthly_payroll.
    end = last_working_day_of_month(y, m)
    wh_rules = config.work_hour_rules_for(end)
    sheet = {r["eid"]: r for r in attendance_sheet(y, m)["rows"]}
    for e in proj.employments.values():
        if not e.hire_submitted or not e.active or e.hire_date is None or e.hire_date > end:
            continue
        line = sheet.get(e.employment_id)
        if line is None:
            continue
        unpaid, days = svc.payroll_days(e, line)
        sick = svc.sick_deduction(e, line, y, m)
        hourly = work_hours.hourly_rate(
            e.pay_type, e.monthly_gross, e.daily_wage, e.weekly_hours
        )
        premium = work_hours.pay_supplement(hourly, line, wh_rules)["total"]
        rates = svc.contribution_rates(e.insurance_profile, end)
        if e.pay_type == "daily":
            if days == 0:
                continue  # καμία παρουσία -> καμία μισθοδοσία
            r = pe.calc_monthly_daily(e.daily_wage, days, e.children_on(end), end,
                                      sick=sick, premiums=premium,
                                      rates=rates)
        else:
            r = pe.calc_monthly(e.monthly_gross, e.children_on(end), end,
                                unpaid_days=unpaid, sick=sick,
                                premiums=premium, rates=rates,
                                base_days=pe.monthly_insurance_days(
                                    e.weekly_hours, line.get("month_planned")))
        rows.append(
            {
                "Εργαζόμενος": e.full_name,
                "Ημέρες": days,
                "Προσαυξ.": r.premiums,
                "Μικτά": r.gross,
                "ΕΦΚΑ εργαζ.": r.efka_employee,
                "ΕΦΚΑ εργοδ.": r.efka_employer,
                "ΦΜΥ": r.fmy,
                "Καθαρά": r.net,
            }
        )
    return rows


# Τα επεξεργάσιμα πεδία του φύλλου παρουσιών: (όνομα input, κλειδί γραμμής,
# τύπος). Το input στον κρυφό πίνακα είναι `{input}_{eid}` (όπως τα διαβάζει
# το approve()), στο modal σκέτο `{input}`. ΜΙΑ πηγή για template/view/query.
SHEET_INPUTS = [
    ("worked", "worked", int),
    ("unpaid", "unpaid", int),
    ("leave", "leave", int),
    ("efka", "efka_days", int),
    ("shalf", "sick_half", int),
    ("sfull", "sick_full", int),
    ("sben", "sick_benefit", float),
] + [(hf, hf, float) for hf in work_hours.SHEET_HOUR_FIELDS]

# input-name -> (κλειδί γραμμής, τύπος) για γρήγορη ανάγνωση από view/query
_INPUT_MAP = {name: (key, cast) for name, key, cast in SHEET_INPUTS}


def _attendance_row(y, m, eid, d, line, wh_rules) -> dict:
    """Χτίζει ΜΙΑ γραμμή του φύλλου: draft `d` με το `line` (εγκεκριμένο ή
    σταδιακά επεξεργασμένο) να υπερισχύει ανά πεδίο. Ίδιο σχήμα παντού."""
    emp = proj.employments[eid]
    hourly = work_hours.hourly_rate(
        emp.pay_type, emp.monthly_gross, emp.daily_wage, emp.weekly_hours
    )

    def g(key, default):
        v = line.get(key) if line else None
        return v if v not in (None, "") else default

    hol = holiday_names(y)
    row = {
        "eid": eid,
        "name": d["name"],
        "pay_type": d["pay_type"],
        "planned": d["planned"],
        "expected_efka": d["efka_days"],
        "source": g("source", d["source"]),
        "hourly_rate": hourly,
        # Συμφωνία προγράμματος ↔ κάρτας (§5.11): ημέρες με απόκλιση κάρτας
        # και ημέρες χωρίς χτύπημα (μετρούν με το πρόγραμμα)
        "card_days": d["card_days"],
        "no_punch_days": d["no_punch_days"],
        # ΚΑΛΥΨΗ ΚΑΡΤΑΣ: οι εισηγμένες παρουσίες καλύπτουν ΟΛΕΣ τις ημέρες
        # εργασίας (φυσικής παρουσίας) του μήνα; full | holiday (λείπουν
        # ΜΟΝΟ αργίες — εντάξει) | partial | none | n/a
        "card_cover": (
            "n/a" if not d["card_needed"]
            else "none" if d["source"] != "recorded"
            else "full" if not d["no_punch_days"]
            else "holiday" if all(x in hol for x in d["no_punch_days"])
            else "partial"
        ),
        # τα ονόματα των αργιών ανάμεσα στις ημέρες χωρίς χτύπημα
        "missing_holidays": [hol[x] for x in d["no_punch_days"] if x in hol],
        # hint του badge: κάθε ημέρα χωρίς χτύπημα, με την αργία της αν είναι
        "no_punch_hint": ", ".join(
            config.gr_date(x) + (f" ({hol[x]})" if x in hol else "")
            for x in d["no_punch_days"]),
        "card_unreported": bool(d["card_days"])
        and proj.schedule_system_on(date(y, m, 1)) == "pre",
    }
    for _name, key, cast in SHEET_INPUTS:
        row[key] = cast(g(key, d[key]))
    row["premium"] = work_hours.pay_supplement(hourly, row, wh_rules)["total"]
    # Περίληψη για τον read-only πίνακα
    row["sick_days"] = row["sick_half"] + row["sick_full"]
    row["premium_hours"] = round(
        sum(row[f] for f in work_hours.SHEET_HOUR_FIELDS if f != "normal"), 2
    )
    return row


def attendance_row(y: int, m: int, eid: str, override: dict | None = None):
    """Μία γραμμή του φύλλου για συγκεκριμένη απασχόληση (modal/staging).

    `override`: τιμές (keyed by κλειδί γραμμής) που υπερισχύουν — ό,τι έχει
    ήδη σταδιακά επεξεργαστεί ο χρήστης. Επιστρέφει None αν η απασχόληση δεν
    ανήκει στην περίοδο."""
    d = svc.attendance_draft(y, m).get(eid)
    if d is None:
        return None
    approved = proj.attendance_sheets.get((y, m))
    line = dict((approved.lines.get(eid) if approved else None) or {})
    if override:
        line.update({k: v for k, v in override.items() if v not in (None, "")})
    wh_rules = config.work_hour_rules_for(date(y, m, 1))
    return _attendance_row(y, m, eid, d, line, wh_rules)


# Τα πεδία ΗΜΕΡΩΝ του φύλλου με την ετικέτα και τη μονάδα τους
_DAY_ITEMS = [
    ("planned", "Προγραμματισμένες ημέρες", "ημ."),
    ("worked", "Ημέρες εργασίας", "ημ."),
    ("unpaid", "Άνευ αποδοχών", "ημ."),
    ("leave", "Λοιπές άδειες", "ημ."),
    ("efka_days", "Ημέρες ΕΦΚΑ", "ημ."),
    ("sick_half", "Ασθένεια — μισές (τριήμερο)", "ημ."),
    ("sick_full", "Ασθένεια — πλήρεις", "ημ."),
    ("sick_benefit", "Επίδομα ασθενείας ΕΦΚΑ", "€"),
]


def attendance_items(row: dict) -> list[tuple[str, str]]:
    """[(ετικέτα, τιμή)] μιας γραμμής του φύλλου — ΜΟΝΟ τα μη μηδενικά:
    ημέρες, ασθένεια και κάθε κατηγορία ωρών (κλάση × ημέρα/νύχτα ×
    εργάσιμη/αργία, π.χ. «Υπερεργασία (νύχτα)»)."""
    def num(v):
        return f"{v:g}".replace(".", ",")

    out = [(lbl, f"{num(row[k])} {unit}") for k, lbl, unit in _DAY_ITEMS
           if row.get(k)]
    for f in work_hours.SHEET_HOUR_FIELDS:
        if row.get(f):
            cls = next(c for c in work_hours.RATE_CLASSES if f.startswith(c))
            variant = work_hours.VARIANT_LABELS[f[len(cls):]]
            out.append((f"{work_hours.RATE_LABELS[cls]} ({variant})",
                        f"{num(row[f])} ώρ."))
    return out


def attendance_premium(y: int, m: int, eid: str, override: dict | None = None):
    """Αναλυτικός υπολογισμός προσαυξήσεων μιας απασχόλησης (για το modal
    ανάλυσης). Επιστρέφει {row, lines, total} — κάθε γραμμή έχει ώρες,
    ωρομίσθιο, συντελεστή και ποσό."""
    row = attendance_row(y, m, eid, override)
    if row is None:
        return None
    rules = config.work_hour_rules_for(date(y, m, 1))
    br = work_hours.pay_supplement(row["hourly_rate"], row, rules)
    return {"row": row, "lines": br["lines"], "total": br["total"]}


def class_hourly_rates(hourly: float, y: int, m: int) -> dict:
    """Το ωρομίσθιο ΚΑΘΕ κλάσης (ωρομίσθιο x (1+κλάση%)) — για το modal εισόδου."""
    rules = config.work_hour_rules_for(date(y, m, 1))
    return {c: work_hours.class_rate(c, hourly, rules)
            for c in work_hours.RATE_CLASSES}


def attendance_sheet(y: int, m: int) -> dict:
    """Το συγκεντρωτικό της περιόδου: εγκεκριμένο (αν υπάρχει) ή προσχέδιο.

    Read-only περίληψη ανά εργαζόμενο· η επεξεργασία γίνεται ανά άτομο σε
    modal (βλ. attendance_row / views.attendance)."""
    approved = proj.attendance_sheets.get((y, m))
    draft = svc.attendance_draft(y, m)
    wh_rules = config.work_hour_rules_for(date(y, m, 1))
    rows = [
        _attendance_row(
            y, m, eid, d,
            dict((approved.lines.get(eid) if approved else None) or {}),
            wh_rules,
        )
        for eid, d in draft.items()
    ]
    return {
        "rows": sorted(rows, key=lambda r: r["name"]),
        "done": approved is not None,
        # Σημαία: εισήχθη κάρτα και καλύπτει τις ημέρες εργασίας ΟΛΩΝ (λείπουν
        # το πολύ αργίες)
        "card_complete": any(r["card_cover"] in ("full", "holiday") for r in rows)
        and all(r["card_cover"] in ("full", "holiday", "n/a") for r in rows),
        "inputs": SHEET_INPUTS,
        # Υποβολές ΕΡΓΑΝΗ πρόσληψης που εκκρεμούν — προαπαιτούμενο των παρουσιών.
        # Όσο υπάρχει έστω μία, η έγκριση μπλοκάρεται (και στο command layer).
        "ergani_pending": svc.attendance_ergani_pending(y, m),
        # «Διάφορα → card_required»: εκκρεμής έλεγχος κάρτας — μπλοκάρει κι αυτός
        "card_pending": [
            {"eid": eid, "name": d["name"]} for eid, d in draft.items()
            if svc.card_month_pending(proj.employments[eid], y, m)
        ],
        # Κλεισμένη μισθοδοσία = κλειδωμένες παρουσίες (§5.6): η επανέγκριση
        # θα άλλαζε ημέρες ΕΦΚΑ που έχουν ήδη πληρωθεί/υποβληθεί.
        "period_closed": svc.is_period_closed(y, m),
        "sources": {
            "recorded": "καταγεγραμμένες παρουσίες",
            "schedule": "τεκμαρτές από το πρόγραμμα",
            "manual": "χειροκίνητη διόρθωση",
        },
    }


def leave_status(e, year: int) -> dict:
    """Κανονική άδεια του έτους: δικαίωμα, ληφθείσες, υπόλοιπο και αν έχει
    υπολογιστεί το Επίδομα Αδείας."""
    ent = svc.annual_leave_entitlement(e.employee_id, year)
    taken = svc.annual_leave_taken(e.employee_id, year)
    return {
        "entitlement": ent,
        "taken": taken,
        "remaining": ent - taken,
        "allowance_done": any(
            a.employee_id == e.employee_id and a.year == year
            for a in proj.allowances
        ),
    }


def next_payroll_period():
    """Η τρέχουσα περίοδος της αλυσίδας: η παλαιότερη εκκρεμής μισθοδοσία
    με εργαζόμενους (οι κενές περίοδοι προσπερνιούνται — βλ.
    `svc.period_has_employment`)."""
    pend = [
        (o.period_year, o.period_month)
        for o in proj.obligations.values()
        if o.obligation_type == "MONTHLY_PAYROLL" and not o.fulfilled
        and svc.period_has_employment(o.period_year, o.period_month)
    ]
    return min(pend) if pend else None


def time_strip(ref: date) -> dict:
    """Λωρίδα χρόνου του header: τα έτη χρήσης ΚΑΙ (πάντα) το προηγούμενο και
    το επόμενο της ημερομηνίας αναφοράς, με κατάσταση ανά μήνα από τις
    υποχρεώσεις ΠΕΡΙΟΔΟΥ, πλήθος εργαζομένων και σήματα έκτακτων καταβολών."""
    exits = [(ev.termination_date, kind)
             for kind, events in (("severance", proj.severances),
                                  ("leave_comp", proj.leave_compensations))
             for ev in events]
    return build_time_strip(ref, proj.obligations.values(),
                            proj.employments.values(), exits)


# Σήματα μήνα (λωρίδα χρόνου): τύπος -> (εικονίδιο, ετικέτα). Τα Δώρα είναι
# ΕΤΗΣΙΕΣ υποχρεώσεις (χωρίς μήνα περιόδου) -> μπαίνουν στον μήνα της
# προθεσμίας τους (Μ. Τετάρτη / 21 Δεκ.)· το Επίδομα Αδείας έχει περίοδο.
STRIP_BADGES = {
    "EASTER_GIFT": ("🥚", "Δώρο Πάσχα"),
    "CHRISTMAS_GIFT": ("🎄", "Δώρο Χριστουγέννων"),
    "LEAVE_ALLOWANCE": ("🏖️", "Επίδομα Αδείας"),
    "severance": ("📤", "Αποζημίωση απόλυσης"),
    "leave_comp": ("🗓️", "Αποζημίωση αδείας"),
}


def _active_in(e, first: date, last: date) -> bool:
    """Ίδιος κανόνας με το `period_has_employment`: αναγγελμένη απασχόληση
    που ήταν ενεργή έστω μία ημέρα του μήνα."""
    return (e.hire_submitted and e.hire_date is not None and e.hire_date <= last
            and (e.termination_date is None or e.termination_date >= first))


def build_time_strip(ref: date, obligations, employments=(), exits=()) -> dict:
    """Ανά μήνα: «done» (όλες ολοκληρωμένες), «late» (ανοικτή με προθεσμία
    πριν το `ref`), «open» (ανοικτή εμπρόθεσμη), «» (καμία υποχρέωση)·
    `emps` = ενεργοί εργαζόμενοι, `badges` = Δώρα/Επίδομα/αποζημιώσεις
    (`exits`: (ημερομηνία αποχώρησης, είδος))."""
    months, badges = {}, {}

    def badge(y, m, kind, done):
        b = badges.setdefault((y, m), {}).setdefault(
            kind, {"kind": kind, "icon": STRIP_BADGES[kind][0],
                   "label": STRIP_BADGES[kind][1], "n": 0, "done": True})
        b["n"] += 1
        b["done"] = b["done"] and done

    for o in obligations:
        if o.obligation_type in STRIP_BADGES:
            y, m = ((o.period_year, o.period_month) if o.period_month
                    else (o.due_date.year, o.due_date.month))
            badge(y, m, o.obligation_type, o.fulfilled)
        if not o.period_month:
            continue  # ετήσιες (π.χ. Δώρα): δεν μετρούν στην κατάσταση μήνα
        c = months.setdefault((o.period_year, o.period_month), {"open": 0, "late": 0, "done": 0})
        c["done" if o.fulfilled else "late" if o.due_date < ref else "open"] += 1
    for d, kind in exits:
        badge(d.year, d.month, kind, True)
    employments = list(employments)
    def state(c):  # η χειρότερη κατάσταση κερδίζει
        return next((k for k in ("late", "open", "done") if c[k]), "")

    # Πάντα και το προηγούμενο και το επόμενο έτος — ακόμη κι αν είναι κενά
    span = [y for y, _ in months] + [ref.year - 1, ref.year + 1]
    now = (ref.year, ref.month)
    years = []
    for y in range(min(span), max(span) + 1):
        ms = []
        for m in range(1, 13):
            c = months.get((y, m), {"open": 0, "late": 0, "done": 0})
            first, last = date(y, m, 1), date(y, m, monthrange(y, m)[1])
            ms.append({
                "m": m, "label": MONTHS_SHORT[m], "name": f"{MONTHS_NOM[m]} {y}", **c,
                "state": state(c),
                "now": (y, m) == now, "future": (y, m) > now,
                "emps": sum(_active_in(e, first, last) for e in employments),
                "badges": list(badges.get((y, m), {}).values()),
            })
        tot = {k: sum(x[k] for x in ms) for k in ("open", "late", "done")}
        years.append({"y": y, "months": ms, **tot, "state": state(tot)})
    return {"years": years, "open_y": ref.year}


def month_detail(y: int, m: int, ref: date) -> dict:
    """ΟΘΟΝΗ ΜΗΝΑ (κλικ σε μήνα της λωρίδας χρόνου): ό,τι αφορά την περίοδο —
    παρουσίες/απουσίες, άδειες, μισθοδοσία, έκτακτες καταβολές, υποχρεώσεις,
    υποβολές και πληρωμές. Μόνο ανάγνωση· ξαναχρησιμοποιεί τα υπάρχοντα
    ερωτήματα, ώστε οι αριθμοί να είναι ΙΔΙΟΙ με τις υπόλοιπες σελίδες."""
    first, last = date(y, m, 1), date(y, m, monthrange(y, m)[1])

    sheet = attendance_sheet(y, m)
    att = []
    for r in sheet["rows"]:
        hours = round(sum(r[f] for f in work_hours.SHEET_HOUR_FIELDS), 2)
        att.append({
            "eid": r["eid"], "name": r["name"], "worked": r["worked"],
            "leave": r["leave"], "sick": r["sick_days"], "unpaid": r["unpaid"],
            "efka": r["efka_days"], "hours": hours, "premium_hours": r["premium_hours"],
        })
    span = max([a["worked"] + a["leave"] + a["sick"] + a["unpaid"] for a in att] + [1])

    leaves = []
    for e in proj.employments.values():
        for lv in e.active_leaves():
            if lv.date_to < first or lv.date_from > last:
                continue
            a, b = max(lv.date_from, first), min(lv.date_to, last)
            code = getattr(lv.leave_type, "value", lv.leave_type)
            leaves.append({
                "name": e.full_name, "type": code, "label": LEAVE_LABELS.get(code, code),
                "from": lv.date_from, "to": lv.date_to, "days": (b - a).days + 1,
                # θέση στον μήνα (%) για τη γραμμή χρόνου
                "x": (a.day - 1) / last.day * 100, "w": ((b - a).days + 1) / last.day * 100,
            })
    leaves.sort(key=lambda lv: (lv["from"], lv["name"]))

    actual = payroll_rows(y, m, preview=False)
    pay = actual or payroll_rows(y, m, preview=True)
    pay_tot = money_totals(pay, ["Μικτά", "ΕΦΚΑ εργαζ.", "ΕΦΚΑ εργοδ.", "ΦΜΥ", "Καθαρά"])

    extras = [r for r in payroll_ledger(y) if r["month"] == m and r["kind"] != "payroll"]

    obs = []
    for o in sorted(proj.obligations.values(), key=ot.sort_key):
        if (o.period_year, o.period_month) != (y, m):
            continue
        obs.append({
            "id": o.obligation_id, "label": OB_LABELS.get(o.obligation_type, o.obligation_type),
            "icon": OB_ICONS.get(o.obligation_type, "📌"),
            "track": OB_TRACKS.get(o.obligation_type, "payroll"),
            "due": o.due_date, "who": emp_name(o.employee_id) if o.employee_id else "",
            "state": "done" if o.fulfilled else "late" if o.due_date < ref else "open",
            "late": days_late(o, ref),
        })

    subs = [{"label": f"ΑΠΔ {s.fund}", "amount": s.total_contributions, "ref": s.reference}
            for s in proj.apd_submissions if (s.period_year, s.period_month) == (y, m)]
    subs += [{"label": "ΦΜΥ", "amount": s.total_fmy, "ref": s.reference}
             for s in proj.fmy_submissions if (s.period_year, s.period_month) == (y, m)]
    payments = [{"label": PAYMENT_KIND_LABELS.get(p.payment_type, p.payment_type.upper()),
                 "amount": p.amount, "who": emp_name(p.beneficiary)
                 if p.beneficiary in proj.employments else p.beneficiary}
                for p in proj.payments if (p.period_year, p.period_month) == (y, m)]

    counts = {k: sum(o["state"] == k for o in obs) for k in ("done", "open", "late")}
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
    ny, nm = (y, m + 1) if m < 12 else (y + 1, 1)
    return {
        "y": y, "m": m, "title": f"{MONTHS_NOM[m]} {y}", "days": last.day,
        "first_weekday": first.weekday(),
        "holidays": sorted(d.day for d in public_holidays(y) if d.month == m),
        "today_day": ref.day if (ref.year, ref.month) == (y, m) else None,
        # ίδιος κανόνας με τη λωρίδα: η χειρότερη κατάσταση κερδίζει
        "state": next((k for k in ("late", "open", "done") if counts[k]), ""),
        "now": (y, m) == (ref.year, ref.month), "future": (y, m) > (ref.year, ref.month),
        "prev": (py, pm, f"{MONTHS_NOM[pm]} {py}"), "next": (ny, nm, f"{MONTHS_NOM[nm]} {ny}"),
        "att": att, "att_span": span, "sheet_done": sheet["done"],
        "att_tot": {k: round(sum(a[k] for a in att), 2)
                    for k in ("worked", "leave", "sick", "unpaid", "efka", "hours")},
        "leaves": leaves,
        # ημέρες του μήνα με έστω μία άδεια — τελεία στο μικρό ημερολόγιο
        "leave_days": sorted({
            d for lv in leaves
            for d in range(max(lv["from"], first).day, min(lv["to"], last).day + 1)
        }),
        "pay": pay, "pay_done": bool(actual), "pay_tot": pay_tot,
        "pay_cost": round(pay_tot.get("Μικτά", 0) + pay_tot.get("ΕΦΚΑ εργοδ.", 0), 2),
        "extras": extras, "obs": obs,
        "ob_counts": counts,
        "subs": subs, "payments": payments,
        "paid_total": round(sum(p["amount"] for p in payments), 2),
    }


def payroll_warnings(y: int, m: int):
    """Ημερομίσθιοι με ΜΗΔΕΝ ημέρες στο συγκεντρωτικό: η μισθοδοσία τους
    ΠΑΡΑΛΕΙΠΕΤΑΙ — να το δει ο χρήστης πριν πατήσει υπολογισμό."""
    out = []
    for r in attendance_sheet(y, m)["rows"]:
        if r["efka_days"] == 0:
            out.append(
                f"{r['name']}: 0 ημέρες ΕΦΚΑ τον {m}/{y} — δεν θα υπολογιστεί "
                "μισθοδοσία. Δείτε παρουσίες/πρόγραμμα."
            )
    return out


def _pct(v) -> str:
    """0.1337 -> '13,37%' (ελληνική μορφή, δύο δεκαδικά)."""
    return f"{v * 100:.2f}".replace(".", ",") + "%"


def apd_warnings(year: int, month: int):
    """ΤΟ ΠΟΣΟΣΤΟ ΤΟΥ ΥΠΟΛΟΓΙΣΜΟΥ ΔΕΝ ΕΙΝΑΙ ΑΥΤΟ ΠΟΥ ΙΣΧΥΕΙ ΤΩΡΑ — να το δει
    ο χρήστης ΠΡΙΝ υποβάλει, όχι όταν το απορρίψει ο ΕΦΚΑ.

    Η ΑΠΔ δηλώνει ό,τι υπολογίστηκε (οι εισφορές έρχονται από τα events), άρα
    το αρχείο ΔΕΝ αλλάζει από μόνο του όταν ενημερωθεί το μητρώο ΚΠΚ. Αν
    ισχύει το νέο ποσοστό, η μισθοδοσία πρέπει να ανοίξει και να
    ξαναϋπολογιστεί (`svc.contribution_drift`)."""
    out = []
    for d in svc.contribution_drift(year, month):
        used, now = d["used"], d["now"]
        how = " (κατ' εκτίμηση)" if d["estimated"] else ""
        out.append(
            f"{d['name']}: η μισθοδοσία υπολογίστηκε με {_pct(used[0])}/"
            f"{_pct(used[1])}{how}, ενώ το μητρώο ΚΠΚ ορίζει τώρα "
            f"{_pct(now[0])}/{_pct(now[1])}. Η ΑΠΔ θα δηλώσει ΟΤΙ "
            f"ΥΠΟΛΟΓΙΣΤΗΚΕ — αν ισχύει το νέο ποσοστό, άνοιξε τη μισθοδοσία "
            f"{month}/{year} και ξαναϋπολόγισε."
        )
    return out


def allowance_warnings(year: int, month: int):
    """1ο/2ο ΕΤΟΣ: το επίδομα στηρίζεται σε ΠΑΡΑΔΟΧΗ — να το δει ο χρήστης
    ΠΡΙΝ πατήσει «Υπολογισμός».

    Η αναλογία των μηνών μετριέται έως 31/12, δηλαδή υποθέτει ότι ο
    εργαζόμενος θα εργαστεί ως το τέλος του έτους. Αν αποχωρήσει νωρίτερα,
    το σωστό ποσό είναι μικρότερο — και ό,τι έχει ήδη καταβληθεί ΔΕΝ
    διορθώνεται (η εκκαθάριση αποχώρησης προσπερνά όποιον έχει ήδη επίδομα).
    Διέξοδος: αναβολή του υπολογισμού με παράταση προθεσμίας, έως 31/12.

    Δεν προειδοποιεί όπου δεν υπάρχει παραδοχή: 3ο έτος+ (ολόκληρο ούτως ή
    άλλως) και ήδη καταχωρημένη αποχώρηση (η αναλογία είναι οριστική)."""
    out = []
    for e in svc.allowance_beneficiaries(year, month):
        if e.hire_date is None or pe.is_third_service_year(year, e.hire_date):
            continue
        if e.termination_date is not None:
            continue
        r = svc.allowance_preview_for(e, year)
        if r.gross <= 0:
            continue
        nth = "1ο" if e.hire_date.year == year else "2ο"
        out.append(
            f"{e.full_name}: {nth} έτος — το επίδομα ({money(r.gross)} € μικτά) "
            f"υπολογίζεται με την παραδοχή ότι θα εργαστεί έως 31/12/{year}. Αν "
            "αποχωρήσει νωρίτερα, το ποσό θα ήταν μικρότερο και ΔΕΝ διορθώνεται "
            "μετά την καταβολή — μπορείτε να αναβάλετε τον υπολογισμό "
            f"(παράταση προθεσμίας, έως 31/12/{year})."
        )
    return out


def gift_rows(gtype: str, year: int, preview: bool, month: int | None = None):
    """Γραμμές Δώρου/Επιδόματος. Για το Επίδομα Αδείας, `month` = η περίοδος
    της υποχρέωσης: μόνο όσοι πήραν την πρώτη τους άδεια εκείνον τον μήνα."""
    beneficiaries = (
        {e.employee_id for e in svc.allowance_beneficiaries(year, month)}
        if gtype == "allowance" and month
        else None
    )
    rows = []
    if not preview:
        src = (
            proj.allowances
            if gtype == "allowance"
            else [g for g in proj.gifts if g.gift_type == gtype]
        )
        for g in src:
            if beneficiaries is not None and g.employee_id not in beneficiaries:
                continue
            if g.year == year:
                rows.append(
                    {
                        "Εργαζόμενος": emp_name(g.employee_id),
                        "Μικτά": g.gross,
                        "ΕΦΚΑ εργαζ.": g.efka_employee,
                        "ΦΜΥ": g.fmy,
                        "Καθαρά": g.net,
                    }
                )
        return rows

    for e in proj.employments.values():
        if not e.hire_submitted or e.hire_date is None:
            continue
        if beneficiaries is not None and e.employee_id not in beneficiaries:
            continue
        if gtype in ("christmas", "easter"):
            # ΜΙΑ πηγή αλήθειας με τον υπολογισμό — μαζί με την ημερομηνία
            # αναφοράς του μισθού (Δώρο Πάσχα: 15 ημέρες πριν τη Μ. Τετάρτη).
            r = svc.gift_preview_for(e, gtype, year)
        else:
            # ΜΙΑ πηγή αλήθειας με τον υπολογισμό — όπως και στα Δώρα από πάνω
            r = svc.allowance_preview_for(e, year)
        if r.gross > 0:
            rows.append(
                {
                    "Εργαζόμενος": e.full_name,
                    "Μικτά": r.gross,
                    "ΕΦΚΑ εργαζ.": r.efka_employee,
                    "ΦΜΥ": r.fmy,
                    "Καθαρά": r.net,
                }
            )
    return rows


def allowance_periods(year: int):
    """Οι περίοδοι Επιδόματος Αδείας του έτους — μία ανά μήνα στον οποίο
    κάποιος εργαζόμενος πήρε την πρώτη του κανονική άδεια."""
    out = []
    obs = [
        o
        for o in proj.obligations.values()
        if o.obligation_type == "LEAVE_ALLOWANCE" and o.period_year == year
    ]
    periods = {o.period_month: o.fulfilled for o in obs}
    # Δεκέμβριος ΠΡΙΝ γεννηθεί η υποχρέωση (κλείσιμο Νοεμβρίου — scheduler
    # `_ensure_year_end_allowance`): όσοι δεν πήραν άδεια, ως προεπισκόπηση
    # με την παραδοχή ότι εργάζονται έως 31/12 — χωρίς κουμπί υπολογισμού.
    expected = 12 not in periods and bool(svc.allowance_beneficiaries(year, 12))
    if expected:
        periods[12] = False
    for m, fulfilled in sorted(periods.items()):
        rows = gift_rows("allowance", year, preview=not fulfilled, month=m)
        if fulfilled and not rows:
            continue  # έκλεισε ως ορφανή: η «πρώτη άδεια» μετακινήθηκε
        out.append(
            {
                "month": m,
                "done": fulfilled,
                "expected": expected and m == 12,
                "rows": rows,
                # Η παραδοχή του 1ου/2ου έτους — ΠΡΙΝ πατηθεί ο υπολογισμός
                "warnings": [] if fulfilled else allowance_warnings(year, m),
                "totals": money_totals(
                    rows, ["Μικτά", "ΕΦΚΑ εργαζ.", "ΦΜΥ", "Καθαρά"]
                ),
            }
        )
    return out


def _latest_event_date(event_type: str, pred) -> date | None:
    """Η ημερομηνία (occurred_at) του ΤΕΛΕΥΤΑΙΟΥ event του τύπου που περνά το
    κριτήριο — read-only ανάγνωση του event log (για την «ημερομηνία
    δημιουργίας» στις εκτυπώσεις: ο επανυπολογισμός αντικαθιστά τον παλιό)."""
    latest = None
    for occurred_at, payload in store.raw_by_type(event_type):
        if pred(json.loads(payload)) and (latest is None or occurred_at > latest):
            latest = occurred_at
    return datetime.fromisoformat(latest).date() if latest else None


def payroll_event_date(y: int, m: int) -> date | None:
    """Πότε γράφτηκε ο (τελευταίος) υπολογισμός μισθοδοσίας της περιόδου."""
    return _latest_event_date(
        "MonthlyPayrollCalculated",
        lambda p: p.get("period_year") == y and p.get("period_month") == m,
    )


def gift_event_date(kind: str, year: int, month: int | None = None) -> date | None:
    """Πότε γράφτηκε ο (τελευταίος) υπολογισμός Δώρου/Επιδόματος Αδείας."""
    if kind == "allowance":
        ben = (
            {e.employee_id for e in svc.allowance_beneficiaries(year, month)}
            if month
            else None
        )
        return _latest_event_date(
            "LeaveAllowanceCalculated",
            lambda p: p.get("year") == year
            and (ben is None or p.get("employee_id") in ben),
        )
    return _latest_event_date(
        "GiftCalculated",
        lambda p: p.get("year") == year and p.get("gift_type") == kind,
    )


def gift_analysis(kind: str, year: int, month: int | None = None):
    """Ανάλυση Δώρου/Επιδόματος Αδείας ΑΝΑ εργαζόμενο — για τις εκτυπώσεις
    (κατάσταση & αποδείξεις). ΜΟΝΟ υπολογισμένα: πηγή τα αποθηκευμένα events
    (GiftCalculated / LeaveAllowanceCalculated)· η ανάλυση της βάσης
    ανασυντίθεται από τα στοιχεία της απασχόλησης (ίδιοι τύποι με το
    payroll_engine). Κενή λίστα αν δεν έχει υπολογιστεί."""
    if kind == "allowance":
        src = [g for g in proj.allowances if g.year == year]
        if month:
            ben = {e.employee_id for e in svc.allowance_beneficiaries(year, month)}
            src = [g for g in src if g.employee_id in ben]
        ref = date(year, 7, 31)
    elif kind in ("easter", "christmas"):
        src = [g for g in proj.gifts if g.gift_type == kind and g.year == year]
        # Ημέρα ΚΑΤΑΒΟΛΗΣ (συντελεστές) — για το Δώρο Πάσχα η Μ. Τετάρτη
        ref = pe.gift_payment_date(kind, year)
    else:
        raise ValueError(f"Άγνωστος τύπος: {kind}")

    out = []
    for g in src:
        e = proj.employments.get(g.employee_id)
        # Η αμοιβή της ΒΑΣΗΣ είναι αυτή που ίσχυε την ημερομηνία αναφοράς του
        # Δώρου (Πάσχα: 15 ημέρες πριν την καταβολή), όχι η σημερινή.
        terms = (
            e.contract_on(pe.gift_wage_date(kind, year))
            if e is not None and kind != "allowance"
            else (e.current_terms if e is not None else None)
        )
        a = {
            "gross": g.gross,
            "efka_employee": g.efka_employee,
            "subsidy_employee": g.subsidy_employee,
            "subsidy_code": g.subsidy_code,
            "subsidy_employer": g.subsidy_employer,
            "taxable": round(g.gross - g.efka_employee, 2),
            "fmy": g.fmy,
            "net": g.net,
            "efka_employer": 0.0,
            "pay_type": terms.pay_type if terms is not None else "monthly",
            "wage": terms.wage if terms is not None else 0.0,
        }
        if e is not None:
            rates = svc.contribution_rates(e.insurance_profile, ref)
            a["efka_employer"] = g.efka_employer or round(g.gross * rates.employer, 2)
        if kind == "allowance":
            # Το Επίδομα Αδείας δεν έχει προσαύξηση συντελεστή αδείας
            a["base"], a["leave_coeff"] = g.gross, 0.0
            if e is not None and e.hire_date:
                # ΙΔΙΑ πηγή με τον υπολογισμό: μέχρι Δεκέμβριο ή αποχώρηση
                a["months"] = pe._allowance_months(
                    year, e.hire_date, e.termination_date
                )
        else:
            coeff = config.GIFT_LEAVE_COEFFICIENT
            a["base"] = round(g.gross / (1 + coeff), 2)
            a["leave_coeff"] = coeff
            start, end = (
                (date(year, 1, 1), date(year, 4, 30))
                if kind == "easter"
                else (date(year, 5, 1), date(year, 12, 31))
            )
            a["period"] = (start, end)
            a["full_days"] = (end - start).days + 1
            # Ημερομηνία του ΜΙΣΘΟΥ της βάσης (Πάσχα: καταβολή − 15 ημέρες)
            a["wage_date"] = pe.gift_wage_date(kind, year)
            a["pay_date"] = ref
            if e is not None and e.hire_date:
                a["days"] = pe._employment_overlap_days(
                    e.hire_date, e.termination_date, start, end
                )
        # ΙΔΙΑ ανάλυση με τη μηνιαία μισθοδοσία (§ modal «Ανάλυση»): εισφορές
        # ανά ταμείο/ΚΠΚ + πλήρης παραγωγή του ΦΜΥ. ΠΡΟΣΟΧΗ στις διαφορές:
        # (α) οι εισφορές Δώρων υπολογίζονται ΧΩΡΙΣ πλαφόν (`_gift_result`),
        # (β) η αναγωγή σε ετήσιο είναι x28 για αμοιβή ~ΜΙΣΟΥ μισθού (Δώρο
        #     Πάσχα, Επίδομα Αδείας) και x14 για ΟΛΟΚΛΗΡΟ (Δώρο Χριστουγέννων).
        a["efka_detail"] = _efka_breakdown(
            e, g.gross, g.efka_employee, ref,
            efka_employer=a["efka_employer"], insurable=g.gross,
        )
        a["kind"] = kind
        a["insurable"] = round(g.gross, 2)
        a["efka_cap"] = config.efka_rates_for(ref).monthly_cap
        a["capped"] = False  # τα Δώρα δεν πλαφονάρονται
        a["tax_detail"] = _tax_breakdown(
            e, a["taxable"], g.fmy, ref,
            periods=(config.ANNUAL_SALARIES if kind == "christmas"
                     else pe.HALF_SALARY_PERIODS),
        )
        a["base_detail"] = _gift_base_breakdown(kind, a, e, year)
        out.append(
            {
                "eid": g.employee_id,
                "name": emp_name(g.employee_id),
                "pay_type": a["pay_type"],
                "a": a,
            }
        )
    return sorted(out, key=lambda r: r["name"])


APD_TOTAL_KEYS = ["Ημέρες", "Αποδοχές", "Εισφ. ασφ/νου", "Εισφ. εργοδότη",
                  "Καταβλητέες"]


def apd_rows(y: int, m: int, fund: str):
    """(rows, totals, error) — error αν λείπει ασφαλιστικό προφίλ.

    ΤΑ ΣΥΝΟΛΑ ΕΙΝΑΙ ΑΥΤΑ ΤΗΣ ΚΕΦΑΛΙΔΑΣ ΤΟΥ ΑΡΧΕΙΟΥ: οι αποδοχές (No 17) και
    οι ημέρες (No 16) μετρούν ΜΙΑ φορά ανά πραγματική αμοιβή — η δεύτερη
    κάλυψη του ίδιου ασφαλισμένου στο ΙΔΙΟ αρχείο (π.χ. επικουρική/πρόνοια
    ΕΦΚΑ, με δικό της ΚΠΚ) ξαναγράφει τις ΙΔΙΕΣ αποδοχές. Οι εισφορές
    αθροίζονται ΠΑΝΤΑ (No 18), αφού κάθε κάλυψη οφείλει τις δικές της.
    Γι' αυτό εδώ ΔΕΝ χρησιμοποιείται το γενικό `money_totals`.
    """
    try:
        pairs = svc.build_apd_rows(y, m).get(fund, [])
    except ValueError as exc:
        return None, {}, str(exc)
    rows = [
        {
            "Εργαζόμενος": f"{i.last_name} {i.first_name}".strip(),
            "Τύπος αποδοχών": (
                f"{r.earnings_type} {ET_LABELS.get(r.earnings_type, '')}".strip()
            ),
            # Το ΚΠΚ εξηγεί γιατί ο ίδιος εργαζόμενος έχει πάνω από μία γραμμή
            # με τις ίδιες αποδοχές (μία ανά κάλυψη).
            "ΚΠΚ": r.kpk,
            "Ημέρες": r.insurance_days,
            "Αποδοχές": r.gross,
            "Εισφ. ασφ/νου": r.contrib_employee,
            "Εισφ. εργοδότη": r.contrib_employer,
            "Καταβλητέες": r.payable,
        }
        for i, r in pairs
    ]
    once = [r for _, r in pairs if not r.repeats_earnings]
    totals = {
        "Ημέρες": sum(r.insurance_days for r in once),
        "Αποδοχές": round(sum(r.gross for r in once), 2),
        "Εισφ. ασφ/νου": round(sum(r.contrib_employee for _, r in pairs), 2),
        "Εισφ. εργοδότη": round(sum(r.contrib_employer for _, r in pairs), 2),
        "Καταβλητέες": round(sum(r.payable for _, r in pairs), 2),
    } if rows else {}
    return rows, totals, None


def money_totals(rows, keys) -> dict:
    return {k: round(sum(r.get(k, 0) or 0 for r in rows), 2) for k in keys if rows}


# ------------------------------------------------------------- Πληρωμές
# Στήλες της σελίδας «Πληρωμές»: μία γραμμή ανά περίοδο. Οι πληρωμές ΠΡΟΣ
# ΕΡΓΑΖΟΜΕΝΟΥΣ (μισθοί, Δώρα, Επίδομα Αδείας) συγκεντρώνονται στη στήλη
# «Μισθοί» — το tooltip λέει τι ακριβώς περιέχει.
PAYMENT_COLUMNS = [
    ("salary", "Μισθοί"),
    ("efka", "ΕΦΚΑ"),
    ("teka", "ΤΕΚΑ"),
    ("fmy", "ΦΜΥ"),
]
PAYMENT_KIND_LABELS = {
    "salary": "Μισθοδοσία",
    "easter_gift": "Δώρο Πάσχα",
    "christmas_gift": "Δώρο Χριστουγέννων",
    "leave_allowance": "Επίδομα Αδείας",
    "leave_compensation": "Αποζημίωση Αδείας",
    "severance": "Αποζημίωση Απόλυσης",
}
# Ποια είδη πληρωμής πέφτουν σε ποια στήλη
PAYMENT_COLUMN_OF = {
    "salary": "salary",
    "easter_gift": "salary",
    "christmas_gift": "salary",
    "leave_allowance": "salary",
    "leave_compensation": "salary",
    "severance": "salary",
    "efka": "efka",
    "teka": "teka",
    "fmy": "fmy",
}
# Η ΥΠΟΧΡΕΩΣΗ ΚΑΤΑΒΟΛΗΣ κάθε είδους πληρωμής — από την προθεσμία της
# προκύπτει ΠΟΤΕ πληρώνεται (μητρώο `obligation_types`, όχι hardcoded μήνες).
PAYMENT_OBLIGATION = {
    "salary": "SALARY_PAYMENT",
    "easter_gift": "EASTER_GIFT_PAYMENT",
    "christmas_gift": "CHRISTMAS_GIFT_PAYMENT",
    "leave_allowance": "LEAVE_ALLOWANCE_PAYMENT",
    # οι αποζημιώσεις αποχώρησης εξοφλούνται μαζί με τους μισθούς
    "leave_compensation": "SALARY_PAYMENT",
    "severance": "SALARY_PAYMENT",
    "efka": "EFKA_PAYMENT",
    "teka": "TEKA_PAYMENT",
    "fmy": "FMY_PAYMENT",
}


def period_gr(y: int, m: int) -> str:
    """«Απριλίου 2026» — για τα tooltip της σελίδας Πληρωμές."""
    return f"{MONTHS_GEN[m]} {y}" if 1 <= m <= 12 else str(y)


def _obligation_due(code: str, y: int, m: int) -> date | None:
    """Η προθεσμία της συγκεκριμένης υποχρέωσης (τιμά και τυχόν παράταση)."""
    for ob in proj.obligations.values():
        if (ob.obligation_type == code and ob.period_year == y
                and ob.period_month == m):
            return ob.due_date
    return None


def _payment_period(p) -> tuple[int, int]:
    """Η ΠΕΡΙΟΔΟΣ ΚΑΤΑΒΟΛΗΣ μιας πληρωμής: ο μήνας που ΠΡΟΒΛΕΠΕΤΑΙ να
    πληρωθεί, δηλαδή ο μήνας της **προθεσμίας** της υποχρέωσης καταβολής.

    ΔΕΝ είναι η μισθολογική περίοδος: οι εισφορές της περιόδου Απριλίου
    καταβάλλονται τέλος Μαΐου (`due=("eom", 1)`) και ο ΦΜΥ τέλος Ιουνίου
    (`("eom", 2)`) — άρα εμφανίζονται στη γραμμή του μήνα πληρωμής τους. Η
    προθεσμία διαβάζεται από την ΙΔΙΑ την υποχρέωση (τιμά παρατάσεις και
    overrides του §6α)· fallback ο κανόνας του μητρώου, και για τις καταβολές
    Δώρων (`due=("as_source",)`) η προθεσμία του υπολογισμού τους."""
    code = PAYMENT_OBLIGATION.get(p.payment_type)
    py, pm = p.period_year, p.period_month
    due = _obligation_due(code, py, pm) if code else None
    if due is None and code:
        source = ot.PAYOUT_SOURCE.get(code)
        source_due = _obligation_due(source, py, pm) if source else None
        try:
            due = ot.due_date(code, py, pm, source_due)
        except ValueError:
            due = None  # as_source χωρίς γνωστή πηγή
    if due is None:
        # Έσχατο fallback: ο μήνας που το Δώρο υπολογίστηκε/φορολογήθηκε
        if p.payment_type.endswith("_gift") and not pm:
            kind = p.payment_type.removesuffix("_gift")
            for g in proj.gifts:
                if (g.gift_type == kind and g.year == py
                        and g.employee_id == p.beneficiary):
                    return svc.gift_fmy_period(g)
        return (py, pm or 12)
    return (due.year, due.month)


def _source_periods(group: list) -> list[tuple[int, int]]:
    """Οι ΜΙΣΘΟΛΟΓΙΚΕΣ περίοδοι από τις οποίες προέρχονται οι πληρωμές μιας
    στήλης (μπορεί να διαφέρουν από τη γραμμή — βλ. `_payment_period`)."""
    return sorted({(p.period_year, p.period_month) for p in group})


def _fmy_sources(y: int, m: int) -> list[str]:
    """Από πού προκύπτει ο ΦΜΥ του μήνα (§7): η μισθοδοσία του μήνα + όσα
    Δώρα/Επιδόματα Αδείας ΥΠΟΛΟΓΙΣΤΗΚΑΝ μέσα σε αυτόν."""
    out = []
    if any(p.period_year == y and p.period_month == m for p in proj.payrolls):
        out.append(f"Μισθοδοσία {period_gr(y, m)}")
    kinds = {
        g.gift_type for g in proj.gifts if svc.gift_fmy_period(g) == (y, m)
    }
    for kind in sorted(kinds):
        out.append(f"{PAYMENT_KIND_LABELS[kind + '_gift']} {y}")
    if any(svc.allowance_fmy_period(a) == (y, m) for a in proj.allowances):
        out.append(f"Επίδομα Αδείας {y}")
    return out


def _payment_tip(col: str, group: list) -> str:
    """Το κείμενο του tooltip: ΑΠΟ ΠΟΥ προέρχεται το ποσό της στήλης.

    Η ΜΙΣΘΟΛΟΓΙΚΗ περίοδος της πηγής δεν είναι η γραμμή (= μήνας πληρωμής),
    γι' αυτό γράφεται ρητά."""
    if col == "salary":
        lines = []
        for kind, label in PAYMENT_KIND_LABELS.items():
            part = [p for p in group if p.payment_type == kind]
            if not part:
                continue
            for sy, sm in _source_periods(part):
                same = [p for p in part
                        if (p.period_year, p.period_month) == (sy, sm)]
                amount = money(round(sum(p.amount for p in same), 2))
                what = f"{label} {period_gr(sy, sm) if sm else sy}"
                who = "εργαζομένου" if len(same) == 1 else "εργαζομένων"
                lines.append(f"{what}: {amount} € — καθαρά {len(same)} {who}")
        return "\n".join(lines)
    if col in ("efka", "teka"):
        fund = "e-ΕΦΚΑ" if col == "efka" else "ΤΕΚΑ"
        lines = []
        for sy, sm in _source_periods(group):
            lines.append(
                f"Καταβλητέες εισφορές ΑΠΔ {fund} περιόδου {period_gr(sy, sm)}"
            )
            sub = [s for s in proj.apd_submissions
                   if s.fund == col and s.period_year == sy
                   and s.period_month == sm]
            if sub and sub[-1].reference:
                lines.append(f"Αρ. υποβολής: {sub[-1].reference}")
        lines.append("(εργαζομένου + εργοδότη, όλες οι καλύψεις του ταμείου)")
        return "\n".join(lines)
    lines = ["Παρακρατηθείς ΦΜΥ που αποδόθηκε στην ΑΑΔΕ, από:"]
    for sy, sm in _source_periods(group):
        lines += [f"• {s}" for s in _fmy_sources(sy, sm)] or [
            f"• Μισθοδοσία {period_gr(sy, sm)}"
        ]
    return "\n".join(lines)


def payments_by_period():
    """ΜΙΑ γραμμή ανά **μήνα ΚΑΤΑΒΟΛΗΣ**: Μισθοί / ΕΦΚΑ / ΤΕΚΑ / ΦΜΥ / Σύνολο.

    Η γραμμή είναι ο μήνας που ΠΡΟΒΛΕΠΕΤΑΙ να πληρωθεί το ποσό (προθεσμία της
    υποχρέωσης καταβολής — `_payment_period`), όχι η μισθολογική περίοδος:
    στη γραμμή του Μαΐου βρίσκονται οι μισθοί Μαΐου, οι εισφορές Απριλίου και
    ο ΦΜΥ Μαρτίου. Κάθε ποσό συνοδεύεται από `tip` που λέει ΑΠΟ ΠΟΥ
    προέρχεται — read-only, τίποτα δεν υπολογίζεται εδώ: αθροίζονται τα
    `PaymentMade` events."""
    periods = {}
    for p in proj.payments:
        col = PAYMENT_COLUMN_OF.get(p.payment_type)
        if col is None:
            continue
        periods.setdefault(_payment_period(p), {}).setdefault(col, []).append(p)

    rows = []
    for (y, m), cols in periods.items():
        cells = {}
        for col, _label in PAYMENT_COLUMNS:
            group = cols.get(col) or []
            amount = round(sum(p.amount for p in group), 2)
            cells[col] = {
                "amount": amount,
                "tip": _payment_tip(col, group) if group else "",
            }
        rows.append({
            "year": y,
            "month": m,
            "label": f"{m:02d}/{y}" if m else str(y),
            "cells": cells,
            "total": round(sum(c["amount"] for c in cells.values()), 2),
        })
    rows.sort(key=lambda r: (r["year"], r["month"]), reverse=True)
    totals = {
        col: round(sum(r["cells"][col]["amount"] for r in rows), 2)
        for col, _ in PAYMENT_COLUMNS
    }
    totals["total"] = round(sum(r["total"] for r in rows), 2)
    return rows, totals


def _efka_breakdown(emp, gross, efka_employee, on_date, efka_employer=None,
                    insurable=None, stored=None):
    """Ανάλυση των κρατήσεων ΑΝΑ ΤΑΜΕΙΟ/ΚΠΚ για την απόδειξη: ποσοστό ×
    ασφαλιστέες αποδοχές (μικτά έως το πλαφόν). Το υπόλοιπο στρογγυλοποίησης
    πάει στην πρώτη (κύρια ΕΦΚΑ) γραμμή, ώστε το άθροισμα να ισούται ΑΚΡΙΒΩΣ
    με τις συνολικές εισφορές του υπολογισμού. Όταν δοθεί `efka_employer`,
    κάθε γραμμή φέρει και το ποσό ΕΡΓΟΔΟΤΗ (ίδια λογική υπολοίπου).

    `insurable`: ΡΗΤΗ βάση εισφορών, για υπολογισμούς που ΔΕΝ εφαρμόζουν το
    μηνιαίο πλαφόν — Δώρα/Επίδομα Αδείας (`pe._gift_result`: εισφορές πάνω
    στο σύνολο). None = μικτά έως το πλαφόν, όπως η μηνιαία μισθοδοσία.

    `stored`: η ΚΑΤΑΓΕΓΡΑΜΜΕΝΗ ανάλυση του υπολογισμού (`analysis
    ["contributions"]`). Όταν υπάρχει, τα ποσά ΚΑΙ τα ποσοστά έρχονται από
    εκεί — αλλιώς μια μεταγενέστερη αλλαγή ΚΠΚ θα έδειχνε νέα ποσοστά με
    παλιά σύνολα, και η διαφορά θα κρυβόταν στη γραμμή του υπολοίπου."""
    if emp is None or emp.insurance_profile is None or (efka_employee or 0) <= 0:
        return []
    rows = svc.contribution_breakdown(emp.insurance_profile, on_date)
    if not rows:
        return []
    if stored:
        by_key = {(x["fund"], x["kpk"]): x for x in stored}
        rows = [
            dict(r, employee=by_key[k]["employee_rate"],
                 employer=by_key[k]["employer_rate"],
                 amount=by_key[k]["employee"], amount_er=by_key[k]["employer"])
            if (k := (r["fund"], r["kpk"])) in by_key else r
            for r in rows
        ]
        if all("amount" in r for r in rows):
            return [
                {"fund": r["fund"], "label": r["label"], "kpk": r["kpk"],
                 "employee": r["employee"], "employer": r["employer"],
                 "components": r.get("components", []),
                 "insurable": round(insurable if insurable is not None
                                    else min(gross or 0.0,
                                             config.efka_rates_for(on_date).monthly_cap), 2),
                 "amount": r["amount"], "amount_er": r["amount_er"]}
                for r in rows
            ]
    cap = config.efka_rates_for(on_date).monthly_cap
    if insurable is None:
        insurable = min(gross or 0.0, cap)
    out = [
        {"fund": r["fund"], "label": r["label"], "kpk": r["kpk"],
         "employee": r["employee"], "employer": r["employer"],
         "components": r.get("components", []),
         "insurable": round(insurable, 2),
         "amount": round(insurable * r["employee"], 2),
         "amount_er": round(insurable * r["employer"], 2)}
        for r in rows
    ]
    for key, total in (("amount", efka_employee), ("amount_er", efka_employer)):
        if total is None:
            continue
        diff = round((total or 0.0) - sum(o[key] for o in out), 2)
        if abs(diff) >= 0.01:
            out[0][key] = round(out[0][key] + diff, 2)
    return out


def _earnings_split(payroll, emp, employee_total, employer_total):
    """Οι αμοιβές της μισθοδοσίας όπως ΘΑ ΔΗΛΩΘΟΥΝ στην ΑΠΔ: τακτικές (001)
    και **αποδοχές ασθενείας (008)** — μία ανά ασθένεια, με τις ημέρες και το
    διάστημά της. Κάθε γραμμή παίρνει το μερίδιό της στις εισφορές, αναλογικά
    προς τις αποδοχές (το υπόλοιπο στρογγυλοποίησης στις τακτικές, ώστε τα
    αθροίσματα να ισούνται ΑΚΡΙΒΩΣ με τις εισφορές του υπολογισμού).

    Επιστρέφει [] όταν δεν υπάρχει τίποτα να αναλυθεί (μόνο τακτικές) — τότε
    η ενότητα «Ανάλυση εισφορών» μένει όπως ήταν. Πηγή: `svc.payroll_earnings_split`
    (ΙΔΙΑ με το αρχείο ΑΠΔ)."""
    lines = svc.payroll_earnings_split(payroll)
    if len(lines) < 2:
        return []
    total_gross = sum(ln.gross for ln in lines) or 1.0
    # Διάστημα των τακτικών (001): ο ΠΛΗΡΗΣ μήνας απασχόλησης — ίδιος κανόνας
    # με τη `build_apd_rows` (η ίδια η split δεν τον κουβαλά, τον υπολογίζει
    # το αρχείο από πρόσληψη/αποχώρηση).
    y, m = payroll.period_year, payroll.period_month
    first = date(y, m, 1)
    last = date(y, m, monthrange(y, m)[1])
    hire = getattr(emp, "hire_date", None)
    term = getattr(emp, "termination_date", None)
    span = (max(hire, first) if hire else first, min(term or last, last))
    out = []
    for ln in lines:
        share = ln.gross / total_gross
        out.append({
            "et": ln.earnings_type,
            "label": ET_LABELS.get(ln.earnings_type, ln.earnings_type),
            "gross": ln.gross,
            "days": ln.days,
            "date_from": ln.date_from or span[0],
            "date_to": ln.date_to or span[1],
            "employee": round((employee_total or 0.0) * share, 2),
            "employer": round((employer_total or 0.0) * share, 2),
        })
    for key, total in (("employee", employee_total), ("employer", employer_total)):
        diff = round((total or 0.0) - sum(o[key] for o in out), 2)
        if abs(diff) >= 0.01:
            out[0][key] = round(out[0][key] + diff, 2)
    return out


def _tax_breakdown(emp, taxable, fmy, on_date, periods=config.ANNUAL_SALARIES):
    """Πώς προκύπτει η παρακράτηση ΦΜΥ, βήμα-βήμα: φορολογητέο × `periods` =
    ετήσιο εισόδημα → κλίμακα κλιμάκιο-κλιμάκιο (με τους συντελεστές που
    ισχύουν για τα ΤΕΚΝΑ/ΗΛΙΚΙΑ του εργαζομένου) → μείωση φόρου → ετήσιος
    φόρος / `periods` = φόρος περιόδου.

    Η ΛΟΓΙΚΗ δεν ξαναγράφεται εδώ: τα βήματα τα δίνει το ΙΔΙΟ καθεστώς που
    έκανε τον υπολογισμό (`config.TaxScale.annual_tax_detail`)."""
    if taxable is None:
        return None
    children = getattr(emp, "children", 0) or 0
    birth = getattr(emp, "birth_date", None)
    scale = config.tax_scale_for(on_date)
    d = scale.annual_tax_detail(taxable * periods, children, birth, on_date)
    d.update({
        "taxable": round(taxable, 2),
        "periods": periods,
        "birth_date": birth,
        "fmy": round(fmy if fmy is not None else d["total"] / periods, 2),
        "annual_tax": round(d["total"], 2),
    })
    return d


def contribution_split(emp, gross, employee_total, employer_total, on_date) -> dict:
    """Κατανομή των εισφορών **ΑΝΑ ΦΟΡΕΑ** (`config.fund_carrier`): `efka`,
    `teka`, `other` → {"employee", "employer"}.

    Ίδια λογική με το `_efka_breakdown` (ποσοστό ΚΠΚ × ασφαλιστέες αποδοχές,
    το υπόλοιπο στρογγυλοποίησης στην κύρια κάλυψη), αλλά **και για τις δύο
    πλευρές** και ομαδοποιημένη κατά φορέα — data-driven, χωρίς έλεγχο σε
    όνομα ταμείου: ό,τι πάει στο ΤΕΚΑ μετριέται ΤΕΚΑ, ό,τι στον e-ΕΦΚΑ ΕΦΚΑ.
    """
    out = {c: {"employee": 0.0, "employer": 0.0} for c in ("efka", "teka", "other")}
    rows = (
        svc.contribution_breakdown(emp.insurance_profile, on_date)
        if emp is not None and emp.insurance_profile is not None
        else []
    )
    if not rows:
        raise ValueError("Λείπει ασφαλιστικό προφίλ ή ανάλυση ΚΠΚ")
    insurable = min(gross or 0.0, config.efka_rates_for(on_date).monthly_cap)
    first = config.fund_carrier(rows[0]["fund"])
    for r in rows:
        carrier = config.fund_carrier(r["fund"])
        cell = out.setdefault(carrier, {"employee": 0.0, "employer": 0.0})
        cell["employee"] = round(cell["employee"] + insurable * r["employee"], 2)
        cell["employer"] = round(cell["employer"] + insurable * r["employer"], 2)
    # Το υπόλοιπο στρογγυλοποίησης στην ΚΥΡΙΑ κάλυψη, ώστε τα αθροίσματα να
    # ισούνται ΑΚΡΙΒΩΣ με τις εισφορές του υπολογισμού.
    for side, total in (("employee", employee_total), ("employer", employer_total)):
        diff = round((total or 0.0) - sum(c[side] for c in out.values()), 2)
        if abs(diff) >= 0.01:
            out[first][side] = round(out[first][side] + diff, 2)
    return out


def _base_breakdown(emp, a: dict, on_date):
    """Πώς προκύπτει η ΒΑΣΗ των αποδοχών και το ωρομίσθιο, με τους αριθμούς
    του συγκεκριμένου εργαζομένου (τύπος → πράξη → αποτέλεσμα). Καθαρά
    παράγωγο των ίδιων κανόνων (`pe.monthly_insurance_days`,
    `work_hours.hourly_rate`) — καμία επανάληψη λογικής υπολογισμού."""
    weekly = getattr(emp, "weekly_hours", None) or 40.0
    d = {
        "weekly_hours": weekly,
        "full_time": weekly >= 40,
        "efka_days": a.get("efka_days"),
        "unpaid_days": a.get("unpaid_days") or 0,
        "base_gross": a.get("base_gross"),
    }
    if a.get("pay_type") == "daily":
        wage = a.get("daily_wage") or 0.0
        d.update({
            "kind": "daily",
            "daily_wage": wage,
            "daily_rate": wage,
            "base_formula": (
                f"{money(wage)} € ημερομίσθιο × {a.get('efka_days')} ημ. ασφάλισης "
                f"= {money(a.get('base_gross') or 0)} €"
            ),
            "hourly_formula": f"{money(wage)} € × 6 / 40",
        })
    else:
        gross = a.get("monthly_gross") or 0.0
        base_days = a.get("base_days") or pe.monthly_insurance_days(weekly)
        daily = round(gross / base_days, 2) if base_days else 0.0
        d.update({
            "kind": "monthly",
            "monthly_gross": gross,
            "base_days": base_days,
            "daily_rate": daily,
            "daily_formula": f"{money(gross)} € / {base_days} ημ. = {money(daily)} €",
            "unpaid_amount": round((a.get("unpaid_days") or 0) * daily, 2),
            "base_formula": (
                f"μηνιαίος μισθός {money(gross)} € − {a.get('unpaid_days')} ημ. "
                f"άνευ αποδοχών × {money(daily)} € "
                f"= {money(a.get('base_gross') or 0)} €"
                if a.get("unpaid_days")
                else f"μηνιαίος μισθός {money(gross)} € (καμία ημέρα άνευ αποδοχών)"
            ),
            "hourly_formula": f"{money(gross)} € / 25 × 6 / {'%g' % weekly} ώρες",
        })
    return d


def _gift_base_breakdown(kind: str, a: dict, emp, year: int) -> dict:
    """Πώς προκύπτει η ΒΑΣΗ του Δώρου / Επιδόματος Αδείας, με τους αριθμούς
    του εργαζομένου: περίοδος αναφοράς, ημέρες/μήνες υπηρεσίας, κανόνας
    (πλήρης ή αναλογικός) και ο συντελεστής αδείας 0,04166 των Δώρων.

    Οι κανόνες είναι ΑΚΡΙΒΩΣ αυτοί του `payroll_engine` (`calc_*_gift*`,
    `calc_leave_allowance*`) — εδώ απλώς περιγράφονται με τα ποσά τους."""
    daily = a.get("pay_type") == "daily"
    wage = a.get("wage") or 0.0
    base, gross = a.get("base") or 0.0, a.get("gross") or 0.0
    d = {
        "kind": kind,
        "pay_type": a.get("pay_type"),
        "wage": wage,
        "wage_label": "Ημερομίσθιο" if daily else "Μηνιαίος μισθός",
        # Πότε αποτιμήθηκε η αμοιβή / πότε καταβάλλεται (Δώρα)
        "wage_date": a.get("wage_date"),
        "pay_date": a.get("pay_date"),
        "base": base,
        "leave_coeff": a.get("leave_coeff") or 0.0,
        "gross": gross,
    }
    if kind in ("easter", "christmas"):
        days, full_days = a.get("days"), a.get("full_days")
        start, end = a.get("period", (None, None))
        full = days is not None and full_days is not None and days >= full_days
        # Οι «κανόνες» γράφονται ΩΣ ΠΑΡΑΣΤΑΣΕΙΣ (χωρίς «=»): το «= βάση €» το
        # προσθέτει το template, ώστε να μη διπλογράφεται το αποτέλεσμα.
        if daily:
            units = 15 if kind == "easter" else 25
            rule_full = f"{units} ημερομίσθια × {money(wage)} €"
            rule_part = (
                f"1 ημερομίσθιο ανά 8 ημερολογιακές ημέρες: "
                f"{days or 0} / 8 → {'%.2f' % ((days or 0) / 8.0)} ημερομίσθια "
                f"(έως {units}) × {money(wage)} €"
            )
        elif kind == "easter":
            rule_full = f"μισός μισθός: {money(wage)} € / 2"
            rule_part = (
                f"1/15 του μισού μισθού ανά 8 ημέρες: {days or 0} / 8 × 1/15 × "
                f"{money(wage / 2)} € (μισός μισθός)"
            )
        else:
            rule_full = f"ένας ολόκληρος μισθός: {money(wage)} €"
            rule_part = (
                f"2/25 του μισθού ανά 19 ημέρες: {days or 0} / 19 × 2/25 × "
                f"{money(wage)} €"
            )
        d.update({
            "period_from": start, "period_to": end,
            "days": days, "full_days": full_days, "full": full,
            "rule": rule_full if full else rule_part,
            "rule_label": ("πλήρης περίοδος αναφοράς" if full
                           else "αναλογικά, μερική απασχόληση στην περίοδο"),
        })
    else:  # allowance
        hire = getattr(emp, "hire_date", None)
        full_year = bool(hire and pe.is_third_service_year(year, hire))
        months = None
        if hire is not None:
            months = pe._allowance_months(
                year, hire, getattr(emp, "termination_date", None)
            )
        cap = f"{money(wage)} €" if daily else f"{money(wage / 2)} € (μισός μισθός)"
        if full_year:
            rule = ("3ο έτος υπηρεσίας+: ολόκληρο το επίδομα — "
                    + (f"13 ημερομίσθια × {money(wage)} €" if daily
                       else f"μισός μισθός: {money(wage)} € / 2"))
        else:
            rule = (
                f"1ο/2ο έτος: 2 ημερομίσθια ανά μήνα υπηρεσίας × {months or 0} μήνες "
                f"→ {2 * (months or 0)} ημερομίσθια × "
                + (f"{money(wage)} € (έως 13 ημερομίσθια)" if daily
                   else f"{money(wage / 25)} € (μισθός/25), "
                        f"με ανώτατο τον μισό μισθό")
            )
        d.update({
            "months": months, "full_year": full_year,
            "cap": cap, "rule": rule,
            "rule_label": ("πλήρες δικαίωμα (3ο έτος υπηρεσίας+)" if full_year
                           else "αναλογικά έως τη λήψη της άδειας/την αποχώρηση"),
        })
    return d


def payroll_analysis(y: int, m: int):
    """Πλήρης ανάλυση μισθοδοσίας ΑΝΑ εργαζόμενο για την περίοδο — η «απόδειξη
    αποδοχών». Πηγή: το πεδίο `analysis` του MonthlyPayrollCalculated (ό,τι
    κράτησε ο υπολογισμός: βάση, προσαυξήσεις ανά είδος, ασθένεια, κρατήσεις).

    Επιστρέφει λίστα από dicts {name, pay_type, a} ταξινομημένη κατά όνομα —
    κενή αν η περίοδος δεν έχει υπολογιστεί ακόμη."""
    out = []
    for p in proj.payrolls:
        if p.period_year != y or p.period_month != m:
            continue
        a = dict(p.analysis or {})
        if not a:
            raise ValueError(
                f"Λείπει η ανάλυση μισθοδοσίας για {p.employee_id} ({m}/{y})"
            )
        # Εμπλουτισμός των γραμμών προσαυξήσεων με την ΑΝΑΛΥΣΗ του συντελεστή
        # (κλάση % · νύχτα 25% · αργία 75% · ωρομίσθιο κλάσης) — για να δείχνει
        # η απόδειξη πώς προέκυψε κάθε ποσό, όχι μόνο το τελικό mult.
        a["premium_detail"] = _premium_breakdown(a, y, m)
        on = last_working_day_of_month(y, m)
        emp = proj.employments.get(p.employee_id)
        # Βάση εισφορών = μικτά ΧΩΡΙΣ τις απαλλασσόμενες προσαυξήσεις
        contrib_gross = round(
            (a.get("gross") or 0.0) - (a.get("premium_exempt") or 0.0), 2)
        a["efka_detail"] = _efka_breakdown(
            emp, contrib_gross, a.get("efka_employee"), on,
            efka_employer=a.get("efka_employer"),
            stored=a.get("contributions"),
        )
        a["base_detail"] = _base_breakdown(emp, a, on)
        a["earnings_split"] = _earnings_split(
            p, emp, a.get("efka_employee"), a.get("efka_employer")
        )
        a["tax_detail"] = _tax_breakdown(emp, a.get("taxable"), a.get("fmy"), on)
        a["insurable"] = round(
            min(contrib_gross, config.efka_rates_for(on).monthly_cap), 2
        )
        a["efka_cap"] = config.efka_rates_for(on).monthly_cap
        out.append(
            {
                "eid": p.employee_id,
                "name": emp_name(p.employee_id),
                "pay_type": a.get("pay_type", "monthly"),
                "a": a,
            }
        )
    return sorted(out, key=lambda r: r["name"])


def _premium_breakdown(a: dict, y: int, m: int) -> list:
    """Αναλύει τις προσαυξήσεις, ΟΜΑΔΟΠΟΙΗΜΕΝΕΣ ανά κλάση ωρομισθίου.

    Επιστρέφει λίστα ομάδων (μία ανά κλάση: πρόσθετη/υπερεργασία/…), κάθε μία με
    το ωρομίσθιο κλάσης (×(1+κλάση%)) και ΑΝΑΛΥΤΙΚΕΣ γραμμές ανά παραλλαγή
    (ημέρα/νύχτα/αργία). Έτσι η «Υπερεργασία» εμφανίζεται ΜΙΑ φορά με τις
    υπο-γραμμές της, όχι επαναλαμβανόμενη. Οι «εντός ωραρίου» ώρες (`normal`)
    καλύπτονται ήδη από τον μισθό (`covered=True`) — πληρώνεται μόνο η
    προσαύξηση νύχτας/αργίας."""
    lines = a.get("premium_lines") or {}
    if not lines:
        return []
    hourly = a.get("hourly_rate") or 0.0
    rules = config.work_hour_rules_for(date(y, m, 1))
    night_pct = round(rules.night_premium * 100)
    holiday_pct = round(rules.holiday_premium * 100)
    groups: dict[str, dict] = {}
    for f, ln in lines.items():
        cls = ln["cls"]
        night = ln.get("night", False)
        holiday = ln.get("holiday", False)
        g = groups.get(cls)
        if g is None:
            crate = work_hours.class_rate(cls, hourly, rules)
            g = groups[cls] = {
                "cls": cls,
                "class_label": ln["class_label"],
                "hourly": hourly,
                "class_rate": crate,  # ωρομίσθιο κλάσης (×(1+κλάση%))
                "class_pct": round((crate / hourly - 1.0) * 100) if hourly else 0,
                "covered": cls == "normal",  # καλύπτεται από τον μισθό
                "rows": [],
                "total": 0.0,
            }
        g["rows"].append(
            {
                "night": night,
                "holiday": holiday,
                "night_pct": night_pct if night else 0,
                "holiday_pct": holiday_pct if holiday else 0,
                "hours": ln["hours"],
                "mult": ln["mult"],
                "amount": ln["amount"],
            }
        )
        g["total"] = round(g["total"] + ln["amount"], 2)
    # Ταξινόμηση ομάδων με τη «φυσική» σειρά κλάσεων, γραμμών κατά ποσό.
    order = {c: i for i, c in enumerate(work_hours.RATE_CLASSES)}
    out = sorted(groups.values(), key=lambda g: order.get(g["cls"], 99))
    for g in out:
        g["rows"].sort(key=lambda r: r["amount"], reverse=True)
    return out


def settlement_rows(kind: str, year: int | None = None,
                    month: int | None = None):
    """Οι υπολογισμένες ΕΚΚΑΘΑΡΙΣΕΙΣ ΑΠΟΧΩΡΗΣΗΣ ανά είδος, νεότερη πρώτη:

    - `severance` → **Αποζημίωση απόλυσης** (`SeveranceCalculated`)
    - `leave`     → **Αποζημίωση μη ληφθείσας αδείας**
      (`LeaveCompensationCalculated`)

    Και οι δύο υπολογίζονται ΑΥΤΟΜΑΤΑ στην αποχώρηση (§2) — εδώ απλώς
    προβάλλονται συγκεντρωτικά, με το ΑΦΜ/όνομα του εργαζόμενου και τη
    διαδρομή της απόδειξής του. Read-only, καμία παρενέργεια. Με `year` και
    `month` μόνο όσες έχουν λήξη μέσα σε εκείνον τον μήνα (γραμμή καθολικού)."""
    events = proj.severances if kind == "severance" else proj.leave_compensations
    rows = []
    for ev in sorted(events, key=lambda x: x.termination_date, reverse=True):
        if year and (ev.termination_date.year,
                     ev.termination_date.month) != (year, month):
            continue
        emp = proj.employments.get(ev.employee_id)
        if kind == "severance":
            note = (
                f"{ev.months:g} μηνιαίες αποδοχές "
                f"{'με' if ev.with_notice else 'χωρίς'} προειδοποίηση × "
                f"{money(ev.monthly_salary)}€ × 7/6 · υπηρεσία "
                f"{ev.service_years:g} έτη"
            )
        else:
            note = (
                f"{ev.leave_days:g} ημέρες υπολοίπου → {ev.comp_days:g} ημέρες "
                f"αποζημίωσης (×1,2 αναγωγή πενθημέρου)"
            )
        rows.append({
            "eid": ev.employee_id,
            "name": emp.full_name if emp is not None else ev.employee_id,
            "date": ev.termination_date,
            "note": note,
            "gross": ev.gross,
            "fmy": ev.fmy,
            "net": ev.net,
        })
    return rows


def settlement_totals(rows):
    """Σύνολα των γραμμών εκκαθάρισης (μικτά/φόρος/καθαρά)."""
    return {
        f: round(sum(r[f] for r in rows), 2) for f in ("gross", "fmy", "net")
    }


def monthly_totals(year: int | None = None):
    """Σύνολα ανά περίοδο — για το γράφημα και τη σελίδα μισθοδοσίας.

    Με `year` περιορίζεται στις περιόδους ΕΝΟΣ έτους: η σελίδα μισθοδοσίας
    δείχνει το έτος που έχει επιλεγεί, αλλιώς τα αθροίσματα της οθόνης δεν
    συγκρίνονται με καμία ετήσια αναφορά."""
    monthly = {}
    for p in proj.payrolls:
        if year is not None and p.period_year != year:
            continue
        k = (p.period_year, p.period_month)
        m = monthly.setdefault(
            k, {"gross": 0, "contrib": 0, "fmy": 0, "net": 0, "cost": 0}
        )
        m["gross"] += p.gross
        m["contrib"] += p.efka_employee + p.efka_employer
        m["fmy"] += p.fmy
        m["net"] += p.net
        # ΣΥΝΟΛΙΚΟ ΚΟΣΤΟΣ ΕΡΓΟΔΟΤΗ = μικτά + εισφορές ΕΡΓΟΔΟΤΗ (ίδιος ορισμός
        # με τις εκτυπώσεις, `pdf_reports`) — τι πραγματικά πληρώνει η εταιρεία
        m["cost"] += p.gross + p.efka_employer
    return {k: {kk: round(vv, 2) for kk, vv in v.items()}
            for k, v in sorted(monthly.items())}


# ------------------------------------------ Καθολικό καταβολών της χρονιάς
# Τα ΕΙΔΗ καταβολής που εμφανίζονται στα «Σύνολα ανά περίοδο», με τη σειρά
# που διαβάζονται μέσα στον ίδιο μήνα: πρώτα η τακτική μισθοδοσία, μετά τα
# έκτακτα. ΜΙΑ πηγή για την ετικέτα και τη σειρά.
# Τα ποσά κάθε γραμμής — ΜΙΑ πηγή για τη στρογγυλοποίηση και τα σύνολα.
LEDGER_AMOUNTS = ("gross", "efka_employee", "fmy", "net", "cost")

LEDGER_KINDS = (
    ("payroll", "Περιόδου"),
    ("easter", "Δώρο Πάσχα"),
    ("christmas", "Δώρο Χριστ."),
    ("allowance", "Επίδομα Αδείας"),
    ("severance", "Αποζημίωση Απόλυσης"),
    ("leave", "Αποζημίωση Αδείας"),
)


def payroll_ledger(year: int, eids=None):
    """ΟΛΕΣ οι υπολογισμένες καταβολές ΕΝΟΣ έτους — μία γραμμή ανά (μήνα,
    είδος): τακτική μισθοδοσία, Δώρα, Επίδομα Αδείας, αποζημιώσεις
    αποχώρησης. Νεότερος μήνας πρώτος.

    Είναι το ΙΔΙΟ σύνολο που μετράει η ετήσια αναφορά της Επισκόπησης, από
    την πλευρά της καταβολής: όποιο είδος λείπει από εδώ, λείπει και από τη
    συμφωνία των δύο οθονών (γι' αυτό μπαίνουν ΚΑΙ οι αποζημιώσεις, που δεν
    έχουν «περίοδο» παρά τον μήνα της αποχώρησης).

    Δύο διαφορετικά μεγέθη, το καθένα στη στήλη του: οι **εισφορές
    εργαζομένου** είναι κράτηση πάνω στα μικτά (μαζί με τον ΦΜΥ βγάζουν τα
    καθαρά), ενώ το **κόστος** είναι μικτά + εισφορές ΕΡΓΟΔΟΤΗ — τι πληρώνει
    η εταιρεία (ίδιος ορισμός με `monthly_totals` και την Επισκόπηση). Τα
    Δώρα/Επίδομα κρατούν στο event μόνο την κράτηση του εργαζόμενου, άρα το
    εργοδοτικό σκέλος ανασυντίθεται από το `_gift_cost` (ΜΙΑ πηγή με την
    ετήσια αναφορά). Οι αποζημιώσεις δεν φέρουν καμία ασφαλιστική κράτηση,
    άρα κόστος = μικτά.

    Με `eids` μόνο οι καταβολές αυτών των απασχολήσεων (καρτέλα αποδοχών
    εργαζομένου: όλες οι προσλήψεις του)."""
    labels = dict(LEDGER_KINDS)
    order = {k: i for i, (k, _l) in enumerate(LEDGER_KINDS)}
    rows = {}

    def cell(month: int, kind: str):
        r = rows.get((month, kind))
        if r is None:
            r = rows[(month, kind)] = {
                "month": month, "kind": kind, "label": labels[kind],
                "gross": 0.0, "efka_employee": 0.0, "fmy": 0.0,
                "net": 0.0, "cost": 0.0,
            }
        return r

    def put(r, gross, efka_employee, fmy, net, cost):
        r["gross"] += gross
        r["efka_employee"] += efka_employee
        r["fmy"] += fmy
        r["net"] += net
        r["cost"] += cost

    def mine(ev):
        return eids is None or ev.employee_id in eids

    for p in proj.payrolls:
        if p.period_year == year and mine(p):
            put(cell(p.period_month, "payroll"), p.gross, p.efka_employee,
                p.fmy, p.net, p.gross + p.efka_employer)

    for g in proj.gifts:
        if g.year == year and mine(g):
            put(cell(svc.gift_fmy_period(g)[1], g.gift_type), g.gross,
                g.efka_employee, g.fmy, g.net, _gift_cost(g))

    for a in proj.allowances:
        if a.year == year and mine(a):
            month = _allowance_month(proj.employments.get(a.employee_id), year)
            put(cell(month, "allowance"), a.gross, a.efka_employee, a.fmy,
                a.net, _gift_cost(a))

    for kind, events in (("severance", proj.severances),
                         ("leave", proj.leave_compensations)):
        for ev in events:
            if ev.termination_date.year == year and mine(ev):
                put(cell(ev.termination_date.month, kind), ev.gross, 0.0,
                    ev.fmy, ev.net, ev.gross)

    out = sorted(rows.values(), key=lambda r: (-r["month"], order[r["kind"]]))
    for r in out:
        for k in LEDGER_AMOUNTS:
            r[k] = round(r[k], 2)
    return out


def ledger_totals(rows):
    """Σύνολα έτους του καθολικού καταβολών."""
    return {k: round(sum(r[k] for r in rows), 2) for k in LEDGER_AMOUNTS}


# PaymentMade.payment_type -> είδος γραμμής του καθολικού. Τα Δώρα πληρώνονται
# με μήνα 0 (ένα ανά έτος)· οι αποζημιώσεις με τους μισθούς (μήνας λήξης).
PAYMENT_KINDS = {"salary": "payroll", "easter_gift": "easter",
                 "christmas_gift": "christmas", "leave_allowance": "allowance",
                 "leave_compensation": "leave", "severance": "severance"}


def employee_pay_card(person_id: str):
    """Καρτέλα αποδοχών ΕΡΓΑΖΟΜΕΝΟΥ (προσώπου — ΟΛΕΣ οι προσλήψεις του, §2):
    ανά έτος (νεότερο πρώτο) οι γραμμές του καθολικού (`payroll_ledger`) με
    το ποσό που ΠΛΗΡΩΘΗΚΕ (`PaymentMade`, δικαιούχος η απασχόληση) δίπλα
    στα καθαρά.

    Οι αποζημιώσεις αποχώρησης πληρώνονται μαζί με τους μισθούς
    (`pay_exit_compensations`).

    ΠΟΛΛΑΠΛΕΣ ΠΡΟΣΛΗΨΕΙΣ: αριθμούνται #1, #2… κατά ημερομηνία πρόσληψης και
    το καθολικό βγαίνει ΑΝΑ πρόσληψη — μια λήξη και επαναπρόσληψη μέσα στον
    ίδιο μήνα δίνει δύο γραμμές, όχι ένα άθροισμα. Επιστρέφει
    {"hires": [...σύνοψη ανά πρόσληψη], "years": [...ανά έτος]}."""
    person = proj.persons.get(person_id)
    emps = sorted((proj.employments[eid] for eid in
                   (person.employment_ids if person is not None else ())
                   if eid in proj.employments),
                  key=lambda e: e.hire_date)
    hires = [{"n": i, "eid": e.employment_id, "hire_date": e.hire_date,
              "termination_date": e.termination_date,
              "gross": 0.0, "net": 0.0, "paid": 0.0}
             for i, e in enumerate(emps, 1)]
    by_eid = {h["eid"]: h for h in hires}
    paid = {}
    for pm in proj.payments:
        kind = PAYMENT_KINDS.get(pm.payment_type)
        if kind and pm.beneficiary in by_eid:
            k = (pm.beneficiary, kind, pm.period_year, pm.period_month)
            paid[k] = paid.get(k, 0) + pm.amount
    years = {p.period_year for p in proj.payrolls if p.employee_id in by_eid}
    years |= {g.year for g in proj.gifts if g.employee_id in by_eid}
    years |= {a.year for a in proj.allowances if a.employee_id in by_eid}
    years |= {ev.termination_date.year
              for ev in list(proj.severances) + list(proj.leave_compensations)
              if ev.employee_id in by_eid}
    order = {k: i for i, (k, _l) in enumerate(LEDGER_KINDS)}
    out = []
    for y in sorted(years, reverse=True):
        rows = []
        for h in hires:
            for r in payroll_ledger(y, {h["eid"]}):
                r["hire"] = h
                gift = r["kind"] in ("easter", "christmas")
                r["paid"] = round(paid.get(
                    (h["eid"], r["kind"], y, 0 if gift else r["month"]), 0), 2)
                h["gross"] += r["gross"]
                h["net"] += r["net"]
                h["paid"] += r["paid"]
                rows.append(r)
        rows.sort(key=lambda r: (-r["month"], order[r["kind"]], r["hire"]["n"]))
        tot = ledger_totals(rows)
        tot["paid"] = round(sum(r["paid"] for r in rows), 2)
        out.append({"year": y, "rows": rows, "totals": tot})
    for h in hires:
        for k in ("gross", "net", "paid"):
            h[k] = round(h[k], 2)
    return {"hires": hires, "years": out}


# ------------------------------------------- Ετήσια αναφορά κόστους (γράφημα)
# Οι ΤΕΣΣΕΡΙΣ συνιστώσες του κόστους εργοδότη μέσα στο έτος — ΜΙΑ πηγή
# αλήθειας για το γράφημα, το legend και τον πίνακα της «Μηνιαίας εξέλιξης».
# Το χρώμα ζει δίπλα στην ετικέτα (ελεγμένη categorical παλέτα: μπλε /
# πορτοκαλί / aqua / κίτρινο — γειτονικά ζεύγη με CVD ΔΕ ≥ 8).
# ΤΡΕΙΣ ΟΠΤΙΚΕΣ του ίδιου ποσού — ο χρήστης διαλέγει ποια διαβάζει:
# «τι πληρώνει η εταιρεία» / «τι συμφωνήθηκε» / «τι πιάνει στο χέρι».
COST_MEASURES = (
    ("cost", "Συνολικό κόστος", "μικτά + εισφορές εργοδότη"),
    ("gross", "Μικτά", "οι μικτές αποδοχές, πριν κρατήσεις"),
    ("net", "Καθαρά", "τα πληρωτέα του εργαζόμενου, μετά εισφορές και ΦΜΥ"),
)

COST_COMPONENTS = (
    ("payroll", "Τακτική μισθοδοσία", "#2A78D6"),
    ("gifts", "Δώρα εορτών", "#EB6834"),
    ("allowance", "Επίδομα αδείας", "#1BAF7A"),
    ("severance", "Αποζημιώσεις αποχώρησης", "#EDA100"),
)


def _rates_on(emp, on):
    """Συντελεστές κρατήσεων της απασχόλησης — με ασφαλή υποχώρηση στους
    καθολικούς, ώστε μια απασχόληση ΧΩΡΙΣ ασφαλιστικό προφίλ να μη ρίχνει την
    επισκόπηση (η αναφορά είναι εκτίμηση, όχι υπολογισμός που γράφει event)."""
    try:
        return svc.contribution_rates(emp.insurance_profile, on)
    except ValueError:
        return config.efka_rates_for(on)


def _employed_in(emp, first: date, last: date) -> bool:
    """Υπήρχε η απασχόληση έστω μία ημέρα μέσα στο διάστημα;"""
    if not emp.hire_submitted or emp.hire_date is None or emp.hire_date > last:
        return False
    return emp.termination_date is None or emp.termination_date >= first


def _forecast_payroll(emp, y: int, m: int) -> tuple[float, float, float]:
    """ΠΡΟΒΛΕΠΟΜΕΝΟΣ μήνας μιας απασχόλησης, στις τρεις οπτικές της αναφοράς:
    (κόστος εργοδότη, μικτά, καθαρά εργαζόμενου) — από τον ΙΔΙΟ υπολογισμό,
    όταν η μισθοδοσία ΔΕΝ έχει ακόμη υπολογιστεί.

    Εκτίμηση, όχι υπολογισμός: παίρνει τη ΣΥΜΒΑΣΗ που ισχύει στο τέλος του
    μήνα και υποθέτει **πλήρη κανονικό μήνα** — καμία άδεια άνευ αποδοχών,
    καμία ασθένεια, καμία προσαύξηση ωρών (γεννιούνται από παρουσίες που δεν
    έχουν συμβεί ακόμη). Ο μερικός μήνας πρόσληψης/αποχώρησης επιμερίζεται
    αναλογικά στις ημερολογιακές ημέρες. Ίδιος `payroll_engine` με τη
    μισθοδοσία — ΜΙΑ πηγή για το πλαφόν και τα ποσοστά."""
    first, last = date(y, m, 1), date(y, m, monthrange(y, m)[1])
    end = last_working_day_of_month(y, m)
    terms = emp.contract_on(end)
    rates = _rates_on(emp, end)
    base_days = pe.monthly_insurance_days(
        terms.weekly_hours,
        len(emp.scheduled_days_in_month(y, m, whole_month=True)),
    )
    covered = pe._employment_overlap_days(
        emp.hire_date, emp.termination_date, first, last
    )
    days = round(base_days * covered / ((last - first).days + 1))
    if terms.pay_type == "daily":
        if days == 0:
            return 0.0, 0.0, 0.0
        r = pe.calc_monthly_daily(terms.wage, days, emp.children_on(end), end,
                                  rates=rates)
    else:
        r = pe.calc_monthly(
            terms.wage, emp.children_on(end), end, unpaid_days=base_days - days,
            rates=rates, base_days=base_days,
        )
    return round(r.gross + r.efka_employer, 2), round(r.gross, 2), round(r.net, 2)


def _gift_cost(g) -> float:
    """Κόστος εργοδότη ενός ΥΠΟΛΟΓΙΣΜΕΝΟΥ Δώρου/Επιδόματος: οι εισφορές
    ΕΡΓΟΔΟΤΗ του event (ήδη μειωμένες κατά την επιδότηση 107)· παλιά events
    χωρίς αυτές → ανασύνθεση από τα ποσοστά της ημέρας καταβολής."""
    kind = getattr(g, "gift_type", "allowance")
    on = (date(g.year, 7, 31) if kind == "allowance"
          else pe.gift_payment_date(kind, g.year))
    emp = proj.employments.get(g.employee_id)
    er = g.efka_employer or (
        round(g.gross * _rates_on(emp, on).employer, 2) if emp else 0.0)
    return round(g.gross + er, 2)


def _allowance_month(emp, year: int) -> int:
    """Ο μήνας στον οποίο χρεώνεται το Επίδομα Αδείας: αυτός της πρώτης άδειας
    (ή της αποχώρησης) — αλλιώς **Δεκέμβριος**, το αργότερο μέχρι πότε πρέπει
    να καταβληθεί μέσα στο έτος (ίδια παραδοχή με την πρόβλεψη, που τότε
    μετρά υπηρεσία ΟΛΟΚΛΗΡΟΥ του έτους)."""
    m = emp.allowance_period_month(year) if emp is not None else None
    return m or 12


# ------------------------------------- Πραγματικό κόστος της εργατοώρας
def _contract_daily_hours(emp, terms) -> float:
    """Συμβατικές ώρες ΑΝΑ ΗΜΕΡΑ ΕΡΓΑΣΙΑΣ: εβδομαδιαίες / σύστημα εργασίας
    (πενθήμερο ή εξαήμερο — όρος της σύμβασης). 40 ώρες σε πενθήμερο -> 8·
    40 σε εξαήμερο -> 6,67."""
    return (terms.weekly_hours or 40.0) / (emp.weekly_system or 5)


def _holidays_off(emp, y: int, m: int, on_leave: set) -> int:
    """Πόσες ΠΡΟΓΡΑΜΜΑΤΙΣΜΕΝΕΣ ημέρες του μήνα είναι **αργίες που δεν
    δουλεύτηκαν**: πληρώνονται κανονικά, αλλά ώρα εργασίας δεν παράγουν.

    ΙΔΙΟΣ ΚΑΝΟΝΑΣ με το `EmploymentState.scheduled_shifts_in_month` (ΜΙΑ πηγή
    αλήθειας για το «τι θεωρείται εργάσιμο» — αλλιώς οι δύο διαδρομές ωρών θα
    διαφωνούσαν):
    · **Κυριακή** του προγράμματος ΔΕΝ είναι αργία-ρεπό: είναι εργασία με
      προσαύξηση 75% — μετράει κανονικά.
    · Καθημερινή **αργία με καταγεγραμμένη παρουσία** δουλεύτηκε — μετράει.
    · Η ίδια η **ημέρα πρόσληψης** θεωρείται εργάσιμη ακόμη κι αν είναι αργία.
    """
    holidays = public_holidays(y)
    return sum(
        1
        for d in emp.scheduled_days_in_month(y, m)
        if d not in on_leave
        and d.weekday() != 6
        and d in holidays
        and d not in emp.attendance
        and d != emp.hire_date
    )


def _month_hours(emp, y: int, m: int) -> dict:
    """ΟΙ ΩΡΕΣ ΤΟΥ ΜΗΝΑ, ΣΠΑΣΜΕΝΕΣ ΣΤΑ ΤΕΣΣΕΡΑ ΜΕΓΕΘΗ ΤΟΥ ΠΙΝΑΚΑ:

        {"scheduled": Π.ΩΡΕΣ, "holiday": ΑΡΓΙΕΣ, "leave": ΑΔΕΙΕΣ,
         "worked": ΩΡΕΣ, "certain": είναι βεβαιωμένες;}

    ΤΑΥΤΟΤΗΤΑ, εξ ορισμού: `scheduled = worked + holiday + leave`. Οι
    «Π.ΩΡΕΣ» δηλαδή ΔΕΝ ξαναμετριούνται από το πρόγραμμα — συντίθενται από
    τα τρία μέρη, ώστε ο πίνακας να μη μπορεί ΠΟΤΕ να μην αθροίζει (οι ώρες
    εργασίας μπορεί να έρχονται από εγκεκριμένο συγκεντρωτικό — με
    υπερεργασία και υπερωρία μέσα — που το πρόγραμμα δεν τις ξέρει).

    ΑΡΓΙΕΣ και ΑΔΕΙΕΣ αποτιμώνται στο ΗΜΕΡΗΣΙΟ ΩΡΑΡΙΟ της σύμβασης: είναι
    πληρωμένες ημέρες που δεν παράγουν εργατοώρα.

    Στις ΩΡΕΣ (`worked`) ΔΕΝ ΜΕΤΡΑΝΕ ΟΙ ΠΛΗΡΩΜΕΝΕΣ ΗΜΕΡΕΣ ΠΟΥ ΔΕΝ
    ΔΟΥΛΕΥΤΗΚΑΝ — άδειες (κανονική,
    ασθενείας, άνευ αποδοχών) και **αργίες**: το κόστος τους μένει στον
    αριθμητή και επιμερίζεται στις ώρες που όντως δουλεύτηκαν. Δύο πηγές:

    1. **ΕΓΚΕΚΡΙΜΕΝΟ συγκεντρωτικό** (`AttendanceSheetApproved`) → οι 20 ώρες
       της κατάταξης, όπως εγκρίθηκαν (κανονικές + πρόσθετη + υπερεργασία +
       υπερωρία). Είναι ήδη καθαρές από άδειες ΚΑΙ αργίες, αφού προκύπτουν
       από βάρδιες — πραγματικές (κάρτα) ή τεκμαρτές, που δεν τεκμαίρονται
       ποτέ σε αργία.
       ⚠️ Παρουσίες καταγεγραμμένες ΧΩΡΙΣ βάρδιες δεν κατατάσσονται (μένουν
       μηδέν ώρες): τότε πέφτουμε στις **ημέρες εργασίας × ωράριο**. Οι ημέρες
       αυτές είναι ήδη καθαρές από άδειες· από τις αργίες τις καθαρίζουμε εδώ,
       εκτός αν προέρχονται από πραγματική καταγραφή (`source == "recorded"`),
       οπότε ΟΣΕΣ αργίες περιέχουν δουλεύτηκαν.
    2. **Μήνας χωρίς έγκριση** (μελλοντικός ή εκκρεμής) → ΕΚΤΙΜΗΣΗ από το
       πρόγραμμα: προγραμματισμένες ημέρες μείον άδειες μείον αργίες, επί το
       ημερήσιο ωράριο.

    ⚠️ Η ΑΚΡΙΒΗΣ κατάταξη ωρών (`svc.hours_analysis`) ΔΕΝ καλείται εδώ: σαρώνει
    το έτος λεπτό-προς-λεπτό (~150ms για 3 απασχολήσεις) και θα έκανε την
    επισκόπηση αργή. Για τους ΕΓΚΕΚΡΙΜΕΝΟΥΣ μήνες το αποτέλεσμά της είναι ήδη
    αποθηκευμένο στο event· για τους υπόλοιπους μιλάμε ούτως ή άλλως για
    πρόβλεψη."""
    on_leave = set(emp.leave_days_in_month(y, m))
    off = _holidays_off(emp, y, m, on_leave)
    # Ημερήσιο ωράριο για τις ΜΗ ΔΟΥΛΕΜΕΝΕΣ πληρωμένες ημέρες (αργίες/άδειες):
    # η σύμβαση στο τέλος του μήνα — ίδια επιλογή με τη διαδρομή του
    # εγκεκριμένου συγκεντρωτικού.
    daily_off = _contract_daily_hours(
        emp, emp.contract_on(date(y, m, monthrange(y, m)[1]))
    )

    def out(worked: float, certain: bool) -> dict:
        holiday = round(off * daily_off, 2)
        leave = round(len(on_leave) * daily_off, 2)
        return {
            "worked": worked,
            "holiday": holiday,
            "leave": leave,
            "scheduled": round(worked + holiday + leave, 2),
            "certain": certain,
        }

    approved = proj.attendance_sheets.get((y, m))
    line = approved.lines.get(emp.employment_id) if approved else None
    if line:
        hours = sum(float(line.get(f) or 0) for f in work_hours.SHEET_HOUR_FIELDS)
        if hours > 0:
            return out(round(hours, 2), True)
        worked = float(line.get("worked") or 0)
        if line.get("source") != "recorded":
            worked = max(0.0, worked - off)  # τεκμαρτές ημέρες: βγάλε τις αργίες
        terms = emp.contract_on(date(y, m, monthrange(y, m)[1]))
        return out(round(worked * _contract_daily_hours(emp, terms), 2), True)

    days = [d for d in emp.scheduled_days_in_month(y, m) if d not in on_leave]
    if not days:
        return out(0.0, False)
    terms = emp.contract_on(days[-1])
    return out(
        round(max(0, len(days) - off) * _contract_daily_hours(emp, terms), 2),
        False,
    )


def _worked_hours(emp, y: int, m: int) -> tuple[float, bool]:
    """(ΩΡΕΣ ΠΡΑΓΜΑΤΙΚΗΣ ΕΡΓΑΣΙΑΣ του μήνα, είναι βεβαιωμένες;) — η στήλη
    «ΩΡΕΣ» του πίνακα, από το `_month_hours`."""
    d = _month_hours(emp, y, m)
    return d["worked"], d["certain"]


def _unused_leave_hours(emp, year: int) -> float:
    """ΩΡΕΣ ΤΗΣ ΑΔΕΙΑΣ ΠΟΥ ΔΕΝ ΕΧΕΙ ΛΗΦΘΕΙ ΑΚΟΜΗ — και θα ληφθεί μέχρι 31/12.

    Η κανονική άδεια εξαντλείται μέσα στο ημερολογιακό έτος: όσες ημέρες
    δικαιώματος δεν έχουν καταχωρηθεί, θα παρθούν (ή θα αποζημιωθούν στην
    αποχώρηση). Πληρώνονται και στις δύο περιπτώσεις, ΩΡΑ ΕΡΓΑΣΙΑΣ όμως δεν
    παράγουν — χωρίς αυτή την αφαίρεση η πρόβλεψη μετρά ώρες που δεν θα
    δουλευτούν και το κόστος της εργατοώρας βγαίνει ΦΘΗΝΟΤΕΡΟ απ' ό,τι είναι.

    Δεν αφορά όσους έχουν αποχωρήσει: το υπόλοιπό τους έγινε ήδη αποζημίωση
    μη ληφθείσας αδείας, και δεν υπάρχουν επόμενοι μήνες να χρεωθούν."""
    if emp.termination_date is not None or emp.hire_date is None:
        return 0.0
    eid = emp.employment_id
    try:
        remaining = max(
            0,
            svc.annual_leave_entitlement(eid, year)
            - svc.annual_leave_taken(eid, year),
        )
    except (KeyError, ValueError):
        return 0.0
    if not remaining:
        return 0.0
    terms = emp.contract_on(date(year, 12, 31))
    return round(remaining * _contract_daily_hours(emp, terms), 2)


def hourly_cost_rows(year: int, per_employee: dict, worked: dict,
                     net_per_employee: dict, unused_leave: dict):
    """ΤΟ ΠΡΑΓΜΑΤΙΚΟ ΚΟΣΤΟΣ ΤΗΣ ΕΡΓΑΤΟΩΡΑΣ, ανά εργαζόμενο και συνολικά.

        κόστος/ώρα = ΟΛΟ το ετήσιο κόστος εργοδότη (μισθοδοσία + Δώρα +
                     Επίδομα Αδείας + αποζημιώσεις, μαζί με τις προβλέψεις)
                     ÷ τις ΩΡΕΣ ΠΡΑΓΜΑΤΙΚΗΣ ΕΡΓΑΣΙΑΣ του έτους

    Ο αριθμητής είναι ΤΟ ΙΔΙΟ ποσό που δείχνει η ετήσια αναφορά (έρχεται από
    εκεί, ξανακομμένο ανά εργαζόμενο) — τα δύο δεν μπορούν να διαφωνήσουν.
    Ο παρονομαστής μετράει ΜΟΝΟ ώρες που δουλεύτηκαν: άδειες και **αργίες**
    πληρώνονται αλλά δεν παράγουν εργατοώρα, άρα ανεβάζουν το κόστος της.
    Στο τέλος αφαιρείται και το **υπόλοιπο της κανονικής αδείας** (ημέρες που
    δεν έχουν ληφθεί ακόμη αλλά θα ληφθούν ή θα αποζημιωθούν μέσα στο έτος):
    αν έμεναν μέσα, ο παρονομαστής θα μετρούσε ώρες που δεν θα δουλευτούν και
    η εργατοώρα θα έβγαινε φθηνότερη απ' ό,τι είναι.

    Δίπλα του, η ΑΛΛΗ ΟΨΗ του ίδιου ποσού: η **αμοιβή ανά εργατοώρα**
    («Εργ/Ωρα») — τα ΚΑΘΑΡΑ που εισπράττει ο εργαζόμενος (μισθοδοσία + Δώρα +
    Επίδομα Αδείας + αποζημιώσεις, μετά εισφορές και ΦΜΥ) διά τις ΙΔΙΕΣ ώρες.
    Η ψαλίδα ανάμεσα στα δύο είναι όσα δεν φτάνουν ποτέ στην τσέπη:
    εργοδοτικές εισφορές, εισφορές εργαζόμενου, ΦΜΥ.

    Σύγκριση με το **ωρομίσθιο της σύμβασης** (`work_hours.hourly_rate` —
    ΜΙΑ πηγή με τις προσαυξήσεις του συγκεντρωτικού), σταθμισμένο με τις ώρες
    κάθε μήνα ώστε μια αύξηση στη μέση του έτους να μετρήσει σωστά. Ο λόγος
    των δύο («×1,63») είναι η πραγματική επιβάρυνση πάνω από τον μισθό:
    εργοδοτικές εισφορές, Δώρα, επίδομα, πληρωμένες μη-εργάσιμες ημέρες
    (άδειες, αργίες)."""
    rows = []
    for eid, parts in per_employee.items():
        emp = proj.employments.get(eid)
        if emp is None or emp.hire_date is None:
            continue
        hours = wage_hours = certain = 0.0
        per_month = worked.get(eid) or {}
        for m in range(1, 13):
            h, sure = per_month.get(m, (0.0, True))
            if not h:
                continue
            terms = emp.contract_on(date(year, m, monthrange(year, m)[1]))
            hours += h
            certain += h if sure else 0.0
            wage_hours += h * work_hours.hourly_rate(
                terms.pay_type, terms.monthly_gross, terms.daily_wage,
                terms.weekly_hours,
            )
        cost = round(sum(parts.values()), 2)
        net = round(net_per_employee.get(eid, 0.0), 2)
        nominal = round(wage_hours / hours, 4) if hours else 0.0
        # ΩΡΕΣ ΕΡΓΑΣΙΑΣ = μηνιαίες ώρες μείον το υπόλοιπο αδείας. Αυτές
        # διαιρούν — και αυτές δείχνει ο πίνακας.
        leave = round(unused_leave.get(eid, 0.0), 2)
        work = max(0.0, round(hours - leave, 2))
        rows.append({
            "eid": eid,
            "name": emp.full_name,
            "cost": cost,
            "net": net,
            # Τι πιάνει στο χέρι ο εργαζόμενος για κάθε ΔΟΥΛΕΜΕΝΗ ώρα
            "take": round(net / work, 4) if work else None,
            "hours": work,
            "month_hours": round(hours, 2),  # πριν την αφαίρεση της αδείας
            "unused_leave": leave,
            # Πόσες από τις ώρες είναι βεβαιωμένες (εγκεκριμένο συγκεντρωτικό)
            "certain_hours": round(certain, 2),
            "estimated": certain < hours or leave > 0,
            "nominal": nominal,
            "rate": round(cost / work, 4) if work else None,
            "ratio": round(cost / (nominal * work), 2) if work and nominal else None,
        })
    rows.sort(key=lambda r: (-(r["rate"] or 0), r["name"]))

    hours = round(sum(r["hours"] for r in rows), 2)
    cost = round(sum(r["cost"] for r in rows), 2)
    net = round(sum(r["net"] for r in rows), 2)
    wage_hours = sum(r["nominal"] * r["hours"] for r in rows)
    nominal = round(wage_hours / hours, 4) if hours else 0.0
    return {
        "rows": rows,
        "total": {
            "hours": hours,
            "month_hours": round(sum(r["month_hours"] for r in rows), 2),
            "unused_leave": round(sum(r["unused_leave"] for r in rows), 2),
            "cost": cost,
            "net": net,
            "take": round(net / hours, 4) if hours else None,
            "certain_hours": round(sum(r["certain_hours"] for r in rows), 2),
            "estimated": any(r["estimated"] for r in rows),
            "nominal": nominal,
            "rate": round(cost / hours, 4) if hours else None,
            "ratio": round(cost / wage_hours, 2) if wage_hours else None,
        },
    }


def annual_cost_report(year: int):
    """ΕΤΗΣΙΑ ΑΝΑΦΟΡΑ ΚΟΣΤΟΥΣ — 12 μήνες × 4 συνιστώσες, με ΠΡΟΒΛΕΨΗ μέχρι το
    τέλος του έτους. Read-only, καμία παρενέργεια.

    Κάθε κελί είναι **πραγματικό** όταν υπάρχει το αντίστοιχο event, αλλιώς
    **προβλεπόμενο** (εκτίμηση από τη σύμβαση που ισχύει). Η διάκριση γίνεται
    ΑΝΑ ΕΡΓΑΖΟΜΕΝΟ, όχι ανά μήνα: η εκκαθάριση αποχώρησης υπολογίζει τη
    μισθοδοσία ΕΝΟΣ μόνο ατόμου μέσα στον μήνα (§2) — οι υπόλοιποι μένουν
    πρόβλεψη στον ίδιο μήνα.

    Το «κόστος» είναι παντού το ΚΟΣΤΟΣ ΕΡΓΟΔΟΤΗ: μικτά + εισφορές εργοδότη
    (ίδιος ορισμός με `monthly_totals` και τις εκτυπώσεις). Οι αποζημιώσεις
    αποχώρησης (απόλυσης & μη ληφθείσας αδείας) δεν φέρουν ασφαλιστικές
    κρατήσεις, άρα κόστος = μικτά — και ΔΕΝ προβλέπονται ποτέ: καμία απόλυση
    δεν είναι προγραμματισμένη."""
    keys = [k for k, _label, _color in COST_COMPONENTS]
    measures = [k for k, _label, _note in COST_MEASURES]
    months = []
    for m in range(1, 13):
        cell = {
            "m": m,
            "label": MONTHS_SHORT[m],
            "long": MONTHS_NOM[m],
            # ΤΟ ΙΔΙΟ κελί σε τρεις οπτικές (κόστος / μικτά / καθαρά)
            "by": {
                ms: {
                    "parts": dict.fromkeys(keys, 0.0),
                    # πόσο του κελιού είναι πρόβλεψη
                    "fore": dict.fromkeys(keys, 0.0),
                }
                for ms in measures
            },
        }
        # Η ΟΠΤΙΚΗ ΤΟΥ ΚΟΣΤΟΥΣ μένει και στο πρώτο επίπεδο (ίδια αντικείμενα,
        # όχι αντίγραφα): είναι η προεπιλογή της οθόνης και ό,τι διαβάζουν οι
        # καλούντες που δεν ξέρουν από οπτικές.
        cell["parts"] = cell["by"]["cost"]["parts"]
        cell["fore"] = cell["by"]["cost"]["fore"]
        months.append(cell)

    # Το ΙΔΙΟ ποσό, ξανακομμένο ανά εργαζόμενο — παρονομαστής του κόστους
    # εργατοώρας (`hourly_cost_rows`), ώστε τα δύο να μη διαφωνούν ΠΟΤΕ.
    per_employee = {}
    # Η άλλη όψη του ίδιου ποσού: τι μένει στην ΤΣΕΠΗ του εργαζόμενου (μετά
    # εισφορές & ΦΜΥ) — αριθμητής της αμοιβής ανά εργατοώρα.
    net_per_employee = {}

    def add(m: int, key: str, cost: float, gross: float, net: float,
            forecast: bool, eid: str):
        """Μία καταβολή στο κελί (μήνας × συνιστώσα), και στις τρεις οπτικές."""
        if not cost:
            return
        cell = months[m - 1]
        for ms, amount in (("cost", cost), ("gross", gross), ("net", net)):
            b = cell["by"][ms]
            b["parts"][key] = round(b["parts"][key] + amount, 2)
            if forecast:
                b["fore"][key] = round(b["fore"][key] + amount, 2)
        who = per_employee.setdefault(eid, dict.fromkeys(keys, 0.0))
        who[key] = round(who[key] + cost, 2)
        net_per_employee[eid] = round(net_per_employee.get(eid, 0.0) + net, 2)

    year_start, year_end = date(year, 1, 1), date(year, 12, 31)

    # ── Τακτική μισθοδοσία ────────────────────────────────────────────────
    done = {(p.employee_id, p.period_month): p
            for p in proj.payrolls if p.period_year == year}
    for m in range(1, 13):
        first, last = date(year, m, 1), date(year, m, monthrange(year, m)[1])
        for e in proj.employments.values():
            p = done.get((e.employee_id, m))
            if p is not None:
                add(m, "payroll", round(p.gross + p.efka_employer, 2), p.gross,
                    p.net, False, e.employee_id)
            elif _employed_in(e, first, last):
                cost, gross, net = _forecast_payroll(e, year, m)
                add(m, "payroll", cost, gross, net, True, e.employee_id)

    # ── Δώρα εορτών ───────────────────────────────────────────────────────
    for kind in ("easter", "christmas"):
        code = "EASTER_GIFT" if kind == "easter" else "CHRISTMAS_GIFT"
        calculated = {g.employee_id: g for g in proj.gifts
                      if g.gift_type == kind and g.year == year}
        for g in calculated.values():
            add(svc.gift_fmy_period(g)[1], "gifts", _gift_cost(g), g.gross,
                g.net, False, g.employee_id)
        due_month = ot.due_date(code, year, 0).month
        for e in proj.employments.values():
            if e.employee_id in calculated:
                continue
            if not _employed_in(e, year_start, year_end):
                continue
            r = svc.gift_preview_for(e, kind, year)
            add(due_month, "gifts", round(r.gross + r.efka_employer, 2),
                r.gross, r.net, True, e.employee_id)

    # ── Επίδομα αδείας ────────────────────────────────────────────────────
    settled = {a.employee_id: a for a in proj.allowances if a.year == year}
    for a in settled.values():
        add(_allowance_month(proj.employments.get(a.employee_id), year),
            "allowance", _gift_cost(a), a.gross, a.net, False, a.employee_id)
    for e in proj.employments.values():
        if e.employee_id in settled or not _employed_in(e, year_start, year_end):
            continue
        r = svc.allowance_preview_for(e, year)
        add(_allowance_month(e, year), "allowance",
            round(r.gross + r.efka_employer, 2), r.gross, r.net, True,
            e.employee_id)

    # ── Αποζημιώσεις αποχώρησης (ΠΟΤΕ πρόβλεψη) ───────────────────────────
    for ev in list(proj.severances) + list(proj.leave_compensations):
        if ev.termination_date.year == year:
            # Καμία ασφαλιστική κράτηση: κόστος = μικτά (§5.11)
            add(ev.termination_date.month, "severance", ev.gross, ev.gross,
                ev.net, False, ev.employee_id)

    # ── Ώρες πραγματικής εργασίας ─────────────────────────────────────────
    # ΜΙΑ πηγή για τον μήνα και για το κόστος εργατοώρας: το `_worked_hours`
    # βγάζει άδειες και αργίες — πληρώνονται, αλλά εργατοώρα δεν παράγουν.
    # Στους μήνες χωρίς εγκεκριμένο συγκεντρωτικό (πρόβλεψη) οι ώρες είναι
    # οι προγραμματισμένες ΜΕΙΟΝ τις ήδη καταχωρημένες ημέρες αδείας.
    #
    # ⚠️ Το ΥΠΟΛΟΙΠΟ της αδείας ΔΕΝ χρεώνεται σε μήνα: δεν ξέρουμε πότε θα
    # ληφθεί, και φορτώνοντάς το στον Δεκέμβριο θα έδειχνε τρύπα που δεν
    # είναι του Δεκεμβρίου. Αφαιρείται ΜΕΤΑ ΤΟ ΣΥΝΟΛΟ, ως ρητή γραμμή.
    worked, unused_leave, hour_parts = {}, {}, {}
    for eid in per_employee:
        emp = proj.employments.get(eid)
        if emp is None or emp.hire_date is None:
            continue
        per_m = {m: _month_hours(emp, year, m) for m in range(1, 13)}
        hour_parts[eid] = per_m
        # Το κόστος εργατοώρας θέλει ΜΟΝΟ τις δουλεμένες (ώρες, βεβαιωμένες;)
        worked[eid] = {m: (d["worked"], d["certain"]) for m, d in per_m.items()}
        unused_leave[eid] = _unused_leave_hours(emp, year)

    for cell in months:
        per_month = [w[cell["m"]] for w in hour_parts.values()]
        # Οι τέσσερις στήλες ωρών του πίνακα, στη σειρά που διαβάζονται:
        # Π.ΩΡΕΣ − ΑΡΓΙΕΣ − ΑΔΕΙΕΣ = ΩΡΕΣ (ταυτότητα του `_month_hours`).
        for col, key in (("sched_hours", "scheduled"),
                         ("holiday_hours", "holiday"),
                         ("leave_hours", "leave"),
                         ("hours", "worked")):
            cell[col] = round(sum(d[key] for d in per_month), 2)
        cell["hours_estimated"] = any(
            d["worked"] and not d["certain"] for d in per_month)

    # ── Σύνολα (ΑΝΑ ΟΠΤΙΚΗ) ───────────────────────────────────────────────
    by = {}
    for ms, ms_label, ms_note in COST_MEASURES:
        running = 0.0
        for cell in months:
            b = cell["by"][ms]
            b["total"] = round(sum(b["parts"].values()), 2)
            b["forecast_total"] = round(sum(b["fore"].values()), 2)
            # «Προβλεπόμενος» μήνας = έστω ένα ευρώ δεν έχει υπολογιστεί ακόμη
            b["estimated"] = b["forecast_total"] > 0
            running = round(running + b["total"], 2)
            b["cumulative"] = running
        actual = round(
            sum(c["by"][ms]["total"] - c["by"][ms]["forecast_total"]
                for c in months), 2)
        forecast = round(sum(c["by"][ms]["forecast_total"] for c in months), 2)
        by[ms] = {
            "key": ms,
            "label": ms_label,
            "note": ms_note,
            "components": [
                {
                    "key": k,
                    "label": label,
                    "color": color,
                    "total": round(sum(c["by"][ms]["parts"][k] for c in months), 2),
                    "forecast": round(sum(c["by"][ms]["fore"][k] for c in months), 2),
                }
                for k, label, color in COST_COMPONENTS
            ],
            "actual": actual,
            "forecast": forecast,
            "total": round(actual + forecast, 2),
            # Ο πρώτος μήνας με πρόβλεψη — από εκεί «σπάει» το γράφημα
            "first_forecast": next(
                (c["m"] for c in months if c["by"][ms]["estimated"]), None),
        }

    # Η οπτική του ΚΟΣΤΟΥΣ και στο πρώτο επίπεδο (προεπιλογή της οθόνης)
    for cell in months:
        cell.update({k: cell["by"]["cost"][k] for k in
                     ("total", "forecast_total", "estimated", "cumulative")})
    cost = by["cost"]
    return {
        "year": year,
        "measures": [by[k] for k, _label, _note in COST_MEASURES],
        "by": by,
        "components": cost["components"],
        "months": months,
        # ΤΡΕΙΣ αριθμοί ωρών, με τη σειρά που διαβάζονται στον πίνακα:
        # άθροισμα μηνών → μείον το υπόλοιπο αδείας → ΩΡΕΣ ΕΡΓΑΣΙΑΣ (ο
        # παρονομαστής του κόστους εργατοώρας).
        "hours": round(sum(c["hours"] for c in months), 2),
        "sched_hours": round(sum(c["sched_hours"] for c in months), 2),
        "holiday_hours": round(sum(c["holiday_hours"] for c in months), 2),
        "leave_hours": round(sum(c["leave_hours"] for c in months), 2),
        "unused_leave_hours": round(sum(unused_leave.values()), 2),
        "work_hours": round(
            sum(c["hours"] for c in months) - sum(unused_leave.values()), 2),
        "actual": cost["actual"],
        "forecast": cost["forecast"],
        "total": cost["total"],
        "first_forecast": cost["first_forecast"],
        # Το ΙΔΙΟ κόστος, ανά εργαζόμενο και ανά ΩΡΑ ΕΡΓΑΣΙΑΣ
        "hourly": hourly_cost_rows(
            year, per_employee, worked, net_per_employee, unused_leave),
    }

# ------------------------------------------------ Λεπτομέρειες υποχρέωσης
def detail_payload(ob_id: str, ref: date):
    """Τι ΘΑ κάνει (preview) ή τι ΕΚΑΝΕ (facts) μια υποχρέωση."""
    ob = proj.obligations.get(ob_id)
    if ob is None:
        return None
    y, m, t = ob.period_year, ob.period_month, ob.obligation_type
    st, reason = obligation_status(ob, ref)
    index = deps.build_index(proj.obligations.values())
    dependents = [
        o
        for o in proj.obligations.values()
        if any(p.obligation_id == ob_id for p in deps.prerequisites(o, index))
    ]
    d = {
        "id": ob_id,
        "type": t,
        "ergani_only": t in ERGANI_ONLY,
        "label": OB_LABELS.get(t, t),
        "icon": OB_ICONS.get(t, "📌"),
        "due": ob.due_date,
        "period": f"{m}/{y}" if m else str(y),
        "status": st,
        "done": ob.fulfilled,
        "reason": reason,
        "late": days_late(ob, ref),  # >0 -> εκπρόθεσμη, επιτρέπεται με προειδοποίηση
        "deps": [
            {"label": dep_label(p), "done": p.fulfilled}
            for p in deps.prerequisites(ob, index)
        ],
        "unlocks": [
            {"label": dep_label(o), "done": o.fulfilled}
            for o in sorted(dependents, key=lambda x: x.due_date)
        ],
        "steps": [],
        "rows": [],
        "totals": {},
        "facts": [],
    }
    # Παράταση προθεσμίας: επιτρέπεται μόνο αν ο ΤΥΠΟΣ τη δηλώνει (σελίδα
    # Παράμετροι) — το όριο μετρά από την ΑΡΧΙΚΗ προθεσμία.
    ext = ot.spec(t).extension if t in ot.TYPES else {}
    d["can_extend"] = bool(ext.get("allowed")) and not ob.fulfilled
    max_days = int(ext.get("max_days", 0) or 0)
    base_due = ob.original_due or ob.due_date
    d["extend_max"] = (
        base_due + timedelta(days=max_days) if max_days else None
    )
    # ΙΔΙΟ όριο με το `extend_obligation`: όποιος τύπος δεν έχει νόημα σε
    # επόμενη χρήση (Επίδομα Αδείας) σταματά στην 31/12 της περιόδου του.
    if ext.get("within_period_year") and ob.period_year:
        limit = date(ob.period_year, 12, 31)
        d["extend_max"] = min(d["extend_max"] or limit, limit)
    # Γιατί να αναβάλει κανείς — και τι του κοστίζει
    d["extend_note"] = (
        "Χρήσιμη στο 1ο/2ο έτος: το ποσό οριστικοποιείται όταν μαθευτεί αν ο "
        "εργαζόμενος θα μείνει ως το τέλος του έτους. ⚠️ Όσο η υποχρέωση μένει "
        "ανοιχτή ΜΠΛΟΚΑΡΕΙ την ΑΠΔ της επόμενης περιόδου, και ο μήνας ΑΠΔ/ΦΜΥ "
        "του επιδόματος παραμένει ο μήνας της άδειας — όποτε κι αν γίνει ο "
        "υπολογισμός."
        if t == "LEAVE_ALLOWANCE" and d["can_extend"] else ""
    )
    # ΔΙΟΡΘΩΣΗ ΜΕΤΑ ΤΗΝ ΟΛΟΚΛΗΡΩΣΗ — οι δύο πόρτες που αντικαθιστούν το
    # καταστροφικό rollback: άνοιγμα μισθοδοσίας & δήλωση λανθασμένης
    # υποβολής. Οι ΚΑΝΟΝΕΣ ζουν στο service· εδώ μόνο τι δείχνει η οθόνη.
    d["can_reopen"] = t == "MONTHLY_PAYROLL" and ob.fulfilled
    d["reopen_note"] = ""
    if d["can_reopen"]:
        blocking = [
            ot.LABELS.get(x, x)
            for x in ("APD_SUBMISSION", "APD_TEKA_SUBMISSION", "FMY_SUBMISSION")
            if any(
                o.obligation_type == x and o.fulfilled
                and o.period_year == y and o.period_month == m
                for o in proj.obligations.values()
            )
        ]
        paid = any(
            o.obligation_type == "SALARY_PAYMENT" and o.fulfilled
            and o.period_year == y and o.period_month == m
            for o in proj.obligations.values()
        )
        if blocking:
            d["can_reopen"] = False
            d["reopen_note"] = (
                "Έχει ήδη υποβληθεί: " + " · ".join(blocking) + ". Για να "
                "διορθωθεί η μισθοδοσία, δήλωσε πρώτα λανθασμένη υποβολή."
            )
        elif paid:
            d["reopen_note"] = (
                "⚠️ Οι μισθοί έχουν ήδη πληρωθεί: ο επανυπολογισμός θα αλλάξει "
                "τα καθαρά και η διαφορά θα χρειαστεί συμψηφισμό — η πληρωμή "
                "δεν αντιστρέφεται."
            )
    d["can_withdraw"] = ob.fulfilled and t in (
        "APD_SUBMISSION", "APD_TEKA_SUBMISSION", "FMY_SUBMISSION"
    )
    d["extended"] = ob.extended
    d["original_due"] = ob.original_due
    per = d["period"]

    if t == "CARD_IMPORT":
        d["upload"] = not ob.fulfilled
        d["card_y"], d["card_m"] = y, m
        if not ob.fulfilled:
            d["rows"] = [
                {"Εργαζόμενος": i["name"],
                 "Ημέρα": i["day"].strftime("%d/%m/%Y") if i["day"] else "—",
                 "Εκκρεμότητα": i["text"],
                 "Μπλοκάρει": "ναι" if i["blocking"] else "όχι"}
                for i in svc.card_period_issues(y, m)
            ]
        d["steps"] = (
            [f"Εισήχθη και ελέγχθηκε η κάρτα εργασίας {per}."]
            if ob.fulfilled
            else [
                "Εισαγωγή του αρχείου «Κάρτα Εργασίας» του ΕΡΓΑΝΗ (xlsx) από τις "
                "Παρουσίες — ο έλεγχος τρέχει αυτόματα· αντιστοίχιση με ΑΦΜ, "
                "επανεισαγωγή αντικαθιστά. Η Ενεργοποίηση εδώ ξαναελέγχει μετά "
                "από διορθώσεις.",
                "Ο έλεγχος απορρίπτει όσο κάποιος εργαζόμενος δεν έχει κανένα "
                "χτύπημα, έχει χτύπημα χωρίς ταίρι ή χτύπημα σε ημέρα άδειας — "
                "διορθώνονται στο ΗΜΕΡΟΛΟΓΙΟ του εργαζομένου. Ημέρες χωρίς "
                "χτύπημα μετρούν με το πρόγραμμα.",
                f"Με την ολοκλήρωση ξεκλειδώνει η έγκριση παρουσιών {per}.",
            ]
        )

    elif t == "ATTENDANCE_APPROVAL":
        sheet = attendance_sheet(y, m)
        # ΑΝΑΛΥΤΙΚΑ ανά εργαζόμενο, ΜΟΝΟ ό,τι έχει τιμή
        d["blocks"] = [
            {"title": r["name"],
             "note": sheet["sources"].get(r["source"], r["source"]),
             "items": attendance_items(r)}
            for r in sheet["rows"]
        ]
        d["steps"] = (
            [f"Εγκρίθηκε το συγκεντρωτικό παρουσιών {per}."]
            if ob.fulfilled
            else [
                "Οι παρουσίες προκύπτουν από τις καταγεγραμμένες ημέρες ή, όπου "
                "δεν υπάρχουν, ΤΕΚΜΑΙΡΟΝΤΑΙ από το πρόγραμμα (το ημερήσιο "
                "υπερισχύει του εβδομαδιαίου).",
                "Η ενεργοποίηση εγκρίνει το προσχέδιο ΩΣ ΕΧΕΙ. Για χειροκίνητες "
                "διορθώσεις, χρησιμοποιήστε τη σελίδα «Παρουσίες» πριν εγκρίνετε.",
                f"Με την έγκριση ξεκλειδώνει η μισθοδοσία {per}.",
            ]
        )

    elif t == "MONTHLY_PAYROLL":
        d["rows"] = payroll_rows(y, m, preview=not ob.fulfilled)
        d["totals"] = money_totals(
            d["rows"], ["Μικτά", "ΕΦΚΑ εργαζ.", "ΕΦΚΑ εργοδ.", "ΦΜΥ", "Καθαρά"]
        )
        d["steps"] = (
            [
                f"Υπολογίστηκε η μισθοδοσία {per} για {len(d['rows'])} "
                f"απασχολήσεις (events MonthlyPayrollCalculated)."
            ]
            if ob.fulfilled
            else [
                f"Θα υπολογιστεί η μισθοδοσία {per} για ΟΛΕΣ τις ενεργές "
                f"απασχολήσεις μαζί ({len(d['rows'])}).",
                "Μισθωτοί: 25 ημέρες ασφάλισης μείον άδειες άνευ αποδοχών. "
                "Ημερομίσθιοι: ημερομίσθιο x ημέρες παρουσίας.",
                "Θα καταγραφεί ένα event MonthlyPayrollCalculated ανά απασχόληση.",
            ]
        )
        if not ob.fulfilled:
            d["steps"] += [f"⚠️ {w}" for w in payroll_warnings(y, m)]

    elif t in ("EASTER_GIFT", "CHRISTMAS_GIFT", "LEAVE_ALLOWANCE"):
        g = {
            "EASTER_GIFT": "easter",
            "CHRISTMAS_GIFT": "christmas",
            "LEAVE_ALLOWANCE": "allowance",
        }[t]
        d["rows"] = gift_rows(g, y, preview=not ob.fulfilled, month=m)
        d["totals"] = money_totals(d["rows"], ["Μικτά", "ΕΦΚΑ εργαζ.", "ΦΜΥ", "Καθαρά"])
        verb = "Υπολογίστηκε" if ob.fulfilled else "Θα υπολογιστεί"
        if g == "christmas":
            nxt = f"1/{y + 1} (υποβολή έως τέλος Φεβρουαρίου {y + 1})"
        elif g == "easter":
            nxt = f"5/{y} (υποβολή έως τέλος Ιουνίου {y})"
        else:
            # Επίδομα Αδείας: περίοδος = ο μήνας της πρώτης κανονικής άδειας
            ny, nm = (y + 1, 1) if m == 12 else (y, (m or 0) + 1)
            nxt = f"{nm}/{ny}"
        if g == "allowance":
            d["steps"] = [
                f"{verb} για τους {len(d['rows'])} εργαζόμενους που πήραν την "
                f"ΠΡΩΤΗ τους κανονική άδεια τον {m}/{y} — το επίδομα "
                "καταβάλλεται όταν ο εργαζόμενος πάρει την άδειά του.",
            ]
            if not ob.fulfilled:
                d["steps"] += [f"⚠️ {w}" for w in allowance_warnings(y, m)]
        else:
            d["steps"] = [
                f"{verb} για όλους τους δικαιούχους μαζί ({len(d['rows'])}), με "
                f"αναλογία περιόδου απασχόλησης και προσαύξηση 0,04166 (Δώρα).",
            ]
        d["steps"].append(
            f"Οι αποδοχές θα συμπεριληφθούν στο αρχείο ΑΠΔ της περιόδου {nxt}, "
            f"μαζί με τη μηνιαία μισθοδοσία — δεν γίνεται ξεχωριστή υποβολή."
        )

    elif t in ot.PAYOUT_SOURCE:
        # Καταβολή Δώρου / Επιδόματος Αδείας στους δικαιούχους
        src = ot.PAYOUT_SOURCE[t]
        g = {
            "EASTER_GIFT": "easter",
            "CHRISTMAS_GIFT": "christmas",
            "LEAVE_ALLOWANCE": "allowance",
        }[src]
        if ob.fulfilled:
            ptype = {
                "EASTER_GIFT_PAYMENT": "easter_gift",
                "CHRISTMAS_GIFT_PAYMENT": "christmas_gift",
                "LEAVE_ALLOWANCE_PAYMENT": "leave_allowance",
            }[t]
            pays = [
                p
                for p in proj.payments
                if p.payment_type == ptype
                and p.period_year == y
                and p.period_month == m
            ]
            d["rows"] = [
                {"Εργαζόμενος": emp_name(p.beneficiary), "Ποσό": p.amount}
                for p in pays
            ]
            d["steps"] = [
                f"Καταβλήθηκαν τα καθαρά ποσά σε {len(pays)} εργαζόμενους."
            ]
        else:
            rows = gift_rows(g, y, preview=False, month=m if g == "allowance" else None)
            d["rows"] = [
                {"Εργαζόμενος": r["Εργαζόμενος"], "Ποσό": r["Καθαρά"]} for r in rows
            ]
            d["steps"] = [
                f"Θα καταβληθούν τα καθαρά ποσά ({OB_LABELS.get(src, src)} {per}) "
                f"σε {len(d['rows'])} εργαζόμενους — ένα event PaymentMade ανά "
                "δικαιούχο."
            ]
        d["totals"] = money_totals(d["rows"], ["Ποσό"])

    elif t in ("APD_SUBMISSION", "APD_TEKA_SUBMISSION"):
        fund = "efka" if t == "APD_SUBMISSION" else "teka"
        rows, totals, err = apd_rows(y, m, fund)
        if err:
            d["steps"] = [f"Αδύνατη η προεπισκόπηση: {err}"]
        else:
            d["rows"] = rows or []
            d["totals"] = totals  # κανόνας κεφαλίδας — βλ. apd_rows()
        # Το αρχείο δεν αποθηκεύεται — το όνομα είναι αυτό της λήψης (ZIP)
        fname = apd_files.zip_name(fund, y, m)
        if ob.fulfilled:
            gen = [
                f
                for f in proj.apd_files
                if f.fund == fund and f.period_year == y and f.period_month == m
            ]
            sub = [
                x
                for x in proj.apd_submissions
                if x.fund == fund and x.period_year == y and x.period_month == m
            ]
            if gen:
                g = gen[-1]
                d["facts"] += [
                    ("Αρχείο", fname),
                    ("Ασφαλισμένοι", str(g.insured_count)),
                    ("Σύνολο αποδοχών", f"{g.total_gross:.2f} €"),
                    ("Καταβλητέες εισφορές", f"{g.total_contributions:.2f} €"),
                ]
            if sub:
                d["facts"].append(("Υποβολή", sub[-1].reference or "—"))
            spec = "e-ΕΦΚΑ 12/2025" if fund == "efka" else "ΤΕΚΑ 3/2026"
            d["steps"] = [
                f"Παρήχθη το αρχείο CSL01 ({fund.upper()}) της περιόδου {per} "
                f"με τη γραμμογράφηση {spec} και καταγράφηκε η υποβολή του. "
                "Δεν αποθηκεύτηκε στον δίσκο — παράγεται on the fly από τα "
                f"«Αρχεία ΑΠΔ» (λήψη ως {fname})."
            ]
        else:
            gifts_in = sorted(
                {
                    r["Τύπος αποδοχών"]
                    for r in (rows or [])
                    if not r["Τύπος αποδοχών"].startswith("001")
                }
            )
            layout = (
                "γραμμογράφηση e-ΕΦΚΑ, εγγραφή τύπου 3: 162 bytes"
                if fund == "efka"
                else "γραμμογράφηση ΤΕΚΑ, εγγραφή τύπου 3: 148 bytes"
            )
            d["steps"] = [
                f"Θα δημιουργηθεί το αρχείο CSL01 ({layout}), για ΟΛΟΥΣ τους "
                f"ασφαλισμένους της περιόδου μαζί — διαθέσιμο για λήψη ως "
                f"{fname} από τα «Αρχεία ΑΠΔ» (δεν αποθηκεύεται στον δίσκο).",
                (
                    "Περιλαμβάνει και: "
                    + ", ".join(gifts_in)
                    + " του προηγούμενου μήνα, στο ίδιο αρχείο με τις τακτικές "
                    "αποδοχές."
                )
                if gifts_in
                else "Περιλαμβάνει μόνο τακτικές αποδοχές (001).",
                "Θα καταγραφούν events APDFileGenerated και APDSubmitted, και η "
                "υποχρέωση θα κλείσει.",
            ]
            # ΠΡΙΝ την υποβολή: συμφωνούν τα ποσοστά της ΑΠΔ με αυτά που
            # πλήρωσε η μισθοδοσία; (αλλιώς το λάθος το βρίσκει ο ΕΦΚΑ)
            d["steps"] += [f"⚠️ {w}" for w in apd_warnings(y, m)]
            if not err and (missing := apd_missing_ama(
                    svc.build_apd_rows(y, m).get(fund, []))):
                d["steps"].append(
                    "⚠️ Λείπει ο ΑΜΑ (υποχρεωτικός στην ΑΠΔ) για: "
                    f"{', '.join(missing)} — η υποβολή θα απορριφθεί.")

    elif t in (
        "SALARY_PAYMENT",
        "EFKA_PAYMENT",
        "TEKA_PAYMENT",
        "FMY_SUBMISSION",
        "FMY_PAYMENT",
    ):
        ptype = {
            "SALARY_PAYMENT": "salary",
            "EFKA_PAYMENT": "efka",
            "TEKA_PAYMENT": "teka",
            "FMY_PAYMENT": "fmy",
        }.get(t)
        if ob.fulfilled:
            if t == "FMY_SUBMISSION":
                sub = [
                    x
                    for x in proj.fmy_submissions
                    if x.period_year == y and x.period_month == m
                ]
                if sub:
                    d["facts"].append(("Δηλωθείς ΦΜΥ", f"{sub[-1].total_fmy:.2f} €"))
                d["steps"] = [f"Υποβλήθηκε η δήλωση απόδοσης ΦΜΥ {per}."]
            else:
                pays = [
                    p
                    for p in proj.payments
                    if p.payment_type == ptype
                    and p.period_year == y
                    and p.period_month == m
                ]
                if t == "SALARY_PAYMENT":
                    d["rows"] = [
                        {"Εργαζόμενος": emp_name(p.beneficiary), "Ποσό": p.amount}
                        for p in pays
                    ]
                    d["totals"] = money_totals(d["rows"], ["Ποσό"])
                    d["steps"] = [
                        f"Πληρώθηκαν οι καθαρές αποδοχές {per} σε {len(pays)} "
                        f"απασχολήσεις."
                    ]
                else:
                    total = sum(p.amount for p in pays)
                    ben = {"efka": "e-ΕΦΚΑ", "teka": "ΤΕΚΑ", "fmy": "ΑΑΔΕ"}.get(
                        ptype or "", ""
                    )
                    d["facts"] += [("Δικαιούχος", ben), ("Ποσό", f"{total:.2f} €")]
                    d["steps"] = [
                        f"Καταβλήθηκε το ποσό στον {ben} για την περίοδο {per}."
                    ]
        else:
            if t == "SALARY_PAYMENT":
                d["rows"] = [
                    {"Εργαζόμενος": emp_name(p.employee_id), "Ποσό": p.net}
                    for p in proj.payrolls
                    if p.period_year == y and p.period_month == m
                ]
                d["totals"] = money_totals(d["rows"], ["Ποσό"])
                d["steps"] = [
                    f"Θα πληρωθούν οι καθαρές αποδοχές {per} όλων των "
                    f"απασχολήσεων μαζί."
                ]
            elif t in ("EFKA_PAYMENT", "TEKA_PAYMENT"):
                fund = "efka" if t == "EFKA_PAYMENT" else "teka"
                try:
                    tt = svc.apd_fund_totals(y, m).get(fund)
                except ValueError:
                    tt = None
                if tt:
                    d["facts"] += [
                        ("Ποσό προς καταβολή", f"{tt['contrib']:.2f} €"),
                        ("Ασφαλισμένοι", str(tt["insured"])),
                    ]
                name = "e-ΕΦΚΑ" if fund == "efka" else "ΤΕΚΑ"
                d["steps"] = [
                    f"Θα καταβληθούν οι εισφορές {name} της περιόδου {per} "
                    f"(όλων των απασχολήσεων, μαζί με Δώρα/Επιδόματα του αρχείου)."
                ]
            else:
                # τακτικές + Δώρα/Επίδομα του μήνα (ίδιο με το αρχείο JL10)
                total = svc.fmy_total(y, m)
                d["facts"].append(("Ποσό", f"{total:.2f} €"))
                verb = (
                    "Θα υποβληθεί η δήλωση απόδοσης ΦΜΥ"
                    if t == "FMY_SUBMISSION"
                    else "Θα καταβληθεί ο παρακρατηθείς ΦΜΥ στην ΑΑΔΕ"
                )
                d["steps"] = [f"{verb} για την περίοδο {per}."]

    elif t == "RETRO_SCHEDULE":
        if ob.fulfilled:
            d["steps"] = [f"Υποβλήθηκε η απολογιστική δήλωση ωραρίου {per}."]
        else:
            d["rows"] = [
                {"Εργαζόμενος": emp_name(eid), "Ημέρα": day.strftime("%d/%m/%Y"),
                 "Δηλωμένο": old or "ρεπό", "Κάρτα": new or "ρεπό"}
                for eid, days in svc.retro_schedule_proposal(y, m).items()
                for day, (old, new) in sorted(days.items())
            ]
            d["steps"] = [
                f"Απολογιστικό σύστημα {per}: οι ημέρες όπου η κάρτα απέκλινε "
                "από το δηλωμένο (πέρα από ανοχή/ευέλικτη προσέλευση) δηλώνονται "
                "στο ΕΡΓΑΝΗ με τις πραγματικές ώρες, έως το τέλος του επόμενου μήνα.",
                "Τίποτα εκκρεμές (π.χ. υποβλήθηκε ήδη ως ημερήσιο πρόγραμμα) — "
                "η υποβολή από την οθόνη ΕΡΓΑΝΗ απλώς κλείνει την υποχρέωση."
                if not d["rows"] else
                f"Η υποβολή από την οθόνη ΕΡΓΑΝΗ στέλνει {len(d['rows'])} ημέρες "
                "σε ΕΝΑ batch και κλείνει την υποχρέωση.",
            ]

    elif t == "LEAVE_DECLARATION":
        if ob.fulfilled:
            d["steps"] = [f"Υποβλήθηκε η δήλωση αδειών {per} στο ΕΡΓΑΝΗ — οι "
                          "δηλωμένες άδειες είναι κλειδωμένες."]
        else:
            d["rows"] = [
                {"Εργαζόμενος": emp_name(eid), "Ημέρα": day.strftime("%d/%m/%Y"),
                 "Κωδικός ΕΡΓΑΝΗ": code}
                for eid, days in svc.leave_declaration_proposal(y, m).items()
                for day, (code, _y, _r) in sorted(days.items())
            ]
            d["steps"] = [
                f"Οι άδειες {per} δηλώνονται στο ΕΡΓΑΝΗ (WTOLeave) απολογιστικά, "
                "έως το τέλος του επόμενου μήνα — μόνο οι ημέρες προγράμματος.",
                "Μετά τη δήλωση η άδεια ΔΕΝ ακυρώνεται ούτε τροποποιείται.",
                f"Η υποβολή από την οθόνη ΕΡΓΑΝΗ στέλνει {len(d['rows'])} ημέρες "
                "σε ΜΙΑ κλήση και κλείνει την υποχρέωση." if d["rows"] else
                "Τίποτα αδήλωτο — η υποβολή απλώς κλείνει την υποχρέωση.",
            ]

    elif t == "WAGE_ADJUSTMENT":
        # ΑΙΤΙΑ: νέα ΓΣΣΕ ή/και ωρίμανση τριετίας — να ΜΗΝ εμφανίζεται πάντα
        # ως ΓΣΣΕ.
        gsse, matured = _wage_adjust_causes(y, m)
        causes = []
        if gsse:
            mw = config.minimum_wage_for(last_working_day_of_month(y, m))
            d["facts"] += [
                ("Νέος κατώτατος (ΓΣΣΕ)",
                 f"{mw.monthly:.2f} € / {mw.daily:.2f} €/ημέρα"),
                ("Ισχύς από", mw.valid_from.strftime("%d/%m/%Y")),
            ]
            causes.append("νέα ΓΣΣΕ/ΚΥΑ")
        if matured:
            d["facts"].append(
                ("Ωρίμανση τριετίας", ", ".join(matured))
            )
            causes.append("ωρίμανση τριετίας")
        cause_txt = " και ".join(causes) or "μεταβολή κατώτατου ορίου"
        if ob.fulfilled:
            d["steps"] = [
                f"Έγινε ο έλεγχος κατώτατου της περιόδου {per} — τυχόν "
                "προσαρμογές καταγράφηκαν ως ContractChanged (βλ. καρτέλες "
                "απασχολήσεων / Event log).",
            ]
        else:
            violations = svc.wage_violations(y, m)
            d["steps"] = [
                f"Προσαρμογή στον κατώτατο μισθό ({cause_txt}) μέσα στην "
                f"περίοδο {per} — έλεγχος όλων των ενεργών συμβάσεων.",
                *(violations or ["Καμία σύμβαση κάτω από το όριο σήμερα — "
                                 "η ενεργοποίηση απλώς καταγράφει τον έλεγχο."]),
            ]
            if violations:
                d["steps"].append(
                    "Η ενεργοποίηση προσαρμόζει τις συμβάσεις κατά την πολιτική "
                    "«Διάφορα» (συμψηφισμός/διατήρηση) — ContractChanged + "
                    "Δήλωση Μεταβολής ΕΡΓΑΝΗ."
                )
    return d


def _wage_adjust_causes(y: int, m: int):
    """(gsse: bool, matured: [ονόματα]) — γιατί χρειάζεται WAGE_ADJUSTMENT
    στην περίοδο: νέα ΓΣΣΕ ή/και ωρίμανση τριετίας ενεργών εργαζομένων."""
    period_end = last_working_day_of_month(y, m)
    prev_y, prev_m = (y, m - 1) if m > 1 else (y - 1, 12)
    prev_end = last_working_day_of_month(prev_y, prev_m)
    gsse = any(
        w.valid_from.year == y and w.valid_from.month == m
        for w in config.minimum_wage_entries()
    )
    matured = []
    for e in proj.employments.values():
        if not e.hire_submitted or not e.active or e.hire_date is None or e.hire_date > period_end:
            continue
        r = config.allowance_rules_for(period_end)
        tri_max = (r.daily_max_triennia if e.pay_type == "daily"
                   else r.monthly_max_triennia)
        ref_old = max(prev_end, e.hire_date)
        if config.triennia_count(e.total_service_years(period_end), tri_max) > \
                config.triennia_count(e.total_service_years(ref_old), tri_max):
            matured.append(e.full_name)
    return gsse, matured


# ---------------------------------------------------- Ζωντανή εικόνα ημέρας
# Δεδομένα του widget «Ζωντανά» (`/live`): ΜΙΑ γραμμή ανά ΕΝΕΡΓΗ απασχόληση,
# με το ΠΡΟΓΡΑΜΜΑ της ημέρας (μπάρα) και τα ΧΤΥΠΗΜΑΤΑ ΚΑΡΤΑΣ (βάρδιες της
# Κάρτας Εργασίας) — ώστε να συγκρίνεται με μια ματιά το δηλωμένο πρόγραμμα
# με την πραγματική προσέλευση/αποχώρηση. Read-only, κανένα event.

# Ανοχή (λεπτά) στη σύγκριση χτυπήματος με το προγραμματισμένο όριο: μέσα σε
# αυτήν το χτύπημα θεωρείται «στην ώρα του».
PUNCH_TOLERANCE_MIN = 5

# Έως πόσες ημέρες παραθύρου κρατάμε ΠΛΗΡΗ λεπτομέρεια (χτυπήματα κάρτας,
# ωριαίος χάρακας). Πάνω από αυτό η προβολή είναι «εβδομάδα/μήνας».
DETAIL_MAX_DAYS = 15
# Πάνω από αυτό (προβολή ΕΤΟΥΣ) οι ημέρες δεν χωρούν καν ως μπάρες — μία
# ημέρα είναι ~2px — οπότε η γραμμή γίνεται ΕΒΔΟΜΑΔΙΑΙΑ ΣΥΝΟΨΗ.
AGG_MIN_DAYS = 95


def _hhmm(minute) -> str:
    """Λεπτά από τα μεσάνυχτα -> «ΩΩ:ΛΛ» (24ωρη μορφή)."""
    m = int(minute) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def _punch_bounds(intervals, plan) -> list:
    """ΠΟΙΟ όριο του προγράμματος συγκρίνεται με κάθε βάρδια της κάρτας:
    [(όριο προσέλευσης, όριο αποχώρησης)] — `None` = δεν συγκρίνεται.

    Ίδιο πλήθος βαρδιών με τμήματα ωραρίου -> αντιστοιχούν ΕΝΑ ΠΡΟΣ ΕΝΑ.
    Αλλιώς συγκρίνονται ΜΟΝΟ η πρώτη προσέλευση και η τελευταία αποχώρηση
    της ημέρας· τα ενδιάμεσα χτυπήματα (διαλείμματα) δεν αντιστοιχούν σε
    όριο και ΔΕΝ χαρακτηρίζονται απόκλιση — αλλιώς μια έξοδος για διάλειμμα
    θα εμφανιζόταν ως «πρόωρη αποχώρηση 4 ωρών»."""
    if not intervals or not plan:
        return [(None, None)] * len(intervals)
    if len(intervals) == len(plan):
        return [(seg["start"], seg["end"]) for seg in plan]
    if len(intervals) == 1:
        return [(plan[0]["start"], plan[-1]["end"])]
    bounds = [(None, None)] * len(intervals)
    bounds[0] = (plan[0]["start"], None)
    bounds[-1] = (None, plan[-1]["end"])
    return bounds


def _punch_deviation(minute: int, kind: str, bound) -> dict:
    """Απόκλιση ενός χτυπήματος από το όριο `bound` του προγράμματος.

    `kind`: "in" (προσέλευση) ή "out" (αποχώρηση). Επιστρέφει
    {"delta", "tone"} — `delta` σε λεπτά (θετικό = αργότερα από το
    πρόγραμμα), `tone`:
      ok    εντός ανοχής ή χωρίς όριο να συγκριθεί
      warn  απόκλιση υπέρ του εργαζομένου (αργοπορία / πρόωρη αποχώρηση)
      info  απόκλιση υπέρ του εργοδότη (νωρίτερα / αργότερα από το ωράριο)
    """
    if bound is None:
        return {"delta": None, "tone": "ok"}
    delta = minute - bound
    if abs(delta) <= PUNCH_TOLERANCE_MIN:
        return {"delta": delta, "tone": "ok"}
    late = delta > 0 if kind == "in" else delta < 0
    return {"delta": delta, "tone": "warn" if late else "info"}


# Μνήμη αναλυμένων ωραρίων: το ΙΔΙΟ string ωραρίου («09:00-17:00») εμφανίζεται
# σε εκατοντάδες ημέρες. Στην προβολή έτους το `live_board` διατρέχει 548
# ημέρες × εργαζόμενους — χωρίς αυτό, χιλιάδες περιττές αναλύσεις.
# ⚠️ Οι επιστρεφόμενες λίστες είναι ΚΟΙΝΕΣ: μόνο ανάγνωση, ποτέ μετάλλαξη.
_SEG_CACHE: dict[str, list] = {}


def _day_segments(e, d: date) -> list:
    """Τα τμήματα ωραρίου της ημέρας `d` (κενή λίστα σε ρεπό/λάθος μορφή)."""
    text = e.day_schedule(d)
    if not text:
        return []
    segs = _SEG_CACHE.get(text)
    if segs is None:
        try:
            segs = constraints.parse_day_schedule(text)
        except ValueError:
            segs = []
        if len(_SEG_CACHE) < 4000:
            _SEG_CACHE[text] = segs
    return segs


def _leave_on(e, d: date):
    """(ετικέτα, κωδικός τύπου, leave_id) της άδειας που καλύπτει την ημέρα `d`, ή
    (None, None, None). Ο ΚΩΔΙΚΟΣ δίνει το χρώμα: το widget χρωματίζει την άδεια
    ανά ΤΥΠΟ (παλέτα `lv-*` του app.css) — το
    πορτοκαλί μένει αποκλειστικά για την ασυμφωνία με την κάρτα."""
    for lv in e.active_leaves():
        if lv.date_from <= d <= lv.date_to:
            code = getattr(lv.leave_type, "value", lv.leave_type)
            return LEAVE_LABELS.get(code, "Άδεια"), code, lv.leave_id
    return None, None, None


def _day_shifts(e, d: date) -> list:
    """Οι ΒΑΡΔΙΕΣ της ημέρας σε λεπτά της ημέρας, από την ΨΗΦΙΑΚΗ ΚΑΡΤΑ
    (τη μόνη πηγή παρουσιών) — δείχνει και τις ΑΝΟΙΧΤΕΣ βάρδιες.

    Επιστρέφει [{"s", "e", "open", "orphan"}] — `e` = None στην ανοιχτή."""
    return e.card_shifts(d)


def _punch_note(dev, plan, kind, shift) -> str:
    """Η επεξήγηση ενός χτυπήματος (tooltip) — τι σημαίνει η απόκλισή του."""
    if shift["open"] and kind == "in":
        return "χωρίς έξοδο ακόμη"
    if shift["orphan"]:
        return "χωρίς είσοδο — ελλιπής καταγραφή"
    if dev["delta"] is None:
        return "ενδιάμεσο χτύπημα" if plan else "χωρίς πρόγραμμα να συγκριθεί"
    if dev["tone"] == "ok":
        return "στην ώρα του"
    return "{}΄ {} από το πρόγραμμα".format(
        abs(dev["delta"]), "αργότερα" if dev["delta"] > 0 else "νωρίτερα",
    )


def _day_punches(e, d: date, plan, off: int) -> tuple:
    """(διαστήματα παρουσίας, χτυπήματα) της ημέρας `d`, σε ΑΠΟΛΥΤΑ λεπτά.

    Οι βάρδιες που περνούν τα μεσάνυχτα συνεχίζουν φυσιολογικά στην επόμενη
    ημέρα του παραθύρου — δεν κόβονται.

    ⚠️ **ΑΝΟΙΧΤΗ ΒΑΡΔΙΑ** (είσοδος χωρίς έξοδο): η μπάρα φεύγει με μηδενικό
    πλάτος και ΜΕΓΑΛΩΝΕΙ στον client μέχρι την τρέχουσα ώρα (`open`), με
    φράγμα τα μεσάνυχτα της ημέρας της (`emax`) — αλλιώς μια ξεχασμένη έξοδος
    του περασμένου μήνα θα τραβούσε μπάρα ως το «τώρα».
    """
    shifts = _day_shifts(e, d)
    # Για την αντιστοίχιση με το πρόγραμμα μετρούν τα ΔΙΑΣΤΗΜΑΤΑ (οι ορφανές
    # εξόδους/ανοιχτές βάρδιες συγκρίνονται με ό,τι όριο τους αναλογεί)
    ivs = [{"s": s["s"] if s["s"] is not None else s["e"],
            "e": s["e"] if s["e"] is not None else s["s"]} for s in shifts]
    punches, actual = [], []
    for sh, iv, bounds in zip(shifts, ivs, _punch_bounds(ivs, plan)):
        marks = []
        if sh["s"] is not None:
            marks.append(("in", sh["s"], bounds[0]))
        if sh["e"] is not None and not sh["open"]:
            marks.append(("out", sh["e"], bounds[1]))
        for kind, raw, bound in marks:
            dev = _punch_deviation(raw, kind, bound)
            if sh["open"] or sh["orphan"]:
                dev = {"delta": dev["delta"], "tone": "open"}
            punches.append({
                "m": off + raw, "kind": kind, **dev,
                "label": "{} {} — {}".format(
                    "Είσοδος" if kind == "in" else "Έξοδος", _hhmm(raw),
                    _punch_note(dev, plan, kind, sh),
                ),
            })
        if sh["orphan"]:
            continue                      # έξοδος χωρίς είσοδο: μόνο το σημείο
        if sh["open"]:
            actual.append({"s": off + sh["s"], "e": off + sh["s"], "open": True,
                           "emax": off + 24 * 60})
        else:
            actual.append({"s": off + sh["s"], "e": off + sh["e"],
                           "open": False, "emax": 0,
                           "text": f"{_hhmm(sh['s'])}-{_hhmm(sh['e'])}",
                           "h": round((sh["e"] - sh["s"]) / 60, 2)})
    return actual, punches


def _merge_runs(offsets: list) -> list:
    """Διαδοχικές ημέρες (λεπτά έναρξης) -> συνεχόμενα διαστήματα {s, e}."""
    runs = []
    for off in offsets:
        if runs and runs[-1]["e"] == off:
            runs[-1]["e"] = off + 24 * 60
        else:
            runs.append({"s": off, "e": off + 24 * 60})
    return runs


def _year_marks(e, dates, base: date):
    """ΠΡΟΒΟΛΗ ΕΤΟΥΣ: **κάθετη γραμμή ανά ημέρα εργασίας** (μια ημέρα είναι
    ~2px — μπάρα με ωράριο δεν έχει νόημα) και **ΜΠΑΡΑ ανά ΣΥΝΕΧΟΜΕΝΟ
    ΔΙΑΣΤΗΜΑ ΑΔΕΙΑΣ**, στο χρώμα του τύπου της.

    ⚠️ Οι γραμμές ΔΕΝ είναι ένα στοιχείο ανά ημέρα: κάθε γραμμή θα
    στρογγυλοποιούνταν ΞΕΧΩΡΙΣΤΑ σε ακέραιο pixel, οπότε άλλες κολλούσαν
    (συμπαγής μάζα) και άλλες χώριζαν. Αντ' αυτού κάθε **ΣΕΡΙ** διαδοχικών
    ημερών βγαίνει ως ΕΝΑ στοιχείο με **επαναλαμβανόμενη ρίγα περιόδου μιας
    ημέρας** — ο browser σχεδιάζει όλη τη σειρά με ενιαία υποδιαίρεση, άρα η
    απόσταση είναι ομοιόμορφη (και τα στοιχεία λιγότερα).

    Επιστρέφει (combs, att, bands):
      combs — διαστήματα ημερών ΠΡΟΓΡΑΜΜΑΤΟΣ {s, e}
      att   — διαστήματα ημερών με ΠΡΑΓΜΑΤΙΚΗ ΠΑΡΟΥΣΙΑ (πάνω από τα combs)
      bands — {"s", "e", "type", "lid", "label"} ανά διάστημα άδειας
    Η ημέρα με άδεια ΔΕΝ παίρνει γραμμή: μιλάει η μπάρα."""
    work_days, att_days, bands = [], [], []
    run = None

    def flush():
        nonlocal run
        if run:
            days = (run["e"] - run["s"]) // (24 * 60)
            bands.append({
                "s": run["s"], "e": run["e"], "type": run["type"], "lid": run["lid"],
                "label": "{} {} – {} ({} ημέρες)".format(
                    run["label"], run["from"].strftime("%d/%m/%Y"),
                    run["to"].strftime("%d/%m/%Y"), days),
            })
            run = None

    for d in dates:
        off = (d - base).days * 24 * 60
        if not e.active_on(d):
            flush()
            continue
        label, code, lid = _leave_on(e, d)
        if label:
            if run and run["lid"] == lid and run["e"] == off:
                run["e"] = off + 24 * 60          # συνέχεια του διαστήματος
                run["to"] = d
            else:
                flush()
                run = {"s": off, "e": off + 24 * 60, "type": code, "lid": lid,
                       "label": label, "from": d, "to": d}
            continue
        flush()
        if _day_segments(e, d):
            work_days.append(off)
            # «Πραγματική παρουσία» = καταγεγραμμένες ώρες Ή διελεύσεις κάρτας
            if e.attendance.get(d) or e.punches_on(d):
                att_days.append(off)
    flush()
    return _merge_runs(work_days), _merge_runs(att_days), bands


def balance_tone(balance: float, accrued: float) -> str:
    """Τόνος υπολοίπου κανονικής άδειας — κοινός για Άδειες, Πρόγραμμα,
    Ζωντανά."""
    if balance < -0.005:
        return "danger"
    if balance <= max(1.0, accrued * 0.2):
        return "warn"
    return "good"


def _accrued_on(e, d: date) -> int:
    """Σωρευμένο δικαίωμα κανονικής άδειας ΣΕ ΕΠΙΠΕΔΟ ΗΜΕΡΑΣ.

    Ο κανόνας (`svc.annual_leave_accrued`) δίνει ακέραιη μερίδα ανά ΜΗΝΑ —
    από την 1η του μήνα ολόκληρη (πρόσληψη 1/1 -> «2» ήδη την 1/1). Εδώ η
    μερίδα του μήνα μοιράζεται αναλογικά στις ημέρες του (από την πρόσληψη,
    αν έγινε μέσα στον μήνα), ώστε στο ΤΕΛΟΣ του μήνα η τιμή να συμπίπτει
    ακριβώς με τον κανόνα. Όταν το πλήρες δικαίωμα ισχύει από 1/1 (3ο έτος
    και μετά), δεν υπάρχει τίποτα να μοιραστεί."""
    eid = e.employment_id
    full_now = svc.annual_leave_accrued(eid, date(d.year, 1, 1))
    if full_now >= svc.annual_leave_entitlement(eid, d.year):
        return full_now
    month_end = date(d.year, d.month, monthrange(d.year, d.month)[1])
    start = date(d.year, d.month, 1)
    prev = (svc.annual_leave_accrued(eid, start - timedelta(days=1))
            if d.month > 1 else 0)          # νέο έτος: η σώρευση ξεκινά από 0
    if e.hire_date and e.hire_date > start:
        start = e.hire_date
    if d < start:
        return prev
    share = svc.annual_leave_accrued(eid, month_end) - prev
    # Μόνο ΩΡΙΜΕΣ ΟΛΟΚΛΗΡΕΣ ημέρες (1,9 -> 1): κλάσμα ημέρας δεν παίρνεται
    return prev + share * ((d - start).days + 1) // ((month_end - start).days + 1)


def _leave_balance(e, dates, base: date, agg: bool) -> list:
    """ΥΠΟΛΟΙΠΟ ΚΑΝΟΝΙΚΗΣ ΑΔΕΙΑΣ (δικαίωμα − ληφθείσες) στο ΤΕΛΟΣ κάθε
    ημέρας του παραθύρου — στην προβολή έτους στο τέλος κάθε μήνα (μια
    ημέρα εκεί είναι ~2px). Ίδιος υπολογισμός με το Πρόγραμμα/τις Άδειες.
    [{"s", "e", "date", "acc", "taken", "bal", "tone"}] σε λεπτά από τη base."""
    if agg:
        periods, cur, end = [], dates[0], dates[-1]
        while cur <= end:
            last = min(date(cur.year, cur.month,
                            monthrange(cur.year, cur.month)[1]), end)
            periods.append((cur, last))
            cur = last + timedelta(days=1)
    else:
        periods = [(d, d) for d in dates]
    out = []
    for a, b in periods:
        if not (e.active_on(a) or e.active_on(b)):
            continue
        acc = _accrued_on(e, b)
        taken = svc.annual_leave_taken_until(e.employment_id, b)
        bal = round(acc - taken, 2)
        out.append({"s": (a - base).days * 24 * 60,
                    "e": ((b - base).days + 1) * 24 * 60,
                    "date": b, "acc": acc, "taken": taken, "bal": bal,
                    "tone": balance_tone(bal, acc)})
    return out


def _month_marks(base: date, end: date, day: date) -> list:
    """Σημάνσεις ΜΗΝΑ για τον χάρακα (προβολή έτους) — 365 ετικέτες ημερών
    θα πατούσαν η μία πάνω στην άλλη."""
    marks = []
    cur = date(base.year, base.month, 1)
    while cur <= end:
        nxt = (date(cur.year + 1, 1, 1) if cur.month == 12
               else date(cur.year, cur.month + 1, 1))
        first = max(cur, base)
        last = min(nxt, end + timedelta(days=1))
        marks.append({
            "off": (first - base).days * 24 * 60,
            "span": (last - first).days * 24 * 60,
            "today": cur.year == day.year and cur.month == day.month,
            # ζυγός/μονός ΜΗΝΑΣ (όχι θέση στο παράθυρο): η απόχρωση δεν
            # «αναβοσβήνει» όταν το live-board.js ξαναζητά με νέο base
            "alt": cur.month % 2 == 0,
            "full": f"{MONTHS_SHORT[cur.month]} {cur.year}",
            "mid": f"{MONTHS_SHORT[cur.month]} {cur.year}",
            "short": MONTHS_SHORT[cur.month],
        })
        cur = nxt
    return marks


def live_board(day: date, base: date | None = None, days: int = 3) -> dict:
    """Η εικόνα ενός ΠΑΡΑΘΥΡΟΥ ΗΜΕΡΩΝ για όλες τις ενεργές απασχολήσεις.

    ⚠️ **ΟΛΕΣ οι θέσεις είναι ΑΠΟΛΥΤΑ ΛΕΠΤΑ από τα μεσάνυχτα της `base`** —
    όχι από τα μεσάνυχτα της ημέρας. Έτσι η προβολή κυλά ΣΥΝΕΧΟΜΕΝΑ από τη
    μια ημερομηνία στην άλλη (η νυχτερινή βάρδια 22:00-06:00 είναι ΜΙΑ
    μπάρα που περνά τα μεσάνυχτα) και ο client μετακινείται ελεύθερα μέσα
    στο παράθυρο· όταν πλησιάσει την άκρη ζητά νέο παράθυρο (`base`/`days`).

    `day` = η ημέρα ΑΝΑΦΟΡΑΣ (το «σήμερα» του UI): από αυτήν βγαίνουν τα
    στοιχεία της στήλης ονομάτων (ώρες προγράμματος, παρουσία, άδεια,
    κατάσταση ΕΡΓΑΝΗ) και η σειρά των γραμμών.
    """
    days = max(1, min(int(days or 3), 600))
    base = base or (day - timedelta(days=1))
    dates = [base + timedelta(days=i) for i in range(days)]
    end = dates[-1]
    span = days * 24 * 60
    # ΒΑΘΟΣ ΛΕΠΤΟΜΕΡΕΙΑΣ: στα φαρδιά παράθυρα (εβδομάδα/μήνας) ένα χτύπημα
    # κάρτας είναι κλάσμα του pixel — δεν φαίνεται, μόνο κοστίζει (χιλιάδες
    # στοιχεία στο DOM). Οι μπάρες προγράμματος/παρουσίας μένουν πάντα.
    detail = days <= DETAIL_MAX_DAYS
    agg = days > AGG_MIN_DAYS          # προβολή έτους -> εβδομαδιαία σύνοψη
    rows = []
    for e in proj.employments.values():
        # Μέσα και όσοι είναι ενεργοί ΜΟΝΟ την ημέρα αναφοράς: η γραμμή τους
        # (στήλη ονομάτων) αφορά το «σήμερα», όπου κι αν κοιτά ο χρήστης.
        if e.lifecycle != "active" or not (
            e.active_on(day) or any(e.active_on(d) for d in dates)
        ):
            continue
        segs, actual, punches, bands, combs = [], [], [], [], []
        if agg:
            combs, actual, bands = _year_marks(e, dates, base)
        for i, d in enumerate(dates if not agg else ()):
            off = i * 24 * 60
            if not e.active_on(d):
                continue
            plan = _day_segments(e, d)
            a, p = _day_punches(e, d, plan, off)
            # ΑΡΓΙΑ χωρίς παρουσία: κλειστά — η βάρδια δεν αναμένεται
            closed = not a and e.holiday_off(d)
            for seg in plan:
                segs.append({"s": off + seg["start"],
                             "e": min(off + seg["end"], span),
                             "tele": seg["telework"], "text": seg["text"],
                             "h": round((seg["end"] - seg["start"]) / 60, 2),
                             "cont": False, "hol": closed})
            actual += [{**iv, "e": min(iv["e"], span)} for iv in a]
            if detail:
                punches += [pu for pu in p if pu["m"] <= span]
            band_label, band_type, lid = _leave_on(e, d)
            if band_label and bands and bands[-1]["e"] == off \
                    and bands[-1]["lid"] == lid:
                # ΣΥΝΕΧΟΜΕΝΗ μπάρα αρχή→τέλος, όχι ένα κουτάκι ανά ημέρα
                bands[-1]["e"] = off + 24 * 60
            elif band_label:
                bands.append({"s": off, "e": off + 24 * 60,
                              "label": band_label, "type": band_type, "lid": lid})
        # Ουρά νυχτερινής βάρδιας της ΠΡΟΗΓΟΥΜΕΝΗΣ του παραθύρου. Χωρίς
        # αυτήν, όταν το νέο παράθυρο άρχιζε την ημέρα της εξόδου, η παρουσία
        # 22:00→06:00 κοβόταν στα μεσάνυχτα και χανόταν και το χτύπημα 06:00.
        prev = base - timedelta(days=1)
        if not agg and e.active_on(prev):
            prev_plan = _day_segments(e, prev)
            prev_actual, prev_punches = _day_punches(
                e, prev, prev_plan, -24 * 60,
            )
            actual = [
                {**iv, "s": max(iv["s"], 0), "e": min(iv["e"], span)}
                for iv in prev_actual
                if not iv["open"] and iv["e"] > 0
            ] + actual
            if detail:
                punches = [
                    p for p in prev_punches if 0 <= p["m"] <= span
                ] + punches
            for seg in _day_segments(e, prev):
                if seg["end"] > 24 * 60:
                    segs.insert(0, {"s": 0, "e": seg["end"] - 24 * 60,
                                    "tele": seg["telework"],
                                    "text": seg["text"], "cont": True,
                                    "hol": False,
                                    "h": round((seg["end"] - 24 * 60) / 60, 2)})
        # --- Στοιχεία της ΗΜΕΡΑΣ ΑΝΑΦΟΡΑΣ (στήλη ονομάτων & ταξινόμηση).
        # ⚠️ Υπολογίζονται από την ΙΔΙΑ την ημέρα αναφοράς, ΟΧΙ από το
        # παράθυρο: όταν ο χρήστης μετακινηθεί σε άλλες ημερομηνίες, η στήλη
        # ονομάτων πρέπει να συνεχίζει να λέει τι ισχύει ΣΗΜΕΡΑ.
        ref_on = e.active_on(day)
        ref_segs = _day_segments(e, day) if ref_on else []
        ref_prev_segs = (
            [s for s in _day_segments(e, day - timedelta(days=1))
             if s["end"] > 24 * 60]
            if ref_on and e.active_on(day - timedelta(days=1)) else []
        )
        ref_status_segs = [
            {**s, "start": 0, "end": s["end"] - 24 * 60}
            for s in ref_prev_segs
        ] + ref_segs
        ref_leave, ref_leave_type, _ = _leave_on(e, day) if ref_on else (None, None, None)
        # ⚠️ Η ΚΑΤΑΣΤΑΣΗ «ΤΩΡΑ» (κουκκίδα, σύνολα κεφαλίδας) ΔΕΝ βγαίνει από
        # τις μπάρες του παραθύρου: σε προβολή έτους αυτές είναι εβδομαδιαία
        # ΣΥΝΟΛΑ (μια «μπάρα 3 ημερών» δεν είναι χρονικό διάστημα) και σε
        # μακρινή ημερομηνία δεν περιέχουν καν το σήμερα. Δίνουμε χωριστά το
        # πρόγραμμα/παρουσία της ΗΜΕΡΑΣ ΑΝΑΦΟΡΑΣ σε λεπτά ΤΗΣ ΗΜΕΡΑΣ.
        # ⚠️ Η ΑΝΟΙΧΤΗ ΒΑΡΔΙΑ (μπήκε, δεν έχει βγει) δεν έχει λήξη: δίνεται με
        # λήξη «πολύ αργότερα», ώστε ο έλεγχος «έχει χτυπήσει;» του client να
        # τη βλέπει ως ΕΝ ΕΞΕΛΙΞΕΙ — αλλιώς ο εργαζόμενος που δουλεύει τώρα θα
        # εμφανιζόταν ως ασυμφωνία με την κάρτα.
        ref_sh = _day_shifts(e, day) if ref_on else []
        if ref_on and e.active_on(day - timedelta(days=1)):
            ref_sh = [
                {**s, "s": 0, "e": s["e"] - 24 * 60}
                for s in _day_shifts(e, day - timedelta(days=1))
                if s["e"] is not None and s["e"] > 24 * 60
            ] + ref_sh
        ref_iv = [{"s": s["s"], "e": (s["e"] if s["e"] is not None
                                      else 2 * 24 * 60)}
                  for s in ref_sh if s["s"] is not None]
        # ΑΡΓΙΑ σήμερα χωρίς παρουσία: η επιχείρηση είναι κλειστή — το
        # πρόγραμμα της ημέρας δεν αναμένεται, άρα ΟΥΤΕ «ασυμφωνία με κάρτα»
        # (μένει μόνο η ουρά νυχτερινής βάρδιας της χθεσινής)
        ref_hol = ref_on and not ref_iv and e.holiday_off(day)
        if ref_hol:
            ref_status_segs = ref_status_segs[:len(ref_prev_segs)]
        rows.append({
            "eid": e.employment_id,
            "name": e.full_name,
            "specialty": (e.insurance_profile.specialty_name
                          if e.insurance_profile else ""),
            "segs": segs,
            "actual": actual,
            "punches": punches,
            "bands": bands,
            # Προβολή έτους: σερί ημερών προγράμματος (το `actual` κρατά τα
            # σερί ημερών με πραγματική παρουσία)
            "combs": combs,
            # Υπόλοιπο κανονικής άδειας: λωρίδα στο κάτω μέρος της γραμμής
            "accr": _leave_balance(e, dates, base, agg),
            "leave": ref_leave,
            "hol": ref_hol,
            "leave_type": ref_leave_type,
            "hours": round(
                sum(s["end"] - s["start"] for s in ref_segs) / 60, 2),
            "worked": round(e.attendance.get(day, 0) or 0, 2) if ref_on else 0,
            # Κατάσταση δήλωσης της ημέρας αναφοράς στο ΕΡΓΑΝΗ (§5.7α)
            "declared": e.day_status(day) if ref_on else "off",
            "start": ref_segs[0]["start"] if ref_segs else None,
            # πρόγραμμα & παρουσία της ημέρας αναφοράς, σε λεπτά ΤΗΣ ΗΜΕΡΑΣ
            "ref": {
                "segs": [s["start"] for s in ref_status_segs],
                "ends": [s["end"] for s in ref_status_segs],
                "tele": [int(s["telework"]) for s in ref_status_segs],
                "acts": [iv["s"] for iv in ref_iv],
                "acte": [iv["e"] for iv in ref_iv],
                # πλήθος ΧΤΥΠΗΜΑΤΩΝ της ημέρας (η κάρτα τα ξέρει ακριβώς· από
                # βάρδιες φύλλου τεκμαίρονται δύο ανά βάρδια)
                "punches": (len(e.punches_on(day)) if e.card_shifts(day)
                            else 2 * len(ref_iv)),
            },
        })
    # Πρώτα όσοι έχουν πρόγραμμα την ημέρα αναφοράς (κατά ώρα έναρξης)
    rows.sort(key=lambda r: (r["start"] is None, r["start"] or 0, r["name"]))
    # Επίσημες ΑΡΓΙΕΣ του παραθύρου (λεπτά από `base`): στήλη σε όλες τις
    # γραμμές + σήμανση στον χάρακα
    hol = {}
    for y in range(base.year, end.year + 1):
        hol.update(holiday_names(y))
    hols = [i * 24 * 60 for i, d in enumerate(dates) if d in hol]
    # ΚΛΕΙΣΜΕΝΗ ΜΙΣΘΟΔΟΣΙΑ: λεπτά [0, closed) του παραθύρου είναι κλειδωμένα
    closed_end = svc.closed_period_end()
    closed = (min(((closed_end - base).days + 1) * 24 * 60, span)
              if closed_end and closed_end >= base else 0)
    # Σημάνσεις χάρακα: ΗΜΕΡΕΣ κανονικά, ΜΗΝΕΣ στην προβολή έτους
    if agg:
        marks = _month_marks(base, end, day)
    else:
        marks = [
            {"off": i * 24 * 60, "span": 24 * 60,
             "today": d == day, "alt": d.weekday() >= 5, "hol": hol.get(d),
             # τρεις εκδοχές ετικέτας — ποια φαίνεται το κρίνει το εύρος zoom
             "full": f"{DAY_NAMES[d.weekday()]} {d.strftime('%d/%m/%Y')}",
             "mid": f"{DAY_NAMES[d.weekday()]} {d.strftime('%d/%m')}",
             "short": str(d.day)}
            for i, d in enumerate(dates)
        ]
    # Επάνω επίπεδο χάρακα στην προβολή μήνα: ο ΜΗΝΑΣ πάνω από τις ημερομηνίες
    months = [] if agg else _month_marks(base, end, day)
    return {"day": day, "base": base, "days": days, "span": span,
            "marks": marks, "months": months, "hols": hols, "closed": closed,
            "hours": span // 60, "detail": detail,
            "agg": agg, "rows": rows}


def _seg_hhmm(seg) -> str:
    return f"{_hhmm(seg['start'])}-{_hhmm(seg['end'])}"


def _in_time(minutes: int) -> str:
    """Λεπτά -> «45΄» / «1 ώρα» / «3 ώρες» (στρογγυλεμένο στην ώρα)."""
    if minutes < 60:
        return f"{minutes}΄"
    h = round(minutes / 60)
    return "1 ώρα" if h == 1 else f"{h} ώρες"


def _shift_day_label(d: date, day: date) -> str:
    if d == day:
        return "Σήμερα"
    if d == day + timedelta(days=1):
        return "Αύριο"
    return f"{DAY_NAMES[d.weekday()]} {d.strftime('%d/%m')}"


def staff_now(day: date, now_min: int, horizon: int = 31) -> list:
    """Ενεργοί εργαζόμενοι της ημέρας `day`: κατάσταση ΤΩΡΑ (λεπτό `now_min`)
    και επόμενη βάρδια — για την Επισκόπηση.

    Κατάσταση από το ΠΡΟΓΡΑΜΜΑ (όχι την κάρτα — αυτή τη δείχνουν τα
    «Ζωντανά»), με προτεραιότητα: άδεια · εργάζεται (μέσα σε τμήμα, μαζί με
    την ουρά της χθεσινής νυχτερινής) · εργασία σε Χ (τμήμα αργότερα σήμερα)
    · εργάστηκε (τμήμα που τελείωσε σήμερα) · ανάπαυση.
    Επόμενη βάρδια = το πρώτο τμήμα που ΞΕΚΙΝΑ μετά το τώρα, σε ημέρα χωρίς
    άδεια/αργία, έως `horizon` ημέρες μπροστά."""
    yday = day - timedelta(days=1)
    rows = []
    for e in proj.employments.values():
        if e.lifecycle != "active" or not e.active_on(day):
            continue
        leave, leave_type, _ = _leave_on(e, day)
        # Τμήματα που ΑΓΓΙΖΟΥΝ το σήμερα, σε λεπτά της ημέρας: η ουρά της
        # χθεσινής νυχτερινής (μετατοπισμένη −24ω) + τα σημερινά (όχι σε αργία)
        segs = []
        if e.active_on(yday) and not _leave_on(e, yday)[0]:
            segs += [{**s, "start": s["start"] - 24 * 60, "end": s["end"] - 24 * 60}
                     for s in _day_segments(e, yday) if s["end"] > 24 * 60]
        if not e.holiday_off(day):
            segs += _day_segments(e, day)
        on = next((s for s in segs if s["start"] <= now_min < s["end"]), None)
        later = next((s for s in segs if s["start"] > now_min), None)
        done = [s for s in segs if s["end"] <= now_min]
        if leave:
            state, text = "leave", LEAVE_NOW.get(leave_type, "Σε άδεια")
        elif on:
            state = "on"
            text = "{} ({})".format("Τηλεεργασία" if on["telework"] else "Εργάζεται",
                                    _seg_hhmm(on))
        elif later:
            state, text = "soon", "Εργασία σε " + _in_time(later["start"] - now_min)
        elif done:
            state, text = "done", f"Εργάστηκε ({_seg_hhmm(done[-1])})"
        else:
            state, text = "rest", "Σε ανάπαυση"
        nxt, nxt_at = None, ""
        for i in range(horizon):
            d = day + timedelta(days=i)
            if not e.active_on(d) or _leave_on(e, d)[0] or e.holiday_off(d):
                continue
            seg = next((s for s in _day_segments(e, d)
                        if i or s["start"] > now_min), None)
            if seg:
                nxt = f"{_shift_day_label(d, day)} {_seg_hhmm(seg)}"
                nxt_at = f"{d.isoformat()}T{_hhmm(seg['start'])}"  # ταξινόμηση
                break
        rows.append({
            "eid": e.employment_id,
            "first": e.first_name,
            "last": e.last_name,
            "specialty": (e.insurance_profile.specialty_name
                          if e.insurance_profile else ""),
            "state": state, "state_text": text, "leave_type": leave_type,
            "next": nxt,
            "next_at": nxt_at,
            **leave_status(e, day.year),
        })
    rows.sort(key=lambda r: (r["last"], r["first"]))
    return rows


# --------------------------------------------------- ΨΗΦΙΑΚΗ ΚΑΡΤΑ ΕΡΓΑΣΙΑΣ
def card_day(day: date, eid: str = "") -> dict:
    """Η ΨΗΦΙΑΚΗ ΚΑΡΤΑ μιας ημέρας: οι ωμές διελεύσεις ανά εργαζόμενο και οι
    ΒΑΡΔΙΕΣ που παράγονται από το ζευγάρωμά τους.

    ΠΡΟΒΟΛΗ ΜΟΝΟ. Με `eid` περιορίζεται σε έναν εργαζόμενο (η φόρμα δείχνει
    τι έχει ήδη καταχωρηθεί για αυτόν, ώστε να μη γράφονται διπλά).
    """
    out = []
    for e in proj.employments.values():
        if eid and e.employment_id != eid:
            continue
        punches = e.punches_on(day)
        if not punches:
            continue
        shifts = e.card_shifts(day)
        out.append({
            "eid": e.employment_id,
            "name": e.full_name,
            "punches": [{
                "id": p["id"], "time": _hhmm(p["m"]), "kind": p["kind"],
                "source": p["source"], "note": p["note"],
                "blind": p.get("blind", False),
                "label": "Είσοδος" if p["kind"] == "in" else "Έξοδος",
            } for p in punches],
            "shifts": [{
                "text": ("{}-{}".format(_hhmm(s["s"]), _hhmm(s["e"]))
                         if not s["open"] and s["s"] is not None
                         else ("{}-…".format(_hhmm(s["s"])) if s["open"]
                               else "…-{}".format(_hhmm(s["e"])))),
                "open": s["open"], "orphan": s["orphan"],
                # η ΜΟΝΑΧΙΚΗ διέλευση — αυτή συμπληρώνεται από το πρόγραμμα
                "lone_id": s["in_id"] if s["open"] else s["out_id"],
                "h": (round((s["e"] - s["s"]) / 60, 2)
                      if s["s"] is not None and s["e"] is not None else None),
            } for s in shifts],
            "hours": round(cp.worked_minutes(shifts) / 60, 2),
            "orphans": e.schedule_completions(day.year, day.month),
        })
    out.sort(key=lambda r: r["name"])
    cap = config.work_hour_rules_for(day).orphan_month_cap
    return {"day": day, "rows": out, "orphan_cap": cap}


def card_pulse() -> int:
    """Ο «σφυγμός» της κάρτας: αυξάνει σε ΚΑΘΕ καταγραφή/ακύρωση διέλευσης.

    Τον ρωτά η οθόνη «Ζωντανά» κάθε λίγα δευτερόλεπτα και ξαναζητά τις γραμμές
    ΜΟΝΟ όταν άλλαξε — έτσι ένα χτύπημα φαίνεται αμέσως (και σε ΑΛΛΟΝ browser),
    χωρίς να κατεβαίνει όλο το παράθυρο κάθε φορά."""
    return proj.card_pulse
