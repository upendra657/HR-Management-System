"""Employee is also the login user — everyone who uses this system is one."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from flask_login import UserMixin
from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager
from app.models.enums import EmployeeStatus, EmploymentType, Role
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.attendance import AttendanceLog, Task
    from app.models.leave import LeaveRequest
    from app.models.organization import Department
    from app.models.performance import PerformanceReview


class Employee(TimestampMixin, UserMixin, db.Model):
    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint(
            "exit_date IS NULL OR exit_date >= join_date",
            name="ck_employees_date_order",
        ),
        CheckConstraint("base_salary >= 0", name="ck_employees_salary_positive"),
        CheckConstraint("manager_id IS NULL OR manager_id <> id", name="ck_employees_self_manage"),
        Index("ix_employees_department_status", "department_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False, index=True)

    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    username: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[Role] = mapped_column(
        Enum(Role, name="role_enum", values_callable=lambda e: [m.value for m in e]),
        default=Role.EMPLOYEE,
        nullable=False,
    )
    employment_type: Mapped[EmploymentType] = mapped_column(
        Enum(
            EmploymentType,
            name="employment_type_enum",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=EmploymentType.FULL_TIME,
        nullable=False,
    )
    status: Mapped[EmployeeStatus] = mapped_column(
        Enum(
            EmployeeStatus,
            name="employee_status_enum",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=EmployeeStatus.ACTIVE,
        nullable=False,
    )
    job_title: Mapped[str] = mapped_column(String(120), nullable=False)
    join_date: Mapped[date] = mapped_column(Date, nullable=False)
    exit_date: Mapped[date | None] = mapped_column(Date)
    base_salary: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    country: Mapped[str | None] = mapped_column(String(60))
    phone: Mapped[str | None] = mapped_column(String(32))

    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False
    )
    department: Mapped[Department] = relationship(back_populates="employees")

    # Self-referential — gives me the reporting chain.
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    manager: Mapped[Employee | None] = relationship(remote_side=[id], back_populates="reports")
    reports: Mapped[list[Employee]] = relationship(back_populates="manager")

    attendance_logs: Mapped[list[AttendanceLog]] = relationship(
        back_populates="employee", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[Task]] = relationship(
        back_populates="employee", cascade="all, delete-orphan"
    )
    leave_requests: Mapped[list[LeaveRequest]] = relationship(
        back_populates="employee",
        foreign_keys="LeaveRequest.employee_id",
        cascade="all, delete-orphan",
    )
    reviews_received: Mapped[list[PerformanceReview]] = relationship(
        back_populates="employee",
        foreign_keys="PerformanceReview.employee_id",
        cascade="all, delete-orphan",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:
        # Flask-Login checks this on login, so leavers can't sign in.
        # This narrows UserMixin.is_active from a plain attribute to a
        # property; mypy does not see the clash because it cannot resolve
        # db.Model as a base, and warn_unused_ignores rejects an ignore here.
        return self.status != EmployeeStatus.TERMINATED

    def has_role(self, *roles: Role) -> bool:
        return self.role in roles

    @property
    def is_hr(self) -> bool:
        return self.role == Role.HR_ADMIN

    @property
    def is_manager(self) -> bool:
        return self.role in {Role.MANAGER, Role.HR_ADMIN}

    def can_view(self, other: Employee) -> bool:
        """HR sees everyone, managers see their own reports, everyone sees themselves."""
        if self.is_hr or self.id == other.id:
            return True
        return self.role == Role.MANAGER and other.manager_id == self.id

    @property
    def initials(self) -> str:
        parts = [p for p in self.full_name.split() if p]
        return "".join(p[0].upper() for p in parts[:2]) or "?"

    @property
    def tenure_days(self) -> int:
        end = self.exit_date or date.today()
        return (end - self.join_date).days

    def __repr__(self) -> str:
        return f"<Employee {self.employee_code} {self.full_name!r}>"


@login_manager.user_loader
def load_user(user_id: str) -> Employee | None:
    return db.session.get(Employee, int(user_id))
