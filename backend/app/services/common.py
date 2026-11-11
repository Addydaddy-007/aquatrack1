"""Small helpers shared by routes."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Tank
from app.services.analytics import IST


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(dt: datetime) -> datetime:
    """A time typed without a timezone is treated as India time."""
    return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt


def check_not_future(dt: datetime, what: str = "Time") -> datetime:
    dt = ensure_aware(dt)
    if dt > now_utc() + timedelta(minutes=5):
        raise HTTPException(422, f"{what} cannot be in the future.")
    return dt


def snapshot(obj) -> dict:
    return jsonable_encoder({c.name: getattr(obj, c.name) for c in obj.__table__.columns})


def audit(db: Session, society_id, actor_id, table: str, record_id, action: str, old: dict | None, new: dict | None):
    db.add(AuditLog(society_id=society_id, actor_id=actor_id, table_name=table, record_id=record_id,
                    action=action, old_values=old, new_values=new))


def get_tank(db: Session, society_id, tank_id: uuid.UUID, active_only: bool = False) -> Tank:
    tank = db.scalar(select(Tank).where(Tank.id == tank_id, Tank.society_id == society_id))
    if tank is None:                                       # same answer for "missing" and "someone else's"
        raise HTTPException(404, "Tank not found")
    if active_only and not tank.is_active:
        raise HTTPException(422, "This tank is inactive.")
    return tank
