"""Creates in-app notifications. Every notification has a dedupe_key, so running the checks 100 times
never creates duplicates (the database also enforces unique(user_id, dedupe_key))."""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import MaintenanceSchedule, Membership, Notification, Society, Tank, WaterReading
from app.services import alerts, analytics
from app.services.common import now_utc


def _user_ids(db: Session, society_id, roles: tuple[str, ...]) -> list:
    return list(db.scalars(select(Membership.user_id).where(
        Membership.society_id == society_id, Membership.status == "active", Membership.role.in_(roles))))


def push(db: Session, society_id, user_ids, type_: str, title: str, message: str, dedupe_key: str):
    user_ids = list(dict.fromkeys(user_ids))
    if not user_ids:
        return
    have = set(db.scalars(select(Notification.user_id).where(
        Notification.dedupe_key == dedupe_key, Notification.user_id.in_(user_ids))))
    for uid in user_ids:
        if uid not in have:
            db.add(Notification(society_id=society_id, user_id=uid, type=type_, title=title,
                                message=message, dedupe_key=dedupe_key))


def after_reading(db: Session, actor_id, tank: Tank, reading: WaterReading, balance: dict | None):
    admins = [u for u in _user_ids(db, tank.society_id, ("admin",)) if u != actor_id]
    push(db, tank.society_id, admins, "new_reading", f"New reading: {tank.name}",
         f"{float(reading.volume_litres):,.0f} L recorded for '{tank.name}'.", f"new-reading:{reading.id}")
    if balance and balance.get("status") == "ok" and balance.get("flagged"):
        gap = balance["discrepancy_litres"]
        push(db, tank.society_id, _user_ids(db, tank.society_id, ("admin",)), "balance_discrepancy",
             f"Unexplained difference: {tank.name}",
             f"The physical reading differs from the records-based expectation by {gap:+,.0f} L "
             f"({balance['discrepancy_pct_of_capacity']}% of capacity). The cause is unknown - check for "
             "unrecorded usage, inflow, overflow or a measurement error.", f"discrepancy:{reading.id}")


def refresh(db: Session, society: Society):
    """Idempotent: creates whatever alerts are currently due. Caller commits."""
    now, today = now_utc(), analytics.today_ist()
    admins = _user_ids(db, society.id, ("admin",))
    staff_admins = _user_ids(db, society.id, ("admin", "staff"))

    tasks = db.scalars(select(MaintenanceSchedule).where(
        MaintenanceSchedule.society_id == society.id, MaintenanceSchedule.status == "pending")).all()
    for t in tasks:
        a = alerts.maintenance_alert(t.title, t.due_date, today, t.remind_before_days)
        if a:
            type_, title, msg, kind = a
            push(db, society.id, admins + ([t.assigned_to] if t.assigned_to else []), type_, title, msg,
                 f"maint-{kind}:{t.id}:{t.due_date.isoformat()}")

    for tank in db.scalars(select(Tank).where(Tank.society_id == society.id, Tank.is_active.is_(True))):
        last = db.scalar(select(WaterReading.measured_at).where(
            WaterReading.tank_id == tank.id, WaterReading.is_voided.is_(False))
            .order_by(WaterReading.measured_at.desc()).limit(1))
        a = alerts.reading_overdue_alert(tank.name, last, tank.created_at, now, society.reading_interval_days)
        if a:
            push(db, society.id, staff_admins, "reading_overdue", a[0], a[1], f"reading-overdue:{tank.id}:{a[2]}")

    first_this_month = today.replace(day=1)
    prev_month_end = first_this_month - timedelta(days=1)
    created_date = analytics.to_ist_date(society.created_at) if society.created_at else today
    if created_date <= prev_month_end:
        label = prev_month_end.strftime("%B %Y")
        push(db, society.id, admins, "monthly_report", f"{label} report is ready",
             f"The monthly water report for {label} is available in Reports.",
             f"monthly-report:{prev_month_end.strftime('%Y-%m')}")


def refresh_and_commit(db: Session, society: Society):
    """Used by GET endpoints. If two requests create the same alert at the same moment, the database's unique
    constraint rejects the second one; we simply roll that back and carry on."""
    try:
        refresh(db, society)
        db.commit()
    except IntegrityError:
        db.rollback()
