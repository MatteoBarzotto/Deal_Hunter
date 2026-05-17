"""SQLAlchemy engine + session factory."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from config import get_secrets, get_settings

from .models import Base, WatchCategory

_secrets = get_secrets()

engine = create_engine(
    _secrets.database_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False}
    if _secrets.database_url.startswith("sqlite")
    else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """Create tables if they don't exist, and seed watch categories from YAML."""
    Base.metadata.create_all(bind=engine)
    _migrate_watch_categories_keywords()
    _seed_watch_categories()


def _migrate_watch_categories_keywords() -> None:
    """Lightweight migration: add keyword columns to existing tables (SQLite only)."""
    if not engine.url.drivername.startswith("sqlite"):
        return
    with engine.begin() as conn:
        cols = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(watch_categories)"))
        }
        if "include_keywords" not in cols:
            conn.execute(
                text("ALTER TABLE watch_categories ADD COLUMN include_keywords VARCHAR(512)")
            )
        if "exclude_keywords" not in cols:
            conn.execute(
                text("ALTER TABLE watch_categories ADD COLUMN exclude_keywords VARCHAR(512)")
            )


def _seed_watch_categories() -> None:
    """If watch_categories is empty, populate from settings.yaml OLX entries."""
    settings = get_settings()
    with session_scope() as session:
        existing = session.execute(select(WatchCategory.id).limit(1)).scalar_one_or_none()
        if existing is not None:
            return
        for cat in settings.olx.categories:
            if not cat.url:
                continue
            session.add(
                WatchCategory(
                    platform="olx",
                    name=cat.name,
                    url=cat.url,
                    min_price_pln=cat.min_price_pln,
                    max_price_pln=cat.max_price_pln,
                    enabled=True,
                )
            )


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session: commits on success, rolls back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
