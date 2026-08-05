"""Authentication behaviour.

The prototype these tests replace compared passwords in plaintext SQL and
had one route with no session check at all. Each test below pins down a
behaviour that regression would silently undo.
"""

from __future__ import annotations

from app.models import Employee, Role


class TestPasswordStorage:
    def test_password_is_hashed_not_stored(self, employee: Employee) -> None:
        assert employee.password_hash != "correct-horse-battery"
        assert "correct-horse-battery" not in employee.password_hash

    def test_hash_uses_a_salted_algorithm(self, employee: Employee) -> None:
        # Werkzeug format: method$salt$hash — two different employees with
        # the same password must not share a hash.
        assert employee.password_hash.count("$") >= 2

    def test_same_password_yields_different_hashes(self, make_employee) -> None:
        a = make_employee("bob")
        b = make_employee("carol")
        assert a.password_hash != b.password_hash

    def test_check_password_round_trip(self, employee: Employee) -> None:
        assert employee.check_password("correct-horse-battery")
        assert not employee.check_password("wrong")


class TestLogin:
    def test_valid_credentials_redirect_to_dashboard(self, login, employee) -> None:
        res = login("alice")
        assert res.status_code == 302
        assert res.headers["Location"].endswith("/dashboard")

    def test_wrong_password_is_rejected(self, login, employee) -> None:
        res = login("alice", password="nope")
        assert res.status_code == 401

    def test_unknown_user_gives_same_message_as_wrong_password(self, client, employee) -> None:
        unknown = client.post("/auth/login", data={"username": "nobody", "password": "x"})
        wrong = client.post("/auth/login", data={"username": "alice", "password": "x"})
        # Identical response prevents username enumeration.
        assert unknown.status_code == wrong.status_code == 401
        assert b"Incorrect username or password" in unknown.data
        assert b"Incorrect username or password" in wrong.data

    def test_terminated_employee_cannot_sign_in(self, login, make_employee) -> None:
        from app.models import EmployeeStatus

        emp = make_employee("dave")
        emp.status = EmployeeStatus.TERMINATED
        res = login("dave")
        assert res.status_code == 403


class TestOpenRedirect:
    def test_external_next_is_ignored(self, client, employee) -> None:
        res = client.post(
            "/auth/login?next=https://evil.example.com/steal",
            data={"username": "alice", "password": "correct-horse-battery"},
        )
        assert "evil.example.com" not in res.headers["Location"]

    def test_relative_next_is_honoured(self, client, employee) -> None:
        res = client.post(
            "/auth/login?next=/dashboard",
            data={"username": "alice", "password": "correct-horse-battery"},
        )
        assert res.headers["Location"] == "/dashboard"


class TestRouteProtection:
    def test_dashboard_requires_login(self, client) -> None:
        res = client.get("/dashboard")
        assert res.status_code == 302
        assert "/auth/login" in res.headers["Location"]

    def test_healthz_is_public(self, client) -> None:
        assert client.get("/healthz").status_code == 200

    def test_logout_clears_the_session(self, client, login, employee) -> None:
        login("alice")
        client.get("/auth/logout")
        assert client.get("/dashboard").status_code == 302


class TestRoles:
    def test_default_role_is_employee(self, employee: Employee) -> None:
        assert employee.role is Role.EMPLOYEE
        assert not employee.is_hr
        assert not employee.is_manager

    def test_hr_admin_is_also_a_manager(self, hr_admin: Employee) -> None:
        assert hr_admin.is_hr
        assert hr_admin.is_manager
