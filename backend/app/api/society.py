"""Sign-up onboarding, society settings and member management."""
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.deps import Ctx, get_profile, require_roles, get_ctx
from app.models import Membership, Profile, Society
from app.schemas import (JoinRequest, MemberOut, MemberUpdate, ProfileUpdate, SocietyCreate, SocietyOut,
                         SocietyUpdate)

router = APIRouter(tags=["society & users"])
ADMIN = require_roles("admin")


def _new_code() -> str:
    return secrets.token_urlsafe(6).upper().replace("-", "A").replace("_", "B")[:8]


@router.get("/me")
def me(profile: Profile = Depends(get_profile), db: Session = Depends(get_db)):
    """Who am I, and which society/role do I have? The frontend uses this to choose the right dashboard."""
    m = db.scalar(select(Membership).where(Membership.user_id == profile.id))
    society = db.get(Society, m.society_id) if m else None
    return {"id": str(profile.id), "full_name": profile.full_name, "email": profile.email, "phone": profile.phone,
            "membership": {"role": m.role, "status": m.status} if m else None,
            "society": {"id": str(society.id), "name": society.name} if society and m.status == "active" else None}


@router.patch("/me")
def update_me(data: ProfileUpdate, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)):
    for k, v in data.model_dump(exclude_unset=True).items():
        if k == "full_name" and v is None:
            continue
        setattr(profile, k, v)
    db.commit()
    return {"id": str(profile.id), "full_name": profile.full_name, "phone": profile.phone}


@router.post("/onboarding/society", response_model=SocietyOut, status_code=201)
def create_society(data: SocietyCreate, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)):
    """The person who registers a society becomes its first administrator."""
    if db.scalar(select(Membership).where(Membership.user_id == profile.id)):
        raise HTTPException(409, "You already belong to a society.")
    society = Society(name=data.name.strip(), address=data.address, invite_code=_new_code(), created_by=profile.id)
    db.add(society)
    db.flush()
    db.add(Membership(society_id=society.id, user_id=profile.id, role="admin", status="active"))
    db.commit()
    return society


@router.post("/onboarding/join", status_code=202)
def join_society(data: JoinRequest, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)):
    """Joining with an invite code creates a PENDING resident membership; an admin must approve it."""
    if db.scalar(select(Membership).where(Membership.user_id == profile.id)):
        raise HTTPException(409, "You already belong to a society.")
    society = db.scalar(select(Society).where(Society.invite_code == data.invite_code.strip().upper()))
    if society is None:
        raise HTTPException(404, "Invalid invite code.")
    db.add(Membership(society_id=society.id, user_id=profile.id, role="resident", status="pending"))
    db.commit()
    return {"message": "Request sent. A society administrator must approve it.", "society": society.name}


@router.get("/society", response_model=SocietyOut)
def get_society(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    return db.get(Society, ctx.society_id)


@router.patch("/society", response_model=SocietyOut)
def update_society(data: SocietyUpdate, ctx: Ctx = Depends(ADMIN), db: Session = Depends(get_db)):
    society = db.get(Society, ctx.society_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        if v is None and k in ("name", "runoff_coefficient", "reading_interval_days", "discrepancy_threshold_pct"):
            continue
        setattr(society, k, v)
    db.commit()
    return society


@router.get("/society/invite-code")
def invite_code(ctx: Ctx = Depends(ADMIN), db: Session = Depends(get_db)):
    return {"invite_code": db.get(Society, ctx.society_id).invite_code}


@router.post("/society/invite-code/rotate")
def rotate_invite_code(ctx: Ctx = Depends(ADMIN), db: Session = Depends(get_db)):
    society = db.get(Society, ctx.society_id)
    society.invite_code = _new_code()
    db.commit()
    return {"invite_code": society.invite_code}


@router.get("/society/members", response_model=list[MemberOut])
def list_members(ctx: Ctx = Depends(ADMIN), db: Session = Depends(get_db)):
    return db.scalars(select(Membership).where(Membership.society_id == ctx.society_id)
                      .order_by(Membership.status, Membership.role)).unique().all()


@router.get("/society/assignable-staff")
def assignable_staff(ctx: Ctx = Depends(ADMIN), db: Session = Depends(get_db)):
    rows = db.scalars(select(Membership).where(Membership.society_id == ctx.society_id, Membership.status == "active",
                                               Membership.role.in_(("admin", "staff")))).unique().all()
    return [{"user_id": str(m.user_id), "full_name": m.full_name, "role": m.role} for m in rows]


@router.patch("/society/members/{member_id}", response_model=MemberOut)
def update_member(member_id: uuid.UUID, data: MemberUpdate, ctx: Ctx = Depends(ADMIN), db: Session = Depends(get_db)):
    """Approve a pending request, change a role, or disable an account - only inside the admin's own society."""
    m = db.scalar(select(Membership).where(Membership.id == member_id, Membership.society_id == ctx.society_id))
    if m is None:
        raise HTTPException(404, "Member not found")
    if m.user_id == ctx.user_id:
        raise HTTPException(422, "You cannot change your own role or status.")
    changes = data.model_dump(exclude_unset=True)
    if changes.get("role") is not None:
        m.role = changes["role"]
    if changes.get("status") is not None:
        m.status = changes["status"]
    db.commit()
    return m
