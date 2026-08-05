"""Leave requests and approvals."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.enums import LeaveStatus, LeaveType
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.employee import Employee


class LeaveRequest(TimestampMixin, db.Model):
    """pending -> approved / rejected / cancelled."""

    __tablename__ = "leave_requests"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_leave_date_order"),
        CheckConstraint("days > 0", name="ck_leave_days_positive"),
        # Either both decision fields are set or neither is.
        CheckConstraint(
            "(decided_by_id IS NULL) = (decided_at IS NULL)",
            name="ck_leave_decision_complete",
        ),
        Index("ix_leave_employee_status", "employee_id", "status"),
        Index("ix_leave_dates", "start_date", "end_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    leave_type: Mapped[LeaveType] = mapped_column(
        Enum(LeaveType, name="leave_type_enum", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    status: Mapped[LeaveStatus] = mapped_column(
        Enum(LeaveStatus, name="leave_status_enum", values_callable=lambda e: [m.value for m in e]),
        default=LeaveStatus.PENDING,
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    days: Mapped[Decimal] = mapped_column(Numeric(4, 1), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)

    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)

    employee: Mapped[Employee] = relationship(
        back_populates="leave_requests", foreign_keys=[employee_id]
    )
    decided_by: Mapped[Employee | None] = relationship(foreign_keys=[decided_by_id])

    @property
    def is_pending(self) -> bool:
        return self.status == LeaveStatus.PENDING

    @property
    def overlaps_today(self) -> bool:
        return self.start_date <= date.today() <= self.end_date

    def __repr__(self) -> str:
        return f"<LeaveRequest emp={self.employee_id} {self.leave_type.value} {self.status.value}>"
