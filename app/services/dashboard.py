"""Everything the landing page needs, assembled in one place.

The dashboard is the only page that mixes personal data with team data, so
without this module the view would be a long sequence of queries with the
role rules interleaved. Keeping it here means the view reads as "fetch, then
render" and the rules below can be tested without a request.

Deliberately capped: the approval queue and the away list are limited, with
the totals counted separately. HR sees every pending request in the company,
and a landing page that loads slower the busier the company gets is a bad
landing page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    AttendanceLog,
    AttendanceStatus,
    Employee,
    LeaveRequest,
    LeaveStatus,
    Role,
)
from app.services import attendance as att
from app.services import leave as lv
from app.services.dates import public_holidays, working_days

# Two working weeks. Enough to see a pattern, few enough that the bars stay
# wide enough to read on a phone.
CHART_DAYS = 14

# Lists on the dashboard are previews - the full versions have their own pages.
QUEUE_PREVIEW = 5
AWAY_PREVIEW = 6
LEAVE_PREVIEW = 5


@dataclass(frozen=True)
class DayPoint:
    """One bar on the personal hours chart."""

    day: date
    hours: float
    incomplete: bool

    @property
    def label(self) -> str:
        return self.day.strftime("%a %d")


@dataclass(frozen=True)
class Dashboard:
    today: date
    log: AttendanceLog | None
    totals: att.DayTotals
    month: att.MonthSummary
    balance: lv.Balance
    recent: list[DayPoint]
    my_leave: list[LeaveRequest]
    queue: list[LeaveRequest]
    queue_total: int
    away: list[LeaveRequest]
    away_total: int

    @property
    def clocked_in(self) -> bool:
        return bool(self.log and self.log.clock_in and not self.log.clock_out)

    @property
    def unbooked_hours(self) -> float:
        """Hours worked today that are not yet booked to a project."""
        if not self.log or not self.log.clock_out:
            return 0.0
        return self.totals.unlogged

    @property
    def chart_hours(self) -> float:
        return round(sum(p.hours for p in self.recent), 1)


def _recent_working_days(today: date, count: int = CHART_DAYS) -> list[date]:
    """The last `count` working days, ending today if today is one.

    Weekends and public holidays are dropped rather than plotted as zero -
    a fortnight of bars with four empty gaps reads as missing data.
    """
    # 30 calendar days comfortably contains 14 working days even with a run
    # of public holidays.
    start = today - timedelta(days=30)
    holidays = public_holidays(start, today)
    days = [d for d in working_days(start, today) if d not in holidays]
    return days[-count:]


def _hours_series(employee_id: int, days: list[date]) -> list[DayPoint]:
    if not days:
        return []

    rows = db.session.scalars(
        select(AttendanceLog).where(
            AttendanceLog.employee_id == employee_id,
            AttendanceLog.work_date.in_(days),
        )
    )
    by_day = {row.work_date: row for row in rows}

    points = []
    for d in days:
        log = by_day.get(d)
        points.append(
            DayPoint(
                day=d,
                hours=log.hours_worked if log and log.hours_worked else 0.0,
                # Clocked in and never out: the zero is a recording gap, not a
                # day off, and the chart colours it differently to say so.
                incomplete=bool(log and log.clock_in and not log.clock_out),
            )
        )
    return points


def _queue(viewer: Employee) -> tuple[list[LeaveRequest], int]:
    """Pending requests this person may decide, newest first, plus the total."""
    if not viewer.is_manager:
        return [], 0

    base = (
        select(LeaveRequest)
        .join(Employee, Employee.id == LeaveRequest.employee_id)
        .where(LeaveRequest.status == LeaveStatus.PENDING)
    )
    if viewer.role is Role.HR_ADMIN:
        # Nobody decides their own, so HR's own request is not in their queue.
        base = base.where(LeaveRequest.employee_id != viewer.id)
    else:
        base = base.where(Employee.manager_id == viewer.id)

    total = db.session.scalar(select(func.count()).select_from(base.subquery())) or 0

    rows = list(
        db.session.scalars(
            base.options(selectinload(LeaveRequest.employee))
            .order_by(LeaveRequest.start_date)
            .limit(QUEUE_PREVIEW)
        )
    )
    return rows, total


def _away(viewer: Employee, today: date) -> tuple[list[LeaveRequest], int]:
    """Who is off right now, scoped the same way the directory is."""
    base = (
        select(LeaveRequest)
        .join(Employee, Employee.id == LeaveRequest.employee_id)
        .where(
            LeaveRequest.status == LeaveStatus.APPROVED,
            LeaveRequest.start_date <= today,
            LeaveRequest.end_date >= today,
        )
    )
    if viewer.role is Role.MANAGER:
        base = base.where(or_(Employee.manager_id == viewer.id, Employee.id == viewer.id))
    elif viewer.role is not Role.HR_ADMIN:
        base = base.where(Employee.id == viewer.id)

    total = db.session.scalar(select(func.count()).select_from(base.subquery())) or 0

    rows = list(
        db.session.scalars(
            base.options(selectinload(LeaveRequest.employee))
            .order_by(LeaveRequest.end_date)
            .limit(AWAY_PREVIEW)
        )
    )
    return rows, total


def build(viewer: Employee, today: date | None = None) -> Dashboard:
    """Assemble the dashboard for one person.

    `today` is injectable so the tests do not have to wait for a Monday.
    """
    today = today or date.today()
    days = _recent_working_days(today)

    queue, queue_total = _queue(viewer)
    away, away_total = _away(viewer, today)

    return Dashboard(
        today=today,
        log=att.day(viewer.id, today),
        totals=att.day_totals(viewer.id, today),
        month=att.month_summary(viewer.id, today.year, today.month),
        balance=lv.balance(viewer, today.year),
        recent=_hours_series(viewer.id, days),
        my_leave=lv.for_employee(viewer.id, limit=LEAVE_PREVIEW),
        queue=queue,
        queue_total=queue_total,
        away=away,
        away_total=away_total,
    )


def chart_payload(board: Dashboard) -> dict[str, list[dict[str, object]]]:
    """The shape charts.js expects. Kept next to the data it describes."""
    return {
        "recent_days": [
            {"label": p.label, "hours": p.hours, "incomplete": p.incomplete} for p in board.recent
        ]
    }


def attendance_mix(board: Dashboard) -> list[tuple[str, int]]:
    """This month's days grouped by status, zero buckets dropped."""
    m = board.month
    pairs = [
        (AttendanceStatus.PRESENT.label, m.present),
        (AttendanceStatus.REMOTE.label, m.remote),
        (AttendanceStatus.LEAVE.label, m.leave),
        (AttendanceStatus.ABSENT.label, m.absent),
        (AttendanceStatus.HOLIDAY.label, m.holiday),
    ]
    return [(label, count) for label, count in pairs if count]
