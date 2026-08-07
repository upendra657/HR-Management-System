"""Timesheet rules: clocking, edit windows, and booking time to projects."""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import AttendanceLog, AttendanceStatus, Project, Role, Task
from app.services import attendance as svc


@pytest.fixture()
def worker(make_employee):
    return make_employee("worker")


@pytest.fixture()
def hr(make_employee):
    return make_employee("hr", role=Role.HR_ADMIN)


@pytest.fixture()
def proj(db):
    p = Project(name="Migration", code="MIG-01", start_date=date.today() - timedelta(days=400))
    db.session.add(p)
    db.session.commit()
    return p


def a_day(offset: int = 0) -> date:
    return date.today() - timedelta(days=offset)


class TestClockIn:
    def test_creates_todays_row(self, worker) -> None:
        log = svc.clock_in(worker, at=time(9, 0))
        assert log.work_date == date.today()
        assert log.clock_in == time(9, 0)
        assert log.status is AttendanceStatus.PRESENT

    def test_remote_is_recorded_as_such(self, worker) -> None:
        log = svc.clock_in(worker, at=time(9, 0), status=AttendanceStatus.REMOTE)
        assert log.status is AttendanceStatus.REMOTE

    def test_cannot_clock_in_twice(self, worker) -> None:
        svc.clock_in(worker, at=time(9, 0))
        with pytest.raises(svc.AttendanceError, match="Already clocked in"):
            svc.clock_in(worker, at=time(10, 0))

    def test_cannot_clock_in_for_the_future(self, worker) -> None:
        with pytest.raises(svc.AttendanceError, match="future"):
            svc.clock_in(worker, on=date.today() + timedelta(days=1), at=time(9, 0))

    def test_cannot_clock_in_on_approved_leave(self, worker, db) -> None:
        db.session.add(
            AttendanceLog(
                employee_id=worker.id,
                work_date=date.today(),
                status=AttendanceStatus.LEAVE,
            )
        )
        db.session.commit()
        with pytest.raises(svc.AttendanceError, match="approved leave"):
            svc.clock_in(worker, at=time(9, 0))

    def test_only_one_row_per_day_even_after_an_absence(self, worker, db) -> None:
        """The unique constraint means this has to update, not insert."""
        db.session.add(
            AttendanceLog(
                employee_id=worker.id,
                work_date=date.today(),
                status=AttendanceStatus.ABSENT,
            )
        )
        db.session.commit()

        svc.clock_in(worker, at=time(11, 0))
        rows = db.session.scalars(select(AttendanceLog)).all()
        assert len(rows) == 1
        assert rows[0].clock_in == time(11, 0)


class TestClockOut:
    def test_records_the_time(self, worker) -> None:
        svc.clock_in(worker, at=time(9, 0))
        log = svc.clock_out(worker, at=time(17, 30))
        assert log.clock_out == time(17, 30)
        assert log.hours_worked == 8.5

    def test_needs_a_clock_in_first(self, worker) -> None:
        with pytest.raises(svc.AttendanceError, match="not clocked in"):
            svc.clock_out(worker, at=time(17, 0))

    def test_cannot_clock_out_twice(self, worker) -> None:
        svc.clock_in(worker, at=time(9, 0))
        svc.clock_out(worker, at=time(17, 0))
        with pytest.raises(svc.AttendanceError, match="Already clocked out"):
            svc.clock_out(worker, at=time(18, 0))

    def test_cannot_clock_out_before_clocking_in(self, worker) -> None:
        """Caught in the service so the message is usable - the check
        constraint would otherwise raise an IntegrityError."""
        svc.clock_in(worker, at=time(9, 0))
        with pytest.raises(svc.AttendanceError, match="must be after"):
            svc.clock_out(worker, at=time(8, 0))

    def test_same_minute_clock_out_is_refused(self, worker) -> None:
        """Times are stored to the minute, so in and out in the same minute
        is a zero-length shift. Refusing is correct - but it does mean a
        misclick cannot be undone by immediately clocking out."""
        svc.clock_in(worker, at=time(9, 0))
        with pytest.raises(svc.AttendanceError, match="must be after"):
            svc.clock_out(worker, at=time(9, 0))

    def test_overnight_shifts_are_not_supported(self, worker) -> None:
        """Known limitation: a shift is one calendar day. Someone working
        22:00 to 06:00 has to be recorded as two days, and the second has no
        clock-in. Worth fixing if this were ever used somewhere with night
        shifts."""
        svc.clock_in(worker, at=time(22, 0))
        with pytest.raises(svc.AttendanceError, match="must be after"):
            svc.clock_out(worker, at=time(6, 0))


