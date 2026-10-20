"""Κεντρικός ετήσιος προγραμματισμός αδειών."""

import json
from calendar import monthrange
from datetime import date, timedelta

from flask import Blueprint, abort, jsonify, render_template, request

from misevent.domain.events import LeaveType
from misevent.rules.calendar_gr import is_working_day, working_days_in_range

from ..clock import today
from ..helpers import flash_to, form_date, run_command
from ..labels import LEAVE_LABELS
from ..queries import balance_tone as _balance_tone
from ..system import proj, svc

bp = Blueprint("leaves", __name__, url_prefix="/leaves")


def _year() -> int:
    year = request.values.get("year", type=int) or today().year
    if not 1900 <= year <= 2100:
        abort(400)
    return year


def _leave_or_404(employment_id, leave_id):
    employment = proj.employments.get(employment_id) or abort(404)
    leave = next(
        (item for item in employment.active_leaves() if item.leave_id == leave_id),
        None,
    )
    return employment, leave or abort(404)


def _days_label(date_from: date, date_to: date) -> str:
    """Πλήθος εργάσιμων ημερών άδειας σύμφωνα με το ημερολόγιο."""
    return str(working_days_in_range(date_from, date_to))


def _accrual(employment, year: int, reference_date: date) -> dict:
    """Κανονική άδεια: σύνοψη έως την ημερομηνία αναφοράς + υπόλοιπο στο τέλος
    κάθε μήνα. Κοινό για το πλάνο αδειών και το πρόγραμμα εργασίας."""
    employment_id = employment.employment_id
    accrued_reference = svc.annual_leave_accrued(employment_id, reference_date)
    taken_reference = svc.annual_leave_taken_until(employment_id, reference_date)
    planned_total = svc.annual_leave_taken(employment_id, year)
    balance_reference = round(accrued_reference - taken_reference, 2)
    year_start = date(year, 1, 1)
    monthly_accrual = []
    for month in range(1, 13):
        month_days = monthrange(year, month)[1]
        checkpoint = date(year, month, month_days)
        active = (
            (employment.hire_date is None or checkpoint >= employment.hire_date)
            and (
                employment.termination_date is None
                or date(year, month, 1) <= employment.termination_date
            )
        )
        accrued = svc.annual_leave_accrued(employment_id, checkpoint)
        taken = svc.annual_leave_taken_until(employment_id, checkpoint)
        balance = round(accrued - taken, 2)
        monthly_accrual.append(
            {
                "month": month,
                "days": month_days,
                "checkpoint": checkpoint,
                "checkpoint_offset": (checkpoint - year_start).days,
                "accrued": accrued,
                "taken": taken,
                "balance": balance,
                "tone": _balance_tone(balance, accrued) if active else "neutral",
                "active": active,
            }
        )
    return {
        "entitlement": svc.annual_leave_entitlement(employment_id, year),
        "reference_date": reference_date,
        "reference_offset": (reference_date - year_start).days,
        "accrued": accrued_reference,
        "taken": taken_reference,
        "planned": planned_total,
        "future_planned": max(0, planned_total - taken_reference),
        "balance": balance_reference,
        "tone": _balance_tone(balance_reference, accrued_reference),
        "months": monthly_accrual,
    }


