"""Leave rules: entitlement, overlaps, who may decide, and the attendance
write-back that makes approval more than a status change."""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import (
    AttendanceLog,
    AttendanceStatus,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
    Role,
)
from app.services import leave as svc
from app.services.dates import chargeable_days


def next_monday(after: date | None = None) -> date:
    """A predictable Monday in the future, so tests never straddle a weekend."""
    day = (after or date.today()) + timedelta(days=1)
    while day.weekday() != 0:
        day += timedelta(days=1)
    return day


@pytest.fixture()
def team(make_employee):
    boss = make_employee("boss", role=Role.MANAGER)
    other_boss = make_employee("other_boss", role=Role.MANAGER)
    return {
        "hr": make_employee("hr", role=Role.HR_ADMIN),
        "boss": boss,
        "other_boss": other_boss,
        "alice": make_employee("alice", manager=boss),
        "bob": make_employee("bob", manager=boss),
        "carol": make_employee("carol", manager=other_boss),
    }


@pytest.fixture()
def monday():
    return next_monday()


class TestSubmit:
    def test_creates_a_pending_request(self, team, monday) -> None:
        req = svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=4),
        )
        assert req.status is LeaveStatus.PENDING
        assert req.decided_by_id is None and req.decided_at is None
        assert float(req.days) == 5.0

    def test_counts_only_working_days(self, team, monday) -> None:
        # Monday to the following Friday spans 12 calendar days, 10 working.
        req = svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=11),
        )
        assert float(req.days) == 10.0

    def test_rejects_end_before_start(self, team, monday) -> None:
        with pytest.raises(svc.LeaveError, match="end date"):
            svc.submit(
                team["alice"],
                leave_type=LeaveType.ANNUAL,
                start=monday,
                end=monday - timedelta(days=3),
            )

    def test_rejects_dates_in_the_past(self, team) -> None:
        past = date.today() - timedelta(days=7)
        with pytest.raises(svc.LeaveError, match="past"):
            svc.submit(team["alice"], leave_type=LeaveType.ANNUAL, start=past, end=past)

    def test_rejects_a_weekend_only_range(self, team, monday) -> None:
        saturday = monday + timedelta(days=5)
        with pytest.raises(svc.LeaveError, match="no working days"):
            svc.submit(
                team["alice"],
                leave_type=LeaveType.ANNUAL,
                start=saturday,
                end=saturday + timedelta(days=1),
            )


class TestOverlap:
    def test_blocks_an_overlapping_request(self, team, monday) -> None:
        svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=4),
        )
        with pytest.raises(svc.LeaveError, match="overlaps"):
            svc.submit(
                team["alice"],
                leave_type=LeaveType.SICK,
                start=monday + timedelta(days=2),
                end=monday + timedelta(days=6),
            )

    def test_allows_adjacent_ranges(self, team, monday) -> None:
        svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=4),
        )
        # Starts the next working day - touching, not overlapping.
        svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday + timedelta(days=7),
            end=monday + timedelta(days=8),
        )
        assert len(svc.for_employee(team["alice"].id)) == 2

    def test_rejected_requests_do_not_block(self, team, monday) -> None:
        req = svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=4),
        )
        svc.reject(req, team["boss"])
        # Same dates again - should be fine now.
        svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=4),
        )

    def test_another_persons_leave_does_not_block(self, team, monday) -> None:
        svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=4),
        )
        svc.submit(
            team["bob"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=4),
        )


