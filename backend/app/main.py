"""AquaTrack API entry point. Run locally:  uvicorn app.main:app --reload   (docs at /docs)"""
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api import dashboard, internal, maintenance, notifications, reports, society, tanks, water
from app.core.config import get_settings
from app.core.database import get_engine

log = logging.getLogger("aquatrack")
settings = get_settings()

app = FastAPI(title="AquaTrack API", version="1.0.0",
              description="Digital Rainwater Harvesting logbook. Zero-IoT: every value comes from manual entries or calculations.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=False,                 # we use an Authorization header, not cookies
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

for r in (society.router, tanks.router, water.router, maintenance.router, notifications.router,
          dashboard.router, reports.router, internal.router):
    app.include_router(r, prefix="/api/v1")


@app.exception_handler(SQLAlchemyError)
async def db_error(request: Request, exc: SQLAlchemyError):
    log.exception("Database error on %s", request.url.path)          # details stay in the server log
    return JSONResponse(status_code=500, content={"detail": "A database error occurred. Please try again."})


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}


@app.get("/health/db", tags=["health"])
def health_db():
    with get_engine().connect() as conn:
        conn.execute(text("select 1"))
    return {"status": "ok", "database": "reachable"}
