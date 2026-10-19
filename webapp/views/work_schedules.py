"""Ετήσια, διαδραστική προβολή προγράμματος εργασίας."""

import json
from calendar import monthrange
from datetime import date, timedelta

from flask import Blueprint, abort, render_template, request

from misevent.rules import constraints
from misevent.rules.calendar_gr import public_holidays

from ..clock import today
from ..helpers import flash_to, form_date, run_command
from ..labels import DAY_NAMES, LEAVE_LABELS
from ..system import proj, svc

bp = Blueprint("work_schedules", __name__, url_prefix="/work-schedules")

MONTH_NAMES = (
    "Ιαν", "Φεβ", "Μαρ", "Απρ", "Μάι", "Ιουν",
    "Ιουλ", "Αυγ", "Σεπ", "Οκτ", "Νοε", "Δεκ",
)


def _year() -> int:
    year = request.values.get("year", type=int) or today().year
    if not 1900 <= year <= 2100:
        abort(400)
    return year


def _closed_end() -> date | None:
    """Τελευταία ημέρα κλεισμένης μισθοδοσίας — ό,τι είναι <= από αυτήν είναι
    παγωμένο (§5.6). Το grid το χρωματίζει και το κλειδώνει· την επιβολή την
    κάνει το command layer (`svc._check_open_days`)."""
    return svc.closed_period_end()


def _ensure_future(dates) -> None:
    """Οι αλλαγές προγράμματος επιτρέπονται ΜΟΝΟ για μελλοντικές ημέρες
    (από αύριο). Παρελθόν/σήμερα είναι κλειδωμένα — έχουν ήδη «κλείσει»."""
    ref = today()
    if any((d if isinstance(d, date) else date.fromisoformat(str(d))) <= ref
           for d in dates):
        raise ValueError(
            "Αλλαγές επιτρέπονται μόνο για μελλοντικές ημέρες (από "
            f"{(ref + timedelta(days=1)).strftime('%d/%m/%Y')})"
        )


def _compact_schedule(segments: list[dict]) -> str:
    parts = []
    for segment in segments:
        text = segment["text"].replace(":00", "").replace("-", "–")
        parts.append(("Τ " if segment["telework"] else "") + text)
    return " / ".join(parts)


def _leave_on(employment, day: date):
    return next(
        (
            leave
            for leave in employment.active_leaves()
            if leave.date_from <= day <= leave.date_to
        ),
        None,
    )


