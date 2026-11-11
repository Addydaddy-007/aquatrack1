"""Reports with CSV / PDF export. Measured, declared and estimated litres are always in separate columns."""
from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dashboard import _savings, _water_rows
from app.core.database import get_db
from app.deps import Ctx, get_ctx, require_roles
from app.models import MaintenanceHistory, MaintenanceSchedule, Society
from app.services import analytics, calculations, reporting

router = APIRouter(prefix="/reports", tags=["reports"])
PERIODS = "^(daily|weekly|monthly|annual)$"
FORMATS = "^(json|csv|pdf)$"
NOTES = ["Measured = read from a tank scale or water meter. Declared = quantity stated by staff. "
         "Estimated = calculated (e.g. rainfall x catchment area x runoff coefficient).",
         "AquaTrack has no sensors: every figure originates from a manual entry or a calculation."]


def _respond(report: dict, fmt: str, filename: str):
    if fmt == "csv":
        return Response(reporting.to_csv(report), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'})
    if fmt == "pdf":
        return Response(reporting.to_pdf(report), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'})
    return report


@router.get("/water")
def water_report(period: str = Query(pattern=PERIODS), ref_date: date | None = None,
                 format: str = Query(default="json", pattern=FORMATS), ctx: Ctx = Depends(get_ctx),
                 db: Session = Depends(get_db)):
    society = db.get(Society, ctx.society_id)
    ref = ref_date or analytics.today_ist()
    start, end = analytics.period_bounds(period, ref)
    coll, util = _water_rows(db, ctx.society_id)
    body = analytics.summarize_report(period, start, end, coll, util)
    notes = list(NOTES)
    if period == "annual":
        in_year = [r for r in util if start <= analytics.to_ist_date(r[0]) <= end]
        s = _savings(society, analytics.totals_by_class(in_year))
        extra = f" (about INR {s['estimated_value_inr']:,.0f} at INR {float(society.water_cost_per_kl):g}/kL)" if "estimated_value_inr" in s else ""
        notes.append(f"Estimated water saved this year: {s['litres']:,.0f} L{extra}. This is an estimate based on "
                     "recorded harvested-water utilization, not a verified municipal saving.")
    report = {"title": f"{period.capitalize()} water report", "subtitle": f"{society.name} | {start:%d/%m/%Y} to {end:%d/%m/%Y} | litres",
              "notes": notes, **body}
    return _respond(report, format, f"aquatrack-{period}-{start.isoformat()}")


@router.get("/maintenance")
def maintenance_report(ref_date: date | None = None, period: str = Query(default="monthly", pattern=PERIODS),
                       format: str = Query(default="json", pattern=FORMATS),
                       ctx: Ctx = Depends(require_roles("admin", "staff")), db: Session = Depends(get_db)):
    society = db.get(Society, ctx.society_id)
    today = analytics.today_ist()
    start, end = analytics.period_bounds(period, ref_date or today)
    done = db.scalars(select(MaintenanceHistory).where(
        MaintenanceHistory.society_id == ctx.society_id, MaintenanceHistory.completed_on >= start,
        MaintenanceHistory.completed_on <= end).order_by(MaintenanceHistory.completed_on)).unique().all()
    open_tasks = db.scalars(select(MaintenanceSchedule).where(
        MaintenanceSchedule.society_id == ctx.society_id, MaintenanceSchedule.status == "pending")
        .order_by(MaintenanceSchedule.due_date)).unique().all()
    rows = [[h.completed_on.isoformat(), h.title, h.category, "Completed", h.due_date_was.isoformat(),
             h.completed_by_name, h.notes or ""] for h in done]
    rows += [["", t.title, t.category, calculations.maintenance_state(t.due_date, today, t.remind_before_days).replace("_", " ").title(),
              t.due_date.isoformat(), t.assigned_to_name or "Unassigned", ""] for t in open_tasks]
    report = {"title": f"{period.capitalize()} maintenance report", "subtitle": f"{society.name} | {start:%d/%m/%Y} to {end:%d/%m/%Y}",
              "notes": ["Completed work in the period, followed by tasks that are still open.",
                        "Status comes from scheduled dates and staff entries; AquaTrack does not detect blockages or equipment faults."],
              "columns": ["Completed on", "Task", "Category", "Status", "Due date", "Person", "Notes"], "rows": rows, "totals_row": None}
    return _respond(report, format, f"aquatrack-maintenance-{start.isoformat()}")
