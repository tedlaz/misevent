"""ΕΡΓΑΝΗ ΙΙ — η ΜΟΝΗ οθόνη αποστολής στο ΕΡΓΑΝΗ.

Εδώ εμφανίζεται και στέλνεται ΟΤΙΔΗΠΟΤΕ προορίζεται για το ΕΡΓΑΝΗ:
  · Αναγγελίες Πρόσληψης (WebE3N)
  · Πρόγραμμα εργασίας (ΟΧΕ εβδομαδιαίο / ημερήσιο — οι αλλαγές των
    «Ζωντανά»/καρτέλας συγκεντρώνονται εδώ)
  · Απολογιστικό (υποχρέωση RETRO_SCHEDULE — μένει στο χρονολόγιο ως
    προθεσμία, ενεργοποιείται ΜΟΝΟ από εδώ: `actions.ERGANI_ONLY`)
  · Δηλώσεις Μεταβολής (Ε4) των αλλαγών σύμβασης
  · Αναγγελίες Λήξης (WebE5N/WebE7N· η καταγγελία μόνο σημειώνεται)
και από κάτω ΕΝΑ ιστορικό (`_history`: κάθε `ErganiSubmissionRecorded`, με
το περιεχόμενο του προγράμματος από `ScheduleDeclaredToErgani`/`ScheduleDaysSubmitted`).

Οι εντολές που ΚΑΤΑΓΡΑΦΟΥΝ (αλλαγή σύμβασης, αποχώρηση, πρόγραμμα) ΔΕΝ
στέλνουν — αφήνουν εκκρεμότητα που εμφανίζεται εδώ.

ΔΥΟ ΤΡΟΠΟΙ ΥΠΟΒΟΛΗΣ, ανά εκκρεμότητα: «📤 Υποβολή» (REST API) ή «✍️
Χειροκίνητα» (ο χρήστης το πληκτρολογεί στο portal και καταχωρεί εδώ το
πρωτόκολλο). Η προεπισκόπηση (`_preview`, `ergani_preview`) και η χειροκίνητη
καταχώριση (`_mode` -> `ergani_manual`) τρέχουν τις ΙΔΙΕΣ εντολές με το API —
καμία λογική εντύπου δεν γράφεται εδώ."""

import base64
import json
from contextlib import nullcontext
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, render_template, request, url_for

from misevent.app.services import (
    DISMISSAL_REASON,
    ErganiPreview,
    ergani_manual,
    ergani_preview,
)
from misevent.integrations import ergani as ergani_mod
from misevent.rules import constraints

from .. import amka_pdf
from ..actions import trigger
from ..clock import today
from ..helpers import flash_to, run_command
from ..labels import DAY_NAMES, ERGANI_LABELS, ERGANI_ONLY, TERMINATION_REASONS
from ..queries import blocked_reason, dep_label, emp_name
from ..system import lock, proj, store, svc
from .work_schedules import _staff, _week_label

bp = Blueprint("ergani", __name__, url_prefix="/ergani")


# ------------------------------------------------------ Εκκρεμότητες
def _hires() -> list[dict]:
    """Απασχολήσεις χωρίς Αναγγελία Πρόσληψης (WebE3N)."""
    rows = [
        {"e": e, "missing": svc.missing_hire_documents(e.employment_id),
         "ready": e.insurance_profile is not None}
        for e in proj.employments.values() if e.lifecycle == "pending"
    ]
    return sorted(rows, key=lambda r: (r["e"].hire_date or date.min, r["e"].full_name))


def _retros() -> list[dict]:
    """Ανοιχτές υποχρεώσεις ΕΡΓΑΝΗ του χρονολογίου (απολογιστικό)."""
    obs = [o for o in proj.obligations.values()
           if o.obligation_type in ERGANI_ONLY and not o.fulfilled]
    return [{"ob": o, "label": dep_label(o), "reason": blocked_reason(o),
             "late": o.due_date < today()}
            for o in sorted(obs, key=lambda o: o.due_date)]


