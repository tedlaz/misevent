"""Πρόγραμμα εργασίας: εντολές των «Ζωντανά» + οθόνη υποβολής στο ΕΡΓΑΝΗ.

Η ΕΠΕΞΕΡΓΑΣΙΑ γίνεται στα «Ζωντανά» (modals: πρόγραμμα ημέρας, μαζική
αλλαγή, αντιγραφή εβδομάδας). Κάθε αλλαγή αφήνει ημέρες `pending`/`modified`
που συγκεντρώνονται στην οθόνη `/work-schedules/` — από εκεί στέλνονται στο
ΕΡΓΑΝΗ ή αναιρούνται. Το ιστορικό αποστολών διαβάζεται από τα events
`ScheduleDeclaredToErgani` (εβδομαδιαίο) και `ScheduleDaysSubmitted`
(ημερήσιο) — κανένα επιπλέον αποθηκευμένο στοιχείο."""

from datetime import date, datetime, timedelta

from flask import Blueprint, abort, render_template, request

from misevent.integrations import card_punches as cp
from misevent.rules import constraints

from ..clock import today
from ..helpers import flash_to, form_date, live_done, modal_error, run_command
from ..labels import DAY_NAMES, LEAVE_LABELS
from ..queries import emp_name
from ..system import proj, store, svc

bp = Blueprint("work_schedules", __name__, url_prefix="/work-schedules")


def _retro_open(d: date, ref: date) -> bool:
    """Απολογιστικό σύστημα τον μήνα της `d` και δεν έχει περάσει η προθεσμία
    δήλωσης (τέλος του ΕΠΟΜΕΝΟΥ μήνα) -> η ημέρα αλλάζει και αναδρομικά."""
    deadline = (d.replace(day=28) + timedelta(days=35)).replace(day=1) - timedelta(days=1)
    return proj.schedule_system_on(d) == "retro" and ref <= deadline


def _ensure_future(dates) -> None:
    """Οι αλλαγές προγράμματος επιτρέπονται ΜΟΝΟ για μελλοντικές ημέρες
    (από αύριο). Παρελθόν/σήμερα είναι κλειδωμένα — έχουν ήδη «κλείσει» —
    εκτός από μήνες σε ΑΠΟΛΟΓΙΣΤΙΚΟ σύστημα, έως την προθεσμία δήλωσης.
    (Η κλεισμένη μισθοδοσία παγώνει ούτως ή άλλως — command layer.)"""
    ref = today()
    if any(d <= ref and not _retro_open(d, ref)
           for d in (d if isinstance(d, date) else date.fromisoformat(str(d))
                     for d in dates)):
        raise ValueError(
            "Αλλαγές επιτρέπονται μόνο για μελλοντικές ημέρες (από "
            f"{(ref + timedelta(days=1)).strftime('%d/%m/%Y')}) — αναδρομικά "
            "μόνο σε μήνα με απολογιστικό σύστημα"
        )


def _week_label(monday: date) -> str:
    return (f"{monday.strftime('%d/%m')}–"
            f"{(monday + timedelta(days=6)).strftime('%d/%m/%Y')}")


def _staff():
    """Οι απασχολήσεις με αναγγελμένη πρόσληψη, αλφαβητικά."""
    return sorted(
        (e for e in proj.employments.values()
         if e.hire_submitted),
        key=lambda e: e.full_name,
    )


def _leave_on(employment, day: date):
    return next(
        (
            leave
            for leave in employment.active_leaves()
            if leave.date_from <= day <= leave.date_to
        ),
        None,
    )


