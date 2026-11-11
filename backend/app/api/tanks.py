"""Tank configuration and calibration tables."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.deps import Ctx, get_ctx, require_roles
from app.models import Tank, TankCalibration
from app.schemas import CalibrationPoint, TankCreate, TankOut, TankUpdate
from app.services import calculations
from app.services.common import audit, get_tank, snapshot

router = APIRouter(prefix="/tanks", tags=["tanks"])
ADMIN = require_roles("admin")


@router.get("", response_model=list[TankOut])
def list_tanks(include_inactive: bool = False, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    q = select(Tank).where(Tank.society_id == ctx.society_id).order_by(Tank.name)
    if not include_inactive:
        q = q.where(Tank.is_active.is_(True))
    return db.scalars(q).all()


@router.post("", response_model=TankOut, status_code=201)
def create_tank(data: TankCreate, ctx: Ctx = Depends(ADMIN), db: Session = Depends(get_db)):
    tank = Tank(society_id=ctx.society_id, **data.model_dump())
    db.add(tank)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A tank with this name already exists.")
    audit(db, ctx.society_id, ctx.user_id, "tanks", tank.id, "create", None, snapshot(tank))
    db.commit()
    return tank


@router.patch("/{tank_id}", response_model=TankOut)
def update_tank(tank_id: uuid.UUID, data: TankUpdate, ctx: Ctx = Depends(ADMIN), db: Session = Depends(get_db)):
    tank = get_tank(db, ctx.society_id, tank_id)
    old = snapshot(tank)
    for k, v in data.model_dump(exclude_unset=True).items():
        if v is None and k in ("name", "capacity_litres", "shape", "default_input_unit", "is_active"):
            continue
        setattr(tank, k, v)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A tank with this name already exists.")
    audit(db, ctx.society_id, ctx.user_id, "tanks", tank.id, "update", old, snapshot(tank))
    db.commit()
    return tank


@router.get("/{tank_id}/calibration", response_model=list[CalibrationPoint])
def get_calibration(tank_id: uuid.UUID, ctx: Ctx = Depends(require_roles("admin", "staff")), db: Session = Depends(get_db)):
    get_tank(db, ctx.society_id, tank_id)
    rows = db.scalars(select(TankCalibration).where(TankCalibration.tank_id == tank_id)
                      .order_by(TankCalibration.height_cm)).all()
    return [CalibrationPoint(height_cm=float(p.height_cm), volume_litres=float(p.volume_litres)) for p in rows]


@router.put("/{tank_id}/calibration", response_model=list[CalibrationPoint])
def set_calibration(tank_id: uuid.UUID, points: list[CalibrationPoint], ctx: Ctx = Depends(ADMIN),
                    db: Session = Depends(get_db)):
    """Replaces the whole table. Old readings keep the litres they were converted to at the time."""
    tank = get_tank(db, ctx.society_id, tank_id)
    try:
        clean = calculations.validate_calibration([(p.height_cm, p.volume_litres) for p in points],
                                                  float(tank.capacity_litres))
    except calculations.CalculationError as exc:
        raise HTTPException(422, str(exc))
    db.execute(delete(TankCalibration).where(TankCalibration.tank_id == tank_id))
    db.add_all(TankCalibration(tank_id=tank_id, height_cm=h, volume_litres=v) for h, v in clean)
    audit(db, ctx.society_id, ctx.user_id, "tank_calibrations", tank_id, "update", None,
          {"points": [{"height_cm": h, "volume_litres": v} for h, v in clean]})
    db.commit()
    return [CalibrationPoint(height_cm=h, volume_litres=v) for h, v in clean]