def _metavoles() -> list[dict]:
    """Μεταβολές σύμβασης που εκκρεμούν Δήλωση Μεταβολής (Ε4)."""
    rows = [{"e": e, "n": n, "t": t}
            for e in proj.employments.values()
            for n, t in svc.pending_metavoli(e.employment_id)]
    return sorted(rows, key=lambda r: (r["t"].effective_from or date.min,
                                       r["e"].full_name))


def _exits() -> list[dict]:
    """Αποχωρήσεις που εκκρεμούν Αναγγελία Λήξης."""
    reasons = dict(TERMINATION_REASONS)
    rows = []
    for e in proj.employments.values():
        if not svc.exit_pending(e.employment_id):
            continue
        reason = e.termination_reason
        rows.append({
            "e": e, "reason": reasons.get(reason, reason or "—"),
            # η καταγγελία ΔΕΝ υπάρχει στο API: μόνο σημείωση «υποβλήθηκε»
            "api": reason != DISMISSAL_REASON,
            "voluntary": reason in ("", "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ"),
            "has_pdf": bool(svc.person_document(e.person_id, "resignation")),
        })
    return sorted(rows, key=lambda r: (r["e"].termination_date, r["e"].full_name))


# --------------------------------------- Πρόγραμμα εργασίας (ΟΧΕ)
def _pending_rows() -> list[dict]:
    """ΜΙΑ γραμμή ανά (εργαζόμενος × εβδομάδα) με ημέρες που εκκρεμούν
    δήλωση (`pending`) ή επαναδήλωση (`modified`), χρονολογικά.

    Ορίζοντας: από την πρόσληψη (όχι πριν την 1/1 του προηγούμενου έτους)
    έως 4 εβδομάδες μπροστά ή την τελευταία ημερήσια μεταβολή — αλλιώς ένα
    μεταβλητό πρόγραμμα θα είχε άπειρες αδήλωτες εβδομάδες."""
    ref = today()
    floor = date(ref.year - 1, 1, 1)
    rows = []
    for e in _staff():
        lo = max(e.payroll_start or floor, floor)
        hi = max([ref + timedelta(days=28), *e.daily_schedule])
        if e.termination_date:
            hi = min(hi, e.termination_date)
        monday = lo - timedelta(days=lo.weekday())
        while monday <= hi:
            days = [d for d in svc.week_submittable_days(e.employment_id, monday)
                    if not _in_pending_weekly(e, d)]
            if days:
                rows.append({
                    "e": e,
                    "monday": monday,
                    "iso_week": monday.isocalendar().week,
                    "label": _week_label(monday),
                    "days": [
                        {"date": d, "status": e.day_status(d),
                         "old": e.day_baseline(d), "new": e.day_schedule(d)}
                        for d in days
                    ],
                })
            monday += timedelta(days=7)
    rows.sort(key=lambda r: (r["monday"], r["e"].full_name))
    return rows


def _in_pending_weekly(e, d: date) -> bool:
    """Η ημέρα καλύπτεται από ΕΚΚΡΕΜΕΣ εβδομαδιαίο (και δεν έχει δική της
    ημερήσια αλλαγή) -> δηλώνεται με το εβδομαδιαίο, όχι ως ημερήσια."""
    return d not in e.daily_schedule and any(
        start <= d and (end is None or d <= end) for start, _p, end in e.pending_weekly)


def _weekly_rows() -> list[dict]:
    """ΜΙΑ γραμμή ανά εκκρεμές εβδομαδιαίο (standing ΟΧΕ), με σειρά ισχύος."""
    rows = [
        {"e": e, "start": start, "end": end,
         "days": [(DAY_NAMES[i], pattern.get(code, ""))
                  for i, code in enumerate(constraints.DAY_CODES)]}
        for e in _staff() for start, pattern, end in e.pending_weekly
    ]
    return sorted(rows, key=lambda r: (r["start"], r["e"].full_name))


def _next_week_names() -> list[str]:
    """Όσοι δεν έχουν δηλωμένο πρόγραμμα για την ΕΠΟΜΕΝΗ εβδομάδα."""
    monday = today() - timedelta(days=today().weekday()) + timedelta(days=7)
    return [e.full_name for e in _staff()
            if e.active_days_in_week(monday) and e.week_status(monday) == "pending"]


