"""SQLAlchemy engine/session factory — shared by all services."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from shared.config import ServiceConfig

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _get_engine(cfg: ServiceConfig):
    global _engine
    if _engine is None:
        # psycopg v3 driver — rewrite scheme so SQLAlchemy doesn't default to psycopg2
        sa_url = cfg.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        _engine = create_engine(
            sa_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
    return _engine


def init_db(cfg: ServiceConfig) -> None:
    """Initialise the global engine from config. Call once at service startup."""
    global _engine, _SessionLocal
    _engine = _get_engine(cfg)
    _SessionLocal = sessionmaker(bind=_engine)


@contextmanager
def session() -> Generator[Session, Any, None]:
    """Get a DB session from the global engine. Yields a ``Session``."""
    if _SessionLocal is None:
        raise RuntimeError("init_db() must be called before session()")
    db = _SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
