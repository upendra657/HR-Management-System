"""Custom `flask` CLI commands."""

from __future__ import annotations

import click
from flask import Flask
from flask.cli import with_appcontext
from sqlalchemy import select

from app.extensions import db


def register_commands(app: Flask) -> None:
    app.cli.add_command(seed)
    app.cli.add_command(create_admin)


@click.command("seed")
@click.option("--employees", default=250, show_default=True, help="How many employees to create.")
@click.option("--months", default=18, show_default=True, help="Months of history to generate.")
@click.option("--reset", is_flag=True, help="Delete existing data first.")
@with_appcontext
def seed(employees: int, months: int, reset: bool) -> None:
    """Populate the database with realistic synthetic data."""
    from scripts.seed import run_seed

    run_seed(employees=employees, months=months, reset=reset)


@click.command("create-admin")
@click.option("--username", prompt=True)
@click.option("--email", prompt=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
@with_appcontext
def create_admin(username: str, email: str, password: str) -> None:
    """Create an HR admin account."""
    from datetime import date

    from app.models import Department, Employee, Role

    if db.session.scalar(select(Employee).where(Employee.username == username)):
        raise click.ClickException(f"Username {username!r} already exists.")

    dept = db.session.scalar(select(Department).where(Department.code == "HR"))
    if dept is None:
        dept = Department(name="Human Resources", code="HR")
        db.session.add(dept)
        db.session.flush()

    count = db.session.scalar(select(db.func.count()).select_from(Employee)) or 0
    admin = Employee(
        employee_code=f"EMP-{count + 1:04d}",
        full_name=username.replace(".", " ").title(),
        username=username,
        email=email,
        role=Role.HR_ADMIN,
        job_title="HR Administrator",
        join_date=date.today(),
        department=dept,
    )
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()

    click.echo(f"Created HR admin {username!r} ({admin.employee_code}).")
