"""Clocking in and out, timesheets, and task logging.

Most of this is guarding against the ways a timesheet goes wrong in practice:
clocking in twice, clocking out before clocking in, logging eight hours of
work on a day you were on leave, or quietly editing last quarter's hours
after payroll has run.

Two known limitations, both from a shift being tied to one calendar day:
overnight shifts cannot be recorded as a single row, and because times are
stored to the minute, clocking in and out within the same minute is refused
as a zero-length shift.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    AttendanceLog,
    AttendanceStatus,
    Employee,
    Project,
    Role,
    Task,
)
from app.services.dates import public_holidays, working_days

# How far back somebody may correct their own timesheet. Beyond this it needs
# HR, because the numbers have usually been reported on by then.
SELF_EDIT_WINDOW_DAYS = 7

# Flagged as suspicious rather than rejected - a genuine 14-hour day happens.
LONG_DAY_HOURS = 12


class AttendanceError(Exception):
    """A rule said no. Views turn this into a flash message."""


@dataclass(frozen=True)
class DayTotals:
    worked: float
    logged: float

    @property
    def unlogged(self) -> float:
        """Hours present but not accounted for by any task."""
        return round(max(0.0, self.worked - self.logged), 2)

    @property
    def over_logged(self) -> bool:
        # Small tolerance: three tasks rounded to 2dp can drift a minute or two.
        return self.logged > self.worked + 0.02


@dataclass(frozen=True)
class MonthSummary:
    year: int
    month: int
    expected_days: int
    present: int
    remote: int
    absent: int
    leave: int
    holiday: int
    hours: float
    missing_clock_out: int

    @property
    def recorded(self) -> int:
        return self.present + self.remote + self.absent + self.leave + self.holiday

    @property
    def unrecorded(self) -> int:
        """Working days in the month with no attendance row at all."""
        return max(0, self.expected_days - self.recorded)


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------
def day(employee_id: int, on: date) -> AttendanceLog | None:
    return db.session.scalar(
        select(AttendanceLog).where(
            AttendanceLog.employee_id == employee_id,
            AttendanceLog.work_date == on,
        )
    )


def month(employee_id: int, year: int, month_no: int) -> list[AttendanceLog]:
    first = date(year, month_no, 1)
    last = date(year, month_no, monthrange(year, month_no)[1])
    return list(
        db.session.scalars(
            select(AttendanceLog)
            .where(
                AttendanceLog.employee_id == employee_id,
                AttendanceLog.work_date >= first,
                AttendanceLog.work_date <= last,
            )
            .order_by(AttendanceLog.work_date)
        )
    )


def month_summary(employee_id: int, year: int, month_no: int) -> MonthSummary:
    first = date(year, month_no, 1)
    last = date(year, month_no, monthrange(year, month_no)[1])

    counts = dict(
        db.session.execute(
            select(AttendanceLog.status, func.count())
            .where(
                AttendanceLog.employee_id == employee_id,
                AttendanceLog.work_date >= first,
                AttendanceLog.work_date <= last,
            )
            .group_by(AttendanceLog.status)
        ).all()
    )

    rows = db.session.execute(
        select(AttendanceLog.clock_in, AttendanceLog.clock_out).where(
            AttendanceLog.employee_id == employee_id,
            AttendanceLog.work_date >= first,
            AttendanceLog.work_date <= last,
            AttendanceLog.clock_in.isnot(None),
            AttendanceLog.clock_out.isnot(None),
        )
    ).all()
    minutes = sum((o.hour * 60 + o.minute) - (i.hour * 60 + i.minute) for i, o in rows)

    missing = (
        db.session.scalar(
            select(func.count())
            .select_from(AttendanceLog)
            .where(
                AttendanceLog.employee_id == employee_id,
                AttendanceLog.work_date >= first,
                AttendanceLog.work_date <= last,
                AttendanceLog.clock_in.isnot(None),
                AttendanceLog.clock_out.is_(None),
            )
        )
        or 0
    )

    holidays = public_holidays(first, last)
    expected = len([d for d in working_days(first, last) if d not in holidays])

    return MonthSummary(
        year=year,
        month=month_no,
        expected_days=expected,
        present=counts.get(AttendanceStatus.PRESENT, 0),
        remote=counts.get(AttendanceStatus.REMOTE, 0),
        absent=counts.get(AttendanceStatus.ABSENT, 0),
        leave=counts.get(AttendanceStatus.LEAVE, 0),
        holiday=counts.get(AttendanceStatus.HOLIDAY, 0),
        hours=round(minutes / 60, 1),
        missing_clock_out=missing,
    )


def tasks_for_day(employee_id: int, on: date) -> list[Task]:
    return list(
        db.session.scalars(
            select(Task)
            .where(Task.employee_id == employee_id, Task.task_date == on)
            .options(selectinload(Task.project))
            .order_by(Task.id)
        )
    )


def day_totals(employee_id: int, on: date) -> DayTotals:
    log = day(employee_id, on)
    logged = db.session.scalar(
        select(func.coalesce(func.sum(Task.hours), 0)).where(
            Task.employee_id == employee_id, Task.task_date == on
        )
    )
    return DayTotals(
        worked=log.hours_worked if log else 0.0,
        logged=float(logged or 0),
    )


def active_projects(on: date | None = None) -> list[Project]:
    """Projects you can book time to on a given date."""
    on = on or date.today()
    return list(
        db.session.scalars(
            select(Project)
            .where(
                Project.start_date <= on,
                (Project.end_date.is_(None)) | (Project.end_date >= on),
            )
            .order_by(Project.code)
        )
    )


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------
def may_edit(actor: Employee, employee_id: int, on: date) -> bool:
    """HR edits anything. Everyone else only their own, and only recently.

    The window exists because timesheets feed reporting - letting someone
    silently rewrite a month-old day is how numbers stop reconciling.
    """
    if actor.role is Role.HR_ADMIN:
        return True
    if actor.id != employee_id:
        return False
    return (date.today() - on).days <= SELF_EDIT_WINDOW_DAYS


def _assert_may_edit(actor: Employee, employee_id: int, on: date) -> None:
    if not may_edit(actor, employee_id, on):
        if actor.id != employee_id:
            raise AttendanceError("You can only change your own timesheet.")
        raise AttendanceError(
            f"That day is more than {SELF_EDIT_WINDOW_DAYS} days ago. Ask HR to correct it."
        )


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def clock_in(
    employee: Employee,
    *,
    on: date | None = None,
    at: time | None = None,
    status: AttendanceStatus = AttendanceStatus.PRESENT,
) -> AttendanceLog:
    on = on or date.today()
    at = at or datetime.now().time().replace(second=0, microsecond=0)

    if on > date.today():
        raise AttendanceError("You cannot clock in for a future date.")
    if not status.is_worked:
        raise AttendanceError("Clocking in only applies to a working day.")

    _assert_may_edit(employee, employee.id, on)

    existing = day(employee.id, on)
    if existing:
        if existing.status is AttendanceStatus.LEAVE:
            raise AttendanceError(
                "That day is booked as approved leave. Cancel the leave "
                "request first if you actually worked."
            )
        if existing.clock_in:
            raise AttendanceError(f"Already clocked in at {existing.clock_in:%H:%M} on {on:%d %b}.")
        existing.clock_in = at
        existing.status = status
        db.session.commit()
        return existing

    log = AttendanceLog(employee_id=employee.id, work_date=on, clock_in=at, status=status)
    db.session.add(log)
    db.session.commit()
    return log


def clock_out(
    employee: Employee, *, on: date | None = None, at: time | None = None
) -> AttendanceLog:
    on = on or date.today()
    at = at or datetime.now().time().replace(second=0, microsecond=0)

    _assert_may_edit(employee, employee.id, on)

    log = day(employee.id, on)
    if log is None or log.clock_in is None:
        raise AttendanceError("You have not clocked in for that day.")
    if log.clock_out:
        raise AttendanceError(f"Already clocked out at {log.clock_out:%H:%M}.")
    if at <= log.clock_in:
        # The database enforces this too; catching it here gives a usable
        # message instead of an IntegrityError.
        raise AttendanceError(f"Clock-out must be after the clock-in at {log.clock_in:%H:%M}.")

    log.clock_out = at
    db.session.commit()
    return log


def record_day(
    actor: Employee,
    employee_id: int,
    *,
    on: date,
    status: AttendanceStatus,
    clock_in_at: time | None = None,
    clock_out_at: time | None = None,
    notes: str | None = None,
) -> AttendanceLog:
    """Create or replace a whole day. Used for corrections and back-filling."""
    if on > date.today():
        raise AttendanceError("You cannot record attendance for a future date.")
    _assert_may_edit(actor, employee_id, on)

    if status.is_worked:
        if clock_in_at is None:
            raise AttendanceError("A worked day needs a clock-in time.")
        if clock_out_at is not None and clock_out_at <= clock_in_at:
            raise AttendanceError("Clock-out must be after clock-in.")
    else:
        # Absent, leave and holiday carry no times - the check constraint
        # would allow them, but they would be meaningless.
        clock_in_at = clock_out_at = None

    log = day(employee_id, on)
    if log is None:
        log = AttendanceLog(employee_id=employee_id, work_date=on)
        db.session.add(log)

    log.status = status
    log.clock_in = clock_in_at
    log.clock_out = clock_out_at
    log.notes = (notes or "").strip() or None
    db.session.commit()
    return log


def log_task(
    actor: Employee,
    employee_id: int,
    *,
    project_id: int,
    on: date,
    hours: float,
    description: str,
    remarks: str | None = None,
) -> Task:
    """Book hours against a project for a day already on the timesheet."""
    _assert_may_edit(actor, employee_id, on)

    if hours <= 0:
        raise AttendanceError("Hours must be greater than zero.")
    if hours > 24:
        raise AttendanceError("A day has 24 hours.")
    if not (description or "").strip():
        raise AttendanceError("Describe what the time was spent on.")

    log = day(employee_id, on)
    if log is None:
        raise AttendanceError(
            f"There is no attendance recorded for {on:%d %b}. Clock in for that day first."
        )
    if not log.status.is_worked:
        raise AttendanceError(
            f"{on:%d %b} is recorded as {log.status.label.lower()}, "
            "so there is no time to book against it."
        )

    project = db.session.get(Project, project_id)
    if project is None:
        raise AttendanceError("That project does not exist.")
    if project.start_date > on or (project.end_date and project.end_date < on):
        raise AttendanceError(f"{project.code} was not running on {on:%d %b}.")

    totals = day_totals(employee_id, on)
    # Only enforced when the day is closed: mid-shift the worked total is
    # still zero, and refusing to log work before clocking out would be
    # actively annoying.
    if log.clock_out and totals.logged + hours > totals.worked + 0.02:
        raise AttendanceError(
            f"That would book {totals.logged + hours:g} hours against a {totals.worked:g}-hour day."
        )

    task = Task(
        employee_id=employee_id,
        project_id=project_id,
        task_date=on,
        hours=Decimal(str(round(hours, 2))),
        description=description.strip(),
        remarks=(remarks or "").strip() or None,
    )
    db.session.add(task)
    db.session.commit()
    return task


def delete_task(actor: Employee, task_id: int) -> None:
    task = db.session.get(Task, task_id)
    if task is None:
        raise AttendanceError("That entry no longer exists.")
    _assert_may_edit(actor, task.employee_id, task.task_date)
    db.session.delete(task)
    db.session.commit()


def open_shifts(since_days: int = 30) -> list[AttendanceLog]:
    """Days somebody clocked in and never clocked out.

    One of the discrepancies the reconciliation report will pick up, exposed
    here so people can fix their own before it gets reported.
    """
    since = date.today() - timedelta(days=since_days)
    return list(
        db.session.scalars(
            select(AttendanceLog)
            .where(
                AttendanceLog.work_date >= since,
                AttendanceLog.work_date < date.today(),
                AttendanceLog.clock_in.isnot(None),
                AttendanceLog.clock_out.is_(None),
            )
            .options(selectinload(AttendanceLog.employee))
            .order_by(AttendanceLog.work_date.desc())
        )
    )
