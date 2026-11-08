"""Section 2: database engine, session, Base, get_db, and Shard Router integration."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.core.shard_router import (
    ShardedSession,
    Shards,
    close_router,
    get_router,
    get_uow,
    init_router,
)

# =============================================================================
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# DB dependency provides the request-scoped ShardedSession unit of work
DB = Shards


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

