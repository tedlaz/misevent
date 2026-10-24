"""Πρόγραμμα εργασίας: εντολές ΕΠΕΞΕΡΓΑΣΙΑΣ των «Ζωντανά» (modals: πρόγραμμα
ημέρας, μαζική αλλαγή, αντιγραφή εβδομάδας).

Κάθε αλλαγή αφήνει ημέρες `pending`/`modified` που συγκεντρώνονται στην
οθόνη ΕΡΓΑΝΗ (`views/ergani.py`) — από εκεί ΜΟΝΟ στέλνονται ή αναιρούνται."""

from datetime import date, timedelta

from flask import Blueprint, abort, redirect, render_template, request, url_for

from misevent.integrations import card_punches as cp
from misevent.rules import config, constraints

from ..clock import today
from ..helpers import form_date, live_done, modal_error, run_command
from ..labels import LEAVE_LABELS
from ..system import proj, svc

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
    rules = config.work_hour_rules_for(selected)
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
                # ΗΜΕΡΗΣΙΑ όρια χρωματισμού της μπάρας (παράμετροι ωραρίου,
                # κατά το σύστημα του εργαζομένου) — ίδια με την κατάταξη
                # `work_hours.classify_shifts`, χωρίς το εβδομαδιαίο σκέλος
                "overwork_from": rules.daily_overwork_start * 60,
                "overtime_from": (rules.daily_legal_5 if employment.weekly_system == 5
                                  else rules.daily_legal_6) * 60,
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
        "rules": rules,
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


# ------------------------------------------------------------ Υποβολή
@bp.get("/")
def index():
    """Η υποβολή προγράμματος ζει πλέον στην οθόνη ΕΡΓΑΝΗ (παλιοί σύνδεσμοι)."""
    return redirect(url_for("ergani.index"))
