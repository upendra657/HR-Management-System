"""Schema invariants.

These assert on database constraints rather than Python logic, so they catch
the case where application code is bypassed by a script or a manual query.
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import AttendanceLog, AttendanceStatus, Employee, Role


class TestAccessRules:
    def test_employee_sees_only_themselves(self, make_employee) -> None:
        alice = make_employee("alice")
        bob = make_employee("bob")
        assert alice.can_view(alice)
        assert not alice.can_view(bob)

    def test_hr_sees_everyone(self, make_employee) -> None:
        hr = make_employee("hr", role=Role.HR_ADMIN)
        other = make_employee("bob")
        assert hr.can_view(other)

    def test_manager_sees_direct_reports_only(self, make_employee) -> None:
        boss = make_employee("boss", role=Role.MANAGER)
        report = make_employee("report", manager=boss)
        stranger = make_employee("stranger")
        assert boss.can_view(report)
        assert not boss.can_view(stranger)

    def test_manager_cannot_see_peers_reports(self, make_employee) -> None:
        boss_a = make_employee("boss_a", role=Role.MANAGER)
        boss_b = make_employee("boss_b", role=Role.MANAGER)
        b_report = make_employee("b_report", manager=boss_b)
        assert not boss_a.can_view(b_report)


class TestAttendanceConstraints:
    def _log(self, employee: Employee, day: date, **kw) -> AttendanceLog:
        return AttendanceLog(
            employee_id=employee.id,
            work_date=day,
            clock_in=kw.pop("clock_in", time(9, 0)),
            clock_out=kw.pop("clock_out", time(17, 30)),
            status=kw.pop("status", AttendanceStatus.PRESENT),
            **kw,
        )

    def test_one_log_per_employee_per_day(self, db, employee) -> None:
        db.session.add(self._log(employee, date(2025, 6, 2)))
        db.session.commit()

        db.session.add(self._log(employee, date(2025, 6, 2)))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_clock_out_must_follow_clock_in(self, db, employee) -> None:
        db.session.add(
            self._log(employee, date(2025, 6, 3), clock_in=time(17, 0), clock_out=time(9, 0))
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_hours_worked_calculation(self, db, employee) -> None:
        log = self._log(employee, date(2025, 6, 4), clock_in=time(9, 0), clock_out=time(17, 30))
        db.session.add(log)
        db.session.commit()
        assert log.hours_worked == 8.5
        assert not log.is_overtime

    def test_overtime_flag(self, db, employee) -> None:
        log = self._log(employee, date(2025, 6, 5), clock_in=time(8, 0), clock_out=time(19, 0))
        db.session.add(log)
        db.session.commit()
        assert log.is_overtime

    def test_missing_clock_out_yields_zero_hours(self, db, employee) -> None:
        log = self._log(employee, date(2025, 6, 6), clock_out=None)
        db.session.add(log)
        db.session.commit()
        assert log.hours_worked == 0.0


class TestEmployeeConstraints:
    def test_username_must_be_unique(self, db, make_employee, department) -> None:
        make_employee("alice")
        dupe = Employee(
            employee_code="EMP-9999",
            full_name="Other Alice",
            username="alice",
            email="other@example.com",
            job_title="Engineer",
            join_date=date(2024, 1, 1),
            department=department,
        )
        dupe.set_password("x" * 12)
        db.session.add(dupe)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_exit_date_cannot_precede_join_date(self, db, employee) -> None:
        employee.exit_date = date(2020, 1, 1)  # joined 2024
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_employee_cannot_manage_themselves(self, db, employee) -> None:
        employee.manager_id = employee.id
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


class TestDisplayHelpers:
    def test_initials(self, make_employee) -> None:
        emp = make_employee("ada_lovelace")
        assert emp.initials == "AL"

    def test_tenure_is_positive(self, employee) -> None:
        assert employee.tenure_days > 0