class TestEditWindow:
    def test_can_correct_a_recent_day(self, worker) -> None:
        assert svc.may_edit(worker, worker.id, a_day(3))

    def test_cannot_correct_an_old_day(self, worker) -> None:
        assert not svc.may_edit(worker, worker.id, a_day(60))

    def test_hr_can_correct_anything(self, hr, worker) -> None:
        assert svc.may_edit(hr, worker.id, a_day(400))

    def test_cannot_edit_someone_elses_day(self, worker, make_employee) -> None:
        other = make_employee("other")
        assert not svc.may_edit(worker, other.id, date.today())

    def test_old_day_raises_a_helpful_error(self, worker) -> None:
        with pytest.raises(svc.AttendanceError, match="Ask HR"):
            svc.clock_in(worker, on=a_day(30), at=time(9, 0))

    def test_editing_another_person_raises_a_different_error(self, worker, make_employee) -> None:
        other = make_employee("other")
        with pytest.raises(svc.AttendanceError, match="your own"):
            svc.record_day(worker, other.id, on=date.today(), status=AttendanceStatus.ABSENT)


class TestRecordDay:
    def test_backfills_a_missing_day(self, worker) -> None:
        log = svc.record_day(
            worker,
            worker.id,
            on=a_day(2),
            status=AttendanceStatus.PRESENT,
            clock_in_at=time(9, 0),
            clock_out_at=time(17, 0),
        )
        assert log.hours_worked == 8.0

    def test_worked_day_needs_a_clock_in(self, worker) -> None:
        with pytest.raises(svc.AttendanceError, match="needs a clock-in"):
            svc.record_day(worker, worker.id, on=a_day(1), status=AttendanceStatus.PRESENT)

    def test_non_worked_day_discards_times(self, worker) -> None:
        """Absent with a clock-in would be meaningless, so the times go."""
        log = svc.record_day(
            worker,
            worker.id,
            on=a_day(1),
            status=AttendanceStatus.ABSENT,
            clock_in_at=time(9, 0),
            clock_out_at=time(17, 0),
        )
        assert log.clock_in is None and log.clock_out is None

    def test_rejects_reversed_times(self, worker) -> None:
        with pytest.raises(svc.AttendanceError, match="after clock-in"):
            svc.record_day(
                worker,
                worker.id,
                on=a_day(1),
                status=AttendanceStatus.PRESENT,
                clock_in_at=time(17, 0),
                clock_out_at=time(9, 0),
            )

    def test_replaces_rather_than_duplicates(self, worker, db) -> None:
        svc.record_day(
            worker,
            worker.id,
            on=a_day(1),
            status=AttendanceStatus.PRESENT,
            clock_in_at=time(9, 0),
            clock_out_at=time(17, 0),
        )
        svc.record_day(
            worker,
            worker.id,
            on=a_day(1),
            status=AttendanceStatus.REMOTE,
            clock_in_at=time(10, 0),
            clock_out_at=time(18, 0),
        )
        rows = db.session.scalars(select(AttendanceLog)).all()
        assert len(rows) == 1
        assert rows[0].status is AttendanceStatus.REMOTE


