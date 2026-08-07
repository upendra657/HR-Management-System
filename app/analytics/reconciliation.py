"""Data quality checks across attendance, leave and task records.

The premise is the one from migration work: two systems that are supposed to
agree usually do not, and the useful thing is a repeatable report of exactly
where and by how much - not a vague sense that the numbers are off.

Each check returns a Finding with a count, a plain-language explanation and
a handful of example rows, so somebody can go and look at the actual records
rather than trusting a number.

The checks are read-only. Nothing here fixes anything, deliberately: a report
that silently edits data is a report you cannot trust twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Any

from sqlalchemy import and_, func, or_, select

from app.analytics.expressions import is_closed_shift, worked_minutes
from app.extensions import db
from app.models import (
    AttendanceLog,
    AttendanceStatus,
    Employee,
    EmployeeStatus,
    LeaveRequest,
    LeaveStatus,
    Project,
    Task,
)
from app.services.dates import chargeable_days

# A shift longer than this is almost certainly a missed clock-out rather than
# a genuine day, but it is flagged rather than corrected.
IMPLAUSIBLE_HOURS = 16

# Rounding tolerance when comparing logged hours to worked hours.
HOURS_TOLERANCE = 0.02

SAMPLE_SIZE = 8


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def badge(self) -> str:
        return {"high": "danger", "medium": "warning", "low": "secondary"}[self.value]

    @property
    def label(self) -> str:
        return self.value.title()


@dataclass(frozen=True)
class Finding:
    code: str
    title: str
    severity: Severity
    explanation: str
    count: int
    scanned: int = 0
    sample: list[dict[str, Any]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.count == 0

    @property
    def rate(self) -> float:
        """Share of the scanned population affected."""
        if not self.scanned:
            return 0.0
        return round(100 * self.count / self.scanned, 2)


def _window(days: int) -> date:
    return date.today() - timedelta(days=days)


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------
def open_shifts(days: int = 90) -> Finding:
    """Clocked in, never clocked out."""
    since = _window(days)
    base = and_(
        AttendanceLog.work_date >= since,
        AttendanceLog.work_date < date.today(),
        AttendanceLog.clock_in.isnot(None),
        AttendanceLog.clock_out.is_(None),
    )

    count = db.session.scalar(select(func.count()).select_from(AttendanceLog).where(base)) or 0
    scanned = (
        db.session.scalar(
            select(func.count())
            .select_from(AttendanceLog)
            .where(
                AttendanceLog.work_date >= since,
                AttendanceLog.work_date < date.today(),
                AttendanceLog.clock_in.isnot(None),
            )
        )
        or 0
    )

    rows = db.session.execute(
        select(
            Employee.employee_code,
            Employee.full_name,
            AttendanceLog.work_date,
            AttendanceLog.clock_in,
        )
        .join(Employee, Employee.id == AttendanceLog.employee_id)
        .where(base)
        .order_by(AttendanceLog.work_date.desc())
        .limit(SAMPLE_SIZE)
    ).all()

    return Finding(
        code="OPEN_SHIFT",
        title="Shifts with no clock-out",
        severity=Severity.MEDIUM,
        explanation=(
            "Someone clocked in and never clocked out. The day contributes "
            "zero hours to every report, so utilisation and payroll are both "
            "understated until it is corrected."
        ),
        count=count,
        scanned=scanned,
        sample=[
            {
                "employee": f"{r.employee_code} {r.full_name}",
                "date": r.work_date.isoformat(),
                "clock_in": r.clock_in.strftime("%H:%M") if r.clock_in else "",
            }
            for r in rows
        ],
    )


def worked_during_approved_leave(days: int = 365) -> Finding:
    """Attendance says present, leave says approved, for the same day."""
    since = _window(days)
    join = and_(
        AttendanceLog.employee_id == LeaveRequest.employee_id,
        AttendanceLog.work_date >= LeaveRequest.start_date,
        AttendanceLog.work_date <= LeaveRequest.end_date,
    )
    base = and_(
        LeaveRequest.status == LeaveStatus.APPROVED,
        LeaveRequest.start_date >= since,
        AttendanceLog.status.in_([AttendanceStatus.PRESENT, AttendanceStatus.REMOTE]),
    )

    count = (
        db.session.scalar(
            select(func.count()).select_from(LeaveRequest).join(AttendanceLog, join).where(base)
        )
        or 0
    )
    scanned = (
        db.session.scalar(
            select(func.count())
            .select_from(LeaveRequest)
            .join(AttendanceLog, join)
            .where(
                LeaveRequest.status == LeaveStatus.APPROVED,
                LeaveRequest.start_date >= since,
            )
        )
        or 0
    )

    rows = db.session.execute(
        select(
            Employee.employee_code,
            Employee.full_name,
            AttendanceLog.work_date,
            AttendanceLog.status,
            LeaveRequest.id.label("leave_id"),
        )
        .select_from(LeaveRequest)
        .join(AttendanceLog, join)
        .join(Employee, Employee.id == LeaveRequest.employee_id)
        .where(base)
        .order_by(AttendanceLog.work_date.desc())
        .limit(SAMPLE_SIZE)
    ).all()

    return Finding(
        code="WORKED_ON_LEAVE",
        title="Worked on an approved leave day",
        severity=Severity.HIGH,
        explanation=(
            "The leave table and the timesheet disagree about the same day. "
            "Either the leave was cancelled informally and never updated, or "
            "the attendance is wrong. Both readings are billable in different "
            "ways, which is why this is worth resolving rather than ignoring."
        ),
        count=count,
        scanned=scanned,
        sample=[
            {
                "employee": f"{r.employee_code} {r.full_name}",
                "date": r.work_date.isoformat(),
                "attendance": r.status.value if hasattr(r.status, "value") else str(r.status),
                "leave_request": r.leave_id,
            }
            for r in rows
        ],
    )


def leave_missing_from_timesheet(days: int = 365) -> Finding:
    """Approved leave days with no corresponding attendance row.

    Expanded in Python rather than SQL: portable date-series generation needs
    a recursive CTE on Postgres and a different trick on SQLite, and the input
    here is bounded by the number of approved requests in the window.
    """
    since = _window(days)
    requests = db.session.scalars(
        select(LeaveRequest).where(
            LeaveRequest.status == LeaveStatus.APPROVED,
            LeaveRequest.start_date >= since,
        )
    ).all()

    expected: set[tuple[int, date]] = set()
    for req in requests:
        for day in chargeable_days(req.start_date, req.end_date):
            if day <= date.today():
                expected.add((req.employee_id, day))

    if not expected:
        return Finding(
            code="LEAVE_NOT_ON_TIMESHEET",
            title="Approved leave missing from the timesheet",
            severity=Severity.MEDIUM,
            explanation="No approved leave in the window.",
            count=0,
            scanned=0,
        )

    present = set(
        db.session.execute(
            select(AttendanceLog.employee_id, AttendanceLog.work_date).where(
                AttendanceLog.work_date >= since,
                AttendanceLog.status == AttendanceStatus.LEAVE,
            )
        ).all()
    )

    missing = sorted(expected - present, key=lambda pair: pair[1], reverse=True)

    names = {}
    if missing:
        ids = {emp_id for emp_id, _ in missing[:SAMPLE_SIZE]}
        names = {
            e.id: f"{e.employee_code} {e.full_name}"
            for e in db.session.scalars(select(Employee).where(Employee.id.in_(ids)))
        }

    return Finding(
        code="LEAVE_NOT_ON_TIMESHEET",
        title="Approved leave missing from the timesheet",
        severity=Severity.MEDIUM,
        explanation=(
            "Leave was approved but the day never appeared on the timesheet. "
            "Attendance reports will count it as an unexplained gap and the "
            "person's attendance rate will look worse than it is."
        ),
        count=len(missing),
        scanned=len(expected),
        sample=[
            {"employee": names.get(emp_id, str(emp_id)), "date": day.isoformat()}
            for emp_id, day in missing[:SAMPLE_SIZE]
        ],
    )


def implausible_shifts(days: int = 365) -> Finding:
    """Shifts longer than a plausible working day."""
    since = _window(days)
    base = and_(
        AttendanceLog.work_date >= since,
        is_closed_shift(),
        worked_minutes() > IMPLAUSIBLE_HOURS * 60,
    )

    count = db.session.scalar(select(func.count()).select_from(AttendanceLog).where(base)) or 0
    scanned = (
        db.session.scalar(
            select(func.count())
            .select_from(AttendanceLog)
            .where(AttendanceLog.work_date >= since, is_closed_shift())
        )
        or 0
    )

    rows = db.session.execute(
        select(
            Employee.employee_code,
            Employee.full_name,
            AttendanceLog.work_date,
            AttendanceLog.clock_in,
            AttendanceLog.clock_out,
            (worked_minutes() / 60.0).label("hours"),
        )
        .join(Employee, Employee.id == AttendanceLog.employee_id)
        .where(base)
        .order_by(AttendanceLog.work_date.desc())
        .limit(SAMPLE_SIZE)
    ).all()

    return Finding(
        code="IMPLAUSIBLE_SHIFT",
        title=f"Shifts longer than {IMPLAUSIBLE_HOURS} hours",
        severity=Severity.LOW,
        explanation=(
            "Almost always a clock-out entered on the wrong day rather than a "
            "genuine shift. Flagged rather than corrected, because occasionally "
            "it is real."
        ),
        count=count,
        scanned=scanned,
        sample=[
            {
                "employee": f"{r.employee_code} {r.full_name}",
                "date": r.work_date.isoformat(),
                "hours": round(float(r.hours), 1),
            }
            for r in rows
        ],
    )


def over_logged_days(days: int = 365) -> Finding:
    """More hours booked to projects than the timesheet says were worked."""
    since = _window(days)

    logged = (
        select(
            Task.employee_id.label("employee_id"),
            Task.task_date.label("task_date"),
            func.sum(Task.hours).label("logged"),
        )
        .where(Task.task_date >= since)
        .group_by(Task.employee_id, Task.task_date)
        .subquery()
    )

    join = and_(
        AttendanceLog.employee_id == logged.c.employee_id,
        AttendanceLog.work_date == logged.c.task_date,
    )
    base = and_(
        is_closed_shift(),
        logged.c.logged > (worked_minutes() / 60.0) + HOURS_TOLERANCE,
    )

    count = (
        db.session.scalar(
            select(func.count()).select_from(logged).join(AttendanceLog, join).where(base)
        )
        or 0
    )
    scanned = (
        db.session.scalar(
            select(func.count())
            .select_from(logged)
            .join(AttendanceLog, join)
            .where(is_closed_shift())
        )
        or 0
    )

    rows = db.session.execute(
        select(
            Employee.employee_code,
            Employee.full_name,
            logged.c.task_date,
            logged.c.logged,
            (worked_minutes() / 60.0).label("worked"),
        )
        .select_from(logged)
        .join(AttendanceLog, join)
        .join(Employee, Employee.id == logged.c.employee_id)
        .where(base)
        .order_by(logged.c.task_date.desc())
        .limit(SAMPLE_SIZE)
    ).all()

    return Finding(
        code="OVER_LOGGED",
        title="More hours booked than worked",
        severity=Severity.HIGH,
        explanation=(
            "Project time bookings exceed the hours actually recorded on the "
            "timesheet. If these hours are billed to a client, they are being "
            "billed twice or against the wrong day."
        ),
        count=count,
        scanned=scanned,
        sample=[
            {
                "employee": f"{r.employee_code} {r.full_name}",
                "date": r.task_date.isoformat(),
                "logged": round(float(r.logged), 2),
                "worked": round(float(r.worked), 2),
            }
            for r in rows
        ],
    )


def tasks_without_attendance(days: int = 365) -> Finding:
    """Work booked on a day with no attendance record at all."""
    since = _window(days)
    join = and_(
        AttendanceLog.employee_id == Task.employee_id,
        AttendanceLog.work_date == Task.task_date,
    )

    count = (
        db.session.scalar(
            select(func.count())
            .select_from(Task)
            .outerjoin(AttendanceLog, join)
            .where(Task.task_date >= since, AttendanceLog.id.is_(None))
        )
        or 0
    )
    scanned = (
        db.session.scalar(select(func.count()).select_from(Task).where(Task.task_date >= since))
        or 0
    )

    rows = db.session.execute(
        select(
            Employee.employee_code,
            Employee.full_name,
            Task.task_date,
            Task.hours,
            Project.code.label("project"),
        )
        .select_from(Task)
        .outerjoin(AttendanceLog, join)
        .join(Employee, Employee.id == Task.employee_id)
        .join(Project, Project.id == Task.project_id)
        .where(Task.task_date >= since, AttendanceLog.id.is_(None))
        .order_by(Task.task_date.desc())
        .limit(SAMPLE_SIZE)
    ).all()

    return Finding(
        code="TASK_NO_ATTENDANCE",
        title="Work booked with no attendance",
        severity=Severity.HIGH,
        explanation=(
            "Hours booked to a project on a day the person has no timesheet "
            "entry for. Either the attendance was never recorded or the work "
            "is dated wrongly."
        ),
        count=count,
        scanned=scanned,
        sample=[
            {
                "employee": f"{r.employee_code} {r.full_name}",
                "date": r.task_date.isoformat(),
                "hours": float(r.hours),
                "project": r.project,
            }
            for r in rows
        ],
    )


def tasks_outside_project_dates(days: int = 365) -> Finding:
    """Time booked to a project before it started or after it closed."""
    since = _window(days)
    base = and_(
        Task.task_date >= since,
        or_(
            Task.task_date < Project.start_date,
            and_(Project.end_date.isnot(None), Task.task_date > Project.end_date),
        ),
    )

    count = (
        db.session.scalar(
            select(func.count())
            .select_from(Task)
            .join(Project, Project.id == Task.project_id)
            .where(base)
        )
        or 0
    )
    scanned = (
        db.session.scalar(select(func.count()).select_from(Task).where(Task.task_date >= since))
        or 0
    )

    rows = db.session.execute(
        select(
            Employee.employee_code,
            Employee.full_name,
            Task.task_date,
            Project.code.label("project"),
            Project.start_date,
            Project.end_date,
        )
        .select_from(Task)
        .join(Project, Project.id == Task.project_id)
        .join(Employee, Employee.id == Task.employee_id)
        .where(base)
        .order_by(Task.task_date.desc())
        .limit(SAMPLE_SIZE)
    ).all()

    return Finding(
        code="TASK_OUTSIDE_PROJECT",
        title="Time booked outside the project's dates",
        severity=Severity.MEDIUM,
        explanation=(
            "Hours recorded against a project on a date it was not running. "
            "Usually a mis-selected project; occasionally a project whose end "
            "date was set retrospectively."
        ),
        count=count,
        scanned=scanned,
        sample=[
            {
                "employee": f"{r.employee_code} {r.full_name}",
                "date": r.task_date.isoformat(),
                "project": r.project,
                "ran": f"{r.start_date} to {r.end_date or 'open'}",
            }
            for r in rows
        ],
    )


def attendance_outside_employment() -> Finding:
    """Attendance dated before someone joined or after they left."""
    base = or_(
        AttendanceLog.work_date < Employee.join_date,
        and_(
            Employee.exit_date.isnot(None),
            AttendanceLog.work_date > Employee.exit_date,
        ),
    )

    count = (
        db.session.scalar(
            select(func.count())
            .select_from(AttendanceLog)
            .join(Employee, Employee.id == AttendanceLog.employee_id)
            .where(base)
        )
        or 0
    )
    scanned = db.session.scalar(select(func.count()).select_from(AttendanceLog)) or 0

    rows = db.session.execute(
        select(
            Employee.employee_code,
            Employee.full_name,
            AttendanceLog.work_date,
            Employee.join_date,
            Employee.exit_date,
        )
        .select_from(AttendanceLog)
        .join(Employee, Employee.id == AttendanceLog.employee_id)
        .where(base)
        .limit(SAMPLE_SIZE)
    ).all()

    return Finding(
        code="OUTSIDE_EMPLOYMENT",
        title="Attendance outside the employment period",
        severity=Severity.HIGH,
        explanation=(
            "A timesheet entry dated before the person joined or after they "
            "left. Almost always a data-entry or import error, and it inflates "
            "headcount-based reporting."
        ),
        count=count,
        scanned=scanned,
        sample=[
            {
                "employee": f"{r.employee_code} {r.full_name}",
                "date": r.work_date.isoformat(),
                "employed": f"{r.join_date} to {r.exit_date or 'present'}",
            }
            for r in rows
        ],
    )


def active_staff_without_manager() -> Finding:
    """Active employees with nobody to approve their leave."""
    base = and_(
        Employee.status == EmployeeStatus.ACTIVE,
        Employee.manager_id.is_(None),
    )
    count = db.session.scalar(select(func.count()).select_from(Employee).where(base)) or 0
    scanned = (
        db.session.scalar(
            select(func.count())
            .select_from(Employee)
            .where(Employee.status == EmployeeStatus.ACTIVE)
        )
        or 0
    )

    rows = db.session.execute(
        select(Employee.employee_code, Employee.full_name, Employee.job_title)
        .where(base)
        .limit(SAMPLE_SIZE)
    ).all()

    return Finding(
        code="NO_MANAGER",
        title="Active staff with no manager",
        severity=Severity.LOW,
        explanation=(
            "Their leave requests can only be approved by HR, and they are "
            "missing from any report built on the reporting line. One person "
            "at the top of the chart is expected."
        ),
        count=count,
        scanned=scanned,
        sample=[
            {"employee": f"{r.employee_code} {r.full_name}", "job_title": r.job_title} for r in rows
        ],
    )


def future_dated_attendance() -> Finding:
    """Timesheet entries for days that have not happened."""
    base = AttendanceLog.work_date > date.today()
    count = db.session.scalar(select(func.count()).select_from(AttendanceLog).where(base)) or 0

    rows = db.session.execute(
        select(Employee.employee_code, Employee.full_name, AttendanceLog.work_date)
        .join(Employee, Employee.id == AttendanceLog.employee_id)
        .where(base)
        .order_by(AttendanceLog.work_date)
        .limit(SAMPLE_SIZE)
    ).all()

    return Finding(
        code="FUTURE_DATED",
        title="Attendance dated in the future",
        severity=Severity.MEDIUM,
        explanation=(
            "Approved future leave is written ahead of time and is expected "
            "here; worked days are not, and mean a wrong date was entered."
        ),
        count=count,
        scanned=db.session.scalar(select(func.count()).select_from(AttendanceLog)) or 0,
        sample=[
            {
                "employee": f"{r.employee_code} {r.full_name}",
                "date": r.work_date.isoformat(),
            }
            for r in rows
        ],
    )


CHECKS = [
    open_shifts,
    worked_during_approved_leave,
    leave_missing_from_timesheet,
    over_logged_days,
    tasks_without_attendance,
    tasks_outside_project_dates,
    attendance_outside_employment,
    implausible_shifts,
    active_staff_without_manager,
    future_dated_attendance,
]


@dataclass(frozen=True)
class Report:
    findings: list[Finding]
    generated_at: date

    @property
    def total_issues(self) -> int:
        return sum(f.count for f in self.findings)

    @property
    def failing(self) -> list[Finding]:
        return [f for f in self.findings if not f.clean]

    @property
    def clean(self) -> list[Finding]:
        return [f for f in self.findings if f.clean]

    def by_severity(self, severity: Severity) -> list[Finding]:
        return [f for f in self.failing if f.severity is severity]

    @property
    def headline(self) -> str:
        if not self.failing:
            return "No discrepancies found."
        high = len(self.by_severity(Severity.HIGH))
        return (
            f"{self.total_issues:,} discrepancies across "
            f"{len(self.failing)} of {len(self.findings)} checks"
            + (f", {high} high severity" if high else "")
        )


def run_all() -> Report:
    """Run every check. Read-only - nothing here modifies data."""
    return Report(findings=[check() for check in CHECKS], generated_at=date.today())


def to_rows(report: Report) -> list[dict[str, Any]]:
    """Flatten for CSV or Excel export."""
    return [
        {
            "code": f.code,
            "title": f.title,
            "severity": f.severity.value,
            "count": f.count,
            "scanned": f.scanned,
            "rate_pct": f.rate,
            "explanation": f.explanation,
        }
        for f in report.findings
    ]
