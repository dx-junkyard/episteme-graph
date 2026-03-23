"""PostgreSQL connection — singleton engine and session factory.

Usage::

    from core.postgres import get_engine, get_session

    with get_session() as session:
        session.execute(text("SELECT 1"))

Environment variables
---------------------
DATABASE_URL : PostgreSQL connection string (default: postgresql://episteme:episteme@postgres:5432/episteme)
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://episteme:episteme@postgres:5432/episteme",
)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the shared SQLAlchemy Engine (connection pool)."""
    logger.info("Connecting to PostgreSQL at %s", _DATABASE_URL.split("@")[-1])
    return create_engine(
        _DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Session:
    """Create a new SQLAlchemy Session."""
    return _session_factory()()


def check_connection() -> bool:
    """Test the PostgreSQL connection. Returns True on success."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("PostgreSQL connection check failed: %s", exc)
        return False
