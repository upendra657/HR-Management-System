"""Directory: access control, filtering, paging, and query counts."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import Employee, EmployeeStatus, Role
from app.services import employees as svc


@contextmanager
def count_queries():
    """Count SQL statements issued inside the block.

    Used to prove the listing does not N+1: rendering 25 rows with a
    department and manager on each must not cost 50 extra queries.

    Expires the session first, otherwise the second measurement in a test
    looks cheaper than the first purely because the identity map is already
    warm - which is a property of the test, not of the query.
    """
    db.session.expire_all()
    counter = {"n": 0}

    def before(*_args, **_kwargs):
        counter["n"] += 1

    event.listen(db.engine, "before_cursor_execute", before)
    try:
        yield counter
    finally:
        event.remove(db.engine, "before_cursor_execute", before)


@pytest.fixture()
def org(make_employee, db):
    """A small org: HR, two managers with reports, plus an unrelated employee."""
    hr = make_employee("hr", role=Role.HR_ADMIN)
    boss_a = make_employee("boss_a", role=Role.MANAGER)
    boss_b = make_employee("boss_b", role=Role.MANAGER)
    a1 = make_employee("a_one", manager=boss_a)
    a2 = make_employee("a_two", manager=boss_a)
    b1 = make_employee("b_one", manager=boss_b)
    loner = make_employee("loner")
    return {
        "hr": hr,
        "boss_a": boss_a,
        "boss_b": boss_b,
        "a1": a1,
        "a2": a2,
        "b1": b1,
        "loner": loner,
    }


class TestVisibility:
    def test_hr_sees_everyone(self, org) -> None:
        page = svc.search(org["hr"])
        assert page.total == 7

    def test_employee_sees_only_themselves(self, org) -> None:
        page = svc.search(org["loner"])
        assert [e.username for e in page.items] == ["loner"]

    def test_manager_sees_own_reports_and_self(self, org) -> None:
        page = svc.search(org["boss_a"])
        assert sorted(e.username for e in page.items) == ["a_one", "a_two", "boss_a"]

    def test_manager_cannot_see_peers_reports(self, org) -> None:
        page = svc.search(org["boss_a"])
        assert "b_one" not in [e.username for e in page.items]

    def test_query_filter_matches_can_view(self, org, db) -> None:
        """The SQL WHERE clause and the Python rule must agree.

        Two implementations of the same rule is the sort of thing that drifts,
        so this checks every viewer against every subject.
        """
        everyone = db.session.scalars(svc.select(Employee)).all()
        for viewer in everyone:
            allowed = {e.id for e in svc.search(viewer, per_page=100).items}
            for subject in everyone:
                assert (subject.id in allowed) == viewer.can_view(subject), (
                    f"{viewer.username} -> {subject.username}"
                )


class TestDetailAccess:
    def test_can_fetch_someone_you_may_see(self, org) -> None:
        assert svc.get_visible(org["boss_a"], org["a1"].id) is not None

    def test_cannot_fetch_someone_you_may_not(self, org) -> None:
        assert svc.get_visible(org["boss_a"], org["b1"].id) is None

    def test_route_returns_404_not_403(self, client, login, org) -> None:
        """404 rather than 403 - a 403 would confirm the record exists."""
        login("boss_a")
        assert client.get(f"/employees/{org['b1'].id}").status_code == 404

    def test_route_allows_permitted_record(self, client, login, org) -> None:
        login("boss_a")
        assert client.get(f"/employees/{org['a1'].id}").status_code == 200

    def test_directory_requires_login(self, client) -> None:
        res = client.get("/employees/")
        assert res.status_code == 302
        assert "/auth/login" in res.headers["Location"]


class TestSearchAndFilter:
    def test_search_matches_name(self, org) -> None:
        page = svc.search(org["hr"], q="a one")
        assert [e.username for e in page.items] == ["a_one"]

    def test_search_is_case_insensitive(self, org) -> None:
        assert svc.search(org["hr"], q="A ONE").total == 1

    def test_search_matches_employee_code(self, org) -> None:
        code = org["loner"].employee_code
        assert svc.search(org["hr"], q=code).total == 1

    def test_search_respects_visibility(self, org) -> None:
        # b_one exists and matches, but boss_a may not see them.
        assert svc.search(org["boss_a"], q="b one").total == 0

    def test_filter_by_status(self, org, db) -> None:
        org["a1"].status = EmployeeStatus.TERMINATED
        org["a1"].exit_date = org["a1"].join_date
        db.session.commit()
        assert svc.search(org["hr"], status=EmployeeStatus.TERMINATED).total == 1
        assert svc.search(org["hr"], status=EmployeeStatus.ACTIVE).total == 6

    def test_filter_by_department(self, org, department) -> None:
        assert svc.search(org["hr"], department_id=department.id).total == 7
        assert svc.search(org["hr"], department_id=department.id + 999).total == 0

    def test_bad_status_is_rejected(self, client, login, org) -> None:
        login("hr")
        assert client.get("/employees/?status=nonsense").status_code == 400


class TestPagination:
    def test_splits_across_pages(self, org) -> None:
        first = svc.search(org["hr"], page=1, per_page=3)
        assert len(first.items) == 3
        assert first.total == 7
        assert first.pages == 3
        assert first.has_next and not first.has_prev

    def test_last_page_is_partial(self, org) -> None:
        last = svc.search(org["hr"], page=3, per_page=3)
        assert len(last.items) == 1
        assert last.has_prev and not last.has_next

    def test_pages_do_not_overlap(self, org) -> None:
        seen = []
        for n in (1, 2, 3):
            seen += [e.id for e in svc.search(org["hr"], page=n, per_page=3).items]
        assert len(seen) == len(set(seen)) == 7

    def test_page_zero_clamps_to_first(self, org) -> None:
        assert svc.search(org["hr"], page=0, per_page=3).page == 1

    def test_index_labels(self, org) -> None:
        page = svc.search(org["hr"], page=2, per_page=3)
        assert (page.first_index, page.last_index) == (4, 6)

    def test_empty_result_has_sane_indices(self, org) -> None:
        page = svc.search(org["hr"], q="nobody-by-this-name")
        assert page.total == 0
        assert page.first_index == 0
        assert page.pages == 1


class TestQueryCount:
    def test_listing_does_not_n_plus_one(self, org) -> None:
        """A bigger page must not cost more queries than a small one."""
        with count_queries() as small:
            page = svc.search(org["hr"], per_page=2)
            [(e.department.name, e.manager.full_name if e.manager else None) for e in page.items]

        with count_queries() as large:
            page = svc.search(org["hr"], per_page=100)
            [(e.department.name, e.manager.full_name if e.manager else None) for e in page.items]

        assert large["n"] == small["n"], (
            f"{small['n']} queries for 2 rows vs {large['n']} for 7 - relationships "
            "are being lazy-loaded per row"
        )

    def test_listing_query_count_is_small(self, org) -> None:
        # Reload the viewer, count, the page itself, then one apiece for the
        # eager-loaded department and manager: five, regardless of page size.
        with count_queries() as c:
            page = svc.search(org["hr"], per_page=100)
            [(e.department.name, e.manager) for e in page.items]
        assert c["n"] <= 5, f"expected at most 5 queries, got {c['n']}"

    def test_lazy_loading_really_would_n_plus_one(self, db, make_employee) -> None:
        """Control: proves the assertion above is not vacuous.

        Needs one department per employee - with everyone in a single
        department the identity map serves the lazy loads from memory and no
        N+1 appears, which is what makes this easy to get wrong in a test.
        """
        from sqlalchemy import select as sa_select

        from app.models import Department

        hr = make_employee("hr_ctl", role=Role.HR_ADMIN)
        for i in range(8):
            dept = Department(name=f"Dept {i}", code=f"D{i}")
            db.session.add(dept)
            db.session.flush()
            make_employee(f"ctl_{i}", department=dept)
        db.session.commit()

        with count_queries() as eager:
            page = svc.search(hr, per_page=100)
            [e.department.name for e in page.items]

        with count_queries() as lazy:
            rows = db.session.scalars(
                sa_select(Employee).order_by(Employee.full_name).limit(100)
            ).all()
            [e.department.name for e in rows]

        assert lazy["n"] > eager["n"], (
            f"eager={eager['n']} lazy={lazy['n']} - if these are equal the "
            "selectinload is doing nothing and could be removed unnoticed"
        )


class TestAttendanceSummary:
    def test_empty_history_is_zeroed(self, org) -> None:
        s = svc.attendance_summary(org["loner"].id)
        assert s.days_recorded == 0
        assert s.attendance_rate == 0.0
        assert s.average_hours == 0.0

    def test_rate_excludes_holidays(self) -> None:
        from app.services.employees import AttendanceSummary

        # 10 recorded days, 2 of them holidays, 8 worked -> 100%, not 80%.
        s = AttendanceSummary(
            days_recorded=10,
            days_present=8,
            days_remote=0,
            days_absent=0,
            days_leave=0,
            days_holiday=2,
            hours_total=64.0,
            missing_clock_out=0,
        )
        assert s.attendance_rate == 100.0

    def test_average_excludes_days_missing_a_clock_out(self) -> None:
        from app.services.employees import AttendanceSummary

        # 10 worked days but 2 never clocked out; 64h over the 8 complete days.
        s = AttendanceSummary(
            days_recorded=10,
            days_present=10,
            days_remote=0,
            days_absent=0,
            days_leave=0,
            days_holiday=0,
            hours_total=64.0,
            missing_clock_out=2,
        )
        assert s.average_hours == 8.0