class TestLogTask:
    def _worked_day(self, worker, on=None, hours=8):
        on = on or date.today()
        svc.record_day(
            worker,
            worker.id,
            on=on,
            status=AttendanceStatus.PRESENT,
            clock_in_at=time(9, 0),
            clock_out_at=time(9 + hours, 0),
        )
        return on

    def test_books_hours_to_a_project(self, worker, proj) -> None:
        on = self._worked_day(worker)
        task = svc.log_task(
            worker,
            worker.id,
            project_id=proj.id,
            on=on,
            hours=3,
            description="Indexing",
        )
        assert float(task.hours) == 3.0

    def test_needs_an_attendance_row(self, worker, proj) -> None:
        with pytest.raises(svc.AttendanceError, match="no attendance recorded"):
            svc.log_task(
                worker,
                worker.id,
                project_id=proj.id,
                on=date.today(),
                hours=3,
                description="Indexing",
            )

    def test_cannot_book_time_to_a_leave_day(self, worker, proj, db) -> None:
        db.session.add(
            AttendanceLog(
                employee_id=worker.id,
                work_date=date.today(),
                status=AttendanceStatus.LEAVE,
            )
        )
        db.session.commit()
        with pytest.raises(svc.AttendanceError, match="no time to book"):
            svc.log_task(
                worker,
                worker.id,
                project_id=proj.id,
                on=date.today(),
                hours=3,
                description="Indexing",
            )

    def test_cannot_exceed_the_hours_worked(self, worker, proj) -> None:
        on = self._worked_day(worker, hours=8)
        svc.log_task(worker, worker.id, project_id=proj.id, on=on, hours=6, description="A")
        with pytest.raises(svc.AttendanceError, match="8-hour day"):
            svc.log_task(worker, worker.id, project_id=proj.id, on=on, hours=3, description="B")

    def test_can_fill_the_day_exactly(self, worker, proj) -> None:
        on = self._worked_day(worker, hours=8)
        svc.log_task(worker, worker.id, project_id=proj.id, on=on, hours=5, description="A")
        svc.log_task(worker, worker.id, project_id=proj.id, on=on, hours=3, description="B")
        assert svc.day_totals(worker.id, on).unlogged == 0.0

    def test_no_cap_while_still_clocked_in(self, worker, proj) -> None:
        """Mid-shift the worked total is zero; refusing to log would be daft."""
        svc.clock_in(worker, at=time(9, 0))
        svc.log_task(
            worker,
            worker.id,
            project_id=proj.id,
            on=date.today(),
            hours=4,
            description="Morning",
        )
        assert svc.day_totals(worker.id, date.today()).logged == 4.0

    def test_rejects_zero_or_negative_hours(self, worker, proj) -> None:
        on = self._worked_day(worker)
        for bad in (0, -1):
            with pytest.raises(svc.AttendanceError, match="greater than zero"):
                svc.log_task(
                    worker,
                    worker.id,
                    project_id=proj.id,
                    on=on,
                    hours=bad,
                    description="X",
                )

    def test_requires_a_description(self, worker, proj) -> None:
        on = self._worked_day(worker)
        with pytest.raises(svc.AttendanceError, match="Describe"):
            svc.log_task(worker, worker.id, project_id=proj.id, on=on, hours=1, description="   ")

    def test_rejects_a_project_that_had_not_started(self, worker, db) -> None:
        on = self._worked_day(worker)
        future = Project(name="Later", code="LTR-01", start_date=date.today() + timedelta(days=30))
        db.session.add(future)
        db.session.commit()
        with pytest.raises(svc.AttendanceError, match="not running"):
            svc.log_task(worker, worker.id, project_id=future.id, on=on, hours=1, description="X")

    def test_rejects_an_unknown_project(self, worker) -> None:
        on = self._worked_day(worker)
        with pytest.raises(svc.AttendanceError, match="does not exist"):
            svc.log_task(worker, worker.id, project_id=9999, on=on, hours=1, description="X")


class TestDayTotals:
    def test_reports_unbooked_hours(self, worker, proj) -> None:
        svc.record_day(
            worker,
            worker.id,
            on=date.today(),
            status=AttendanceStatus.PRESENT,
            clock_in_at=time(9, 0),
            clock_out_at=time(17, 0),
        )
        svc.log_task(
            worker,
            worker.id,
            project_id=proj.id,
            on=date.today(),
            hours=5,
            description="A",
        )
        totals = svc.day_totals(worker.id, date.today())
        assert (totals.worked, totals.logged, totals.unlogged) == (8.0, 5.0, 3.0)
        assert not totals.over_logged