def _day_context(selected: date, focus_id=None, supplied=None) -> dict:
    rows = []
    for employment in sorted(
        (item for item in proj.employments.values() if item.hire_submitted),
        key=lambda item: item.full_name,
    ):
        if not employment.active_on(selected):
            continue
        scheduled_raw = (
            supplied.get(employment.employment_id, employment.day_schedule(selected))
            if supplied else employment.day_schedule(selected)
        )
        leave = _leave_on(employment, selected)
        raw = "" if leave else scheduled_raw
        try:
            segments = constraints.parse_day_schedule(raw)
        except ValueError:
            segments = []
        previous_day = selected - timedelta(days=1)
        following_day = selected + timedelta(days=1)

        def adjacent_segments(day):
            if not employment.active_on(day):
                return []
            try:
                return constraints.parse_day_schedule(employment.day_schedule(day))
            except ValueError:
                return []

        previous_segments = adjacent_segments(previous_day)
        following_segments = adjacent_segments(following_day)
        minimum_start = (
            max(item["end"] for item in previous_segments)
            - 24 * 60 + constraints.MIN_DAILY_REST_HOURS * 60
            if previous_segments else 0
        )
        maximum_end = (
            24 * 60 + min(item["start"] for item in following_segments)
            - constraints.MIN_DAILY_REST_HOURS * 60
            if following_segments else 33 * 60
        )
        rows.append(
            {
                "employment": employment,
                "raw": raw,
                "segments": segments,
                "working": bool(segments),
                "absent": bool(leave),
                "absence_label": (
                    LEAVE_LABELS.get(leave.leave_type.value, leave.leave_type.value)
                    if leave else ""
                ),
                "minimum_start": max(0, minimum_start),
                "maximum_end": min(33 * 60, maximum_end),
                # ΧΤΥΠΗΜΑΤΑ ΚΑΡΤΑΣ (ακριβείς ώρες) — μόνο προβολή, κάτω από
                # το πρόγραμμα· ανοιχτή/ορφανή = ένα σημάδι
                "punches": [
                    {**s, "a": s["s"] if s["s"] is not None else s["e"],
                     "b": s["e"] if s["e"] is not None else s["s"],
                     "text": " - ".join(cp.hhmm(m) if m is not None else "—"
                                        for m in (s["s"], s["e"]))}
                    for s in employment.card_shifts(selected)
                ],
            }
        )

    # Ο αναλυτικός επεξεργαστής καλύπτει ολόκληρη την εργάσιμη ημέρα και
    # τη συνέχειά της έως τις 09:00 της επόμενης ημερολογιακής.
    timeline_start = 0
    timeline_end = 33 * 60
    slots = list(range(timeline_start, timeline_end, 60))
    counts = [
        sum(
            any(segment["start"] < minute + 60 and segment["end"] > minute
                for segment in row["segments"])
            for row in rows
        )
        for minute in slots
    ]
    return {
        "selected": selected,
        "day_name": (
            "Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"
        )[selected.weekday()],
        "previous_day": selected - timedelta(days=1),
        "next_day": selected + timedelta(days=1),
        "year": selected.year,
        "rows": rows,
        "focus_id": focus_id,
        "timeline_start": timeline_start,
        "timeline_end": timeline_end,
        "timeline_minutes": timeline_end - timeline_start,
        "slots": slots,
        "counts": counts,
    }



@bp.get("/day")
def day_overview():
    try:
        selected = date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        abort(400)
    return render_template(
        "partials/_work_schedule_day_overview.html",
        **_day_context(selected, request.args.get("focus")),
        nav=bool(request.args.get("nav")),
    )


@bp.post("/day")
def update_day_overview():
    selected = form_date(request.form, "date")
    focus_id = request.form.get("focus") or None
    supplied = {
        employment_id: request.form.get(f"hours_{employment_id}", "").strip()
        for employment_id in proj.employments
        if f"hours_{employment_id}" in request.form
    }

    def command():
        for hours in supplied.values():
            constraints.parse_day_schedule(hours)
        changed = 0
        for employment_id, hours in supplied.items():
            employment = proj.employments[employment_id]
            if hours != employment.day_schedule(selected):
                _ensure_future([selected])
                svc.set_daily_schedule(employment_id, {selected: hours})
                changed += 1
        return f"Αποθηκεύτηκαν {changed} μεταβολές για {selected.strftime('%d/%m/%Y')}"

    ok, message = run_command(command)
    if ok:
        return live_done(message)
    html = render_template(
        "partials/_work_schedule_day_overview.html",
        **_day_context(selected, focus_id, supplied),
    )
    return modal_error(html, message)


@bp.get("/modal-close")
def modal_close():
    return ""


