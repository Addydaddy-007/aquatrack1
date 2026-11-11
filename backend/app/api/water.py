"""Water management: readings, collections, utilization, history, balance, audit trail.

Every query filters by ctx.society_id. Staff may correct their own records, admins any record.
Records are never deleted: a wrong entry is edited or voided, and the old values go to audit_logs.
"""
import uuid
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.deps import Ctx, require_roles
from app.models import (AuditLog, Society, TankCalibration, WaterCollection, WaterReading, WaterUtilization)
from app.schemas import (CollectionIn, CollectionOut, CollectionUpdate, EstimateIn, ReadingIn, ReadingOut,
                         ReadingUpdate, UtilizationIn, UtilizationOut, UtilizationUpdate)
from app.services import calculations, notifications
from app.services.analytics import IST
from app.services.balance import tank_balance
from app.services.common import audit, check_not_future, get_tank, snapshot

router = APIRouter(prefix="/water", tags=["water"])
STAFF = require_roles("admin", "staff")       # residents have no access to water-record endpoints

TABLE_MODELS = {"water_readings": WaterReading, "water_collections": WaterCollection,
                "water_utilizations": WaterUtilization}


# ---------- helpers ----------
def _fetch(db: Session, model, record_id: uuid.UUID, ctx: Ctx, for_edit: bool = False):
    rec = db.scalar(select(model).where(model.id == record_id, model.society_id == ctx.society_id))
    if rec is None:
        raise HTTPException(404, "Record not found")
    if for_edit and ctx.role != "admin" and rec.recorded_by != ctx.user_id:
        raise HTTPException(403, "Staff can only correct their own records. Ask an administrator.")
    return rec


def _volume(db: Session, tank, input_type: str, value: float) -> float:
    pts = [(float(h), float(v)) for h, v in db.execute(
        select(TankCalibration.height_cm, TankCalibration.volume_litres).where(TankCalibration.tank_id == tank.id))]
    try:
        return calculations.volume_from_input(input_type, value, float(tank.capacity_litres), tank.shape,
                                              float(tank.total_height_cm) if tank.total_height_cm else None, pts)
    except calculations.CalculationError as exc:
        raise HTTPException(422, str(exc))


def _commit_unique(db: Session, message: str):
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, message)


def _apply(rec, values: dict):
    for k, v in values.items():
        setattr(rec, k, v)


def _action(changes: dict) -> str:
    return "void" if changes.get("is_voided") is True else "update"


def _resolve_collection(society: Society, v: dict) -> dict:
    try:
        if v["data_source"] == "calculated_estimate":
            if v.get("rainfall_mm") is None:
                raise HTTPException(422, "Rainfall (mm) is required for an estimate.")
            area = v.get("catchment_area_m2") or (float(society.catchment_area_m2) if society.catchment_area_m2 else None)
            coef = v.get("runoff_coefficient") or float(society.runoff_coefficient)
            if not area:
                raise HTTPException(422, "Enter a catchment area, or set it in the society settings.")
            v.update(catchment_area_m2=area, runoff_coefficient=coef,
                     volume_litres=calculations.estimate_collection(v["rainfall_mm"], area, coef))
        elif not v.get("volume_litres"):
            raise HTTPException(422, "Collected quantity (litres) is required.")
    except calculations.CalculationError as exc:
        raise HTTPException(422, str(exc))
    return v


def _resolve_utilization(v: dict) -> dict:
    try:
        if v["data_source"] == "water_meter":
            if v.get("meter_start") is None or v.get("meter_end") is None or not v.get("meter_unit"):
                raise HTTPException(422, "Meter start, meter end and unit are required for meter-based usage.")
            v["volume_litres"] = calculations.meter_consumption(v["meter_start"], v["meter_end"], v["meter_unit"])
        else:
            if not v.get("volume_litres"):
                raise HTTPException(422, "Quantity (litres) is required.")
            v.update(meter_start=None, meter_end=None, meter_unit=None)
    except calculations.CalculationError as exc:
        raise HTTPException(422, str(exc))
    return v


def _plain(rec, fields) -> dict:
    return {f: (float(getattr(rec, f)) if hasattr(getattr(rec, f), "as_tuple") else getattr(rec, f)) for f in fields}