def pending_count() -> int:
    """Πλήθος ΟΛΩΝ των εκκρεμών υποβολών ΕΡΓΑΝΗ — για την ειδοποίηση στις
    Υποχρεώσεις της Επισκόπησης."""
    return (len(_hires()) + len(_weekly_rows()) + len(_pending_rows())
            + len(_retros()) + len(_metavoles()) + len(_exits()))


# ------------------------------------------------------------ Ιστορικό
def _wto_afms(payload: str) -> list[str]:
    """Τα ΑΦΜ των εργαζομένων μιας δήλωσης WTO/WTOLeave (ίδια δομή `WTOS`,
    με σειρά, χωρίς διπλά)."""
    try:
        decl = json.loads(payload)["WTOS"]["WTO"]
    except (TypeError, ValueError, KeyError):
        return []
    return list(dict.fromkeys(
        r.get("f_afm") for w in decl
        for r in w.get("Ergazomenoi", {}).get("ErgazomenoiWTO", [])))


def _history() -> list[dict]:
    """ΕΝΑ ιστορικό: κάθε υποβολή ΕΡΓΑΝΗ (`ErganiSubmissionRecorded`), νεότερη
    πρώτη, με ημερομηνία το `occurred_at` της (τοπική ώρα) — και, για το
    πρόγραμμα (WTO), το ΠΕΡΙΕΧΟΜΕΝΟ που δηλώθηκε ανά εργαζόμενο.

    ⚠️ Μία υποβολή WTO καλύπτει ΠΟΛΛΟΥΣ εργαζομένους (μία κλήση —
    `ergani.merge_wto`, κενό `employee_id`). Το περιεχόμενο ζει σε ΧΩΡΙΣΤΑ
    events, ένα ανά εργαζόμενο, που γράφονται ΑΜΕΣΩΣ μετά την επιτυχή υποβολή
    (`ScheduleDeclaredToErgani` = εβδομαδιαίο, `ScheduleDaysSubmitted` =
    ημερήσιο με `batch_no`): ανήκουν στην ΤΕΛΕΥΤΑΙΑ υποβολή WTO που περιέχει
    το ΑΦΜ τους. Το «Αφορά» βγαίνει από τα ΑΦΜ του payload (σωστό και στην
    απόρριψη). Event προγράμματος χωρίς υποβολή πριν (παλιά εγγραφή) γίνεται
    δική του γραμμή — καμία πληροφορία δεν χάνεται. Οι υποβολές διαγραμμένης
    απασχόλησης παραλείπονται (όπως στα projections)."""
    names = {}
    for e in proj.employments.values():
        names.setdefault(e.afm, e.full_name)
    rows, wto = [], None
    for rec in store.load_log():
        ev, kind = rec["event"], rec["event_type"]
        if kind not in ("ErganiSubmissionRecorded", "ScheduleDaysSubmitted",
                        "ScheduleDeclaredToErgani"):
            continue
        eid = ev.employee_id
        if eid and eid != "COMPANY" and eid not in proj.employments:
            continue
        at = datetime.fromisoformat(rec["occurred_at"]).astimezone()
        if kind == "ErganiSubmissionRecorded":
            # WTO και WTOLeave: ΜΙΑ κλήση για πολλούς (κενό employee_id) — το
            # «Αφορά» από τα ΑΦΜ του payload, όχι «ΕΤΑΙΡΕΙΑ»
            afms = (_wto_afms(ev.payload) if ev.doc_type in ("WTO", "WTOLeave")
                    else [])
            who = ([names.get(a, a) for a in afms] if afms
                   else [emp_name(eid) if eid else "ΕΤΑΙΡΕΙΑ"])
            row = {
                "seq": rec["seq"], "at": at, "doc": ev.doc_type,
                "label": ERGANI_LABELS.get(ev.doc_type, ev.doc_type),
                "who": ", ".join(who), "status": ev.status,
                "reference": ev.reference, "environment": ev.environment,
                "schedule": [], "afms": set(afms),
            }
            rows.append(row)
            wto = row if ev.doc_type == "WTO" else wto
            continue
        emp = proj.employments[eid]
        if kind == "ScheduleDaysSubmitted":
            item = {"name": emp.full_name, "kind": "daily", "no": ev.batch_no,
                    "period": None,
                    "days": [(f"{iso[8:]}/{iso[5:7]}/{iso[:4]}", h)
                             for iso, h in sorted(ev.days.items())]}
        else:
            item = {"name": emp.full_name, "kind": "weekly", "no": None,
                    "period": (ev.effective_from, ev.end_date),
                    "days": [(DAY_NAMES[i], ev.days.get(code, ""))
                             for i, code in enumerate(constraints.DAY_CODES)]}
        row = wto if wto is not None and emp.afm in wto["afms"] else None
        if row is None:
            row = {"seq": None, "at": at, "doc": "WTO", "label": ERGANI_LABELS["WTO"],
                   "who": emp.full_name, "status": "submitted", "reference": "",
                   "environment": "", "schedule": [], "afms": set()}
            rows.append(row)
        row["schedule"].append(item)
    return rows[::-1]


