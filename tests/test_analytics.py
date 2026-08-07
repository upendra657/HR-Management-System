"""Reconciliation checks and aggregate reports.

Each check gets a test that plants exactly the discrepancy it looks for and
one that confirms it stays quiet on clean data. A check that never fires is
indistinguishable from a check that does not work.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.analytics import reconciliation as recon
from app.analytics import reports as rp
from app.extensions import db
from app.models import (
    AttendanceLog,
    AttendanceStatus,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
    Project,
    Role,
    Task,
)


def a_weekday(offset: int) -> date:
    """A weekday `offset` days back, skipping over weekends."""
    day = date.today() - timedelta(days=offset)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


@pytest.fixture()
def project(db):
    p = Project(name="Migration", code="MIG-01", start_date=date.today() - timedelta(days=500))
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture()
def staff(make_employee):
    boss = make_employee("boss", role=Role.MANAGER)
    return {
        "hr": make_employee("hr", role=Role.HR_ADMIN),
        "boss": boss,
        "alice": make_employee("alice", manager=boss),
    }


def add_day(employee, on, status=AttendanceStatus.PRESENT, ci=time(9, 0), co=time(17, 0)):
    log = AttendanceLog(
        employee_id=employee.id,
        work_date=on,
        status=status,
        clock_in=ci if status.is_worked else None,
        clock_out=co if status.is_worked else None,
    )
    db.session.add(log)
    db.session.commit()
    return log


class TestFindingShape:
    def test_rate_is_a_percentage_of_scanned(self) -> None:
        f = recon.Finding(
            code="X",
            title="t",
            severity=recon.Severity.LOW,
            explanation="",
            count=3,
            scanned=200,
        )
        assert f.rate == 1.5
        assert not f.clean

    def test_rate_is_zero_when_nothing_scanned(self) -> None:
        f = recon.Finding(code="X", title="t", severity=recon.Severity.LOW, explanation="", count=0)
        assert f.rate == 0.0
        assert f.clean


class TestOpenShifts:
    def test_quiet_on_closed_shifts(self, staff) -> None:
        add_day(staff["alice"], a_weekday(3))
        assert recon.open_shifts().count == 0

    def test_finds_a_missing_clock_out(self, staff) -> None:
        add_day(staff["alice"], a_weekday(3), co=None)
        finding = recon.open_shifts()
        assert finding.count == 1
        assert "alice" in finding.sample[0]["employee"].lower()

    def test_ignores_today(self, staff) -> None:
        """Still clocked in right now is not a discrepancy."""
        add_day(staff["alice"], date.today(), co=None)
        assert recon.open_shifts().count == 0


class TestWorkedDuringLeave:
    def _approved(self, employee, start, end, approver):
        req = LeaveRequest(
            employee_id=employee.id,
            leave_type=LeaveType.ANNUAL,
            status=LeaveStatus.APPROVED,
            start_date=start,
            end_date=end,
            days=1,
            decided_by_id=approver.id,
            decided_at=datetime.now(timezone.utc),
        )
        db.session.add(req)
        db.session.commit()
        return req

    def test_quiet_when_the_timesheet_agrees(self, staff) -> None:
        day = a_weekday(5)
        self._approved(staff["alice"], day, day, staff["boss"])
        add_day(staff["alice"], day, status=AttendanceStatus.LEAVE)
        assert recon.worked_during_approved_leave().count == 0

    def test_flags_working_on_approved_leave(self, staff) -> None:
        day = a_weekday(5)
        self._approved(staff["alice"], day, day, staff["boss"])
        add_day(staff["alice"], day, status=AttendanceStatus.PRESENT)

        finding = recon.worked_during_approved_leave()
        assert finding.count == 1
        assert finding.severity is recon.Severity.HIGH

    def test_flags_leave_missing_from_the_timesheet(self, staff) -> None:
        day = a_weekday(5)
        self._approved(staff["alice"], day, day, staff["boss"])
        # No attendance row at all.
        finding = recon.leave_missing_from_timesheet()
        assert finding.count == 1

    def test_quiet_when_leave_is_on_the_timesheet(self, staff) -> None:
        day = a_weekday(5)
        self._approved(staff["alice"], day, day, staff["boss"])
        add_day(staff["alice"], day, status=AttendanceStatus.LEAVE)
        assert recon.leave_missing_from_timesheet().count == 0


class TestOverLogged:
    def test_quiet_when_bookings_fit(self, staff, project) -> None:
        day = a_weekday(4)
        add_day(staff["alice"], day)  # 8 hours
        db.session.add(
            Task(
                employee_id=staff["alice"].id,
                project_id=project.id,
                task_date=day,
                hours=6,
                description="work",
            )
        )
        db.session.commit()
        assert recon.over_logged_days().count == 0

    def test_flags_booking_more_than_worked(self, staff, project) -> None:
        day = a_weekday(4)
        add_day(staff["alice"], day)  # 8 hours
        for hours in (5, 5):
            db.session.add(
                Task(
                    employee_id=staff["alice"].id,
                    project_id=project.id,
                    task_date=day,
                    hours=hours,
                    description="work",
                )
            )
        db.session.commit()

        finding = recon.over_logged_days()
        assert finding.count == 1
        assert finding.sample[0]["logged"] == 10.0
        assert finding.sample[0]["worked"] == 8.0


class TestTaskChecks:
    def test_flags_task_with_no_attendance(self, staff, project) -> None:
        db.session.add(
            Task(
                employee_id=staff["alice"].id,
                project_id=project.id,
                task_date=a_weekday(4),
                hours=3,
                description="work",
            )
        )
        db.session.commit()
        assert recon.tasks_without_attendance().count == 1

    def test_flags_task_outside_project_dates(self, staff, db) -> None:
        later = Project(name="Later", code="LTR-01", start_date=date.today() - timedelta(days=2))
        db.session.add(later)
        db.session.commit()

        day = a_weekday(30)
        add_day(staff["alice"], day)
        db.session.add(
            Task(
                employee_id=staff["alice"].id,
                project_id=later.id,
                task_date=day,
                hours=3,
                description="work",
            )
        )
        db.session.commit()
        assert recon.tasks_outside_project_dates().count == 1


class TestEmploymentPeriod:
    def test_flags_attendance_before_joining(self, staff) -> None:
        before = staff["alice"].join_date - timedelta(days=10)
        add_day(staff["alice"], before)
        finding = recon.attendance_outside_employment()
        assert finding.count == 1
        assert finding.severity is recon.Severity.HIGH

    def test_quiet_within_employment(self, staff) -> None:
        add_day(staff["alice"], a_weekday(3))
        assert recon.attendance_outside_employment().count == 0


class TestImplausibleShifts:
    def test_flags_a_very_long_shift(self, staff) -> None:
        add_day(staff["alice"], a_weekday(3), ci=time(5, 0), co=time(23, 0))
        assert recon.implausible_shifts().count == 1

    def test_quiet_on_a_long_but_believable_day(self, staff) -> None:
        add_day(staff["alice"], a_weekday(3), ci=time(8, 0), co=time(20, 0))
        assert recon.implausible_shifts().count == 0


class TestNoManager:
    def test_counts_staff_without_a_manager(self, staff) -> None:
        # hr and boss have none in this fixture; alice reports to boss.
        assert recon.active_staff_without_manager().count == 2


class TestFutureDated:
    def test_flags_a_future_worked_day(self, staff) -> None:
        add_day(staff["alice"], date.today() + timedelta(days=3))
        assert recon.future_dated_attendance().count == 1


class TestReport:
    def test_runs_every_check(self, staff) -> None:
        report = recon.run_all()
        assert len(report.findings) == len(recon.CHECKS)
        assert {f.code for f in report.findings} == {
            "OPEN_SHIFT",
            "WORKED_ON_LEAVE",
            "LEAVE_NOT_ON_TIMESHEET",
            "OVER_LOGGED",
            "TASK_NO_ATTENDANCE",
            "TASK_OUTSIDE_PROJECT",
            "OUTSIDE_EMPLOYMENT",
            "IMPLAUSIBLE_SHIFT",
            "NO_MANAGER",
            "FUTURE_DATED",
        }

    def test_headline_reads_cleanly_when_there_is_nothing(self, make_employee) -> None:
        boss = make_employee("solo_boss", role=Role.MANAGER)
        make_employee("solo", manager=boss)
        report = recon.Report(findings=[], generated_at=date.today())
        assert report.headline == "No discrepancies found."

    def test_checks_do_not_modify_data(self, staff, project) -> None:
        """A report that edits data is a report you cannot trust twice."""
        day = a_weekday(4)
        add_day(staff["alice"], day, co=None)

        def counts():
            return {
                "attendance": db.session.scalar(select(func.count()).select_from(AttendanceLog)),
                "tasks": db.session.scalar(select(func.count()).select_from(Task)),
            }

        before = counts()
        recon.run_all()
        after = counts()
        assert before == after

    def test_export_rows_cover_every_check(self, staff) -> None:
        rows = recon.to_rows(recon.run_all())
        assert len(rows) == len(recon.CHECKS)
        assert set(rows[0]) == {
            "code",
            "title",
            "severity",
            "count",
            "scanned",
            "rate_pct",
            "explanation",
        }


class TestReports:
    def test_headline_counts_active_staff(self, staff) -> None:
        h = rp.headline()
        assert h.headcount == 3
        assert h.departments == 1

    def test_headcount_by_department(self, staff) -> None:
        rows = rp.headcount_by_department()
        assert rows[0]["department"] == "Engineering"
        assert rows[0]["headcount"] == 3

    def test_hours_are_summed_in_sql(self, staff) -> None:
        add_day(staff["alice"], a_weekday(2), ci=time(9, 0), co=time(17, 30))
        assert rp.headline().hours_30d == 8.5

    def test_open_shift_contributes_no_hours(self, staff) -> None:
        add_day(staff["alice"], a_weekday(2), co=None)
        assert rp.headline().hours_30d == 0.0

    def test_attendance_by_month_groups(self, staff) -> None:
        add_day(staff["alice"], a_weekday(2))
        rows = rp.attendance_by_month()
        assert rows
        assert rows[-1]["worked"] >= 1

    def test_project_utilisation(self, staff, project) -> None:
        day = a_weekday(3)
        add_day(staff["alice"], day)
        db.session.add(
            Task(
                employee_id=staff["alice"].id,
                project_id=project.id,
                task_date=day,
                hours=4,
                description="work",
            )
        )
        db.session.commit()
        rows = rp.project_utilisation()
        assert rows[0]["code"] == "MIG-01"
        assert rows[0]["hours"] == 4.0
        assert rows[0]["people"] == 1

    def test_overtime_counts_long_days(self, staff) -> None:
        add_day(staff["alice"], a_weekday(2), ci=time(8, 0), co=time(19, 0))
        assert rp.overtime_days() == 1

    def test_timesheet_export_shape(self, staff) -> None:
        add_day(staff["alice"], a_weekday(2), ci=time(9, 0), co=time(17, 30))
        rows = rp.timesheet_export(a_weekday(10), date.today())
        assert len(rows) == 1
        assert rows[0]["hours"] == 8.5
        assert set(rows[0]) == {
            "employee_code",
            "name",
            "department",
            "date",
            "status",
            "clock_in",
            "clock_out",
            "hours",
        }


class TestRoutes:
    def test_dashboard_needs_manager(self, client, login, staff) -> None:
        login("alice")
        assert client.get("/reports/").status_code == 403

    def test_manager_can_see_dashboard(self, client, login, staff) -> None:
        login("boss")
        assert client.get("/reports/").status_code == 200

    def test_reconciliation_needs_manager(self, client, login, staff) -> None:
        login("alice")
        assert client.get("/reports/reconciliation").status_code == 403

    def test_reconciliation_renders(self, client, login, staff) -> None:
        add_day(staff["alice"], a_weekday(3), co=None)
        login("hr")
        res = client.get("/reports/reconciliation")
        assert res.status_code == 200
        assert b"no clock-out" in res.data.lower()

    def test_csv_export(self, client, login, staff) -> None:
        add_day(staff["alice"], a_weekday(2))
        login("hr")
        res = client.get("/reports/export/timesheet.csv?days=30")
        assert res.status_code == 200
        assert res.mimetype == "text/csv"
        body = res.get_data(as_text=True)
        assert "employee_code" in body
        assert "8.0" in body

    def test_excel_export(self, client, login, staff) -> None:
        add_day(staff["alice"], a_weekday(2))
        login("hr")
        res = client.get("/reports/export/timesheet.xlsx?days=30")
        assert res.status_code == 200
        # xlsx is a zip - check the magic bytes rather than trusting the header.
        assert res.get_data()[:2] == b"PK"

    def test_unknown_format_404s(self, client, login, staff) -> None:
        login("hr")
        assert client.get("/reports/export/timesheet.json").status_code == 404

    def test_export_range_is_capped(self, client, login, staff) -> None:
        login("hr")
        res = client.get("/reports/export/timesheet.csv?days=99999")
        assert res.status_code == 200
        assert str(date.today().year) in res.headers["Content-Disposition"]

    def test_export_ignores_a_junk_range(self, client, login, staff) -> None:
        login("hr")
        assert client.get("/reports/export/timesheet.csv?days=abc").status_code == 200
