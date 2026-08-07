"""App factory — lets the tests build isolated instances with their own config."""

from __future__ import annotations

from typing import Any

from flask import Flask

from app.config import Config, get_config
from app.extensions import csrf, db, login_manager, migrate


def create_app(config: type[Config] | str | None = None) -> Flask:
    app = Flask(__name__)

    config_class = config if isinstance(config, type) else get_config(config)
    app.config.from_object(config_class)

    _init_extensions(app)
    _register_blueprints(app)
    _register_cli(app)
    _register_errorhandlers(app)
    _register_template_globals(app)

    return app


def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)


def _register_blueprints(app: Flask) -> None:
    # Imported here, not at module level, or blueprints importing models
    # circles back into create_app.
    from app.blueprints.attendance import bp as attendance_bp
    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.employees import bp as employees_bp
    from app.blueprints.leave import bp as leave_bp
    from app.blueprints.main import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(employees_bp, url_prefix="/employees")
    app.register_blueprint(leave_bp, url_prefix="/leave")
    app.register_blueprint(attendance_bp, url_prefix="/timesheet")


def _register_cli(app: Flask) -> None:
    from app.cli import register_commands

    register_commands(app)


def _register_errorhandlers(app: Flask) -> None:
    from flask import render_template

    @app.errorhandler(403)
    def forbidden(_: Any):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_: Any):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(_: Any):
        db.session.rollback()
        return render_template("errors/500.html"), 500


def _register_template_globals(app: Flask) -> None:
    from datetime import date

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {"today": date.today(), "demo_mode": app.config.get("DEMO_MODE", False)}

    @app.shell_context_processor
    def shell_context() -> dict[str, Any]:
        # so `flask shell` opens with the models already there
        import app.models as models

        return {"db": db, **{name: getattr(models, name) for name in models.__all__}}
