"""Stored as real Postgres enums. Adding a value needs a migration, which is
the point — it stops free text creeping into these columns."""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    EMPLOYEE = "employee"
    MANAGER = "manager"
    HR_ADMIN = "hr_admin"

    @property
    def label(self) -> str:
        return {"employee": "Employee", "manager": "Manager", "hr_admin": "HR Admin"}[self.value]


class EmploymentType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERN = "intern"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class EmployeeStatus(str, Enum):
    ACTIVE = "active"
    ON_LEAVE = "on_leave"
    TERMINATED = "terminated"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class AttendanceStatus(str, Enum):
    PRESENT = "present"
    REMOTE = "remote"
    ABSENT = "absent"
    LEAVE = "leave"
    HOLIDAY = "holiday"

    @property
    def label(self) -> str:
        return self.value.title()

    @property
    def is_worked(self) -> bool:
        return self in {AttendanceStatus.PRESENT, AttendanceStatus.REMOTE}


class LeaveType(str, Enum):
    ANNUAL = "annual"
    SICK = "sick"
    UNPAID = "unpaid"
    PARENTAL = "parental"
    BEREAVEMENT = "bereavement"

    @property
    def label(self) -> str:
        return self.value.title()


class LeaveStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return self.value.title()

    @property
    def badge(self) -> str:
        return {
            "pending": "warning",
            "approved": "success",
            "rejected": "danger",
            "cancelled": "secondary",
        }[self.value]


class ReviewStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"

    @property
    def label(self) -> str:
        return self.value.title()