# ---------- readings ----------
@router.post("/readings", response_model=ReadingOut, status_code=201)
def create_reading(data: ReadingIn, ctx: Ctx = Depends(STAFF), db: Session = Depends(get_db)):
    tank = get_tank(db, ctx.society_id, data.tank_id, active_only=True)
    when = check_not_future(data.measured_at, "Measurement time")
    reading = WaterReading(society_id=ctx.society_id, tank_id=tank.id, measured_at=when, input_type=data.input_type,
                           input_value=data.input_value, volume_litres=_volume(db, tank, data.input_type, data.input_value),
                           data_source="manual_physical", remarks=data.remarks, recorded_by=ctx.user_id)
    db.add(reading)
    _commit_unique(db, "A reading for this tank at this exact time already exists.")
    audit(db, ctx.society_id, ctx.user_id, "water_readings", reading.id, "create", None, snapshot(reading))
    newest = db.scalar(select(func.max(WaterReading.measured_at)).where(
        WaterReading.tank_id == tank.id, WaterReading.is_voided.is_(False)))
    balance = tank_balance(db, tank, db.get(Society, ctx.society_id)) if newest == reading.measured_at else None
    notifications.after_reading(db, ctx.user_id, tank, reading, balance)
    db.commit()
    db.refresh(reading)
    return reading


@router.patch("/readings/{record_id}", response_model=ReadingOut)
def update_reading(record_id: uuid.UUID, data: ReadingUpdate, ctx: Ctx = Depends(STAFF), db: Session = Depends(get_db)):
    rec = _fetch(db, WaterReading, record_id, ctx, for_edit=True)
    changes = data.model_dump(exclude_unset=True)
    old = snapshot(rec)
    if changes.get("measured_at"):
        changes["measured_at"] = check_not_future(changes["measured_at"], "Measurement time")
    if "input_type" in changes or "input_value" in changes:
        itype = changes.get("input_type") or rec.input_type
        ivalue = changes.get("input_value") if changes.get("input_value") is not None else float(rec.input_value)
        changes.update(input_type=itype, input_value=ivalue, volume_litres=_volume(db, rec.tank, itype, ivalue))
    changes = {k: v for k, v in changes.items() if v is not None or k == "remarks"}
    _apply(rec, changes)
    _commit_unique(db, "A reading for this tank at this exact time already exists.")
    audit(db, ctx.society_id, ctx.user_id, "water_readings", rec.id, _action(changes), old, snapshot(rec))
    db.commit()
    return rec


# ---------- collections ----------
@router.post("/estimate-collection")
def estimate_preview(data: EstimateIn, ctx: Ctx = Depends(STAFF), db: Session = Depends(get_db)):
    """Preview of Rainfall x Catchment area x Runoff coefficient. Always labelled as an estimate."""
    society = db.get(Society, ctx.society_id)
    area = data.catchment_area_m2 or (float(society.catchment_area_m2) if society.catchment_area_m2 else None)
    coef = data.runoff_coefficient or float(society.runoff_coefficient)
    if not area:
        raise HTTPException(422, "Enter a catchment area, or set it in the society settings.")
    try:
        litres = calculations.estimate_collection(data.rainfall_mm, area, coef)
    except calculations.CalculationError as exc:
        raise HTTPException(422, str(exc))
    return {"volume_litres": litres, "is_estimate": True, "data_source": "calculated_estimate",
            "formula": f"{data.rainfall_mm:g} mm x {area:g} m2 x {coef:g} = {litres:,.2f} L"}


COLLECTION_FIELDS = ["collected_at", "data_source", "source", "volume_litres", "rainfall_mm", "catchment_area_m2",
                     "runoff_coefficient", "remarks"]


@router.post("/collections", response_model=CollectionOut, status_code=201)
def create_collection(data: CollectionIn, ctx: Ctx = Depends(STAFF), db: Session = Depends(get_db)):
    tank = get_tank(db, ctx.society_id, data.tank_id, active_only=True)
    vals = {f: getattr(data, f) for f in COLLECTION_FIELDS}
    vals["collected_at"] = check_not_future(vals["collected_at"], "Collection time")
    vals = _resolve_collection(db.get(Society, ctx.society_id), vals)
    rec = WaterCollection(society_id=ctx.society_id, tank_id=tank.id, recorded_by=ctx.user_id, **vals)
    db.add(rec)
    db.flush()
    audit(db, ctx.society_id, ctx.user_id, "water_collections", rec.id, "create", None, snapshot(rec))
    db.commit()
    db.refresh(rec)
    return rec