# ------------------------------------------------ Μαζική αλλαγή (Ζωντανά)
def _range_modal(form=None):
    return render_template(
        "partials/_work_schedule_range_modal.html",
        employees=_staff(), form=form or {},
        tomorrow=today() + timedelta(days=1),
    )


@bp.get("/range")
def range_modal():
    return _range_modal()


@bp.post("/range")
def update_range():
    """Ίδιο ωράριο (κενό = ΡΕΠΟ) σε ένα διάστημα ημερών για έναν ή πολλούς
    εργαζομένους. Ημέρες εκτός περιόδου απασχόλησης παραλείπονται."""
    f = request.form

    def command():
        eids = f.getlist("eid")
        if not eids:
            raise ValueError("Επιλέξτε τουλάχιστον έναν εργαζόμενο")
        start, end = form_date(f, "date_from"), form_date(f, "date_to")
        if end < start:
            raise ValueError("Η ημερομηνία «έως» προηγείται της «από»")
        if (end - start).days > 366:
            raise ValueError("Το διάστημα δεν μπορεί να ξεπερνά το έτος")
        _ensure_future([start])
        hours = (f.get("hours") or "").strip()
        constraints.parse_day_schedule(hours)
        grouped = {}
        for eid in eids:
            employment = proj.employments.get(eid)
            if employment is None:
                raise ValueError("Η επιλογή περιέχει άγνωστο εργαζόμενο")
            days = {start + timedelta(days=i): hours
                    for i in range((end - start).days + 1)
                    if employment.active_on(start + timedelta(days=i))}
            if days:
                grouped[eid] = days
        if not grouped:
            raise ValueError("Καμία ενεργή ημέρα στο διάστημα")
        # Πλήρης προέλεγχος όλων των εργαζομένων πριν γραφτεί το πρώτο event.
        for eid, days in grouped.items():
            svc.validate_daily_schedule(eid, days)
        for eid, days in grouped.items():
            svc.set_daily_schedule(eid, days)
        n = sum(len(days) for days in grouped.values())
        return f"Ενημερώθηκαν {n} ημέρες ({len(grouped)} εργαζόμενοι) · {hours or 'ΡΕΠΟ'}"

    ok, message = run_command(command)
    return live_done(message) if ok else modal_error(_range_modal(f), message)


# ----------------------------------------- Αντιγραφή εβδομάδας (Ζωντανά)
def _paste_modal(form=None):
    monday = today() - timedelta(days=today().weekday())
    return render_template(
        "partials/_work_schedule_paste_modal.html",
        source=monday, target=monday + timedelta(days=7), form=form or {},
    )


@bp.get("/week/paste")
def paste_modal():
    return _paste_modal()


@bp.post("/week/paste")
def paste_week():
    def command():
        source_start = form_date(request.form, "source_start")
        target_start = form_date(request.form, "target_start")
        if source_start.weekday() != 0 or target_start.weekday() != 0:
            raise ValueError("Η αντιγραφή εβδομάδας πρέπει να ξεκινά Δευτέρα")
        if source_start == target_start:
            raise ValueError("Η εβδομάδα προέλευσης και προορισμού είναι ίδια")
        _ensure_future([target_start])  # όλη η εβδομάδα-στόχος πρέπει να είναι μελλοντική

        grouped = {}
        for employment_id, employment in proj.employments.items():
            days = {}
            for offset in range(7):
                source_day = source_start + timedelta(days=offset)
                target_day = target_start + timedelta(days=offset)
                if not (employment.active_on(source_day)
                        and employment.active_on(target_day)):
                    continue
                value = employment.day_schedule(source_day)
                if value != employment.day_schedule(target_day):
                    days[target_day] = value
            if days:
                grouped[employment_id] = days

        for employment_id, days in grouped.items():
            svc.validate_daily_schedule(employment_id, days)
        for employment_id, days in grouped.items():
            svc.set_daily_schedule(employment_id, days)
        changed_cells = sum(len(days) for days in grouped.values())
        if not changed_cells:
            return "Δεν υπήρχαν διαφορές για αντιγραφή"
        return (
            f"Αντιγράφηκαν {changed_cells} ημέρες εβδομάδας "
            f"σε {len(grouped)} εργαζομένους"
        )

    ok, message = run_command(command)
    return live_done(message) if ok else modal_error(_paste_modal(request.form), message)