def _planner_context(year: int) -> dict:
    year_start, year_end = date(year, 1, 1), date(year, 12, 31)
    days_in_year = (year_end - year_start).days + 1
    reference_date = min(max(today(), year_start), year_end)
    month_data = [(month, monthrange(year, month)[1]) for month in range(1, 13)]
    rows = []
    employments = sorted(
        (e for e in proj.employments.values() if e.hire_submitted),
        key=lambda e: e.full_name,
    )
    for employment in employments:
        if employment.hire_date and employment.hire_date > year_end:
            continue
        if employment.termination_date and employment.termination_date < year_start:
            continue

        inactive = []
        if employment.hire_date and employment.hire_date > year_start:
            inactive_end = min(employment.hire_date - timedelta(days=1), year_end)
            length = (inactive_end - year_start).days + 1
            inactive.append(
                {"left": 0.0, "width": length / days_in_year * 100,
                 "label": f"Δεν εργάζεται έως {inactive_end.strftime('%d/%m/%Y')}"}
            )
        if employment.termination_date and employment.termination_date < year_end:
            inactive_start = max(
                employment.termination_date + timedelta(days=1), year_start
            )
            offset = (inactive_start - year_start).days
            length = (year_end - inactive_start).days + 1
            inactive.append(
                {"left": offset / days_in_year * 100,
                 "width": length / days_in_year * 100,
                 "label": f"Δεν εργάζεται από {inactive_start.strftime('%d/%m/%Y')}"}
            )

        contract_end = None
        if (
            employment.employment_relationship == "1"
            and employment.fixed_term_to is not None
            and year_start <= employment.fixed_term_to <= year_end
        ):
            offset = (employment.fixed_term_to - year_start).days
            contract_end = {
                "date": employment.fixed_term_to,
                "left": (offset + 0.5) / days_in_year * 100,
                "label": (
                    "Προβλεπόμενη λήξη σύμβασης ορισμένου χρόνου · "
                    f"{employment.fixed_term_to.strftime('%d/%m/%Y')}"
                ),
            }

        visible = []
        for leave in sorted(
            employment.active_leaves(), key=lambda item: (item.date_from, item.date_to)
        ):
            if leave.date_to < year_start or leave.date_from > year_end:
                continue
            start = max(leave.date_from, year_start)
            end = min(leave.date_to, year_end)
            visible.append((leave, start, end))

        # Παράλληλες/επικαλυπτόμενες άδειες μπαίνουν σε ξεχωριστές λωρίδες.
        lane_ends: list[date] = []
        bars = []
        for leave, start, end in visible:
            lane = next(
                (index for index, lane_end in enumerate(lane_ends) if start > lane_end),
                len(lane_ends),
            )
            if lane == len(lane_ends):
                lane_ends.append(end)
            else:
                lane_ends[lane] = end
            offset = (start - year_start).days
            length = (end - start).days + 1
            bars.append(
                {
                    "leave": leave,
                    "label": LEAVE_LABELS.get(leave.leave_type.value, leave.leave_type.value),
                    "days_label": _days_label(start, end),
                    "left": offset / days_in_year * 100,
                    "width": length / days_in_year * 100,
                    "lane": lane,
                    "start_clipped": leave.date_from < year_start,
                    "end_clipped": leave.date_to > year_end,
                }
            )

        accrual_daily = [
            svc.annual_leave_accrued(
                employment.employment_id, year_start + timedelta(days=offset)
            )
            for offset in range(days_in_year)
        ]
        rows.append(
            {
                "employment": employment,
                "bars": bars,
                "inactive": inactive,
                "contract_end": contract_end,
                "height": 70 + (max(len(lane_ends), 1) - 1) * 28,
                "accrual_daily": accrual_daily,
                "accrual": _accrual(employment, year, reference_date),
            }
        )

    # ΚΛΕΙΔΩΜΕΝΟ ΠΑΡΕΛΘΟΝ: ό,τι ανήκει σε κλεισμένη μισθοδοσία δεν αλλάζει
    # (§5.6). Το χρωματίζουμε ως ΜΩΒ ΖΩΝΗ πάνω στο ίδιο ποσοστιαίο timeline
    # με τις υπόλοιπες μπάρες, και το JS δεν επιτρέπει δημιουργία/μετακίνηση.
    closed = svc.closed_payroll_period()
    closed_end = svc.closed_period_end()
    closed_band = None
    if closed_end is not None and closed_end >= year_start:
        visible_end = min(closed_end, year_end)
        closed_band = {
            "width": ((visible_end - year_start).days + 1) / days_in_year * 100,
            "label": f"Κλεισμένη μισθοδοσία έως {visible_end.strftime('%d/%m/%Y')}",
        }
    return {
        "year": year,
        "rows": rows,
        "days_in_year": days_in_year,
        "months": month_data,
        "closed_end": closed_end,
        "closed_label": f"{closed[1]}/{closed[0]}" if closed else "",
        "closed_band": closed_band,
        "closed_months": [
            month for month, _ in month_data if svc.is_period_closed(year, month)
        ],
        "workday_offsets": [
            offset
            for offset in range(days_in_year)
            if is_working_day(year_start + timedelta(days=offset))
        ],
        "reference_date": reference_date,
        "leave_labels": LEAVE_LABELS,
    }


def _panel(year: int):
    return render_template("partials/_leave_planner_panel.html", **_planner_context(year))


def _back() -> str:
    """Από ποια σελίδα άνοιξε το modal: "work" = πρόγραμμα εργασίας,
    "live" = Ζωντανά (εκεί δεν υπάρχει panel — κλείνει το modal)."""
    back = request.values.get("back")
    return back if back in ("work", "live") else ""


def _return_panel(year: int):
    """Το panel που ανανεώνεται μετά την αποθήκευση — της σελίδας προέλευσης."""
    if _back() == "live":
        return ""
    if _back():
        # lazy: το work_schedules εισάγει το `_accrual` από εδώ
        from .work_schedules import _panel as work_panel

        return work_panel(year)
    return _panel(year)


