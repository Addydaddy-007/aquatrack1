"""Maintenance tasks, recurring schedules and completion history.
Reminders are based purely on the due dates people enter - the app cannot detect blocked filters or failures."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.deps import Ctx, get_ctx, require_roles
from app.models import MaintenanceHistory, MaintenanceSchedule, Membership
from app.schemas import CompleteIn, HistoryOut, TaskCreate, TaskOut, TaskUpdate
from app.services import analytics, calculations
from app.services.common import audit, snapshot

router = APIRouter(prefix="/maintenance", tags=["maintenance"])
ADMIN = require_roles("admin")
STAFF = require_roles("admin", "staff")


def task_out(t: MaintenanceSchedule, today=None) -> dict:
    today = today or analytics.today_ist()
    out = TaskOut.model_validate(t).model_dump()
    out["state"] = t.status if t.status != "pending" else calculations.maintenance_state(t.due_date, today, t.remind_before_days)
    return out


def _check_assignee(db: Session, society_id, user_id):
    if user_id is None:
        return
    ok = db.scalar(select(Membership.id).where(Membership.society_id == society_id, Membership.user_id == user_id,
                                               Membership.status == "active", Membership.role.in_(("admin", "staff"))))
    if ok is None:
        raise HTTPException(422, "Tasks can only be assigned to active staff or administrators of this society.")


def _task(db: Session, ctx: Ctx, task_id: uuid.UUID) -> MaintenanceSchedule:
    t = db.scalar(select(MaintenanceSchedule).where(MaintenanceSchedule.id == task_id,
                                                    MaintenanceSchedule.society_id == ctx.society_id))
    if t is None:
        raise HTTPException(404, "Task not found")
    return t


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(data: TaskCreate, ctx: Ctx = Depends(ADMIN), db: Session = Depends(get_db)):
    _check_assignee(db, ctx.society_id, data.assigned_to)
    t = MaintenanceSchedule(society_id=ctx.society_id, created_by=ctx.user_id, **data.model_dump())
    db.add(t)
    db.flush()
    audit(db, ctx.society_id, ctx.user_id, "maintenance_schedules", t.id, "create", None, snapshot(t))
    db.commit()
    db.refresh(t)
    return task_out(t)


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(mine: bool = False, status: str | None = None, ctx: Ctx = Depends(STAFF), db: Session = Depends(get_db)):
    """Admins see every task. Staff see only tasks assigned to them (their 'assigned work')."""
    q = select(MaintenanceSchedule).where(MaintenanceSchedule.society_id == ctx.society_id)
    if ctx.role == "staff" or mine:
        q = q.where(MaintenanceSchedule.assigned_to == ctx.user_id)
    if status in ("pending", "completed", "cancelled"):
        q = q.where(MaintenanceSchedule.status == status)
    rows = db.scalars(q.order_by(MaintenanceSchedule.due_date)).unique().all()
    return [task_out(t) for t in rows]


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: uuid.UUID, data: TaskUpdate, ctx: Ctx = Depends(ADMIN), db: Session = Depends(get_db)):
    t = _task(db, ctx, task_id)
    old, changes = snapshot(t), data.model_dump(exclude_unset=True)
    if "assigned_to" in changes:
        _check_assignee(db, ctx.society_id, changes["assigned_to"])
    for k, v in changes.items():
        if v is None and k not in ("assigned_to", "description", "interval_days"):
            continue
        setattr(t, k, v)
    db.flush()
    audit(db, ctx.society_id, ctx.user_id, "maintenance_schedules", t.id, "update", old, snapshot(t))
    db.commit()
    db.refresh(t)
    return task_out(t)


@router.post("/tasks/{task_id}/complete", response_model=TaskOut)
def complete_task(task_id: uuid.UUID, data: CompleteIn, ctx: Ctx = Depends(STAFF), db: Session = Depends(get_db)):
    """Logs the completion. A recurring task moves to its next due date; a one-off task is closed."""
    t = _task(db, ctx, task_id)
    if ctx.role == "staff" and t.assigned_to != ctx.user_id:
        raise HTTPException(403, "This task is not assigned to you.")
    if t.status != "pending":
        raise HTTPException(409, "This task is not open.")
    done = data.completed_on or analytics.today_ist()
    if done > analytics.today_ist():
        raise HTTPException(422, "Completion date cannot be in the future.")
    old = snapshot(t)
    db.add(MaintenanceHistory(society_id=ctx.society_id, schedule_id=t.id, due_date_was=t.due_date,
                              completed_on=done, completed_by=ctx.user_id, notes=data.notes))
    if t.interval_days:
        t.due_date = calculations.next_due_date(done, t.interval_days)
    else:
        t.status = "completed"
    db.flush()
    audit(db, ctx.society_id, ctx.user_id, "maintenance_schedules", t.id, "update", old, snapshot(t))
    db.commit()
    db.refresh(t)
    return task_out(t)


@router.get("/history", response_model=list[HistoryOut])
def history(limit: int = 100, ctx: Ctx = Depends(STAFF), db: Session = Depends(get_db)):
    return db.scalars(select(MaintenanceHistory).where(MaintenanceHistory.society_id == ctx.society_id)
                      .order_by(MaintenanceHistory.completed_on.desc()).limit(min(max(limit, 1), 500))).unique().all()


@router.get("/updates")
def updates_for_everyone(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    """Read-only summary for residents: what is coming up and what was recently done (no staff names, no notes)."""
    today = analytics.today_ist()
    tasks = db.scalars(select(MaintenanceSchedule).where(
        MaintenanceSchedule.society_id == ctx.society_id, MaintenanceSchedule.status == "pending")
        .order_by(MaintenanceSchedule.due_date).limit(10)).unique().all()
    done = db.scalars(select(MaintenanceHistory).where(MaintenanceHistory.society_id == ctx.society_id)
                      .order_by(MaintenanceHistory.completed_on.desc()).limit(10)).unique().all()
    return {"upcoming": [{"title": t.title, "category": t.category, "due_date": t.due_date.isoformat(),
                          "state": calculations.maintenance_state(t.due_date, today, t.remind_before_days)} for t in tasks],
            "recently_completed": [{"title": h.title, "category": h.category, "completed_on": h.completed_on.isoformat()}
                                   for h in done]}