def _planner_context(year: int) -> dict:
    start, end = date(year, 1, 1), date(year, 12, 31)
    day_count = (end - start).days + 1
    holidays = public_holidays(year)
    # Η ΕΠΟΜΕΝΗ εβδομάδα (Δευτέρα) σε σχέση με το «σήμερα» του UI — για να
    # επισημανθεί έγκαιρα αν κάποιος εργαζόμενος δεν έχει δηλωμένο πρόγραμμα.
    ref = today()
    next_monday = ref - timedelta(days=ref.weekday()) + timedelta(days=7)
    if not (start <= next_monday <= end):
        next_monday = None  # η επόμενη εβδομάδα δεν φαίνεται στο εμφανιζόμενο έτος
    closed_end = _closed_end()
    days = []
    for offset in range(day_count):
        day = start + timedelta(days=offset)
        days.append(
            {
                "index": offset,
                "date": day,
                "day": day.day,
                "dow": DAY_NAMES[day.weekday()],
                "weekend": day.weekday() >= 5,
                "holiday": day in holidays,
                "month_start": day.day == 1,
                "today": day == today(),
                # Κλεισμένη μισθοδοσία: η ημέρα είναι κλειδωμένη (μωβ)
                "closed": closed_end is not None and day <= closed_end,
            }
        )

    months = []
    for month in range(1, 13):
        month_start = date(year, month, 1)
        count = monthrange(year, month)[1]
        months.append(
            {
                "month": month,
                "label": MONTH_NAMES[month - 1],
                "count": count,
                "start_index": (month_start - start).days,
                "end_index": (month_start - start).days + count - 1,
            }
        )

    weeks = []
    cursor = start
    while cursor <= end:
        monday = cursor - timedelta(days=cursor.weekday())
        sunday = monday + timedelta(days=6)
        visible_start, visible_end = max(start, monday), min(end, sunday)
        iso_week = monday.isocalendar().week
        weeks.append(
            {
                "label": f"Ε{iso_week}",
                "title": (
                    f"Εβδομάδα {iso_week} · "
                    f"{monday.strftime('%d/%m')}–{sunday.strftime('%d/%m')}"
                ),
                "count": (visible_end - visible_start).days + 1,
                "start_index": (visible_start - start).days,
                "end_index": (visible_end - start).days,
                "week_start": monday,
            }
        )
        cursor = sunday + timedelta(days=1)

    rows = []
    employments = sorted(
        (item for item in proj.employments.values() if item.hire_submitted),
        key=lambda item: item.full_name,
    )
    for employment in employments:
        if employment.hire_date and employment.hire_date > end:
            continue
        if employment.termination_date and employment.termination_date < start:
            continue

        cells = []
        scheduled_days = total_minutes = overrides = telework_days = 0
        for item in days:
            day = item["date"]
            active = (
                (employment.hire_date is None or day >= employment.hire_date)
                and (
                    employment.termination_date is None
                    or day <= employment.termination_date
                )
            )
            raw = employment.day_schedule(day) if active else ""
            try:
                segments = constraints.parse_day_schedule(raw)
            except ValueError:
                segments = []
            minutes = sum(segment["end"] - segment["start"] for segment in segments)
            telework = bool(segments) and all(segment["telework"] for segment in segments)
            mixed = bool(segments) and any(segment["telework"] for segment in segments)
            override = active and day in employment.daily_schedule
            leave = _leave_on(employment, day) if active else None
            contract_end = (
                employment.employment_relationship == "1"
                and employment.fixed_term_to == day
            )

            if segments:
                scheduled_days += 1
                total_minutes += minutes
            if override:
                overrides += 1
            if telework:
                telework_days += 1

            if not active:
                state = "inactive"
                description = "Δεν εργάζεται"
            elif segments:
                state = "telework" if telework else "work"
                description = raw
            else:
                state = "off"
                description = "Ρεπό"

            leave_label = (
                LEAVE_LABELS.get(leave.leave_type.value, leave.leave_type.value)
                if leave else ""
            )
            details = [day.strftime("%d/%m/%Y"), description]
            if item["holiday"]:
                details.append("Αργία")
            if override:
                details.append("Ημερήσια μεταβολή")
            if leave_label:
                details.append(leave_label)
            if contract_end:
                details.append("Προβλεπόμενη λήξη σύμβασης ορισμένου χρόνου")
            coverage = employment.day_status(day) if active else "off"
            cell_monday = day - timedelta(days=day.weekday())
            upcoming = (
                active and next_monday is not None
                and cell_monday == next_monday and coverage == "pending"
            )
            if coverage == "pending":
                details.append(
                    "ΕΠΟΜΕΝΗ εβδομάδα χωρίς δηλωμένο πρόγραμμα"
                    if upcoming else "Αδήλωτο στο ΕΡΓΑΝΗ"
                )
            elif coverage == "modified":
                details.append("Τροποποιημένο — εκκρεμεί υποβολή στο ΕΡΓΑΝΗ")
            if item["closed"]:
                details.append("Κλεισμένη μισθοδοσία — δεν επιτρέπονται αλλαγές")
            cells.append(
                {
                    **item,
                    "active": active,
                    "raw": raw,
                    "short": _compact_schedule(segments),
                    "minutes": minutes,
                    "state": state,
                    "telework": telework,
                    "mixed": mixed,
                    "override": override,
                    "coverage": coverage,
                    "upcoming": upcoming,
                    "leave": leave,
                    "leave_label": leave_label,
                    "contract_end": contract_end,
                    "title": " · ".join(details),
                }
            )

        next_week_pending = (
            next_monday is not None
            and employment.active_days_in_week(next_monday)
            and employment.week_status(next_monday) == "pending"
        )
        pending_count = sum(1 for c in cells if c["coverage"] == "pending")
        modified_count = sum(1 for c in cells if c["coverage"] == "modified")
        rows.append(
            {
                "employment": employment,
                "cells": cells,
                "scheduled_days": scheduled_days,
                "hours": round(total_minutes / 60),
                "overrides": overrides,
                "telework_days": telework_days,
                "mode": employment.schedule_mode,
                "pending_count": pending_count,
                "modified_count": modified_count,
                "next_week_pending": bool(next_week_pending),
            }
        )

    upcoming_names = [
        r["employment"].full_name for r in rows if r["next_week_pending"]
    ]
    next_week = None
    if next_monday is not None and upcoming_names:
        next_week = {
            "label": (
                f"{next_monday.strftime('%d/%m')}–"
                f"{(next_monday + timedelta(days=6)).strftime('%d/%m/%Y')}"
            ),
            "names": upcoming_names,
            "start_index": (next_monday - start).days,
        }

    closed = svc.closed_payroll_period()
    return {
        "year": year,
        "days": days,
        "day_count": day_count,
        "months": months,
        "weeks": weeks,
        "month_names": MONTH_NAMES,
        "rows": rows,
        "next_week": next_week,
        # Κλειδωμένο παρελθόν: το JS δεν επιτρέπει επιλογή/άνοιγμα ημέρας
        # <= closed_end, το CSS το βάφει μωβ.
        "closed_end": closed_end,
        "closed_label": f"{closed[1]}/{closed[0]}" if closed else "",
    }


