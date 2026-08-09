"""Read-only protection for the public demo.

The demo exists so somebody can look at the data and the reports. It does
not need to let strangers approve leave or delete timesheets, and leaving
that open means the demo is wrecked within a week.

So when DEMO_MODE is on, anything that would write is refused. Signing in
and out still works, because otherwise you cannot see the role-based views
at all - and those are half the point.
"""

from __future__ import annotations

from typing import Any

from flask import Flask, flash, redirect, request, url_for

# Endpoints that may still POST in demo mode.
ALLOWED = {"auth.login", "auth.logout"}

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DEMO_ACCOUNTS = [
    ("demo_hr", "HR Admin", "hr_admin"),
    ("demo_manager", "Manager", "manager"),
    ("demo_employee", "Employee", "employee"),
]
DEMO_PASSWORD = "demo12345"


def init_demo(app: Flask) -> None:
    if not app.config.get("DEMO_MODE"):
        return

    @app.before_request
    def block_writes() -> Any:
        if request.method in SAFE_METHODS:
            return None
        if request.endpoint in ALLOWED:
            return None

        flash(
            "This is a read-only demo, so changes are not saved. "
            "The code and full behaviour are on GitHub.",
            "info",
        )
        # Send them back where they came from rather than to a dead end.
        target = request.referrer
        if target and target.startswith(request.host_url):
            return redirect(target)
        return redirect(url_for("main.dashboard"))
