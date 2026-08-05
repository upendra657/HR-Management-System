"""Everything imported here so Alembic autogenerate sees the whole schema."""

from __future__ import annotations

from app.models.attendance import AttendanceLog, Task
from app.models.employee import Employee
from app.models.enums import (
    AttendanceStatus,
    EmployeeStatus,
    EmploymentType,
    LeaveStatus,
    LeaveType,
    ReviewStatus,
    Role,
)
from app.models.leave import LeaveRequest
from app.models.organization import Department, Project
from app.models.performance import PerformanceReview

__all__ = [
    "AttendanceLog",
    "AttendanceStatus",
    "Department",
    "Employee",
    "EmployeeStatus",
    "EmploymentType",
    "LeaveRequest",
    "LeaveStatus",
    "LeaveType",
    "PerformanceReview",
    "Project",
    "ReviewStatus",
    "Role",
    "Task",
]
