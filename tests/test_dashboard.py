"""The landing page.

It is the only page that mixes personal data with team data, so most of what
is worth testing here is scoping: an employee must not learn who else is off,
and a manager must not see another manager's approval queue.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta

import pytest

from app.models import AttendanceLog, AttendanceStatus, LeaveRequest, LeaveStatus, LeaveType, Role
from app.services import dashboard as dash
from tests.conftest import count_queries

# A fixed Wednesday, so "the last 14 working days" is the same window every
# run and the assertions below do not drift with the calendar.
WEDNESDAY = date(2026, 6, 17)


@pytest.fixture()
def org(make_employee):
    boss = make_employee("boss", role=Role.MANAGER)
    other_boss = make_employee("other_boss", role=Role.MANAGER)
    return {
        "boss": boss,
        "other_boss": other_boss,
        "alice": make_employee("alice", manager=boss),
        "bob": make_employee("bob", manager=boss),
        "carol": make_employee("carol", manager=other_boss),
        "hr": make_employee("hr", role=Role.HR_ADMIN),
    }


def add_leave(db, employee, *, start, end, status, kind=LeaveType.ANNUAL, days=None):
    request = LeaveRequest(
        employee_id=employee.id,
        leave_type=kind,
        start_date=start,
        end_date=end,
        days=days if days is not None else (end - start).days + 1,
        status=status,
    )
    if status in (LeaveStatus.APPROVED, LeaveStatus.REJECTED):
        # ck_leave_decision_complete: both halves of the decision or neither.
        request.decided_by_id = employee.manager_id or employee.id
        request.decided_at = datetime(2026, 6, 1, 9, 0)
    db.session.add(request)
    db.session.commit()
    return request


def payload(html: str) -> dict:
    match = re.search(r'<script id="chart-data" type="application/json">(.*?)</script>', html, re.S)
    assert match, "chart data script tag missing"
    return json.loads(match.group(1))


class TestItRenders:
    def test_every_role_gets_a_page(self, client, login, org) -> None:
        for username in ("alice", "boss", "hr"):
            login(username)
            assert client.get("/dashboard").status_code == 200, username

    def test_signed_out_is_redirected(self, client) -> None:
        res = client.get("/dashboard")
        assert res.status_code == 302
        assert "/auth/login" in res.headers["Location"]

    def test_root_sends_you_here(self, client, login, org) -> None:
        login("alice")
        res = client.get("/")
        assert res.headers["Location"].endswith("/dashboard")

    def test_the_placeholder_is_gone(self, client, login, org) -> None:
        """It used to say analytics were coming in phase 3."""
        login("alice")
        html = client.get("/dashboard").get_data(as_text=True)
        assert "phase 3" not in html.lower()

    def test_a_titled_name_is_not_truncated(self, client, login, make_employee) -> None:
        """Greeting on the first word turned 'Dr Julie Hudson' into 'Dr'."""
        titled = make_employee("julie")
        titled.full_name = "Dr Julie Hudson"
        from app.extensions import db as _db

        _db.session.commit()

        login("julie")
        html = client.get("/dashboard").get_data(as_text=True)
        assert "Dr Julie Hudson" in html


class TestPersonalContent:
    def test_shows_the_clock_in_button_when_not_clocked_in(self, client, login, org) -> None:
        login("alice")
        html = client.get("/dashboard").get_data(as_text=True)
        assert "Clock in" in html
        assert "Not clocked in" in html

    def test_shows_clock_out_while_a_shift_is_open(self, client, login, org, db) -> None:
        db.session.add(
            AttendanceLog(
                employee_id=org["alice"].id,
                work_date=date.today(),
                status=AttendanceStatus.PRESENT,
                clock_in=time(9, 0),
            )
        )
        db.session.commit()

        login("alice")
        html = client.get("/dashboard").get_data(as_text=True)
        assert "still clocked in" in html
        assert "Clock out" in html

    def test_leave_balance_counts_pending_against_you(self, client, login, org, db) -> None:
        """The same rule the leave page uses - two pending requests must not
        both look affordable."""
        today = date.today()
        add_leave(
            db,
            org["alice"],
            start=today + timedelta(days=30),
            end=today + timedelta(days=34),
            status=LeaveStatus.PENDING,
            days=5,
        )

        login("alice")
        html = client.get("/dashboard").get_data(as_text=True)
        board = dash.build(org["alice"])
        assert board.balance.pending == 5
        assert str(board.balance.remaining) in html

    def test_recent_requests_are_listed(self, client, login, org, db) -> None:
        today = date.today()
        add_leave(
            db,
            org["alice"],
            start=today + timedelta(days=10),
            end=today + timedelta(days=11),
            status=LeaveStatus.PENDING,
            kind=LeaveType.SICK,
        )
        login("alice")
        html = client.get("/dashboard").get_data(as_text=True)
        assert "Sick" in html
        assert "Pending" in html


class TestScoping:
    def test_an_employee_sees_no_approval_queue(self, client, login, org, db) -> None:
        today = date.today()
        add_leave(
            db,
            org["bob"],
            start=today + timedelta(days=5),
            end=today + timedelta(days=6),
            status=LeaveStatus.PENDING,
        )
        login("alice")
        html = client.get("/dashboard").get_data(as_text=True)
        assert "Awaiting your approval" not in html
        assert "Bob" not in html

    def test_a_manager_sees_only_their_own_reports(self, client, login, org, db) -> None:
        """Carol reports to the other manager, so she must not appear."""
        today = date.today()
        for who in ("bob", "carol"):
            add_leave(
                db,
                org[who],
                start=today + timedelta(days=5),
                end=today + timedelta(days=6),
                status=LeaveStatus.PENDING,
            )

        login("boss")
        html = client.get("/dashboard").get_data(as_text=True)
        assert "Bob" in html
        assert "Carol" not in html

    def test_hr_sees_every_pending_request(self, client, login, org, db) -> None:
        today = date.today()
        for who in ("bob", "carol"):
            add_leave(
                db,
                org[who],
                start=today + timedelta(days=5),
                end=today + timedelta(days=6),
                status=LeaveStatus.PENDING,
            )

        login("hr")
        html = client.get("/dashboard").get_data(as_text=True)
        assert "Bob" in html
        assert "Carol" in html

    def test_hr_does_not_see_their_own_request_in_the_queue(self, client, login, org, db) -> None:
        """Nobody decides their own, so it would be an undismissable item."""
        today = date.today()
        add_leave(
            db,
            org["hr"],
            start=today + timedelta(days=5),
            end=today + timedelta(days=6),
            status=LeaveStatus.PENDING,
        )
        queue, total = dash._queue(org["hr"])
        assert total == 0
        assert queue == []

    def test_an_employee_only_sees_their_own_leave_in_the_away_list(self, client, org, db) -> None:
        today = date.today()
        add_leave(db, org["bob"], start=today, end=today, status=LeaveStatus.APPROVED, days=1)
        _, total = dash._away(org["alice"], today)
        assert total == 0

        add_leave(db, org["alice"], start=today, end=today, status=LeaveStatus.APPROVED, days=1)
        away, total = dash._away(org["alice"], today)
        assert total == 1
        assert away[0].employee_id == org["alice"].id


class TestPreviewsAreCapped:
    def test_the_queue_is_capped_but_the_total_is_not(self, client, login, org, db) -> None:
        """HR's queue is company-wide; the page must not grow with the company."""
        today = date.today()
        for i in range(dash.QUEUE_PREVIEW + 4):
            add_leave(
                db,
                org["bob"],
                start=today + timedelta(days=10 + i * 3),
                end=today + timedelta(days=11 + i * 3),
                status=LeaveStatus.PENDING,
            )

        queue, total = dash._queue(org["hr"])
        assert total == dash.QUEUE_PREVIEW + 4
        assert len(queue) == dash.QUEUE_PREVIEW

    def test_the_away_list_is_capped(self, client, org, db) -> None:
        today = date.today()
        for who in org:
            add_leave(db, org[who], start=today, end=today, status=LeaveStatus.APPROVED, days=1)

        away, total = dash._away(org["hr"], today)
        assert total == len(org)
        assert len(away) == dash.AWAY_PREVIEW


