"""Dashboard numbers. Everything is computed from the database; nothing is hard-coded.
When there is no data the response says so (`has_*` flags) so the UI can show an empty state."""
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.water import _item
from app.core.database import get_db
from app.deps import Ctx, get_ctx, require_roles
from app.models import (MaintenanceHistory, MaintenanceSchedule, Society, Tank, WaterCollection, WaterReading,
                        WaterUtilization)
from app.services import analytics, notifications
from app.services.analytics import CLASS_LABELS
from app.services.common import now_utc

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _tank_status(db: Session, tank: Tank, society: Society) -> dict:
    r = db.scalar(select(WaterReading).where(WaterReading.tank_id == tank.id, WaterReading.is_voided.is_(False))
                  .order_by(WaterReading.measured_at.desc()).limit(1))
    cap = float(tank.capacity_litres)
    base = {"tank_id": str(tank.id), "name": tank.name, "capacity_litres": cap}
    if r is None:
        return {**base, "has_reading": False}
    litres = float(r.volume_litres)
    age_days = (now_utc() - r.measured_at).days
    return {**base, "has_reading": True, "volume_litres": litres, "percent_full": round(litres / cap * 100, 1),
            "measured_at": r.measured_at.isoformat(), "data_source": r.data_source,
            "stale": age_days > society.reading_interval_days, "age_days": age_days}


def _water_rows(db: Session, society_id):
    coll = db.execute(select(WaterCollection.collected_at, WaterCollection.volume_litres, WaterCollection.data_source)
                      .where(WaterCollection.society_id == society_id, WaterCollection.is_voided.is_(False))).all()
    util = db.execute(select(WaterUtilization.used_at, WaterUtilization.volume_litres, WaterUtilization.data_source,
                             WaterUtilization.purpose)
                      .where(WaterUtilization.society_id == society_id, WaterUtilization.is_voided.is_(False))).all()
    return coll, util


def _savings(society: Society, util_totals: dict) -> dict:
    litres = util_totals["total"]
    out = {"litres": litres, "is_estimate": True, "by_class": {k: util_totals[k] for k in analytics.CLASSES},
           "basis": "Recorded harvested-water utilization. This is an estimate of municipal/tanker water avoided, "
                    "not a verified saving."}
    if society.water_cost_per_kl is not None:
        out["estimated_value_inr"] = round(litres / 1000 * float(society.water_cost_per_kl), 2)
    return out