class TestBalance:
    def test_starts_at_full_entitlement(self, team) -> None:
        bal = svc.balance(team["alice"])
        assert bal.entitlement == svc.ANNUAL_ENTITLEMENT
        assert bal.remaining == svc.ANNUAL_ENTITLEMENT

    def test_pending_requests_are_held_against_it(self, team, monday) -> None:
        svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=4),
        )
        bal = svc.balance(team["alice"])
        assert bal.pending == 5.0
        assert bal.remaining == svc.ANNUAL_ENTITLEMENT - 5

    def test_approval_moves_pending_to_taken(self, team, monday) -> None:
        req = svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=4),
        )
        svc.approve(req, team["boss"])
        bal = svc.balance(team["alice"])
        assert (bal.taken, bal.pending) == (5.0, 0.0)

    def test_sick_leave_does_not_draw_down_annual(self, team, monday) -> None:
        svc.submit(
            team["alice"],
            leave_type=LeaveType.SICK,
            start=monday,
            end=monday + timedelta(days=4),
        )
        assert svc.balance(team["alice"]).remaining == svc.ANNUAL_ENTITLEMENT

    def test_cannot_exceed_entitlement(self, team, monday) -> None:
        start = monday
        # Book 20 of the 21 days across four separate weeks.
        for week in range(4):
            svc.submit(
                team["alice"],
                leave_type=LeaveType.ANNUAL,
                start=start + timedelta(days=week * 7),
                end=start + timedelta(days=week * 7 + 4),
            )
        assert svc.balance(team["alice"]).remaining == 1

        with pytest.raises(svc.LeaveError, match=r"would use"):
            svc.submit(
                team["alice"],
                leave_type=LeaveType.ANNUAL,
                start=start + timedelta(days=28),
                end=start + timedelta(days=32),
            )

    def test_two_pending_requests_cannot_together_exceed(self, team, monday) -> None:
        """The reason pending counts: otherwise both get approved separately."""
        svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=18),
        )  # 15 working days
        with pytest.raises(svc.LeaveError):
            svc.submit(
                team["alice"],
                leave_type=LeaveType.ANNUAL,
                start=monday + timedelta(days=21),
                end=monday + timedelta(days=32),
            )  # would be another 10


class TestWhoMayDecide:
    def _pending(self, team, monday, who="alice"):
        return svc.submit(
            team[who],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=2),
        )

    def test_direct_manager_may_approve(self, team, monday) -> None:
        req = self._pending(team, monday)
        assert svc.can_decide(team["boss"], req)

    def test_hr_may_approve(self, team, monday) -> None:
        req = self._pending(team, monday)
        assert svc.can_decide(team["hr"], req)

    def test_another_manager_may_not(self, team, monday) -> None:
        req = self._pending(team, monday)
        assert not svc.can_decide(team["other_boss"], req)
        with pytest.raises(svc.LeaveError, match="not able to decide"):
            svc.approve(req, team["other_boss"])

    def test_a_peer_may_not(self, team, monday) -> None:
        req = self._pending(team, monday)
        assert not svc.can_decide(team["bob"], req)

    def test_nobody_approves_their_own(self, team, monday) -> None:
        """Even HR. Being an admin is not the same as being impartial."""
        req = self._pending(team, monday, who="hr")
        assert not svc.can_decide(team["hr"], req)
        with pytest.raises(svc.LeaveError):
            svc.approve(req, team["hr"])

    def test_cannot_decide_twice(self, team, monday) -> None:
        req = self._pending(team, monday)
        svc.approve(req, team["boss"])
        with pytest.raises(svc.LeaveError, match="already been"):
            svc.reject(req, team["boss"])

    def test_approval_queue_is_scoped(self, team, monday) -> None:
        self._pending(team, monday, who="alice")
        self._pending(team, monday, who="carol")

        boss_queue = {r.employee.username for r in svc.pending_for_approver(team["boss"])}
        assert boss_queue == {"alice"}

        hr_queue = {r.employee.username for r in svc.pending_for_approver(team["hr"])}
        assert hr_queue == {"alice", "carol"}

        assert svc.pending_for_approver(team["bob"]) == []