class TestHoursChart:
    def test_the_window_is_working_days_only(self) -> None:
        days = dash._recent_working_days(WEDNESDAY)
        assert len(days) == dash.CHART_DAYS
        assert days[-1] == WEDNESDAY
        assert all(d.weekday() < 5 for d in days)

    def test_the_window_is_contiguous_and_ends_today(self) -> None:
        days = dash._recent_working_days(WEDNESDAY)
        assert days == sorted(days)
        assert len(set(days)) == len(days)

    def test_days_with_no_record_are_zero_not_missing(self, org, db) -> None:
        """A gap in the bars would misread as a chart bug rather than a day off."""
        board = dash.build(org["alice"], today=WEDNESDAY)
        assert len(board.recent) == dash.CHART_DAYS
        assert all(p.hours == 0.0 for p in board.recent)

    def test_an_open_shift_is_flagged_rather_than_shown_as_zero_hours(self, org, db) -> None:
        db.session.add(
            AttendanceLog(
                employee_id=org["alice"].id,
                work_date=WEDNESDAY,
                status=AttendanceStatus.PRESENT,
                clock_in=time(9, 0),
            )
        )
        db.session.commit()

        board = dash.build(org["alice"], today=WEDNESDAY)
        last = board.recent[-1]
        assert last.day == WEDNESDAY
        assert last.hours == 0.0
        assert last.incomplete is True

    def test_worked_hours_reach_the_payload(self, client, login, org, db) -> None:
        db.session.add(
            AttendanceLog(
                employee_id=org["alice"].id,
                work_date=date.today(),
                status=AttendanceStatus.PRESENT,
                clock_in=time(9, 0),
                clock_out=time(17, 30),
            )
        )
        db.session.commit()

        login("alice")
        data = payload(client.get("/dashboard").get_data(as_text=True))
        assert "recent_days" in data
        assert data["recent_days"][-1]["hours"] == 8.5

    def test_the_payload_carries_only_this_pages_keys(self, client, login, org) -> None:
        """charts.js is shared with the reports page and guards on these."""
        login("alice")
        data = payload(client.get("/dashboard").get_data(as_text=True))
        assert set(data) == {"recent_days"}

    def test_the_canvas_is_present(self, client, login, org) -> None:
        login("alice")
        html = client.get("/dashboard").get_data(as_text=True)
        assert 'id="chart-my-hours"' in html


