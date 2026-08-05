"""Attendance and task logs. These two hold most of the rows, so the indexes
here are the ones I actually thought about."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.enums import AttendanceStatus
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.employee import Employee
    from app.models.organization import Project


class AttendanceLog(TimestampMixin, db.Model):
    """One row per employee per day."""

    __tablename__ = "attendance_logs"
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date", name="uq_attendance_employee_date"),
        CheckConstraint(
            "clock_out IS NULL OR clock_in IS NULL OR clock_out > clock_in",
            name="ck_attendance_time_order",
        ),
        Index("ix_attendance_employee_date", "employee_id", "work_date"),  # one person's history
        Index("ix_attendance_date_status", "work_date", "status"),  # daily company-wide counts
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    clock_in: Mapped[time | None] = mapped_column(Time)
    clock_out: Mapped[time | None] = mapped_column(Time)
    status: Mapped[AttendanceStatus] = mapped_column(
        Enum(
            AttendanceStatus,
            name="attendance_status_enum",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=AttendanceStatus.PRESENT,
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(String(255))

    employee: Mapped[Employee] = relationship(back_populates="attendance_logs")

    @property
    def hours_worked(self) -> float:
        if not self.clock_in or not self.clock_out:
            return 0.0
        start = self.clock_in.hour * 60 + self.clock_in.minute
        end = self.clock_out.hour * 60 + self.clock_out.minute
        return round(max(0, end - start) / 60, 2)

    @property
    def is_overtime(self) -> bool:
        return self.hours_worked > 9

    def __repr__(self) -> str:
        return f"<AttendanceLog emp={self.employee_id} {self.work_date} {self.status.value}>"


class Task(TimestampMixin, db.Model):
    """Work logged against a project on a given day."""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("hours > 0 AND hours <= 24", name="ck_tasks_hours_range"),
        Index("ix_tasks_employee_date", "employee_id", "task_date"),
        Index("ix_tasks_project_date", "project_id", "task_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    task_date: Mapped[date] = mapped_column(Date, nullable=False)
    hours: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text)

    employee: Mapped[Employee] = relationship(back_populates="tasks")
    project: Mapped[Project] = relationship(back_populates="tasks")

    def __repr__(self) -> str:
        return f"<Task emp={self.employee_id} {self.task_date} {self.hours}h>"
