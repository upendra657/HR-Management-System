"""Directory queries.

The important one is `visible_to()`. Rather than fetching everyone and
filtering in Python - which leaks the row count even if it hides the rows,
and gets slow the moment the company is real - the access rule is pushed
into the WHERE clause. Everything else builds on top of that base query.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    AttendanceLog,
    AttendanceStatus,
    Department,
    Employee,
    EmployeeStatus,
    Role,
)

if TYPE_CHECKING:
    from app.models import Task


@dataclass(frozen=True)
class Page:
    """A slice of results plus what the template needs to draw the pager."""

    items: list
    page: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))  # ceiling division

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def first_index(self) -> int:
        return 0 if not self.total else (self.page - 1) * self.per_page + 1

    @property
    def last_index(self) -> int:
        return min(self.page * self.per_page, self.total)

    def window(self, size: int = 5) -> list[int]:
        """Page numbers to show either side of the current one."""
        half = size // 2
        start = max(1, min(self.page - half, self.pages - size + 1))
        return list(range(start, min(self.pages, start + size - 1) + 1))


def visible_to(viewer: Employee) -> Select:
    """Base SELECT restricted to the employees this viewer may see.

    Mirrors Employee.can_view exactly. If one changes, the other must - which
    is why tests assert the two agree for every pair.
    """
    stmt = select(Employee)
    if viewer.role is Role.HR_ADMIN:
        return stmt
    if viewer.role is Role.MANAGER:
        return stmt.where(or_(Employee.manager_id == viewer.id, Employee.id == viewer.id))
    return stmt.where(Employee.id == viewer.id)


def search(
    viewer: Employee,
    *,
    q: str | None = None,
    department_id: int | None = None,
    status: EmployeeStatus | None = None,
    page: int = 1,
    per_page: int = 25,
) -> Page:
    """Filtered, paginated directory listing."""
    stmt = visible_to(viewer)

    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Employee.full_name).like(like),
                func.lower(Employee.email).like(like),
                func.lower(Employee.employee_code).like(like),
                func.lower(Employee.job_title).like(like),
            )
        )
    if department_id:
        stmt = stmt.where(Employee.department_id == department_id)
    if status:
        stmt = stmt.where(Employee.status == status)

    # Count off a subquery of the filtered statement, so the count and the
    # page can never disagree about what is being filtered.
    total = db.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    page = max(1, page)
    rows = db.session.scalars(
        stmt.options(
            # Without these, rendering a department or manager name per row
            # fires a query per row - the classic N+1. selectinload issues one
            # extra query for the whole page instead.
            selectinload(Employee.department),
            selectinload(Employee.manager),
        )
        .order_by(Employee.full_name)
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()

    return Page(items=list(rows), page=page, per_page=per_page, total=total)


def get_visible(viewer: Employee, employee_id: int) -> Employee | None:
    """Fetch one employee, or None if this viewer is not allowed to see them."""
    employee: Employee | None = db.session.scalar(
        visible_to(viewer)
        .where(Employee.id == employee_id)
        .options(
            selectinload(Employee.department),
            selectinload(Employee.manager),
            selectinload(Employee.reports),
        )
    )
    return employee


def departments() -> list[Department]:
    return list(db.session.scalars(select(Department).order_by(Department.name)))


def headcount_by_department() -> list[tuple[str, int]]:
    """Active headcount per department, in one grouped query."""
    rows = db.session.execute(
        select(Department.name, func.count(Employee.id))
        .join(Employee, Employee.department_id == Department.id)
        .where(Employee.status == EmployeeStatus.ACTIVE)
        .group_by(Department.name)
        .order_by(func.count(Employee.id).desc())
    ).all()
    return [(name, count) for name, count in rows]


@dataclass(frozen=True)
class AttendanceSummary:
    """Aggregates for one employee over a window."""

    days_recorded: int
    days_present: int
    days_remote: int
    days_absent: int
    days_leave: int
    days_holiday: int
    hours_total: float
    missing_clock_out: int

    @property
    def attendance_rate(self) -> float:
        """Share of expected working days actually worked.

        Holidays come out of the denominator - nobody was meant to be in, so
        counting them would drag every rate down by the same few percent and
        make the number useless for comparison.
        """
        countable = self.days_recorded - self.days_holiday
        if countable <= 0:
            return 0.0
        return round(100 * (self.days_present + self.days_remote) / countable, 1)

    @property
    def average_hours(self) -> float:
        """Averaged over days that have both a clock-in and a clock-out.

        Days with a missing clock-out are excluded rather than counted as
        zero, which would silently drag the average down.
        """
        worked = self.days_present + self.days_remote - self.missing_clock_out
        return round(self.hours_total / worked, 2) if worked > 0 else 0.0


def attendance_summary(employee_id: int, days: int = 90) -> AttendanceSummary:
    """One person's attendance over the last `days`, in three queries.

    The day counts and the missing-clock-out count are aggregated in SQL. The
    hours are not: Postgres and SQLite disagree enough about time arithmetic
    that a portable expression gets ugly, and at ~60 rows per employee the
    Python sum is not worth the trouble.

    That does not hold for the org-wide reports, where the same calculation
    over 80k rows has to happen in the database. Those get their own query.
    """
    since = date.today() - timedelta(days=days)

    counts: dict[AttendanceStatus, int] = dict(
        db.session.execute(
            select(AttendanceLog.status, func.count())
            .where(
                AttendanceLog.employee_id == employee_id,
                AttendanceLog.work_date >= since,
            )
            .group_by(AttendanceLog.status)
        )
        .tuples()
        .all()
    )

    missing = (
        db.session.scalar(
            select(func.count())
            .select_from(AttendanceLog)
            .where(
                AttendanceLog.employee_id == employee_id,
                AttendanceLog.work_date >= since,
                AttendanceLog.clock_in.isnot(None),
                AttendanceLog.clock_out.is_(None),
            )
        )
        or 0
    )

    # Only the two time columns come back, not whole ORM objects.
    rows = db.session.execute(
        select(AttendanceLog.clock_in, AttendanceLog.clock_out).where(
            AttendanceLog.employee_id == employee_id,
            AttendanceLog.work_date >= since,
            AttendanceLog.clock_in.isnot(None),
            AttendanceLog.clock_out.isnot(None),
        )
    ).all()
    minutes = sum((out.hour * 60 + out.minute) - (inn.hour * 60 + inn.minute) for inn, out in rows)

    return AttendanceSummary(
        days_recorded=sum(counts.values()),
        days_present=counts.get(AttendanceStatus.PRESENT, 0),
        days_remote=counts.get(AttendanceStatus.REMOTE, 0),
        days_absent=counts.get(AttendanceStatus.ABSENT, 0),
        days_leave=counts.get(AttendanceStatus.LEAVE, 0),
        days_holiday=counts.get(AttendanceStatus.HOLIDAY, 0),
        hours_total=round(minutes / 60, 1),
        missing_clock_out=missing,
    )


def recent_attendance(employee_id: int, limit: int = 14) -> list[AttendanceLog]:
    return list(
        db.session.scalars(
            select(AttendanceLog)
            .where(AttendanceLog.employee_id == employee_id)
            .order_by(AttendanceLog.work_date.desc())
            .limit(limit)
        )
    )


def recent_tasks(employee_id: int, limit: int = 10) -> list[Task]:
    from app.models import Task as TaskModel

    return list(
        db.session.scalars(
            select(TaskModel)
            .where(TaskModel.employee_id == employee_id)
            .options(selectinload(TaskModel.project))
            .order_by(TaskModel.task_date.desc(), TaskModel.id.desc())
            .limit(limit)
        )
    )
