"""Shared pytest fixtures.

Tests run against SQLite in memory by default so they need no services, and
against PostgreSQL in CI (TEST_DATABASE_URL) so that constraints and enum
types are exercised the way production will run them.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date
from typing import Any, ClassVar

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.config import TestingConfig
from app.extensions import db as _db
from app.models import Department, Employee, Project, Role

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")


class _Config(TestingConfig):
    SQLALCHEMY_DATABASE_URI = TEST_DB_URL
    # SQLite has no connection pool worth configuring.
    SQLALCHEMY_ENGINE_OPTIONS: ClassVar[dict[str, Any]] = {}


@pytest.fixture()
def app() -> Iterator[Flask]:
    application = create_app(_Config)
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def db(app: Flask):
    """The app fixture is required: it establishes the application context."""
    return _db


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    return app.test_client()


# --------------------------------------------------------------------------
# Domain fixtures
# --------------------------------------------------------------------------
@pytest.fixture()
def department(db) -> Department:
    dept = Department(name="Engineering", code="ENG")
    db.session.add(dept)
    db.session.commit()
    return dept


@pytest.fixture()
def project(db) -> Project:
    proj = Project(name="Migration Programme", code="MIG-01", start_date=date(2025, 1, 1))
    db.session.add(proj)
    db.session.commit()
    return proj


def _make_employee(
    db,
    department: Department,
    *,
    username: str,
    role: Role = Role.EMPLOYEE,
    manager: Employee | None = None,
    password: str = "correct-horse-battery",
) -> Employee:
    count = db.session.query(Employee).count()
    emp = Employee(
        employee_code=f"EMP-{count + 1:04d}",
        full_name=username.replace("_", " ").title(),
        username=username,
        email=f"{username}@example.com",
        role=role,
        job_title="Engineer",
        join_date=date(2024, 1, 15),
        department=department,
        manager=manager,
    )
    emp.set_password(password)
    db.session.add(emp)
    db.session.commit()
    return emp


@pytest.fixture()
def make_employee(db, department):
    """Factory so tests can build exactly the org shape they need."""

    def _factory(username: str, **kwargs) -> Employee:
        return _make_employee(db, kwargs.pop("department", department), username=username, **kwargs)

    return _factory


@pytest.fixture()
def employee(make_employee) -> Employee:
    return make_employee("alice")


@pytest.fixture()
def hr_admin(make_employee) -> Employee:
    return make_employee("hr_admin", role=Role.HR_ADMIN)


@pytest.fixture()
def login(client):
    """Sign a user in through the real login form, not a session shortcut."""

    def _login(username: str, password: str = "correct-horse-battery"):
        return client.post(
            "/auth/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )

    return _login
