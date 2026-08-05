"""Performance reviews."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.enums import ReviewStatus
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.employee import Employee


class PerformanceReview(TimestampMixin, db.Model):
    __tablename__ = "performance_reviews"
    __table_args__ = (
        CheckConstraint("period_end >= period_start", name="ck_review_period_order"),
        CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)", name="ck_review_rating_range"
        ),
        CheckConstraint("reviewer_id <> employee_id", name="ck_review_no_self_review"),
        UniqueConstraint(
            "employee_id", "period_start", "period_end", name="uq_review_employee_period"
        ),
        Index("ix_review_employee_status", "employee_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )

    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[ReviewStatus] = mapped_column(
        Enum(
            ReviewStatus, name="review_status_enum", values_callable=lambda e: [m.value for m in e]
        ),
        default=ReviewStatus.DRAFT,
        nullable=False,
    )
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    strengths: Mapped[str | None] = mapped_column(Text)
    improvements: Mapped[str | None] = mapped_column(Text)
    employee_comment: Mapped[str | None] = mapped_column(Text)

    employee: Mapped[Employee] = relationship(
        back_populates="reviews_received", foreign_keys=[employee_id]
    )
    reviewer: Mapped[Employee] = relationship(foreign_keys=[reviewer_id])

    @property
    def is_editable(self) -> bool:
        return self.status == ReviewStatus.DRAFT

    def __repr__(self) -> str:
        return f"<PerformanceReview emp={self.employee_id} {self.period_start}..{self.period_end}>"
