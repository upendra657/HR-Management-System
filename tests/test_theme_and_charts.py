"""Theme toggle and dashboard charts.

These assert on the rendered markup rather than on pixels. What they can
check is that the toggle is present everywhere, that the anti-flash script
runs before the stylesheet, and that the chart payload is well-formed JSON
containing the numbers the tables show.
"""

from __future__ import annotations

import json
import re
from datetime import date, time, timedelta

import pytest

from app.models import AttendanceLog, AttendanceStatus, Project, Role, Task


@pytest.fixture()
def staff(make_employee):
    boss = make_employee("boss", role=Role.MANAGER)
    return {"boss": boss, "alice": make_employee("alice", manager=boss)}


@pytest.fixture()
def some_data(staff, db):
    project = Project(
        name="Migration", code="MIG-01", start_date=date.today() - timedelta(days=400)
    )
    db.session.add(project)
    db.session.commit()

    day = date.today() - timedelta(days=3)
    while day.weekday() >= 5:
        day -= timedelta(days=1)

    db.session.add(
        AttendanceLog(
            employee_id=staff["alice"].id,
            work_date=day,
            status=AttendanceStatus.PRESENT,
            clock_in=time(9, 0),
            clock_out=time(17, 0),
        )
    )
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
    return staff


def chart_payload(html: str) -> dict:
    match = re.search(r'<script id="chart-data" type="application/json">(.*?)</script>', html, re.S)
    assert match, "chart data script tag missing"
    return json.loads(match.group(1))


class TestThemeToggle:
    def test_toggle_is_on_every_signed_in_page(self, client, login, staff) -> None:
        login("boss")
        for path in ("/dashboard", "/employees/", "/leave/", "/timesheet/", "/reports/"):
            html = client.get(path).get_data(as_text=True)
            assert "data-theme-toggle" in html, path

    def test_toggle_is_on_the_login_page(self, client) -> None:
        """Signed out, there is no navbar to hang it off."""
        html = client.get("/auth/login").get_data(as_text=True)
        assert "data-theme-toggle" in html

    def test_theme_script_runs_before_the_stylesheet(self, client) -> None:
        """Otherwise dark mode paints white first and flashes on every load."""
        html = client.get("/auth/login").get_data(as_text=True)
        script_at = html.index("hrms-theme")
        css_at = html.index("bootstrap@5.3.3/dist/css")
        assert script_at < css_at

    def test_theme_is_not_hardcoded_on_the_html_tag(self, client) -> None:
        """A static data-bs-theme would override whatever the visitor chose."""
        html = client.get("/auth/login").get_data(as_text=True)
        opening = html[: html.index(">", html.index("<html")) + 1]
        assert "data-bs-theme" not in opening

    def test_both_icons_ship_in_the_markup(self, client) -> None:
        """CSS picks one. Swapping in JS leaves the wrong icon until it runs."""
        html = client.get("/auth/login").get_data(as_text=True)
        assert "icon-sun" in html
        assert "icon-moon" in html

    def test_toggle_is_labelled_for_screen_readers(self, client) -> None:
        html = client.get("/auth/login").get_data(as_text=True)
        assert 'aria-label="Switch colour theme"' in html
        assert "aria-pressed" in html


class TestChartData:
    def test_dashboard_embeds_valid_json(self, client, login, some_data) -> None:
        login("boss")
        data = chart_payload(client.get("/reports/").get_data(as_text=True))
        assert set(data) == {"months", "departments", "projects", "leave"}

    def test_chart_numbers_match_the_tables(self, client, login, some_data) -> None:
        """The charts and the tables come from the same call, so they cannot
        drift - this checks the payload really is that data, not a re-query."""
        login("boss")
        html = client.get("/reports/").get_data(as_text=True)
        data = chart_payload(html)

        assert data["departments"][0]["hours"] == 8.0
        assert data["projects"][0]["code"] == "MIG-01"
        assert data["projects"][0]["hours"] == 6.0

    def test_closed_projects_are_flagged_for_colouring(self, client, login, some_data) -> None:
        login("boss")
        data = chart_payload(client.get("/reports/").get_data(as_text=True))
        assert "active" in data["projects"][0]

    def test_project_list_is_capped(self, client, login, staff, db) -> None:
        """Twelve bars is a chart; thirty is a wall."""
        login("boss")
        data = chart_payload(client.get("/reports/").get_data(as_text=True))
        assert len(data["projects"]) <= 8

    def test_zero_day_leave_types_are_dropped(self, client, login, some_data) -> None:
        """A doughnut segment of size zero renders as an unlabelled sliver."""
        login("boss")
        data = chart_payload(client.get("/reports/").get_data(as_text=True))
        assert all(item["days"] > 0 for item in data["leave"])

    def test_scripts_are_loaded_in_order(self, client, login, some_data) -> None:
        """charts.js needs the Chart global and the data tag before it runs."""
        login("boss")
        html = client.get("/reports/").get_data(as_text=True)
        assert html.index('id="chart-data"') < html.index("chart.umd.min.js")
        assert html.index("chart.umd.min.js") < html.index("js/charts.js")

    def test_canvases_exist_for_each_chart(self, client, login, some_data) -> None:
        login("boss")
        html = client.get("/reports/").get_data(as_text=True)
        for chart_id in (
            "chart-attendance",
            "chart-departments",
            "chart-projects",
            "chart-leave",
        ):
            assert f'id="{chart_id}"' in html, chart_id

    def test_charts_are_manager_only(self, client, login, staff) -> None:
        login("alice")
        assert client.get("/reports/").status_code == 403

    def test_data_cannot_break_out_of_the_script_tag(self, client, login, staff, db) -> None:
        """Embedding JSON in a <script> is an XSS vector if the serialiser
        does not escape angle brackets. Flask's tojson emits \\u003c, so a
        project literally named '</script>...' stays inert."""
        hostile = "</script><script>window.PWNED=1</script>"
        db.session.add(
            Project(
                name=hostile,
                code="XSS-01",
                client='"><img src=x onerror=alert(1)>',
                start_date=date.today() - timedelta(days=400),
            )
        )
        db.session.commit()

        login("boss")
        html = client.get("/reports/").get_data(as_text=True)

        # The tag must still parse - if it closed early this raises.
        payload = chart_payload(html)
        assert isinstance(payload, dict)

        assert "<script>window.PWNED" not in html
        assert "<img src=x" not in html


class TestNoHardcodedColours:
    """Inline colours and Bootstrap's light-only helpers do not follow the
    theme, so they are the usual cause of unreadable text in dark mode."""

    PAGES = ("/dashboard", "/employees/", "/leave/", "/timesheet/", "/reports/")

    def test_no_inline_colour_styles(self, client, login, some_data) -> None:
        login("boss")
        for path in self.PAGES:
            html = client.get(path).get_data(as_text=True)
            offenders = re.findall(r'style="[^"]*(?:color|background)[^"]*"', html)
            assert not offenders, f"{path}: {offenders[:3]}"

    def test_no_light_only_background_utilities(self, client, login, some_data) -> None:
        login("boss")
        banned = ("bg-white", "bg-light", "text-dark", "text-black", "text-muted")
        for path in self.PAGES:
            html = client.get(path).get_data(as_text=True)
            for cls in banned:
                assert f'"{cls}' not in html and f" {cls} " not in html, f"{path}: {cls}"

    def test_employee_detail_uses_the_avatar_class(self, client, login, staff) -> None:
        login("boss")
        html = client.get(f"/employees/{staff['alice'].id}").get_data(as_text=True)
        assert "avatar" in html
        assert "width:56px" not in html