def _panel():
    monday = today() - timedelta(days=today().weekday()) + timedelta(days=7)
    return render_template(
        "partials/_ergani_panel.html",
        hires=_hires(), retros=_retros(), metavoles=_metavoles(), exits=_exits(),
        rows=_pending_rows(), weekly=_weekly_rows(),
        next_week={"label": _week_label(monday), "names": _next_week_names()},
        history=_history(),
    )


def _respond(command):
    ok, message = run_command(command)
    # Επιτυχία από το modal χειροκίνητης υποβολής -> κλείνει (OOB)
    html = _panel() + ('<div id="modal-host" hx-swap-oob="innerHTML"></div>' if ok else "")
    return flash_to(html, message, err=not ok)


def _mode(field: str = "reference"):
    """Τρόπος υποβολής του αιτήματος: με πεδίο πρωτοκόλλου στη φόρμα (modal
    «✍️ Χειροκίνητα») η εντολή καταγράφεται ως ΧΕΙΡΟΚΙΝΗΤΗ — αλλιώς REST API."""
    if field in request.form:
        return ergani_manual(request.form.get(field, ""))
    return nullcontext()


# ------------------------------------- Προεπισκόπηση / χειροκίνητη υποβολή
def _capture(command) -> dict:
    """Τρέχει την εντολή σε ΠΡΟΕΠΙΣΚΟΠΗΣΗ: το έντυπο που ΘΑ έστελνε, χωρίς
    καμία εγγραφή (`ErganiPreview` πριν από κάθε event)."""
    try:
        with lock, ergani_preview():
            command()
    except ErganiPreview as hit:
        if hit.payload is None:
            return {"empty": True}
        return {
            "empty": False,
            "rows": ergani_mod.payload_rows(hit.payload),
            "raw": json.dumps(hit.payload, ensure_ascii=False, indent=2, default=str),
        }
    raise ValueError("Η εντολή δεν παρήγαγε έντυπο ΕΡΓΑΝΗ")


def _preview(title, who, parts, post_url, hidden=()):
    """Modal προεπισκόπησης: `parts` = [(ετικέτα, εντολή, πεδίο πρωτοκόλλου)]
    — ένα ανά υποβολή στο portal. Σφάλμα της εντολής -> toast, όχι modal."""
    try:
        rendered = [{"label": label, "field": field, **_capture(command)}
                    for label, command, field in parts]
    except (ValueError, KeyError) as exc:
        return flash_to("", str(exc), err=True)
    return render_template(
        "partials/_ergani_submit_modal.html", title=title, who=who,
        parts=rendered, post_url=post_url, hidden=hidden,
        tab=request.args.get("tab", "fields"),
    )


@bp.get("/preview/e3/<employment_id>")
def preview_e3(employment_id):
    return _preview(
        "Αναγγελία Πρόσληψης — WebE3N", emp_name(employment_id),
        [("WebE3N", lambda: svc.submit_hire_e3(employment_id), "reference")],
        url_for("ergani.submit_e3", employment_id=employment_id))


@bp.get("/preview/e4/<employment_id>/<int:n>")
def preview_e4(employment_id, n):
    return _preview(
        "Δήλωση Μεταβολής — Ε4", emp_name(employment_id),
        [("Ε4", lambda: svc.submit_metavoli(employment_id, n), "reference")],
        url_for("ergani.submit_e4", employment_id=employment_id, n=n))


