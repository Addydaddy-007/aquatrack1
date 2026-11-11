"""Database engine and per-request session."""
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine():
    # pool_pre_ping drops dead connections (Supabase/Render may close idle ones).
    return create_engine(get_settings().sqlalchemy_url, pool_pre_ping=True, pool_size=5, max_overflow=5,
                         connect_args={"sslmode": "require"})


@lru_cache
def _session_factory():
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db():
    """FastAPI dependency: one session per request; rolled back on any error, closed afterwards."""
    db: Session = _session_factory()()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