def _panel(year: int):
    return render_template(
        "partials/_work_schedule_panel.html", **_planner_context(year)
    )


@bp.get("/")
def index():
    return render_template("work_schedules.html", **_planner_context(_year()))


def _day_context(selected: date, focus_id=None, supplied=None) -> dict:
    rows = []
    for employment in sorted(
        (item for item in proj.employments.values() if item.hire_submitted),
        key=lambda item: item.full_name,
    ):
        active = (
            (employment.hire_date is None or selected >= employment.hire_date)
            and (
                employment.termination_date is None
                or selected <= employment.termination_date
            )
        )
        if not active:
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
            if employment.hire_date and day < employment.hire_date:
                return []
            if employment.termination_date and day > employment.termination_date:
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
        return flash_to(_panel(selected.year), message)
    html = render_template(
        "partials/_work_schedule_day_overview.html",
        **_day_context(selected, focus_id, supplied),
    )
    response = flash_to(html, message, err=True)
    response.headers["HX-Retarget"] = "#modal-host"
    response.headers["HX-Reswap"] = "innerHTML"
    return response


@bp.post("/bulk")
def bulk_update():
    year = _year()

    def command():
        try:
            selection = json.loads(request.form.get("selection", "[]"))
        except (TypeError, ValueError):
            raise ValueError("Μη έγκυρη μαζική επιλογή")
        if not isinstance(selection, list) or not selection:
            raise ValueError("Δεν έχουν επιλεγεί ημέρες")
        if len(selection) > 5000:
            raise ValueError("Η μαζική επιλογή είναι υπερβολικά μεγάλη")

        action = request.form.get("action", "")
        if action not in {"save", "delete"}:
            raise ValueError("Άγνωστη μαζική ενέργεια")
        if action == "delete":
            value = ""
        else:
            raw = request.form.get("hours", "").strip()
            segments = constraints.parse_day_schedule(raw)
            if not segments:
                raise ValueError("Συμπληρώστε το ωράριο της μαζικής μεταβολής")
            value = "+".join(
                ("Τ" if segment["telework"] else "") + segment["text"]
                for segment in segments
            )

        grouped = {}
        for item in selection:
            if not isinstance(item, dict):
                raise ValueError("Μη έγκυρο cell στη μαζική επιλογή")
            employment_id = str(item.get("employment_id", ""))
            employment = proj.employments.get(employment_id)
            if employment is None:
                raise ValueError("Η επιλογή περιέχει άγνωστο εργαζόμενο")
            selected = date.fromisoformat(str(item.get("date", "")))
            if (
                (employment.hire_date and selected < employment.hire_date)
                or (employment.termination_date and selected > employment.termination_date)
            ):
                raise ValueError("Η επιλογή περιέχει ανενεργή ημέρα εργαζομένου")
            grouped.setdefault(employment_id, {})[selected] = value

        _ensure_future({d for days in grouped.values() for d in days})
        # Πλήρης προέλεγχος όλων των εργαζομένων πριν γραφτεί το πρώτο event.
        for employment_id, days in grouped.items():
            svc.validate_daily_schedule(employment_id, days)
        for employment_id, days in grouped.items():
            svc.set_daily_schedule(employment_id, days)
        label = {"save": "ωράριο", "delete": "ρεπό"}[action]
        return f"Εφαρμόστηκε {label} σε {len(selection)} επιλεγμένα cells"

    ok, message = run_command(command)
    return flash_to(_panel(year), message, err=not ok)