class TestAttendanceWriteBack:
    def test_approval_marks_the_days_as_leave(self, team, monday) -> None:
        req = svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=2),
        )
        svc.approve(req, team["boss"])

        logs = db.session.scalars(
            select(AttendanceLog).where(AttendanceLog.employee_id == team["alice"].id)
        ).all()
        assert len(logs) == 3
        assert all(log.status is AttendanceStatus.LEAVE for log in logs)

    def test_rejection_writes_nothing(self, team, monday) -> None:
        req = svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=2),
        )
        svc.reject(req, team["boss"])
        assert db.session.scalars(select(AttendanceLog)).all() == []

    def test_existing_attendance_is_overwritten_not_duplicated(self, team, monday, db) -> None:
        """The unique constraint would reject a duplicate, so this must update."""
        db.session.add(
            AttendanceLog(
                employee_id=team["alice"].id,
                work_date=monday,
                status=AttendanceStatus.PRESENT,
                clock_in=time(9, 0),
                clock_out=time(17, 0),
            )
        )
        db.session.commit()

        req = svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=2),
        )
        svc.approve(req, team["boss"])

        logs = db.session.scalars(
            select(AttendanceLog).where(AttendanceLog.employee_id == team["alice"].id)
        ).all()
        assert len(logs) == 3, "should have updated the existing row, not inserted a second"

        replaced = next(log for log in logs if log.work_date == monday)
        assert replaced.status is AttendanceStatus.LEAVE
        assert replaced.clock_in is None and replaced.clock_out is None
        assert "09:00" in (replaced.notes or ""), "original times should be preserved in the note"

    def test_cancelling_approved_leave_clears_the_days(self, team, monday) -> None:
        req = svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=2),
        )
        svc.approve(req, team["boss"])
        svc.cancel(req, team["alice"])

        assert db.session.scalars(select(AttendanceLog)).all() == []
        assert svc.balance(team["alice"]).taken == 0.0

    def test_cancelling_keeps_real_attendance(self, team, monday, db) -> None:
        """If someone actually worked a day since, cancelling must not delete it."""
        req = svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=2),
        )
        svc.approve(req, team["boss"])

        worked = db.session.scalar(select(AttendanceLog).where(AttendanceLog.work_date == monday))
        worked.status = AttendanceStatus.PRESENT
        worked.clock_in, worked.clock_out = time(9, 0), time(17, 0)
        db.session.commit()

        svc.cancel(req, team["alice"])

        remaining = db.session.scalars(select(AttendanceLog)).all()
        assert len(remaining) == 1
        assert remaining[0].status is AttendanceStatus.PRESENT

    def test_write_back_skips_public_holidays(self, team) -> None:
        """Christmas inside a booked week costs neither a day nor a row."""
        year = date.today().year + 1
        start, end = date(year, 12, 22), date(year, 12, 29)
        expected = chargeable_days(start, end)

        req = svc.submit(team["alice"], leave_type=LeaveType.ANNUAL, start=start, end=end)
        svc.approve(req, team["boss"])

        logs = db.session.scalars(select(AttendanceLog)).all()
        assert len(logs) == len(expected)
        assert date(year, 12, 25) not in {log.work_date for log in logs}


class TestCancel:
    def test_only_the_requester_may_cancel(self, team, monday) -> None:
        req = svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=2),
        )
        with pytest.raises(svc.LeaveError, match="Only the person"):
            svc.cancel(req, team["boss"])

    def test_cannot_cancel_a_rejected_request(self, team, monday) -> None:
        req = svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=2),
        )
        svc.reject(req, team["boss"])
        with pytest.raises(svc.LeaveError, match="already"):
            svc.cancel(req, team["alice"])

    def test_cancelled_request_keeps_its_approver(self, team, monday) -> None:
        """The decision columns are all-or-nothing, and the history matters."""
        req = svc.submit(
            team["alice"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=2),
        )
        svc.approve(req, team["boss"])
        svc.cancel(req, team["alice"])
        assert req.decided_by_id == team["boss"].id
        assert req.decided_at is not None


class TestRoutes:
    def test_approvals_page_needs_manager_role(self, client, login, team) -> None:
        login("alice")
        assert client.get("/leave/approvals").status_code == 403

    def test_manager_can_open_approvals(self, client, login, team) -> None:
        login("boss")
        assert client.get("/leave/approvals").status_code == 200

    def test_cannot_decide_someone_elses_report(self, client, login, team, monday) -> None:
        req = svc.submit(
            team["carol"],
            leave_type=LeaveType.ANNUAL,
            start=monday,
            end=monday + timedelta(days=2),
        )
        login("boss")
        res = client.post(f"/leave/{req.id}/approve")
        assert res.status_code == 404

        db.session.refresh(req)
        assert req.status is LeaveStatus.PENDING

    def test_submit_through_the_form(self, client, login, team, monday) -> None:
        login("alice")
        res = client.post(
            "/leave/new",
            data={
                "leave_type": "annual",
                "start": monday.isoformat(),
                "end": (monday + timedelta(days=2)).isoformat(),
                "reason": "Family visit",
            },
            follow_redirects=True,
        )
        assert res.status_code == 200
        assert db.session.scalar(select(LeaveRequest)) is not None

    def test_form_reports_rule_violations(self, client, login, team) -> None:
        login("alice")
        past = (date.today() - timedelta(days=5)).isoformat()
        res = client.post(
            "/leave/new",
            data={"leave_type": "annual", "start": past, "end": past},
            follow_redirects=True,
        )
        assert b"past" in res.data
        assert db.session.scalar(select(LeaveRequest)) is None

    def test_bad_leave_type_is_rejected(self, client, login, team, monday) -> None:
        login("alice")
        res = client.post(
            "/leave/new",
            data={
                "leave_type": "sabbatical",
                "start": monday.isoformat(),
                "end": monday.isoformat(),
            },
        )
        assert res.status_code == 400
