"""Request dependencies = the security gate.

Flow for every protected request:
  1. read the Bearer token and verify it (security.decode_token)
  2. make sure a profile row exists for the user id in the token
  3. load the user's ACTIVE membership from OUR database -> society_id and role
  4. require_roles(...) rejects roles that are not allowed
Routes then filter every query by ctx.society_id, so one society can never see another's data.
The society and role are never read from the request body, query string or token metadata.
"""
import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models import Membership, Profile

bearer = HTTPBearer(auto_error=False)


@dataclass
class Ctx:
    user_id: uuid.UUID
    society_id: uuid.UUID
    role: str
    membership_id: uuid.UUID


def get_claims(creds: HTTPAuthorizationCredentials | None = Depends(bearer),
               settings: Settings = Depends(get_settings)) -> dict:
    if creds is None:
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    try:
        return security.decode_token(creds.credentials, settings.supabase_jwt_secret, settings.jwks_url)
    except security.AuthError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"})


def get_profile(claims: dict = Depends(get_claims), db: Session = Depends(get_db)) -> Profile:
    try:
        uid = uuid.UUID(claims["sub"])
    except ValueError:
        raise HTTPException(401, "Invalid token subject")
    profile = db.get(Profile, uid)
    if profile is None:                       # first request after sign-up
        meta = claims.get("user_metadata") or {}
        email = claims.get("email")
        name = (meta.get("full_name") or meta.get("name") or (email or "New user").split("@")[0])[:120]
        profile = Profile(id=uid, full_name=name, email=email)
        db.add(profile)
        db.commit()
    return profile


def get_ctx(profile: Profile = Depends(get_profile), db: Session = Depends(get_db)) -> Ctx:
    m = db.scalar(select(Membership).where(Membership.user_id == profile.id))
    if m is None:
        raise HTTPException(403, detail={"code": "no_society", "message": "Create or join a society first."})
    if m.status != "active":
        raise HTTPException(403, detail={"code": f"membership_{m.status}",
                                         "message": "Your membership is not active. Ask your society administrator."})
    return Ctx(user_id=profile.id, society_id=m.society_id, role=m.role, membership_id=m.id)


def require_roles(*roles: str):
    def checker(ctx: Ctx = Depends(get_ctx)) -> Ctx:
        if ctx.role not in roles:
            raise HTTPException(403, f"This action requires one of: {', '.join(roles)}")
        return ctx
    return checker