@bp.post("/week/paste")
def paste_week():
    year = _year()

    def command():
        source_start = form_date(request.form, "source_start")
        target_start = form_date(request.form, "target_start")
        if source_start.weekday() != 0 or target_start.weekday() != 0:
            raise ValueError("Η αντιγραφή εβδομάδας πρέπει να ξεκινά Δευτέρα")
        if source_start == target_start:
            raise ValueError("Η εβδομάδα προέλευσης και προορισμού είναι ίδια")
        _ensure_future([target_start])  # όλη η εβδομάδα-στόχος πρέπει να είναι μελλοντική

        grouped = {}
        changed_cells = 0

        for employment_id, employment in proj.employments.items():
            def active_on(day):
                """Ο εργαζόμενος ήταν σε ισχύ εκείνη την ημέρα;"""
                started = not employment.hire_date or day >= employment.hire_date
                ended = employment.termination_date and day > employment.termination_date
                return started and not ended

            days = {}
            for offset in range(7):
                source_day = source_start + timedelta(days=offset)
                target_day = target_start + timedelta(days=offset)

                source_active = active_on(source_day)
                target_active = active_on(target_day)
                if not source_active or not target_active:
                    continue
                value = employment.day_schedule(source_day)
                if value != employment.day_schedule(target_day):
                    days[target_day] = value
            if days:
                grouped[employment_id] = days
                changed_cells += len(days)

        for employment_id, days in grouped.items():
            svc.validate_daily_schedule(employment_id, days)
        for employment_id, days in grouped.items():
            svc.set_daily_schedule(employment_id, days)
        if not changed_cells:
            return "Δεν υπήρχαν διαφορές για αντιγραφή"
        return (
            f"Αντιγράφηκαν {changed_cells} ημέρες εβδομάδας "
            f"σε {len(grouped)} εργαζομένους"
        )

    ok, message = run_command(command)
    return flash_to(_panel(year), message, err=not ok)


@bp.get("/modal-close")
def modal_close():
    return ""


# ------------------------------------------- Υποβολή προγράμματος στο ΕΡΓΑΝΗ
def _submittable_week_rows(employment, year: int) -> list[dict]:
    """Οι εβδομάδες του έτους με ημέρες που εκκρεμούν δήλωση/επαναδήλωση
    (pending/modified), με σύνοψη ανά εβδομάδα — για το per-row modal."""
    eid = employment.employment_id
    year_start, year_end = date(year, 1, 1), date(year, 12, 31)
    lo = max(employment.hire_date or year_start, year_start)
    hi = min(employment.termination_date or year_end, year_end)
    out = []
    monday = lo - timedelta(days=lo.weekday())
    while monday <= hi:
        days = svc.week_submittable_days(eid, monday)
        if days:
            pending = sum(1 for d in days if employment.day_status(d) == "pending")
            out.append(
                {
                    "iso": monday.isoformat(),
                    "label": (
                        f"{monday.strftime('%d/%m')}–"
                        f"{(monday + timedelta(days=6)).strftime('%d/%m/%Y')}"
                    ),
                    "iso_week": monday.isocalendar().week,
                    "pending": pending,
                    "modified": len(days) - pending,
                }
            )
        monday += timedelta(days=7)
    return out


def _selection_from_weeks(picked_iso, employment_ids=None):
    """{eid: [date,…]} από επιλεγμένες Δευτέρες — οι submittable ημέρες κάθε
    εβδομάδας. Περιορισμός στους `employment_ids` αν δοθεί (per-row modal)."""
    mondays = []
    for iso in picked_iso:
        try:
            mondays.append(date.fromisoformat(iso))
        except ValueError:
            raise ValueError("Μη έγκυρη εβδομάδα") from None
    eids = employment_ids if employment_ids is not None else list(proj.employments)
    selection = {}
    for eid in eids:
        days = []
        for monday in mondays:
            days += svc.week_submittable_days(eid, monday)
        if days:
            selection[eid] = days
    return selection


@bp.get("/<employment_id>/ergani")
def ergani_modal(employment_id):
    employment = proj.employments.get(employment_id) or abort(404)
    year = _year()
    return render_template(
        "partials/_work_schedule_ergani_modal.html",
        e=employment,
        weeks=_submittable_week_rows(employment, year),
        year=year,
    )


@bp.post("/<employment_id>/ergani")
def submit_ergani(employment_id):
    employment = proj.employments.get(employment_id) or abort(404)
    year = _year()

    def command():
        picked = request.form.getlist("week")
        if not picked:
            raise ValueError("Δεν επιλέχθηκε εβδομάδα προς υποβολή")
        selection = _selection_from_weeks(picked, [employment_id])
        if not selection:
            raise ValueError("Δεν υπάρχουν ημέρες προς υποβολή στις εβδομάδες")
        res = svc.submit_schedule_days(selection)
        return (
            f"Υποβολή #{res['batch_no']}: {res['count']} ημερήσια προγράμματα "
            f"στο ΕΡΓΑΝΗ για {employment.full_name}"
        )

    ok, message = run_command(command)
    if ok:
        return flash_to(_panel(year), message)
    html = render_template(
        "partials/_work_schedule_ergani_modal.html",
        e=employment,
        weeks=_submittable_week_rows(employment, year),
        year=year,
    )
    response = flash_to(html, message, err=True)
    response.headers["HX-Retarget"] = "#modal-host"
    response.headers["HX-Reswap"] = "innerHTML"
    return response


