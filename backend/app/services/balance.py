"""Water balance for one tank, built from the two most recent valid physical readings."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Society, Tank, WaterCollection, WaterReading, WaterUtilization
from app.services import analytics, calculations


def tank_balance(db: Session, tank: Tank, society: Society) -> dict:
    readings = db.scalars(select(WaterReading).where(WaterReading.tank_id == tank.id, WaterReading.is_voided.is_(False))
                          .order_by(WaterReading.measured_at.desc()).limit(2)).all()
    base = {"tank_id": str(tank.id), "tank_name": tank.name, "capacity_litres": float(tank.capacity_litres)}
    if len(readings) < 2:
        return {**base, "status": "insufficient_data",
                "message": "At least two physical readings are needed to compare records with the tank.",
                "latest_reading": _reading(readings[0]) if readings else None}
    last, prev = readings[0], readings[1]

    def sums(model, time_col):
        rows = db.execute(select(model.data_source, func.coalesce(func.sum(model.volume_litres), 0))
                          .where(model.tank_id == tank.id, model.is_voided.is_(False),
                                 time_col > prev.measured_at, time_col <= last.measured_at)
                          .group_by(model.data_source)).all()
        return [(None, float(v), s) for s, v in rows]          # shaped like analytics rows: (time, litres, source)

    coll = analytics.totals_by_class(sums(WaterCollection, WaterCollection.collected_at))
    util = analytics.totals_by_class(sums(WaterUtilization, WaterUtilization.used_at))
    result = calculations.water_balance(float(prev.volume_litres), coll["total"], util["total"],
                                        float(last.volume_litres), float(tank.capacity_litres),
                                        float(society.discrepancy_threshold_pct))
    return {**base, "status": "ok", **result,
            "period_start": prev.measured_at.isoformat(), "period_end": last.measured_at.isoformat(),
            "collected_by_class": coll, "utilized_by_class": util,
            "note": "Opening and closing values are physical readings; collection/utilization include "
                    "manually declared values and estimates as shown in the class breakdown."}


def _reading(r: WaterReading) -> dict:
    return {"measured_at": r.measured_at.isoformat(), "volume_litres": float(r.volume_litres),
            "data_source": r.data_source}
