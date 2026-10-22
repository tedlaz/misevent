"""Άδειες: modals δημιουργίας/επεξεργασίας — τα ανοίγουν τα «Ζωντανά».

Η δημιουργία γίνεται με double click αρχή + τέλος στον μήνα (live-board.js
στέλνει κατευθείαν POST)· η επεξεργασία με κλικ σε ζώνη άδειας. Η ακύρωση
ζει στην καρτέλα απασχόλησης."""


from flask import Blueprint, abort, render_template, request

from misevent.domain.events import LeaveType

from ..clock import today
from ..helpers import form_date, live_done, modal_error, run_command
from ..labels import LEAVE_LABELS
from ..system import proj, svc

bp = Blueprint("leaves", __name__, url_prefix="/leaves")


def _leave_or_404(employment_id, leave_id):
    employment = proj.employments.get(employment_id) or abort(404)
    leave = next(
        (item for item in employment.active_leaves() if item.leave_id == leave_id),
        None,
    )
    return employment, leave or abort(404)


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
        leave_labels=LEAVE_LABELS,
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
        return live_done(message)
    selected = form_date(request.form, "date_from", today())
    html = render_template(
        "partials/_leave_create_modal.html",
        e=employment,
        selected_date=selected,
        leave_labels=LEAVE_LABELS,
        form=request.form,
    )
    return modal_error(html, message)


@bp.post("/<employment_id>/<leave_id>")
def update_leave(employment_id, leave_id):
    _leave_or_404(employment_id, leave_id)

    def command():
        svc.reschedule_leave(
            employment_id,
            leave_id,
            _leave_type_from_form(),
            form_date(request.form, "date_from"),
            form_date(request.form, "date_to"),
            request.form.get("notes", ""),
        )
        return "Η άδεια ενημερώθηκε"

    ok, message = run_command(command)
    if ok:
        return live_done(message)
    employment, leave = _leave_or_404(employment_id, leave_id)
    html = render_template(
        "partials/_leave_edit_modal.html",
        e=employment,
        lv=leave,
        leave_labels=LEAVE_LABELS,
        form=request.form,
    )
    return modal_error(html, message)
