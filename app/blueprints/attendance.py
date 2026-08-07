"""Timesheet: clocking, monthly view, task logging."""

from __future__ import annotations

from datetime import date, time
from typing import Any

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.models import AttendanceStatus
from app.services import attendance as svc
from app.services import employees as emp_svc

bp = Blueprint("attendance", __name__)


def _parse_date(value: str | None, fallback: date | None = None) -> date | None:
    try:
        return date.fromisoformat(value) if value else fallback
    except ValueError:
        return fallback


def _parse_time(value: str | None) -> time | None:
    try:
        return time.fromisoformat(value) if value else None
    except ValueError:
        return None


def _parse_float(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


@bp.route("/")
@login_required
def index() -> Any:
    """Whose timesheet - your own unless you asked for someone you may see."""
    target_id = request.args.get("employee", type=int) or current_user.id
    employee = emp_svc.get_visible(current_user, target_id)
    if employee is None:
        abort(404)

    today = date.today()
    year = request.args.get("year", type=int) or today.year
    month_no = request.args.get("month", type=int) or today.month
    if not 1 <= month_no <= 12:
        abort(400)

    logs = svc.month(employee.id, year, month_no)
    return render_template(
        "attendance/index.html",
        employee=employee,
        logs=logs,
        summary=svc.month_summary(employee.id, year, month_no),
        today_log=svc.day(employee.id, today),
        today_totals=svc.day_totals(employee.id, today),
        year=year,
        month_no=month_no,
        is_self=employee.id == current_user.id,
    )


@bp.route("/clock-in", methods=["POST"])
@login_required
def clock_in() -> Any:
    status = AttendanceStatus.REMOTE if request.form.get("remote") else AttendanceStatus.PRESENT
    try:
        # An explicit time covers "I started at 8 but only opened this at 10".
        log = svc.clock_in(current_user, at=_parse_time(request.form.get("at")), status=status)
        flash(f"Clocked in at {log.clock_in:%H:%M}.", "success")
    except svc.AttendanceError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("attendance.index"))


@bp.route("/clock-out", methods=["POST"])
@login_required
def clock_out() -> Any:
    try:
        log = svc.clock_out(current_user, at=_parse_time(request.form.get("at")))
        flash(f"Clocked out at {log.clock_out:%H:%M} - {log.hours_worked} hours.", "success")
    except svc.AttendanceError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("attendance.index"))


@bp.route("/day/<the_date>", methods=["GET", "POST"])
@login_required
def day(the_date: str) -> Any:
    on = _parse_date(the_date)
    if on is None:
        abort(404)

    target_id = request.args.get("employee", type=int) or current_user.id
    employee = emp_svc.get_visible(current_user, target_id)
    if employee is None:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action", "task")

        if action == "task":
            try:
                svc.log_task(
                    current_user,
                    employee.id,
                    project_id=request.form.get("project_id", type=int) or 0,
                    on=on,
                    hours=_parse_float(request.form.get("hours")) or 0,
                    description=request.form.get("description", ""),
                    remarks=request.form.get("remarks"),
                )
                flash("Time logged.", "success")
                return redirect(url_for("attendance.day", the_date=the_date, employee=employee.id))
            except svc.AttendanceError as exc:
                flash(str(exc), "danger")

        elif action == "record":
            try:
                status = AttendanceStatus(request.form.get("status", ""))
            except ValueError:
                abort(400)
            try:
                svc.record_day(
                    current_user,
                    employee.id,
                    on=on,
                    status=status,
                    clock_in_at=_parse_time(request.form.get("clock_in")),
                    clock_out_at=_parse_time(request.form.get("clock_out")),
                    notes=request.form.get("notes"),
                )
                flash("Day updated.", "success")
                return redirect(url_for("attendance.day", the_date=the_date, employee=employee.id))
            except svc.AttendanceError as exc:
                flash(str(exc), "danger")

    return render_template(
        "attendance/day.html",
        employee=employee,
        on=on,
        log=svc.day(employee.id, on),
        tasks=svc.tasks_for_day(employee.id, on),
        totals=svc.day_totals(employee.id, on),
        projects=svc.active_projects(on),
        statuses=list(AttendanceStatus),
        editable=svc.may_edit(current_user, employee.id, on),
    )


@bp.route("/task/<int:task_id>/delete", methods=["POST"])
@login_required
def delete_task(task_id: int) -> Any:
    the_date = request.form.get("date", "")
    try:
        svc.delete_task(current_user, task_id)
        flash("Entry removed.", "info")
    except svc.AttendanceError as exc:
        flash(str(exc), "danger")

    if _parse_date(the_date):
        return redirect(url_for("attendance.day", the_date=the_date))
    return redirect(url_for("attendance.index"))
