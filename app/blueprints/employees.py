"""Employee directory."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, current_app, render_template, request
from flask_login import current_user, login_required

from app.models import EmployeeStatus
from app.services import employees as svc

bp = Blueprint("employees", __name__)


def _int_arg(name: str, default: int) -> int:
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


@bp.route("/")
@login_required
def index() -> Any:
    status = None
    raw_status = request.args.get("status")
    if raw_status:
        try:
            status = EmployeeStatus(raw_status)
        except ValueError:
            abort(400)

    page = svc.search(
        current_user,
        q=request.args.get("q") or None,
        department_id=_int_arg("department", 0) or None,
        status=status,
        page=_int_arg("page", 1),
        per_page=current_app.config["ITEMS_PER_PAGE"],
    )

    return render_template(
        "employees/index.html",
        page=page,
        departments=svc.departments(),
        statuses=list(EmployeeStatus),
        q=request.args.get("q", ""),
        selected_department=_int_arg("department", 0),
        selected_status=raw_status or "",
    )


@bp.route("/<int:employee_id>")
@login_required
def detail(employee_id: int) -> Any:
    employee = svc.get_visible(current_user, employee_id)
    if employee is None:
        # 404 rather than 403 - telling someone "you may not see employee 17"
        # confirms employee 17 exists.
        abort(404)

    return render_template(
        "employees/detail.html",
        employee=employee,
        summary=svc.attendance_summary(employee.id),
        attendance=svc.recent_attendance(employee.id),
        tasks=svc.recent_tasks(employee.id),
    )
