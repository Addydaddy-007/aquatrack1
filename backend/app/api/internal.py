"""Endpoint for a scheduler (Render cron job / GitHub Actions) so alerts are created even if nobody logs in."""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models import Society
from app.services import notifications

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


@router.post("/run-checks")
def run_checks(x_cron_secret: str = Header(default=""), settings: Settings = Depends(get_settings), db: Session = Depends(get_db)):
    if not settings.cron_secret or not secrets.compare_digest(x_cron_secret, settings.cron_secret):
        raise HTTPException(403, "Forbidden")
    count = 0
    for society in db.scalars(select(Society)).all():
        notifications.refresh(db, society)
        count += 1
    db.commit()
    return {"societies_checked": count}