@bp.get("/preview/e5/<employment_id>")
def preview_e5(employment_id):
    return _preview(
        "Αναγγελία Λήξης", emp_name(employment_id),
        [("Λήξη", lambda: svc.submit_exit(employment_id), "reference")],
        url_for("ergani.submit_e5", employment_id=employment_id))


@bp.get("/preview/retro/<ob_id>")
def preview_retro(ob_id):
    ob = proj.obligations.get(ob_id)
    if ob is None:
        abort(404)
    return _preview(
        dep_label(ob), "ΕΤΑΙΡΕΙΑ",
        [(dep_label(ob), lambda: trigger(ob_id, via_ergani=True), "reference")],
        url_for("ergani.submit_retro", ob_id=ob_id))


@bp.post("/preview/schedules")
def preview_schedules():
    """Η επιλογή της φόρμας προγράμματος: εβδομαδιαίο και ημερήσιο είναι ΔΥΟ
    υποβολές στο portal, με δικό τους πρωτόκολλο η καθεμία."""
    try:
        weekly, daily = _selection("weekly"), _selection("week")
    except ValueError as exc:
        return flash_to("", str(exc), err=True)
    if not (weekly or daily):
        return flash_to("", "Δεν επιλέχθηκαν εκκρεμείς υποβολές προγράμματος", err=True)
    parts = []
    if weekly:
        parts.append(("Εβδομαδιαίο (WTOWeek)",
                      lambda: svc.submit_weekly_schedules(weekly), "weekly_reference"))
    if daily:
        parts.append(("Ημερήσιο (WTODaily)",
                      lambda: svc.submit_schedule_days(daily), "daily_reference"))
    hidden = [(f, v) for f in ("weekly", "week") for v in request.form.getlist(f)]
    names = sorted({emp_name(e) for e in (*weekly, *daily)})
    return _preview("Οργάνωση Χρόνου Εργασίας — ΟΧΕ", ", ".join(names), parts,
                    url_for("ergani.submit_schedules"), hidden)


# ------------------------------------------------------------- Routes
@bp.get("/")
def index():
    return render_template("ergani.html", panel=_panel())


