"""Shared SQL expressions.

Time arithmetic on a TIME column is the awkward bit: Postgres wants EXTRACT
and SQLite wants STRFTIME. SQLAlchemy's `extract()` compiles to the right one
for each, so the reports run unchanged against both.
"""

from __future__ import annotations

from sqlalchemy import Integer, cast, func

from app.models import AttendanceLog


def minutes_of(column):
    """Minutes since midnight for a TIME column."""
    return cast(func.extract("hour", column), Integer) * 60 + cast(
        func.extract("minute", column), Integer
    )


def worked_minutes():
    """Elapsed minutes for a shift, as a SQL expression.

    Only meaningful where both ends are present - callers filter for that.
    Negative values are impossible thanks to ck_attendance_time_order.
    """
    return minutes_of(AttendanceLog.clock_out) - minutes_of(AttendanceLog.clock_in)


def worked_hours():
    return worked_minutes() / 60.0


def is_closed_shift():
    """Both clock times recorded."""
    return AttendanceLog.clock_in.isnot(None) & AttendanceLog.clock_out.isnot(None)
