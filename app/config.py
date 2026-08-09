"""Config comes from env vars so the same image runs everywhere. See .env.example."""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, ClassVar


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


def _normalise_db_url(url: str) -> str:
    """Render (and Heroku) hand out postgres:// URLs.

    SQLAlchemy 2.0 dropped that alias and will refuse to start, and the
    default driver would be psycopg2 rather than the psycopg 3 this project
    installs. Rewriting here means the deploy works with the URL the platform
    gives you, untouched.
    """
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-do-not-use-in-production")

    SQLALCHEMY_DATABASE_URI = _normalise_db_url(
        os.environ.get("DATABASE_URL", "postgresql+psycopg://hrms:hrms@localhost:5432/hrms")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS: ClassVar[dict[str, Any]] = {
        "pool_pre_ping": True,  # Render drops idle connections
        "pool_recycle": 300,
    }

    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.environ.get("SESSION_MINUTES", 60)))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_HTTPONLY = True

    ITEMS_PER_PAGE = int(os.environ.get("ITEMS_PER_PAGE", 25))
    DEMO_MODE = _bool("DEMO_MODE", False)


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_ECHO = _bool("SQL_ECHO", False)


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SECRET_KEY = "testing"
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL", "postgresql+psycopg://hrms:hrms@localhost:5432/hrms_test"
    )


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"

    def __init__(self) -> None:
        if self.SECRET_KEY == "dev-only-do-not-use-in-production":
            raise RuntimeError("SECRET_KEY must be set in production.")


CONFIGS: dict[str, type[Config]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None) -> type[Config]:
    key = (name or os.environ.get("FLASK_ENV") or "development").lower()
    return CONFIGS.get(key, DevelopmentConfig)
