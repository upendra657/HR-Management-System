"""Authentication: sign in, sign out, password change."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select

from app.extensions import db
from app.models import Employee

bp = Blueprint("auth", __name__, template_folder="../templates/auth")


def _is_safe_next(target: str | None) -> bool:
    """Only allow relative paths, otherwise ?next= is an open redirect."""
    if not target:
        return False
    parsed = urlparse(target)
    return not parsed.netloc and not parsed.scheme and target.startswith("/")


@bp.route("/login", methods=["GET", "POST"])
def login() -> Any:
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        employee = db.session.scalar(select(Employee).where(Employee.username == username))

        # Same message either way, so this can't be used to find valid usernames.
        if employee is None or not employee.check_password(password):
            flash("Incorrect username or password.", "danger")
            return render_template("auth/login.html", username=username), 401

        if not employee.is_active:
            flash("This account is no longer active.", "warning")
            return render_template("auth/login.html", username=username), 403

        login_user(employee, remember=bool(request.form.get("remember")))

        nxt = request.args.get("next")
        return redirect(nxt if _is_safe_next(nxt) else url_for("main.dashboard"))

    return render_template("auth/login.html")


@bp.route("/logout")
@login_required
def logout() -> Any:
    logout_user()
    flash("Signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password() -> Any:
    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""

        if not current_user.check_password(current):
            flash("Your current password is incorrect.", "danger")
        elif len(new) < 10:
            flash("Choose a password of at least 10 characters.", "danger")
        elif new != confirm:
            flash("The new passwords do not match.", "danger")
        else:
            current_user.set_password(new)
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for("main.dashboard"))

    return render_template("auth/change_password.html")
