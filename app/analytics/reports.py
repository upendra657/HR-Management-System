"""Company-wide aggregations.

All of these group in SQL and return small result sets. The temptation is to
pull rows into pandas and group there, which reads more naturally but moves
80,000 rows across the wire to produce twelve. pandas earns its place at the
export step, not the aggregation step.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Integer, and_, case, cast, func, select

from app.analytics.expressions import is_closed_shift, worked_minutes
from app.extensions import db
from app.models import (
    AttendanceLog,
    AttendanceStatus,
    Department,
    Employee,
    EmployeeStatus,
    LeaveRequest,
    LeaveStatus,
    Project,
    Task,
)

WORKED = (AttendanceStatus.PRESENT, AttendanceStatus.REMOTE)


@dataclass(frozen=True)
class Headline:
    headcount: int
    departments: int
    active_projects: int
    hours_30d: float
    attendance_rate_30d: float
    open_leave_requests: int
    on_leave_today: int


def headline() -> Headline:
    since = date.today() - timedelta(days=30)

    headcount = (
        db.session.scalar(
            select(func.count())
            .select_from(Employee)
            .where(Employee.status == EmployeeStatus.ACTIVE)
        )
        or 0
    )

    minutes = (
        db.session.scalar(
            select(func.coalesce(func.sum(worked_minutes()), 0)).where(
                AttendanceLog.work_date >= since, is_closed_shift()
            )
        )
        or 0
    )

    # Attendance rate over the same window, holidays excluded from the base.
    counts = dict(
        db.session.execute(
            select(AttendanceLog.status, func.count())
            .where(AttendanceLog.work_date >= since)
            .group_by(AttendanceLog.status)
        ).all()
    )
    countable = sum(n for s, n in counts.items() if s is not AttendanceStatus.HOLIDAY)
    worked = sum(counts.get(s, 0) for s in WORKED)

    today = date.today()
    return Headline(
        headcount=headcount,
        departments=db.session.scalar(select(func.count()).select_from(Department)) or 0,
        active_projects=db.session.scalar(
            select(func.count()).select_from(Project).where(Project.is_active.is_(True))
        )
        or 0,
        hours_30d=round(float(minutes) / 60, 1),
        attendance_rate_30d=round(100 * worked / countable, 1) if countable else 0.0,
        open_leave_requests=db.session.scalar(
            select(func.count())
            .select_from(LeaveRequest)
            .where(LeaveRequest.status == LeaveStatus.PENDING)
        )
        or 0,
        on_leave_today=db.session.scalar(
            select(func.count())
            .select_from(LeaveRequest)
            .where(
                LeaveRequest.status == LeaveStatus.APPROVED,
                LeaveRequest.start_date <= today,
                LeaveRequest.end_date >= today,
            )
        )
        or 0,
    )


def headcount_by_department() -> list[dict[str, Any]]:
    rows = db.session.execute(
        select(
            Department.name,
            func.count(Employee.id).label("headcount"),
            func.sum(case((Employee.status == EmployeeStatus.ACTIVE, 1), else_=0)).label("active"),
        )
        .join(Employee, Employee.department_id == Department.id)
        .group_by(Department.name)
        .order_by(func.count(Employee.id).desc())
    ).all()
    return [
        {"department": r.name, "headcount": r.headcount, "active": int(r.active or 0)} for r in rows
    ]


def attendance_by_month(months: int = 12) -> list[dict[str, Any]]:
    """Monthly attendance mix. Grouped on a formatted date so it works on both
    backends - date_trunc is Postgres-only."""
    since = date.today() - timedelta(days=months * 31)
    period = func.strftime("%Y-%m", AttendanceLog.work_date)
    if db.engine.dialect.name != "sqlite":
        period = func.to_char(AttendanceLog.work_date, "YYYY-MM")

    rows = db.session.execute(
        select(
            period.label("period"),
            func.count().label("records"),
            func.sum(case((AttendanceLog.status.in_(WORKED), 1), else_=0)).label("worked"),
            func.sum(case((AttendanceLog.status == AttendanceStatus.ABSENT, 1), else_=0)).label(
                "absent"
            ),
            func.sum(case((AttendanceLog.status == AttendanceStatus.LEAVE, 1), else_=0)).label(
                "leave"
            ),
            func.sum(case((AttendanceLog.status == AttendanceStatus.HOLIDAY, 1), else_=0)).label(
                "holiday"
            ),
            func.coalesce(func.sum(case((is_closed_shift(), worked_minutes()), else_=0)), 0).label(
                "minutes"
            ),
        )
        .where(AttendanceLog.work_date >= since)
        .group_by(period)
        .order_by(period)
    ).all()

    out = []
    for r in rows:
        countable = int(r.records) - int(r.holiday or 0)
        out.append(
            {
                "period": r.period,
                "records": int(r.records),
                "worked": int(r.worked or 0),
                "absent": int(r.absent or 0),
                "leave": int(r.leave or 0),
                "hours": round(float(r.minutes or 0) / 60, 1),
                "attendance_rate": round(100 * int(r.worked or 0) / countable, 1)
                if countable
                else 0.0,
            }
        )
    return out


def project_utilisation(days: int = 90, limit: int = 15) -> list[dict[str, Any]]:
    since = date.today() - timedelta(days=days)
    rows = db.session.execute(
        select(
            Project.code,
            Project.name,
            Project.client,
            Project.is_active,
            func.coalesce(func.sum(Task.hours), 0).label("hours"),
            func.count(func.distinct(Task.employee_id)).label("people"),
        )
        .join(Task, and_(Task.project_id == Project.id, Task.task_date >= since))
        .group_by(Project.code, Project.name, Project.client, Project.is_active)
        .order_by(func.sum(Task.hours).desc())
        .limit(limit)
    ).all()
    return [
        {
            "code": r.code,
            "name": r.name,
            "client": r.client or "Internal",
            "active": bool(r.is_active),
            "hours": round(float(r.hours), 1),
            "people": int(r.people),
        }
        for r in rows
    ]


def leave_by_type(days: int = 365) -> list[dict[str, Any]]:
    since = date.today() - timedelta(days=days)
    rows = db.session.execute(
        select(
            LeaveRequest.leave_type,
            func.count().label("requests"),
            func.coalesce(func.sum(LeaveRequest.days), 0).label("days"),
            func.sum(case((LeaveRequest.status == LeaveStatus.APPROVED, 1), else_=0)).label(
                "approved"
            ),
        )
        .where(LeaveRequest.start_date >= since)
        .group_by(LeaveRequest.leave_type)
        .order_by(func.sum(LeaveRequest.days).desc())
    ).all()
    return [
        {
            "type": r.leave_type.label if hasattr(r.leave_type, "label") else str(r.leave_type),
            "requests": int(r.requests),
            "days": float(r.days),
            "approved": int(r.approved or 0),
        }
        for r in rows
    ]


def top_hours(days: int = 30, limit: int = 10) -> list[dict[str, Any]]:
    """Highest recorded hours - a crude overwork signal, not a leaderboard."""
    since = date.today() - timedelta(days=days)
    rows = db.session.execute(
        select(
            Employee.employee_code,
            Employee.full_name,
            Department.name.label("department"),
            func.coalesce(func.sum(worked_minutes()), 0).label("minutes"),
            func.count().label("days"),
        )
        .select_from(AttendanceLog)
        .join(Employee, Employee.id == AttendanceLog.employee_id)
        .join(Department, Department.id == Employee.department_id)
        .where(AttendanceLog.work_date >= since, is_closed_shift())
        .group_by(Employee.employee_code, Employee.full_name, Department.name)
        .order_by(func.sum(worked_minutes()).desc())
        .limit(limit)
    ).all()
    return [
        {
            "employee": f"{r.employee_code} {r.full_name}",
            "department": r.department,
            "hours": round(float(r.minutes) / 60, 1),
            "days": int(r.days),
            "avg": round(float(r.minutes) / 60 / int(r.days), 2) if r.days else 0.0,
        }
        for r in rows
    ]


def department_hours(days: int = 30) -> list[dict[str, Any]]:
    since = date.today() - timedelta(days=days)
    rows = db.session.execute(
        select(
            Department.name,
            func.coalesce(func.sum(worked_minutes()), 0).label("minutes"),
            func.count(func.distinct(AttendanceLog.employee_id)).label("people"),
        )
        .select_from(AttendanceLog)
        .join(Employee, Employee.id == AttendanceLog.employee_id)
        .join(Department, Department.id == Employee.department_id)
        .where(AttendanceLog.work_date >= since, is_closed_shift())
        .group_by(Department.name)
        .order_by(func.sum(worked_minutes()).desc())
    ).all()
    return [
        {
            "department": r.name,
            "hours": round(float(r.minutes) / 60, 1),
            "people": int(r.people),
            "avg_per_person": round(float(r.minutes) / 60 / int(r.people), 1) if r.people else 0.0,
        }
        for r in rows
    ]


def overtime_days(days: int = 30, threshold: int = 9) -> int:
    """Closed shifts longer than the threshold."""
    since = date.today() - timedelta(days=days)
    return (
        db.session.scalar(
            select(func.count())
            .select_from(AttendanceLog)
            .where(
                AttendanceLog.work_date >= since,
                is_closed_shift(),
                worked_minutes() > threshold * 60,
            )
        )
        or 0
    )


# --------------------------------------------------------------------------
def timesheet_export(start: date, end: date) -> list[dict[str, Any]]:
    """Flat rows for CSV/Excel: one line per attendance day with hours."""
    rows = db.session.execute(
        select(
            Employee.employee_code,
            Employee.full_name,
            Department.name.label("department"),
            AttendanceLog.work_date,
            AttendanceLog.status,
            AttendanceLog.clock_in,
            AttendanceLog.clock_out,
            case(
                (is_closed_shift(), cast(worked_minutes(), Integer)),
                else_=0,
            ).label("minutes"),
        )
        .select_from(AttendanceLog)
        .join(Employee, Employee.id == AttendanceLog.employee_id)
        .join(Department, Department.id == Employee.department_id)
        .where(AttendanceLog.work_date >= start, AttendanceLog.work_date <= end)
        .order_by(Employee.employee_code, AttendanceLog.work_date)
    ).all()

    return [
        {
            "employee_code": r.employee_code,
            "name": r.full_name,
            "department": r.department,
            "date": r.work_date.isoformat(),
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "clock_in": r.clock_in.strftime("%H:%M") if r.clock_in else "",
            "clock_out": r.clock_out.strftime("%H:%M") if r.clock_out else "",
            "hours": round(float(r.minutes) / 60, 2),
        }
        for r in rows
    ]