def _saved(year: int, message: str):
    """Επιτυχία: panel της σελίδας προέλευσης + toast· στα Ζωντανά και
    εντολή ανανέωσης του grid (`lb-refresh`)."""
    resp = flash_to(_return_panel(year), message)
    if _back() == "live":
        resp.headers["HX-Trigger"] = json.dumps(
            {"flash": {"msg": message, "err": False}, "lb-refresh": True}
        )
    return resp


@bp.get("/")
def index():
    return render_template("leaves.html", **_planner_context(_year()))


@bp.get("/modal-close")
def modal_close():
    return ""


@bp.get("/<employment_id>/<leave_id>/modal")
def leave_modal(employment_id, leave_id):
    employment, leave = _leave_or_404(employment_id, leave_id)
    return render_template(
        "partials/_leave_edit_modal.html",
        e=employment,
        lv=leave,
        year=_year(),
        leave_labels=LEAVE_LABELS,
        back=_back(),
    )


@bp.get("/<employment_id>/new")
def new_leave_modal(employment_id):
    employment = proj.employments.get(employment_id) or abort(404)
    selected = request.args.get("date", "")
    try:
        selected_date = date.fromisoformat(selected)
        selected_to = date.fromisoformat(request.args.get("date_to") or selected)
    except (TypeError, ValueError):
        abort(400)
    return render_template(
        "partials/_leave_create_modal.html",
        e=employment,
        selected_date=selected_date,
        selected_to=max(selected_to, selected_date),
        year=_year(),
        leave_labels=LEAVE_LABELS,
        back=_back(),
    )


def _leave_type_from_form():
    leave_type = request.form.get("leave_type", "ANNUAL")
    try:
        return LeaveType[leave_type]
    except KeyError:
        raise ValueError("Άγνωστος τύπος άδειας") from None


@bp.post("/<employment_id>/new")
def create_leave(employment_id):
    employment = proj.employments.get(employment_id) or abort(404)
    year = _year()

    def command():
        kind = _leave_type_from_form()
        start = form_date(request.form, "date_from")
        end = form_date(request.form, "date_to")
        notes = request.form.get("notes", "")
        if kind == LeaveType.ANNUAL:
            svc.plan_annual_leave(employment_id, start, end, notes)
        else:
            svc.record_leave(employment_id, kind, start, end, notes)
        return "Η άδεια δημιουργήθηκε"

    ok, message = run_command(command)
    if ok:
        return _saved(year, message)
    selected = form_date(request.form, "date_from", date(year, 1, 1))
    html = render_template(
        "partials/_leave_create_modal.html",
        e=employment,
        selected_date=selected,
        year=year,
        leave_labels=LEAVE_LABELS,
        form=request.form,
        back=_back(),
    )
    response = flash_to(html, message, err=True)
    response.headers["HX-Retarget"] = "#modal-host"
    response.headers["HX-Reswap"] = "innerHTML"
    return response


def _reschedule(employment_id, leave_id):
    kind = _leave_type_from_form()
    start = form_date(request.form, "date_from")
    end = form_date(request.form, "date_to")
    svc.reschedule_leave(
        employment_id,
        leave_id,
        kind,
        start,
        end,
        request.form.get("notes", ""),
    )
    return start, end


@bp.post("/<employment_id>/<leave_id>")
def update_leave(employment_id, leave_id):
    _leave_or_404(employment_id, leave_id)
    year = _year()

    def command():
        _reschedule(employment_id, leave_id)
        return "Η άδεια ενημερώθηκε"

    ok, message = run_command(command)
    if ok:
        return _saved(year, message)
    employment, leave = _leave_or_404(employment_id, leave_id)
    html = render_template(
        "partials/_leave_edit_modal.html",
        e=employment,
        lv=leave,
        year=year,
        leave_labels=LEAVE_LABELS,
        form=request.form,
        back=_back(),
    )
    response = flash_to(html, message, err=True)
    response.headers["HX-Retarget"] = "#modal-host"
    response.headers["HX-Reswap"] = "innerHTML"
    return response


@bp.post("/<employment_id>/<leave_id>/drag")
def drag_leave(employment_id, leave_id):
    _leave_or_404(employment_id, leave_id)

    result = {}

    def command():
        start, end = _reschedule(employment_id, leave_id)
        result.update(date_from=start.isoformat(), date_to=end.isoformat())
        return "Η περίοδος άδειας ενημερώθηκε"

    ok, message = run_command(command)
    if not ok:
        return jsonify(ok=False, message=message), 400
    return jsonify(ok=True, message=message, **result)