@router.get("/admin")
def admin_dashboard(ctx: Ctx = Depends(require_roles("admin", "staff")), db: Session = Depends(get_db)):
    society = db.get(Society, ctx.society_id)
    notifications.refresh_and_commit(db, society)
    today = analytics.today_ist()
    tanks = db.scalars(select(Tank).where(Tank.society_id == ctx.society_id, Tank.is_active.is_(True)).order_by(Tank.name)).all()
    status = [_tank_status(db, t, society) for t in tanks]
    with_reading = [s for s in status if s["has_reading"]]
    coll, util = _water_rows(db, ctx.society_id)
    coll_tot, util_tot = analytics.totals_by_class(coll), analytics.totals_by_class(util)

    since = now_utc() - timedelta(days=30)
    history = db.scalars(select(WaterReading).where(WaterReading.society_id == ctx.society_id, WaterReading.is_voided.is_(False),
                                                    WaterReading.measured_at >= since).order_by(WaterReading.measured_at)).unique().all()

    pending = db.scalars(select(MaintenanceSchedule).where(MaintenanceSchedule.society_id == ctx.society_id,
                                                           MaintenanceSchedule.status == "pending")
                         .order_by(MaintenanceSchedule.due_date)).unique().all()

    def m(t):
        return {"id": str(t.id), "title": t.title, "category": t.category, "due_date": t.due_date.isoformat(),
                "assigned_to_name": t.assigned_to_name}
    overdue = [m(t) for t in pending if t.due_date < today]
    upcoming = [m(t) for t in pending if today <= t.due_date <= today + timedelta(days=14)]

    recent = []
    for model, kind, col in ((WaterReading, "reading", WaterReading.measured_at),
                             (WaterCollection, "collection", WaterCollection.collected_at),
                             (WaterUtilization, "utilization", WaterUtilization.used_at)):
        for r in db.scalars(select(model).where(model.society_id == ctx.society_id, model.is_voided.is_(False))
                            .order_by(col.desc()).limit(6)).unique().all():
            when = getattr(r, col.key)
            recent.append(_item(kind, r, when, purpose=getattr(r, "purpose", None)))
    recent = sorted(recent, key=lambda i: i["at"], reverse=True)[:8]

    return {
        "stats": {
            "current_storage_litres": round(sum(s["volume_litres"] for s in with_reading), 2),
            "current_storage_note": "Latest physical reading of each tank, added together." if with_reading else None,
            "tanks_without_reading": len(status) - len(with_reading),
            "total_capacity_litres": round(sum(s["capacity_litres"] for s in status), 2),
            "total_collected": coll_tot, "total_utilized": util_tot,
            "estimated_savings": _savings(society, util_tot),
            "upcoming_maintenance_count": len(upcoming), "overdue_maintenance_count": len(overdue),
        },
        "tanks": status,
        "charts": {
            "weekly_utilization": analytics.daily_totals(util, today - timedelta(days=6), today),
            "monthly_collection": analytics.monthly_totals(coll, today, 12),
            "storage_history": [{"tank_name": r.tank_name, "measured_at": r.measured_at.isoformat(),
                                 "volume_litres": float(r.volume_litres)} for r in history],
            "utilization_by_purpose": analytics.by_purpose(util),
            "collection_vs_utilization": [{"month": c["month"], "collected": c["litres"], "utilized": u["litres"]}
                                          for c, u in zip(analytics.monthly_totals(coll, today, 6),
                                                          analytics.monthly_totals(util, today, 6))],
        },
        "upcoming_maintenance": upcoming[:5], "overdue_maintenance": overdue[:5], "recent_activity": recent,
        "has_tanks": bool(status), "has_readings": bool(with_reading), "has_collections": bool(coll), "has_utilizations": bool(util),
        "data_classes": CLASS_LABELS,
    }


@router.get("/resident")
def resident_dashboard(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    """Read-only, simplified view. Contains no remarks, staff names or audit data."""
    society = db.get(Society, ctx.society_id)
    today = analytics.today_ist()
    tanks = db.scalars(select(Tank).where(Tank.society_id == ctx.society_id, Tank.is_active.is_(True)).order_by(Tank.name)).all()
    status = [_tank_status(db, t, society) for t in tanks]
    with_reading = [s for s in status if s["has_reading"]]
    coll, util = _water_rows(db, ctx.society_id)
    start, end = analytics.period_bounds("monthly", today)
    in_month = lambda rows: [r for r in rows if start <= analytics.to_ist_date(r[0]) <= end]
    cap = sum(s["capacity_litres"] for s in with_reading)
    stored = sum(s["volume_litres"] for s in with_reading)
    util_tot = analytics.totals_by_class(util)
    updates = db.scalars(select(MaintenanceHistory).where(MaintenanceHistory.society_id == ctx.society_id)
                         .order_by(MaintenanceHistory.completed_on.desc()).limit(5)).unique().all()
    return {
        "available_litres": round(stored, 2) if with_reading else None,
        "storage_percent": round(stored / cap * 100, 1) if cap else None,
        "tanks": [{k: s.get(k) for k in ("name", "capacity_litres", "volume_litres", "percent_full", "measured_at", "has_reading")} for s in status],
        "month": {"label": today.strftime("%B %Y"), "collected": analytics.totals_by_class(in_month(coll)),
                  "utilized": analytics.totals_by_class(in_month(util))},
        "estimated_savings": _savings(society, util_tot),
        "trend": [{"month": c["month"], "collected": c["litres"], "utilized": u["litres"]}
                  for c, u in zip(analytics.monthly_totals(coll, today, 6), analytics.monthly_totals(util, today, 6))],
        "maintenance_updates": [{"title": h.title, "completed_on": h.completed_on.isoformat()} for h in updates],
        "has_readings": bool(with_reading), "data_classes": CLASS_LABELS,
        "disclaimer": "Storage comes from manual tank readings. Collection may include calculated estimates; "
                      "savings are estimates, not verified municipal savings.",
    }
