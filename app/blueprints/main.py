"""Landing and dashboard routes."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required

bp = Blueprint("main", __name__)


@bp.route("/")
def index() -> Any:
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/dashboard")
@login_required
def dashboard() -> Any:
    return render_template("main/dashboard.html")


@bp.route("/healthz")
def healthz() -> Any:
    """Liveness probe for the host platform. Never requires auth."""
    return {"status": "ok"}, 200
