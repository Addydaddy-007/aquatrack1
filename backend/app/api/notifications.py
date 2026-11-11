"""In-app notifications. Each user only ever sees and changes their own."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.deps import Ctx, get_ctx
from app.models import Notification, Society
from app.schemas import NotificationOut
from app.services import notifications as engine

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_notifications(unread_only: bool = False, limit: int = 30, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    if ctx.role in ("admin", "staff"):                      # lazily generate any alerts that are now due
        engine.refresh_and_commit(db, db.get(Society, ctx.society_id))
    q = select(Notification).where(Notification.user_id == ctx.user_id)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    items = db.scalars(q.order_by(Notification.created_at.desc()).limit(min(max(limit, 1), 100))).all()
    unread = db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == ctx.user_id, Notification.read_at.is_(None)))
    return {"unread_count": unread, "items": [NotificationOut.model_validate(n) for n in items]}


@router.post("/{notification_id}/read", status_code=204)
def mark_read(notification_id: uuid.UUID, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    n = db.scalar(select(Notification).where(Notification.id == notification_id, Notification.user_id == ctx.user_id))
    if n is None:
        raise HTTPException(404, "Notification not found")
    if n.read_at is None:
        n.read_at = datetime.now(timezone.utc)
        db.commit()


@router.post("/read-all", status_code=204)
def mark_all_read(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    db.execute(update(Notification).where(Notification.user_id == ctx.user_id, Notification.read_at.is_(None))
               .values(read_at=datetime.now(timezone.utc)))
    db.commit()