@bp.post("/ergani/weeks")
def submit_ergani_weeks():
    """COMPANY-WIDE: υποβολή των εκκρεμών/τροποποιημένων ημερών ΟΛΩΝ των
    εργαζομένων για τις επιλεγμένες (διαδοχικές) εβδομάδες, σε ΕΝΑ batch."""
    year = _year()

    def command():
        picked = request.form.getlist("week") or json.loads(
            request.form.get("weeks", "[]") or "[]"
        )
        if not picked:
            raise ValueError("Δεν επιλέχθηκαν εβδομάδες")
        selection = _selection_from_weeks(picked)
        if not selection:
            raise ValueError("Καμία εκκρεμής ημέρα στις επιλεγμένες εβδομάδες")
        res = svc.submit_schedule_days(selection)
        return (
            f"Υποβολή #{res['batch_no']}: {res['count']} ημερήσια προγράμματα "
            f"στο ΕΡΓΑΝΗ ({res['employees']} εργαζόμενοι)"
        )

    ok, message = run_command(command)
    return flash_to(_panel(year), message, err=not ok)


@bp.post("/ergani/revert")
def revert_changes():
    """Ακύρωση μη υποβεβλημένων αλλαγών — επαναφορά στη δηλωμένη τιμή ΕΡΓΑΝΗ."""
    year = _year()

    def command():
        try:
            cells = json.loads(request.form.get("cells", "[]") or "[]")
        except (TypeError, ValueError):
            raise ValueError("Μη έγκυρη επιλογή") from None
        selection = {}
        for item in cells:
            eid = str(item.get("employment_id", ""))
            if eid not in proj.employments:
                raise ValueError("Άγνωστος εργαζόμενος στην επιλογή")
            selection.setdefault(eid, []).append(item.get("date", ""))
        if not selection:
            raise ValueError("Δεν επιλέχθηκαν αλλαγές προς ακύρωση")
        n = svc.revert_days(selection)
        if not n:
            return "Δεν υπήρχαν μη υποβεβλημένες αλλαγές προς ακύρωση"
        return f"Ακυρώθηκαν {n} αλλαγές — επαναφορά στη δηλωμένη τιμή"

    ok, message = run_command(command)
    return flash_to(_panel(year), message, err=not ok)


@bp.get("/<employment_id>/day")
def day_modal(employment_id):
    employment = proj.employments.get(employment_id) or abort(404)
    try:
        selected = date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        abort(400)
    return render_template(
        "partials/_work_schedule_day_modal.html",
        e=employment,
        selected=selected,
        current_hours=employment.day_schedule(selected),
        year=_year(),
    )


@bp.post("/<employment_id>/day")
def update_days(employment_id):
    employment = proj.employments.get(employment_id) or abort(404)
    year = _year()

    def command():
        start = form_date(request.form, "date_from")
        end = form_date(request.form, "date_to")
        if end < start:
            raise ValueError("Η ημερομηνία «έως» προηγείται της «από»")
        _ensure_future([start])  # όλο το διάστημα πρέπει να είναι μελλοντικό
        hours = (request.form.get("hours") or "").strip()
        days, current = {}, start
        while current <= end:
            days[current] = hours
            current += timedelta(days=1)
        svc.set_daily_schedule(employment_id, days)
        what = hours or "ΡΕΠΟ"
        return f"Ενημερώθηκαν {len(days)} ημέρες · {what}"

    ok, message = run_command(command)
    if ok:
        return flash_to(_panel(year), message)
    selected = form_date(request.form, "date_from", date(year, 1, 1))
    html = render_template(
        "partials/_work_schedule_day_modal.html",
        e=employment,
        selected=selected,
        current_hours=request.form.get("hours", ""),
        year=year,
        form=request.form,
    )
    response = flash_to(html, message, err=True)
    response.headers["HX-Retarget"] = "#modal-host"
    response.headers["HX-Reswap"] = "innerHTML"
    return response
