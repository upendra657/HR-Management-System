"""Leave requests and approvals.

Views stay thin: parse the form, call the service, translate LeaveError into
a flash message. Every rule lives in app/services/leave.py.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.models import LeaveType
from app.security import manager_required
from app.services import leave as svc

bp = Blueprint("leave", __name__)


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@bp.route("/")
@login_required
def index() -> Any:
    return render_template(
        "leave/index.html",
        requests=svc.for_employee(current_user.id),
        balance=svc.balance(current_user),
        pending_count=len(svc.pending_for_approver(current_user)),
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new() -> Any:
    form = {
        "leave_type": request.form.get("leave_type", LeaveType.ANNUAL.value),
        "start": request.form.get("start", ""),
        "end": request.form.get("end", ""),
        "reason": request.form.get("reason", ""),
    }

    if request.method == "POST":
        start = _parse_date(form["start"])
        end = _parse_date(form["end"])

        try:
            leave_type = LeaveType(form["leave_type"])
        except ValueError:
            abort(400)

        if start is None or end is None:
            flash("Enter both a start and an end date.", "danger")
        else:
            try:
                svc.submit(
                    current_user,
                    leave_type=leave_type,
                    start=start,
                    end=end,
                    reason=form["reason"],
                )
                flash("Leave request submitted.", "success")
                return redirect(url_for("leave.index"))
            except svc.LeaveError as exc:
                flash(str(exc), "danger")

    return render_template(
        "leave/new.html",
        form=form,
        types=list(LeaveType),
        balance=svc.balance(current_user),
        today=date.today().isoformat(),
    )


@bp.route("/approvals")
@login_required
@manager_required
def approvals() -> Any:
    return render_template(
        "leave/approvals.html",
        requests=svc.pending_for_approver(current_user),
    )


@bp.route("/<int:request_id>/<any(approve,reject):action>", methods=["POST"])
@login_required
@manager_required
def decide(request_id: int, action: str) -> Any:
    leave_request = svc.get_for_actor(current_user, request_id)
    if leave_request is None:
        abort(404)

    note = request.form.get("note")
    try:
        if action == "approve":
            svc.approve(leave_request, current_user, note)
            flash(
                f"Approved {leave_request.employee.full_name}'s leave. "
                "The days have been marked on their timesheet.",
                "success",
            )
        else:
            svc.reject(leave_request, current_user, note)
            flash(f"Rejected {leave_request.employee.full_name}'s request.", "info")
    except svc.LeaveError as exc:
        flash(str(exc), "danger")

    return redirect(url_for("leave.approvals"))


@bp.route("/<int:request_id>/cancel", methods=["POST"])
@login_required
def cancel(request_id: int) -> Any:
    leave_request = svc.get_for_actor(current_user, request_id)
    if leave_request is None:
        abort(404)

    try:
        svc.cancel(leave_request, current_user)
        flash("Request cancelled.", "info")
    except svc.LeaveError as exc:
        flash(str(exc), "danger")

    return redirect(url_for("leave.index"))