class TestMonthSummary:
    def test_counts_by_status(self, worker) -> None:
        today = date.today()
        svc.record_day(
            worker,
            worker.id,
            on=today,
            status=AttendanceStatus.PRESENT,
            clock_in_at=time(9, 0),
            clock_out_at=time(17, 0),
        )
        s = svc.month_summary(worker.id, today.year, today.month)
        assert s.present == 1
        assert s.hours == 8.0
        assert s.expected_days > 15  # every month has more than 15 working days

    def test_unrecorded_counts_the_gap(self, worker) -> None:
        today = date.today()
        s = svc.month_summary(worker.id, today.year, today.month)
        assert s.recorded == 0
        assert s.unrecorded == s.expected_days


class TestOpenShifts:
    def test_finds_days_with_no_clock_out(self, worker, db) -> None:
        db.session.add(
            AttendanceLog(
                employee_id=worker.id,
                work_date=a_day(3),
                status=AttendanceStatus.PRESENT,
                clock_in=time(9, 0),
            )
        )
        db.session.commit()
        assert len(svc.open_shifts()) == 1

    def test_ignores_today(self, worker) -> None:
        """Still being clocked in right now is not a discrepancy."""
        svc.clock_in(worker, at=time(9, 0))
        assert svc.open_shifts() == []

    def test_ignores_closed_days(self, worker) -> None:
        svc.record_day(
            worker,
            worker.id,
            on=a_day(2),
            status=AttendanceStatus.PRESENT,
            clock_in_at=time(9, 0),
            clock_out_at=time(17, 0),
        )
        assert svc.open_shifts() == []


class TestRoutes:
    def test_timesheet_requires_login(self, client) -> None:
        assert client.get("/timesheet/").status_code == 302

    def test_own_timesheet_renders(self, client, login, worker) -> None:
        login("worker")
        assert client.get("/timesheet/").status_code == 200

    def test_cannot_view_someone_elses(self, client, login, worker, make_employee) -> None:
        other = make_employee("other")
        login("worker")
        assert client.get(f"/timesheet/?employee={other.id}").status_code == 404

    def test_clock_in_through_the_route(self, client, login, worker) -> None:
        login("worker")
        res = client.post("/timesheet/clock-in", follow_redirects=True)
        assert res.status_code == 200
        assert svc.day(worker.id, date.today()).clock_in is not None

    def test_clock_out_through_the_route(self, client, login, worker) -> None:
        svc.clock_in(worker, at=time(9, 0))
        login("worker")
        # Explicit time, so the test does not depend on the wall clock being
        # later than 09:00 when it happens to run.
        res = client.post("/timesheet/clock-out", data={"at": "17:30"}, follow_redirects=True)
        assert res.status_code == 200

        log = svc.day(worker.id, date.today())
        assert log.clock_out == time(17, 30)
        assert log.hours_worked == 8.5

    def test_clock_in_accepts_a_corrected_time(self, client, login, worker) -> None:
        login("worker")
        client.post("/timesheet/clock-in", data={"at": "08:15"}, follow_redirects=True)
        assert svc.day(worker.id, date.today()).clock_in == time(8, 15)

    def test_bad_month_is_rejected(self, client, login, worker) -> None:
        login("worker")
        assert client.get("/timesheet/?month=13").status_code == 400

    def test_task_form_reports_errors(self, client, login, worker, proj) -> None:
        login("worker")
        res = client.post(
            f"/timesheet/day/{date.today().isoformat()}",
            data={"action": "task", "project_id": proj.id, "hours": "3", "description": "Indexing"},
            follow_redirects=True,
        )
        # No attendance row for today yet, so this must fail cleanly.
        assert b"no attendance recorded" in res.data.lower()
        assert db.session.scalar(select(Task)) is None
