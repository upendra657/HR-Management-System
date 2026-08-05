"""Departments and projects — reference data everything else points at."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Date, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.attendance import Task
    from app.models.employee import Employee


class Department(TimestampMixin, db.Model):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # RESTRICT on the FK side: can't delete a department that still has people.
    employees: Mapped[list[Employee]] = relationship(
        back_populates="department", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Department {self.code} {self.name!r}>"

    @property
    def headcount(self) -> int:
        return len(self.employees)


class Project(TimestampMixin, db.Model):
    """What tasks get logged against."""

    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_projects_date_order",
        ),
        Index("ix_projects_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    client: Mapped[str | None] = mapped_column(String(120))
    site: Mapped[str | None] = mapped_column(String(120))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    tasks: Mapped[list[Task]] = relationship(back_populates="project")

    def __repr__(self) -> str:
        return f"<Project {self.code} {self.name!r}>"