@router.patch("/collections/{record_id}", response_model=CollectionOut)
def update_collection(record_id: uuid.UUID, data: CollectionUpdate, ctx: Ctx = Depends(STAFF), db: Session = Depends(get_db)):
    rec = _fetch(db, WaterCollection, record_id, ctx, for_edit=True)
    old, changes = snapshot(rec), data.model_dump(exclude_unset=True)
    vals = _plain(rec, COLLECTION_FIELDS)
    vals.update({k: v for k, v in changes.items() if k in COLLECTION_FIELDS})
    if changes.get("collected_at"):
        vals["collected_at"] = check_not_future(changes["collected_at"], "Collection time")
    if vals["data_source"] == "calculated_estimate" and "rainfall_mm" not in changes and "volume_litres" in changes:
        raise HTTPException(422, "An estimate's volume is calculated from rainfall; edit rainfall instead.")
    vals = _resolve_collection(db.get(Society, ctx.society_id), vals)
    if "is_voided" in changes and changes["is_voided"] is not None:
        vals["is_voided"] = changes["is_voided"]
    _apply(rec, vals)
    db.flush()
    audit(db, ctx.society_id, ctx.user_id, "water_collections", rec.id, _action(changes), old, snapshot(rec))
    db.commit()
    return rec


# ---------- utilization ----------
UTIL_FIELDS = ["used_at", "purpose", "data_source", "volume_litres", "meter_start", "meter_end", "meter_unit", "remarks"]


@router.get("/utilizations/last-meter")
def last_meter(tank_id: uuid.UUID, ctx: Ctx = Depends(STAFF), db: Session = Depends(get_db)):
    """Pre-fills 'meter start' with the previous meter reading so staff only type the new one."""
    get_tank(db, ctx.society_id, tank_id)
    row = db.scalar(select(WaterUtilization).where(
        WaterUtilization.society_id == ctx.society_id, WaterUtilization.tank_id == tank_id,
        WaterUtilization.data_source == "water_meter", WaterUtilization.is_voided.is_(False))
        .order_by(WaterUtilization.used_at.desc()).limit(1))
    return {"meter_end": float(row.meter_end), "meter_unit": row.meter_unit} if row else {"meter_end": None, "meter_unit": None}


@router.post("/utilizations", response_model=UtilizationOut, status_code=201)
def create_utilization(data: UtilizationIn, ctx: Ctx = Depends(STAFF), db: Session = Depends(get_db)):
    tank = get_tank(db, ctx.society_id, data.tank_id, active_only=True)
    vals = {f: getattr(data, f) for f in UTIL_FIELDS}
    vals["used_at"] = check_not_future(vals["used_at"], "Usage time")
    vals = _resolve_utilization(vals)
    rec = WaterUtilization(society_id=ctx.society_id, tank_id=tank.id, recorded_by=ctx.user_id, **vals)
    db.add(rec)
    db.flush()
    audit(db, ctx.society_id, ctx.user_id, "water_utilizations", rec.id, "create", None, snapshot(rec))
    db.commit()
    db.refresh(rec)
    return rec


@router.patch("/utilizations/{record_id}", response_model=UtilizationOut)
def update_utilization(record_id: uuid.UUID, data: UtilizationUpdate, ctx: Ctx = Depends(STAFF), db: Session = Depends(get_db)):
    rec = _fetch(db, WaterUtilization, record_id, ctx, for_edit=True)
    old, changes = snapshot(rec), data.model_dump(exclude_unset=True)
    vals = _plain(rec, UTIL_FIELDS)
    vals.update({k: v for k, v in changes.items() if k in UTIL_FIELDS})
    if changes.get("used_at"):
        vals["used_at"] = check_not_future(changes["used_at"], "Usage time")
    vals = _resolve_utilization(vals)
    if "is_voided" in changes and changes["is_voided"] is not None:
        vals["is_voided"] = changes["is_voided"]
    _apply(rec, vals)
    db.flush()
    audit(db, ctx.society_id, ctx.user_id, "water_utilizations", rec.id, _action(changes), old, snapshot(rec))
    db.commit()
    return rec


# ---------- history, audit, balance ----------
def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=IST)


