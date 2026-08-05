"""Role checks.

The old version did `session['role'] == 'hr'` inline in every view and missed
one. A decorator means a route is either guarded or it obviously isn't.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

from flask import abort
from flask_login import current_user

from app.models.enums import Role

F = TypeVar("F", bound=Callable[..., Any])


def roles_required(*roles: Role) -> Callable[[F], F]:
    """403 rather than a redirect if they're logged in but lack the role —
    a redirect just looks like a broken link."""

    def decorator(view: F) -> F:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.has_role(*roles):
                abort(403)
            return view(*args, **kwargs)

        return cast(F, wrapped)

    return decorator


hr_required = roles_required(Role.HR_ADMIN)
manager_required = roles_required(Role.MANAGER, Role.HR_ADMIN)


def ensure_can_view(employee: Any) -> None:
    if not current_user.is_authenticated or not current_user.can_view(employee):
        abort(403)