@bp.get("/submission/<int:seq>")
def submission_modal(seq):
    """Το JSON μιας υποβολής σε MODAL (ακατέργαστο, μορφοποιημένο).
    `seq` = θέση του event στο log (σταθερή — βλ. `_history`)."""
    rec = next((r for r in store.load_log() if r["seq"] == seq
                and r["event_type"] == "ErganiSubmissionRecorded"), None)
    if rec is None:
        abort(404)
    ev = rec["event"]
    try:
        raw = json.dumps(json.loads(ev.payload), ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        raw = ev.payload or ""      # παλιά/μη έγκυρη εγγραφή: όπως είναι
    return render_template(
        "partials/_ergani_json_modal.html", ev=ev, raw=raw,
        label=ERGANI_LABELS.get(ev.doc_type, ev.doc_type),
        who=emp_name(ev.employee_id) if ev.employee_id else "ΕΤΑΙΡΕΙΑ",
        at=datetime.fromisoformat(rec["occurred_at"]).astimezone(),
    )


@bp.get("/modal-close")
def modal_close():
    """Κλείσιμο modal — αδειάζει τον host."""
    return ""


@bp.post("/e3/<employment_id>")
def submit_e3(employment_id):
    def command():
        missing = svc.missing_hire_documents(employment_id)
        with _mode():
            res = svc.submit_hire_e3(employment_id)
        msg = (f"{emp_name(employment_id)}: Αναγγελία Πρόσληψης WebE3N "
               f"({res['status']})")
        return msg + (f" ⚠ χωρίς: {', '.join(missing)}" if missing else "")
    return _respond(command)


def _selection(field: str = "week") -> dict:
    """{eid: [ημέρες ή ισχύς από]} από τα checkboxes `<eid>|<ISO>`:
    `week` = εβδομάδα ημερήσιων (Δευτέρα)· `weekly` = εκκρεμές εβδομαδιαίο."""
    selection = {}
    for value in request.form.getlist(field):
        eid, _, iso = value.partition("|")
        try:
            when = date.fromisoformat(iso)
        except ValueError:
            raise ValueError("Μη έγκυρη επιλογή") from None
        if eid not in proj.employments:
            raise ValueError("Άγνωστος εργαζόμενος στην επιλογή")
        selection.setdefault(eid, []).extend(
            [when] if field == "weekly" else
            [d for d in svc.week_submittable_days(eid, when)
             if not _in_pending_weekly(proj.employments[eid], d)])
    return {eid: v for eid, v in selection.items() if v}


@bp.post("/schedules")
def submit_schedules():
    """Υποβολή ΟΛΩΝ των επιλεγμένων: πρώτα τα εβδομαδιαία (standing ΟΧΕ —
    καλύπτουν τις ημέρες τους), μετά τα ημερήσια σε ΕΝΑ αριθμημένο batch."""
    def command():
        weekly, daily = _selection("weekly"), _selection("week")
        if not (weekly or daily):
            raise ValueError("Δεν επιλέχθηκαν εκκρεμείς υποβολές προγράμματος")
        parts = []
        if weekly:
            with _mode("weekly_reference"):
                parts.append(f"{svc.submit_weekly_schedules(weekly)} εβδομαδιαία")
        if daily:
            with _mode("daily_reference"):
                res = svc.submit_schedule_days(daily)
            parts.append(f"υποβολή #{res['batch_no']}: {res['count']} ημερήσια")
        return "Υποβλήθηκαν στο ΕΡΓΑΝΗ: " + " · ".join(parts)
    return _respond(command)


@bp.post("/schedules/revert")
def revert_schedules():
    """Ακύρωση μη υποβεβλημένων αλλαγών — επαναφορά στη δηλωμένη τιμή ΕΡΓΑΝΗ
    (οι αδήλωτες ημέρες δεν έχουν τιμή επαναφοράς και αγνοούνται)."""
    def command():
        selection = _selection()
        if not selection:
            raise ValueError("Δεν επιλέχθηκαν ημερήσιες αλλαγές")
        n = svc.revert_days(selection)
        if not n:
            return "Δεν υπήρχαν τροποποιημένες ημέρες προς αναίρεση"
        return f"Αναιρέθηκαν {n} αλλαγές — επαναφορά στη δηλωμένη τιμή"
    return _respond(command)


@bp.post("/retro/<ob_id>")
def submit_retro(ob_id):
    """Το απολογιστικό: η εντολή της υποχρέωσης (την κλείνει κιόλας)."""
    def command():
        with _mode():
            return trigger(ob_id, via_ergani=True)
    return _respond(command)


@bp.post("/e4/<employment_id>/<int:n>")
def submit_e4(employment_id, n):
    def command():
        with _mode():
            res = svc.submit_metavoli(employment_id, n)
        return f"{emp_name(employment_id)}: Δήλωση Μεταβολής ({res['status']})"
    return _respond(command)


@bp.post("/e5/<employment_id>")
def submit_e5(employment_id):
    """Αναγγελία Λήξης. Η δήλωση αποχώρησης (PDF/φωτογραφίες -> ΕΝΑ PDF, ίδια
    μετατροπή με την αποχώρηση) μπορεί να ανέβει εδώ, αν δεν δόθηκε τότε."""
    files = [{"name": u.filename, "data": base64.b64encode(u.read()).decode()}
             for u in request.files.getlist("resignation") if u.filename]

    def command():
        if files:
            svc.attach_document(proj.employments[employment_id].person_id,
                                "resignation", amka_pdf.to_pdf(files),
                                "resignation.pdf")
        with _mode():
            res = svc.submit_exit(employment_id)
        return f"{emp_name(employment_id)}: Αναγγελία Λήξης ({res['status']})"
    return _respond(command)


@bp.post("/e5/<employment_id>/external")
def external_e5(employment_id):
    """Καταγγελία: υποβλήθηκε στο portal — σημειώνεται (καμία αποστολή)."""
    def command():
        svc.record_external_exit_submission(
            employment_id, request.form.get("reference", ""))
        return f"{emp_name(employment_id)}: η Αναγγελία Λήξης σημειώθηκε ως υποβληθείσα"
    return _respond(command)
