"""Demo mode: read-only enforcement and the Render URL quirk."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, ClassVar

import pytest
from sqlalchemy import func, select

from app import create_app
from app.config import TestingConfig, _normalise_db_url
from app.extensions import db as _db
from app.models import AttendanceLog, Employee, LeaveRequest, Role


class TestDatabaseUrlNormalisation:
    """Render hands out postgres:// which SQLAlchemy 2.0 refuses outright."""

    def test_rewrites_the_render_scheme(self) -> None:
        assert (
            _normalise_db_url("postgres://u:p@host:5432/db")
            == "postgresql+psycopg://u:p@host:5432/db"
        )

    def test_adds_the_psycopg_driver(self) -> None:
        assert _normalise_db_url("postgresql://u:p@host/db") == "postgresql+psycopg://u:p@host/db"

    def test_leaves_an_explicit_driver_alone(self) -> None:
        url = "postgresql+psycopg://u:p@host/db"
        assert _normalise_db_url(url) == url

    def test_leaves_sqlite_alone(self) -> None:
        assert _normalise_db_url("sqlite:///x.db") == "sqlite:///x.db"


class _DemoConfig(TestingConfig):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS: ClassVar[dict[str, Any]] = {}
    DEMO_MODE = True


@pytest.fixture()
def demo_app():
    application = create_app(_DemoConfig)
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def demo_client(demo_app):
    return demo_app.test_client()


@pytest.fixture()
def demo_people(demo_app):
    from app.models import Department

    dept = Department(name="Engineering", code="ENG")
    _db.session.add(dept)
    _db.session.commit()

    def make(username: str, role: Role = Role.EMPLOYEE, manager=None) -> Employee:
        n = _db.session.query(Employee).count()
        emp = Employee(
            employee_code=f"EMP-{n + 1:04d}",
            full_name=username.title(),
            username=username,
            email=f"{username}@example.com",
            role=role,
            job_title="Engineer",
            join_date=date.today() - timedelta(days=400),
            department=dept,
            manager=manager,
        )
        emp.set_password("correct-horse-battery")
        _db.session.add(emp)
        _db.session.commit()
        return emp

    boss = make("boss", Role.MANAGER)
    return {"boss": boss, "alice": make("alice", manager=boss)}


def _login(client, username: str) -> None:
    client.post("/auth/login", data={"username": username, "password": "correct-horse-battery"})


class TestReadOnlyDemo:
    def test_login_still_works(self, demo_client, demo_people) -> None:
        res = demo_client.post(
            "/auth/login",
            data={"username": "alice", "password": "correct-horse-battery"},
        )
        assert res.status_code == 302
        assert res.headers["Location"].endswith("/dashboard")

    def test_logout_still_works(self, demo_client, demo_people) -> None:
        _login(demo_client, "alice")
        assert demo_client.get("/auth/logout").status_code == 302

    def test_browsing_is_unaffected(self, demo_client, demo_people) -> None:
        _login(demo_client, "boss")
        for path in ("/dashboard", "/employees/", "/leave/", "/timesheet/", "/reports/"):
            assert demo_client.get(path).status_code == 200, path

    def test_clock_in_is_refused(self, demo_client, demo_people) -> None:
        _login(demo_client, "alice")
        res = demo_client.post("/timesheet/clock-in", follow_redirects=True)
        assert b"read-only demo" in res.data.lower()
        assert _db.session.scalar(select(func.count()).select_from(AttendanceLog)) == 0

    def test_leave_request_is_refused(self, demo_client, demo_people) -> None:
        _login(demo_client, "alice")
        monday = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)
        res = demo_client.post(
            "/leave/new",
            data={
                "leave_type": "annual",
                "start": monday.isoformat(),
                "end": monday.isoformat(),
            },
            follow_redirects=True,
        )
        assert b"read-only demo" in res.data.lower()
        assert _db.session.scalar(select(func.count()).select_from(LeaveRequest)) == 0

    def test_password_change_is_refused(self, demo_client, demo_people) -> None:
        """Otherwise a visitor locks everyone else out of the demo."""
        _login(demo_client, "alice")
        demo_client.post(
            "/auth/password",
            data={
                "current_password": "correct-horse-battery",
                "new_password": "hijacked-the-demo",
                "confirm_password": "hijacked-the-demo",
            },
            follow_redirects=True,
        )
        alice = _db.session.scalar(select(Employee).where(Employee.username == "alice"))
        assert alice.check_password("correct-horse-battery")

    def test_exports_still_work(self, demo_client, demo_people) -> None:
        """They are GETs, and they are worth showing."""
        _login(demo_client, "boss")
        assert demo_client.get("/reports/export/timesheet.csv").status_code == 200


class TestNormalModeStillWrites:
    def test_writes_are_allowed_without_demo_mode(self, client, login, employee) -> None:
        login("alice")
        client.post("/timesheet/clock-in", follow_redirects=True)
        assert _db.session.scalar(select(func.count()).select_from(AttendanceLog)) == 1
