"""
Database connection — lazy SQLAlchemy engine bound to $DATABASE_URL.

Nothing in the pipeline depends on the DB yet; this module just wires the
connection so future models/queries can `from seo.db import engine, session`
without any further plumbing. If DATABASE_URL is unset, the module still
imports cleanly — calling engine() then raises with a clear message.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _normalize_url(url: str) -> str:
    """
    Render (and Heroku) hand out `postgres://…` URLs; SQLAlchemy 2.x wants
    `postgresql+psycopg://…`. Also route the SQLAlchemy default driver to
    psycopg 3 so we only ship one Postgres driver.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@lru_cache(maxsize=1)
def engine() -> Engine:
    """Return the process-wide SQLAlchemy engine, creating it on first call."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it in Render → Environment "
            "(Internal Database URL from your Postgres service)."
        )
    return create_engine(
        _normalize_url(url),
        pool_pre_ping=True,   # drop stale connections after Render idle spin-down
        pool_recycle=1800,    # recycle every 30 min
        future=True,
    )


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=engine(), expire_on_commit=False, future=True)


@contextmanager
def session():
    """Short-lived DB session with automatic commit/rollback.

    Usage:
        with session() as s:
            s.execute(text("SELECT 1"))
    """
    s = _session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def ping() -> bool:
    """Sanity-check the connection. Returns True on success, False otherwise."""
    try:
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
