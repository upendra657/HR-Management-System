"""Leave requests, entitlement and the approval workflow.

This is the one part of the app with real business logic, and the reason
there is a service layer at all. Approving leave is not a status update -
it has to check who is allowed to decide, then write the approved days into
the attendance table so the timesheet agrees with the decision. Doing that
in a view function would mean duplicating it the moment a second caller
appears.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    AttendanceLog,
    AttendanceStatus,
    Employee,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
    Role,
)
from app.services.dates import chargeable_days

# Days of paid annual leave per calendar year. A real system would vary this
# by contract and length of service; one constant is honest about the scope.
ANNUAL_ENTITLEMENT = 21

# Only annual leave draws down the balance. Sick and bereavement are granted
# separately, unpaid is by definition not paid, parental sits outside it.
COUNTS_AGAINST_BALANCE = {LeaveType.ANNUAL}

# Statuses that still hold the dates - used for overlap detection. A rejected
# or cancelled request does not block a new one for the same days.
BLOCKING = (LeaveStatus.PENDING, LeaveStatus.APPROVED)


class LeaveError(Exception):
    """A business rule said no. Views turn this into a flash message."""


@dataclass(frozen=True)
class Balance:
    entitlement: int
    taken: float
    pending: float

    @property
    def remaining(self) -> float:
        return round(self.entitlement - self.taken - self.pending, 1)

    @property
    def used_pct(self) -> float:
        if self.entitlement <= 0:
            return 0.0
        return round(100 * (self.taken + self.pending) / self.entitlement, 1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------
def balance(employee: Employee, year: int | None = None) -> Balance:
    """Annual leave position for a calendar year.

    Pending requests are held against the balance as well as approved ones,
    so somebody cannot get two requests approved that together exceed their
    entitlement just because neither was decided yet.
    """
    year = year or date.today().year
    start, end = date(year, 1, 1), date(year, 12, 31)

    def total(status: LeaveStatus) -> float:
        value = db.session.scalar(
            select(func.coalesce(func.sum(LeaveRequest.days), 0)).where(
                LeaveRequest.employee_id == employee.id,
                LeaveRequest.leave_type.in_(COUNTS_AGAINST_BALANCE),
                LeaveRequest.status == status,
                LeaveRequest.start_date >= start,
                LeaveRequest.start_date <= end,
            )
        )
        return float(value or 0)

    return Balance(
        entitlement=ANNUAL_ENTITLEMENT,
        taken=total(LeaveStatus.APPROVED),
        pending=total(LeaveStatus.PENDING),
    )


def overlapping(
    employee_id: int, start: date, end: date, *, exclude_id: int | None = None
) -> list[LeaveRequest]:
    """Live requests that clash with this date range.

    Two ranges overlap when each starts before the other ends - simpler and
    more reliable than enumerating the ways they can miss each other.
    """
    stmt = select(LeaveRequest).where(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status.in_(BLOCKING),
        and_(LeaveRequest.start_date <= end, LeaveRequest.end_date >= start),
    )
    if exclude_id:
        stmt = stmt.where(LeaveRequest.id != exclude_id)
    return list(db.session.scalars(stmt))


def for_employee(employee_id: int, limit: int | None = None) -> list[LeaveRequest]:
    stmt = (
        select(LeaveRequest)
        .where(LeaveRequest.employee_id == employee_id)
        .options(selectinload(LeaveRequest.decided_by))
        .order_by(LeaveRequest.start_date.desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(db.session.scalars(stmt))


def pending_for_approver(approver: Employee) -> list[LeaveRequest]:
    """The approval queue: requests this person is allowed to decide."""
    stmt = (
        select(LeaveRequest)
        .join(Employee, Employee.id == LeaveRequest.employee_id)
        .where(LeaveRequest.status == LeaveStatus.PENDING)
        .options(selectinload(LeaveRequest.employee).selectinload(Employee.department))
        .order_by(LeaveRequest.start_date)
    )
    if approver.role is Role.HR_ADMIN:
        # HR sees everything except their own request.
        stmt = stmt.where(LeaveRequest.employee_id != approver.id)
    elif approver.role is Role.MANAGER:
        stmt = stmt.where(Employee.manager_id == approver.id)
    else:
        return []
    return list(db.session.scalars(stmt))


def can_decide(approver: Employee, request: LeaveRequest) -> bool:
    """Nobody decides their own request, regardless of role."""
    if approver.id == request.employee_id:
        return False
    if approver.role is Role.HR_ADMIN:
        return True
    return approver.role is Role.MANAGER and request.employee.manager_id == approver.id


def get_for_actor(actor: Employee, request_id: int) -> LeaveRequest | None:
    """A request the actor may see: their own, or one they could decide."""
    req = db.session.scalar(
        select(LeaveRequest)
        .where(LeaveRequest.id == request_id)
        .options(selectinload(LeaveRequest.employee))
    )
    if req is None:
        return None
    if req.employee_id == actor.id or can_decide(actor, req):
        return req
    return None


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def submit(
    employee: Employee,
    *,
    leave_type: LeaveType,
    start: date,
    end: date,
    reason: str | None = None,
    today: date | None = None,
) -> LeaveRequest:
    """Create a pending request, or raise LeaveError explaining why not."""
    today = today or date.today()

    if end < start:
        raise LeaveError("The end date cannot be before the start date.")
    if start < today:
        raise LeaveError("Leave cannot be booked for dates in the past.")

    days = chargeable_days(start, end)
    if not days:
        raise LeaveError(
            "That range contains no working days - weekends and public "
            "holidays do not need to be booked."
        )

    clash = overlapping(employee.id, start, end)
    if clash:
        first = clash[0]
        raise LeaveError(
            f"This overlaps an existing {first.status.label.lower()} request "
            f"({first.start_date:%d %b} to {first.end_date:%d %b})."
        )

    count = float(len(days))
    if leave_type in COUNTS_AGAINST_BALANCE:
        bal = balance(employee, start.year)
        if count > bal.remaining:
            raise LeaveError(
                f"That would use {count:g} days but only {bal.remaining:g} "
                f"remain of your {bal.entitlement}-day entitlement for {start.year}."
            )

    request = LeaveRequest(
        employee_id=employee.id,
        leave_type=leave_type,
        status=LeaveStatus.PENDING,
        start_date=start,
        end_date=end,
        days=Decimal(str(count)),
        reason=(reason or "").strip() or None,
    )
    db.session.add(request)
    db.session.commit()
    return request


def approve(request: LeaveRequest, approver: Employee, note: str | None = None) -> LeaveRequest:
    """Approve, and write the days onto the timesheet.

    The attendance write is the point. Without it the leave table says one
    thing and the attendance table another, which is exactly the discrepancy
    the reconciliation report has to hunt for.
    """
    _assert_decidable(request, approver)

    request.status = LeaveStatus.APPROVED
    request.decided_by_id = approver.id
    request.decided_at = _now()
    request.decision_note = (note or "").strip() or None

    _write_attendance(request)
    db.session.commit()
    return request


def reject(request: LeaveRequest, approver: Employee, note: str | None = None) -> LeaveRequest:
    _assert_decidable(request, approver)

    request.status = LeaveStatus.REJECTED
    request.decided_by_id = approver.id
    request.decided_at = _now()
    request.decision_note = (note or "").strip() or None
    db.session.commit()
    return request


def cancel(request: LeaveRequest, actor: Employee) -> LeaveRequest:
    """Withdraw a request. Only the person who made it, and only if it has
    not already been decided against them."""
    if request.employee_id != actor.id:
        raise LeaveError("Only the person who requested leave can cancel it.")
    if request.status not in BLOCKING:
        raise LeaveError(f"This request is already {request.status.label.lower()}.")

    was_approved = request.status is LeaveStatus.APPROVED
    request.status = LeaveStatus.CANCELLED
    # The decision fields stay as they were: the constraint requires both or
    # neither, and clearing them would erase who approved it in the first place.

    if was_approved:
        _clear_attendance(request)
    db.session.commit()
    return request


# --------------------------------------------------------------------------
def _assert_decidable(request: LeaveRequest, approver: Employee) -> None:
    if request.status is not LeaveStatus.PENDING:
        raise LeaveError(f"This request has already been {request.status.label.lower()}.")
    if not can_decide(approver, request):
        raise LeaveError("You are not able to decide this request.")


def _write_attendance(request: LeaveRequest) -> None:
    """Mark the approved days as leave.

    Existing rows are overwritten rather than skipped: if somebody clocked in
    on a day that is now approved leave, the approval is the later and more
    authoritative decision. The original times are kept in the note so the
    change is not silent.
    """
    days = chargeable_days(request.start_date, request.end_date)
    if not days:
        return

    existing = {
        log.work_date: log
        for log in db.session.scalars(
            select(AttendanceLog).where(
                AttendanceLog.employee_id == request.employee_id,
                AttendanceLog.work_date.in_(days),
            )
        )
    }

    for day in days:
        log = existing.get(day)
        if log is None:
            db.session.add(
                AttendanceLog(
                    employee_id=request.employee_id,
                    work_date=day,
                    status=AttendanceStatus.LEAVE,
                    notes=f"Leave #{request.id}",
                )
            )
            continue

        if log.status is AttendanceStatus.LEAVE:
            continue

        if log.clock_in:
            log.notes = (
                f"Was {log.status.value} {log.clock_in:%H:%M}"
                f"-{log.clock_out:%H:%M}; replaced by leave #{request.id}"
                if log.clock_out
                else f"Was {log.status.value} from {log.clock_in:%H:%M}; "
                f"replaced by leave #{request.id}"
            )
        else:
            log.notes = f"Leave #{request.id}"
        log.status = AttendanceStatus.LEAVE
        log.clock_in = None
        log.clock_out = None


def _clear_attendance(request: LeaveRequest) -> None:
    """Remove the leave days written when this request was approved.

    Only rows still marked as leave are removed - if somebody has since
    recorded actual attendance on one of those days, that is real data and
    cancelling a request should not delete it.
    """
    days = chargeable_days(request.start_date, request.end_date)
    if not days:
        return
    db.session.execute(
        delete(AttendanceLog).where(
            AttendanceLog.employee_id == request.employee_id,
            AttendanceLog.work_date.in_(days),
            AttendanceLog.status == AttendanceStatus.LEAVE,
        )
    )


def on_leave_today(viewer: Employee) -> list[LeaveRequest]:
    """Who from the viewer's team is off right now."""
    today = date.today()
    stmt = (
        select(LeaveRequest)
        .join(Employee, Employee.id == LeaveRequest.employee_id)
        .where(
            LeaveRequest.status == LeaveStatus.APPROVED,
            LeaveRequest.start_date <= today,
            LeaveRequest.end_date >= today,
        )
        .options(selectinload(LeaveRequest.employee))
        .order_by(LeaveRequest.end_date)
    )
    if viewer.role is Role.MANAGER:
        stmt = stmt.where(or_(Employee.manager_id == viewer.id, Employee.id == viewer.id))
    elif viewer.role is not Role.HR_ADMIN:
        stmt = stmt.where(Employee.id == viewer.id)
    return list(db.session.scalars(stmt))
