"""Checks on the generated data.

Seed data is easy to get subtly wrong - attendance on weekends, leave that
does not match the timesheet, everybody working on every project. These run
at small scale so they stay fast, but the invariants hold at any size.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    AttendanceLog,
    AttendanceStatus,
    Department,
    Employee,
    EmployeeStatus,
    LeaveRequest,
    LeaveStatus,
    Project,
    Role,
    Task,
)
from scripts.seed import run_seed, working_days


@pytest.fixture()
def seeded(app):
    run_seed(employees=25, months=4, reset=True, quiet=True)
    return app


class TestHelpers:
    def test_working_days_skips_weekends(self) -> None:
        # 2025-06-02 is a Monday, 2025-06-08 the following Sunday.
        days = list(working_days(date(2025, 6, 2), date(2025, 6, 8)))
        assert len(days) == 5
        assert all(d.weekday() < 5 for d in days)

    def test_working_days_inclusive(self) -> None:
        days = list(working_days(date(2025, 6, 2), date(2025, 6, 2)))
        assert days == [date(2025, 6, 2)]


class TestShape:
    def test_creates_all_entities(self, seeded) -> None:
        for model in (Department, Project, Employee, AttendanceLog, Task, LeaveRequest):
            count = db.session.scalar(select(func.count()).select_from(model))
            assert count > 0, f"{model.__name__} is empty"

    def test_requested_employee_count(self, seeded) -> None:
        assert db.session.scalar(select(func.count()).select_from(Employee)) == 25

    def test_has_one_hr_admin_and_managers(self, seeded) -> None:
        roles = dict(
            db.session.execute(select(Employee.role, func.count()).group_by(Employee.role)).all()
        )
        assert roles[Role.HR_ADMIN] == 1
        assert roles[Role.MANAGER] >= 1

    def test_everyone_but_the_head_has_a_manager(self, seeded) -> None:
        without = db.session.scalars(select(Employee).where(Employee.manager_id.is_(None))).all()
        assert len(without) == 1
        assert without[0].role is Role.HR_ADMIN

    def test_passwords_are_hashed_and_usable(self, seeded) -> None:
        emp = db.session.scalars(select(Employee)).first()
        assert "demo12345" not in emp.password_hash
        assert emp.check_password("demo12345")


class TestDeterminism:
    def test_same_seed_gives_same_data(self, app) -> None:
        run_seed(employees=12, months=2, reset=True, seed=99, quiet=True)
        first = [e.full_name for e in db.session.scalars(select(Employee).order_by(Employee.id))]

        run_seed(employees=12, months=2, reset=True, seed=99, quiet=True)
        second = [e.full_name for e in db.session.scalars(select(Employee).order_by(Employee.id))]

        assert first == second

    def test_different_seed_gives_different_data(self, app) -> None:
        run_seed(employees=12, months=2, reset=True, seed=1, quiet=True)
        first = [e.full_name for e in db.session.scalars(select(Employee).order_by(Employee.id))]

        run_seed(employees=12, months=2, reset=True, seed=2, quiet=True)
        second = [e.full_name for e in db.session.scalars(select(Employee).order_by(Employee.id))]

        assert first != second


class TestAttendanceInvariants:
    def test_no_weekend_attendance(self, seeded) -> None:
        for log in db.session.scalars(select(AttendanceLog)):
            assert log.work_date.weekday() < 5

    def test_no_attendance_before_join_or_after_exit(self, seeded) -> None:
        rows = db.session.execute(
            select(AttendanceLog.work_date, Employee.join_date, Employee.exit_date).join(
                Employee, Employee.id == AttendanceLog.employee_id
            )
        ).all()
        for work_date, join_date, exit_date in rows:
            assert work_date >= join_date
            if exit_date:
                assert work_date <= exit_date

    def test_worked_days_have_a_clock_in(self, seeded) -> None:
        bad = db.session.scalars(
            select(AttendanceLog).where(
                AttendanceLog.status.in_([AttendanceStatus.PRESENT, AttendanceStatus.REMOTE]),
                AttendanceLog.clock_in.is_(None),
            )
        ).all()
        assert bad == []

    def test_non_worked_days_have_no_clock_times(self, seeded) -> None:
        bad = db.session.scalars(
            select(AttendanceLog).where(
                AttendanceLog.status.notin_([AttendanceStatus.PRESENT, AttendanceStatus.REMOTE]),
                AttendanceLog.clock_in.isnot(None),
            )
        ).all()
        assert bad == []

    def test_some_clock_outs_are_missing(self, seeded) -> None:
        # Deliberate: the reports have to cope with an unclosed shift.
        missing = db.session.scalar(
            select(func.count())
            .select_from(AttendanceLog)
            .where(
                AttendanceLog.status == AttendanceStatus.PRESENT,
                AttendanceLog.clock_out.is_(None),
            )
        )
        assert missing > 0


class TestLeaveInvariants:
    def test_decision_fields_are_all_or_nothing(self, seeded) -> None:
        for lr in db.session.scalars(select(LeaveRequest)):
            assert (lr.decided_by_id is None) == (lr.decided_at is None)

    def test_pending_requests_are_undecided(self, seeded) -> None:
        pending = db.session.scalars(
            select(LeaveRequest).where(LeaveRequest.status == LeaveStatus.PENDING)
        ).all()
        assert all(lr.decided_by_id is None for lr in pending)

    def test_decided_requests_have_an_approver(self, seeded) -> None:
        decided = db.session.scalars(
            select(LeaveRequest).where(
                LeaveRequest.status.in_([LeaveStatus.APPROVED, LeaveStatus.REJECTED])
            )
        ).all()
        assert decided
        assert all(lr.decided_by_id is not None for lr in decided)

    def test_nobody_approves_their_own_leave(self, seeded) -> None:
        for lr in db.session.scalars(select(LeaveRequest)):
            if lr.decided_by_id:
                assert lr.decided_by_id != lr.employee_id

    def test_approved_leave_mostly_shows_in_attendance(self, seeded) -> None:
        """Most approved leave is reflected on the timesheet - but not all.

        The gap is intentional and is what the reconciliation report exists
        to surface, so this asserts a band rather than perfect agreement.
        """
        approved = db.session.scalars(
            select(LeaveRequest).where(LeaveRequest.status == LeaveStatus.APPROVED)
        ).all()

        marked = total = 0
        for lr in approved:
            for day in working_days(lr.start_date, lr.end_date):
                log = db.session.scalar(
                    select(AttendanceLog).where(
                        AttendanceLog.employee_id == lr.employee_id,
                        AttendanceLog.work_date == day,
                    )
                )
                if log is None:
                    continue
                total += 1
                if log.status is AttendanceStatus.LEAVE:
                    marked += 1

        assert total > 0
        assert 0.75 <= marked / total <= 0.99, f"agreement was {marked / total:.0%}"


class TestTaskInvariants:
    def test_tasks_fall_within_project_dates(self, seeded) -> None:
        rows = db.session.execute(
            select(Task.task_date, Project.start_date, Project.end_date).join(
                Project, Project.id == Task.project_id
            )
        ).all()
        for task_date, start, end in rows:
            assert task_date >= start
            if end:
                assert task_date <= end

    def test_task_hours_are_sane(self, seeded) -> None:
        rows = db.session.scalars(select(Task.hours)).all()
        assert all(0 < float(h) <= 24 for h in rows)

    def test_tasks_only_on_days_with_attendance(self, seeded) -> None:
        orphans = db.session.scalar(
            select(func.count())
            .select_from(Task)
            .outerjoin(
                AttendanceLog,
                (AttendanceLog.employee_id == Task.employee_id)
                & (AttendanceLog.work_date == Task.task_date),
            )
            .where(AttendanceLog.id.is_(None))
        )
        assert orphans == 0


class TestReset:
    def test_reset_replaces_rather_than_appends(self, app) -> None:
        run_seed(employees=10, months=2, reset=True, quiet=True)
        first = db.session.scalar(select(func.count()).select_from(Employee))

        run_seed(employees=10, months=2, reset=True, quiet=True)
        second = db.session.scalar(select(func.count()).select_from(Employee))

        assert first == second == 10

    def test_refuses_to_run_over_existing_data(self, app) -> None:
        run_seed(employees=10, months=2, reset=True, quiet=True)
        with pytest.raises(SystemExit):
            run_seed(employees=10, months=2, reset=False, quiet=True)


class TestTerminatedEmployees:
    def test_terminated_have_an_exit_date_and_cannot_log_in(self, app) -> None:
        run_seed(employees=60, months=6, reset=True, quiet=True)
        gone = db.session.scalars(
            select(Employee).where(Employee.status == EmployeeStatus.TERMINATED)
        ).all()
        assert gone, "expected some terminated employees in a sample of 60"
        for e in gone:
            assert e.exit_date is not None
            assert e.exit_date >= e.join_date
            assert not e.is_active