# ------------------------------------- Οθόνη «Πρόγραμμα → ΕΡΓΑΝΗ»
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


def pending_count() -> int:
    """Πλήθος εκκρεμών υποβολών (εβδομαδιαία + εβδομάδες ημερήσιων) — για
    την ειδοποίηση στις Υποχρεώσεις."""
    return len(_weekly_rows()) + len(_pending_rows())


def _history() -> list[dict]:
    """Ιστορικό αποστολών, νεότερα πρώτα. Κάθε υποβολή είναι Ή εβδομαδιαία
    (`ScheduleDeclaredToErgani` — π.χ. το αρχικό πρόγραμμα της πρόσληψης)
    Ή ημερήσια (`ScheduleDaysSubmitted`, ανά batch)· η τελευταία που
    καλύπτει μια ημέρα είναι η ενεργή (βλ. `day_baseline`)."""
    batches = {}
    for seq, rec in enumerate(store.load_log()):
        ev = rec["event"]
        if rec["event_type"] == "ScheduleDaysSubmitted":
            key, kind = ("d", ev.batch_no), "daily"
            days = [(f"{iso[8:]}/{iso[5:7]}/{iso[:4]}", h)
                    for iso, h in sorted(ev.days.items())]
        elif rec["event_type"] == "ScheduleDeclaredToErgani":
            key, kind = ("w", seq), "weekly"
            days = [(DAY_NAMES[i], ev.days.get(code, ""))
                    for i, code in enumerate(constraints.DAY_CODES)]
        else:
            continue
        b = batches.setdefault(key, {
            "no": ev.batch_no if kind == "daily" else None,
            "kind": kind,
            "seq": seq,
            "at": datetime.fromisoformat(rec["occurred_at"]),
            "items": [],
        })
        b["items"].append({
            "name": emp_name(ev.employee_id),
            "days": days,
            "period": kind == "weekly" and (ev.effective_from, ev.end_date),
        })
    out = sorted(batches.values(), key=lambda b: b["seq"], reverse=True)
    for b in out:
        b["count"] = sum(len(i["days"]) for i in b["items"])
    return out


def _next_week_names() -> list[str]:
    """Όσοι δεν έχουν δηλωμένο πρόγραμμα για την ΕΠΟΜΕΝΗ εβδομάδα."""
    monday = today() - timedelta(days=today().weekday()) + timedelta(days=7)
    return [e.full_name for e in _staff()
            if e.active_days_in_week(monday) and e.week_status(monday) == "pending"]


def _panel():
    monday = today() - timedelta(days=today().weekday()) + timedelta(days=7)
    return render_template(
        "partials/_work_schedule_ergani_panel.html",
        rows=_pending_rows(), weekly=_weekly_rows(), history=_history(),
        next_week={"label": _week_label(monday), "names": _next_week_names()},
    )


@bp.get("/")
def index():
    return render_template("work_schedules.html", panel=_panel())


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


@bp.post("/ergani")
def submit_ergani():
    """Υποβολή ΟΛΩΝ των επιλεγμένων: πρώτα τα εβδομαδιαία (standing ΟΧΕ —
    καλύπτουν τις ημέρες τους), μετά τα ημερήσια σε ΕΝΑ αριθμημένο batch."""
    def command():
        weekly, daily = _selection("weekly"), _selection("week")
        if not (weekly or daily):
            raise ValueError("Δεν επιλέχθηκαν εκκρεμείς υποβολές")
        parts = []
        if weekly:
            parts.append(f"{svc.submit_weekly_schedules(weekly)} εβδομαδιαία")
        if daily:
            res = svc.submit_schedule_days(daily)
            parts.append(f"υποβολή #{res['batch_no']}: {res['count']} ημερήσια")
        return "Υποβλήθηκαν στο ΕΡΓΑΝΗ: " + " · ".join(parts)

    ok, message = run_command(command)
    return flash_to(_panel(), message, err=not ok)


@bp.post("/ergani/revert")
def revert_changes():
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

    ok, message = run_command(command)
    return flash_to(_panel(), message, err=not ok)