class TestQueryCost:
    """The landing page is the most-loaded page in the app, and it renders
    employee names off leave requests - the classic place to N+1."""

    def test_the_cost_does_not_grow_with_the_number_of_requests(self, org, db) -> None:
        today = date.today()
        for i in range(3):
            add_leave(
                db,
                org["bob"],
                start=today + timedelta(days=10 + i * 3),
                end=today + timedelta(days=11 + i * 3),
                status=LeaveStatus.PENDING,
            )
        with count_queries() as few:
            board = dash.build(org["hr"])
            [r.employee.full_name for r in board.queue]

        for i in range(3, 12):
            add_leave(
                db,
                org["bob"],
                start=today + timedelta(days=10 + i * 3),
                end=today + timedelta(days=11 + i * 3),
                status=LeaveStatus.PENDING,
            )
        with count_queries() as many:
            board = dash.build(org["hr"])
            [r.employee.full_name for r in board.queue]

        assert many["n"] == few["n"], "the queue is loading employees one at a time"

    def test_the_whole_page_stays_within_budget(self, client, login, org, db) -> None:
        """Not a micro-optimisation - a bound, so an added panel is a visible
        decision rather than a silent regression."""
        today = date.today()
        add_leave(
            db,
            org["bob"],
            start=today + timedelta(days=5),
            end=today + timedelta(days=6),
            status=LeaveStatus.PENDING,
        )
        login("hr")
        with count_queries() as c:
            assert client.get("/dashboard").status_code == 200
        assert c["n"] <= 20, f"{c['n']} queries to render one page"


class TestDarkModeSafety:
    """Same sweep the other pages get - light-only utilities are the usual
    cause of unreadable text once the theme flips."""

    def test_no_inline_colour_styles(self, client, login, org) -> None:
        login("hr")
        html = client.get("/dashboard").get_data(as_text=True)
        assert not re.findall(r'style="[^"]*(?:color|background)[^"]*"', html)

    def test_no_light_only_utilities(self, client, login, org) -> None:
        login("hr")
        html = client.get("/dashboard").get_data(as_text=True)
        for cls in ("bg-white", "bg-light", "text-dark", "text-black", "text-muted"):
            assert f'"{cls}' not in html and f" {cls} " not in html, cls

    def test_the_toggle_is_here_too(self, client, login, org) -> None:
        login("alice")
        assert "data-theme-toggle" in client.get("/dashboard").get_data(as_text=True)