def _item(kind: str, rec, when, **extra) -> dict:
    return {"record_type": kind, "id": str(rec.id), "at": when.isoformat(), "tank_id": str(rec.tank_id),
            "tank_name": rec.tank_name, "volume_litres": float(rec.volume_litres), "data_source": rec.data_source,
            "recorded_by": str(rec.recorded_by), "recorded_by_name": rec.recorded_by_name, "remarks": rec.remarks,
            "is_voided": rec.is_voided, **extra}


@router.get("/history")
def history(date_from: date | None = None, date_to: date | None = None, tank_id: uuid.UUID | None = None,
            record_type: str | None = Query(default=None, pattern="^(reading|collection|utilization)$"),
            purpose: str | None = Query(default=None, pattern="^(garden_irrigation|common_area_cleaning|vehicle_washing|other)$"),
            recorded_by: uuid.UUID | None = None, q: str | None = Query(default=None, max_length=100),
            include_voided: bool = True, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
            ctx: Ctx = Depends(STAFF), db: Session = Depends(get_db)):
    """One searchable list across all record types, newest first."""
    specs = [("reading", WaterReading, WaterReading.measured_at),
             ("collection", WaterCollection, WaterCollection.collected_at),
             ("utilization", WaterUtilization, WaterUtilization.used_at)]
    items = []
    for kind, model, col in specs:
        if (record_type and record_type != kind) or (purpose and kind != "utilization"):
            continue
        cond = [model.society_id == ctx.society_id]
        if date_from:
            cond.append(col >= _day_start(date_from))
        if date_to:
            cond.append(col < _day_start(date_to + timedelta(days=1)))
        if tank_id:
            cond.append(model.tank_id == tank_id)
        if recorded_by:
            cond.append(model.recorded_by == recorded_by)
        if q:
            cond.append(model.remarks.ilike(f"%{q}%"))
        if purpose:
            cond.append(WaterUtilization.purpose == purpose)
        if not include_voided:
            cond.append(model.is_voided.is_(False))
        rows = db.scalars(select(model).where(*cond).order_by(col.desc()).limit(offset + limit + 1)).unique().all()
        for r in rows:
            if kind == "reading":
                items.append(_item(kind, r, r.measured_at, input_type=r.input_type, input_value=float(r.input_value)))
            elif kind == "collection":
                items.append(_item(kind, r, r.collected_at, source=r.source, rainfall_mm=float(r.rainfall_mm) if r.rainfall_mm is not None else None))
            else:
                items.append(_item(kind, r, r.used_at, purpose=r.purpose, meter_start=float(r.meter_start) if r.meter_start is not None else None,
                                   meter_end=float(r.meter_end) if r.meter_end is not None else None, meter_unit=r.meter_unit))
    items.sort(key=lambda i: i["at"], reverse=True)
    return {"items": items[offset:offset + limit], "has_more": len(items) > offset + limit}


@router.get("/audit/{table_name}/{record_id}")
def audit_trail(table_name: str, record_id: uuid.UUID, ctx: Ctx = Depends(STAFF), db: Session = Depends(get_db)):
    if table_name not in TABLE_MODELS:
        raise HTTPException(404, "Unknown record type")
    logs = db.scalars(select(AuditLog).where(AuditLog.society_id == ctx.society_id, AuditLog.table_name == table_name,
                                             AuditLog.record_id == record_id).order_by(AuditLog.created_at)).all()
    return [{"at": l.created_at.isoformat(), "action": l.action, "actor_id": str(l.actor_id) if l.actor_id else None,
             "old_values": l.old_values, "new_values": l.new_values} for l in logs]


@router.get("/balance")
def balance(tank_id: uuid.UUID | None = None, ctx: Ctx = Depends(STAFF), db: Session = Depends(get_db)):
    """Opening stock + recorded collection - recorded utilization = expected closing stock, compared with the
    latest physical reading. The difference is shown as an unexplained discrepancy."""
    society = db.get(Society, ctx.society_id)
    if tank_id:
        return [tank_balance(db, get_tank(db, ctx.society_id, tank_id), society)]
    from app.models import Tank
    tanks = db.scalars(select(Tank).where(Tank.society_id == ctx.society_id, Tank.is_active.is_(True)).order_by(Tank.name)).all()
    return [tank_balance(db, t, society) for t in tanks]
