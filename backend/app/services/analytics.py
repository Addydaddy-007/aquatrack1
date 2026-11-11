"""Aggregation helpers (pure Python). Day/month buckets use India time (IST, no daylight saving)."""
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

SOURCE_CLASS = {
    "manual_physical": "measured",
    "water_meter": "measured",
    "manual_declared": "declared",
    "calculated_estimate": "estimated",
}
CLASSES = ("measured", "declared", "estimated")
CLASS_LABELS = {"measured": "Measured (tank scale / water meter)",
                "declared": "Manually declared",
                "estimated": "Calculated estimate"}


def to_ist_date(dt: datetime) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST).date()


def today_ist() -> date:
    return datetime.now(IST).date()


def classify(data_source: str) -> str:
    return SOURCE_CLASS.get(data_source, "declared")


def totals_by_class(rows) -> dict:
    """rows: iterable of (datetime, litres, data_source, ...)."""
    out = {c: 0.0 for c in CLASSES}
    for r in rows:
        out[classify(r[2])] += float(r[1])
    out = {k: round(v, 2) for k, v in out.items()}
    out["total"] = round(sum(out.values()), 2)
    return out


def daily_totals(rows, start: date, end: date) -> list[dict]:
    sums: dict = defaultdict(float)
    for r in rows:
        d = to_ist_date(r[0])
        if start <= d <= end:
            sums[d] += float(r[1])
    n = (end - start).days + 1
    return [{"date": (start + timedelta(days=i)).isoformat(), "litres": round(sums[start + timedelta(days=i)], 2)}
            for i in range(n)]


def month_keys(today: date, n: int) -> list[str]:
    y, m, keys = today.year, today.month, []
    for _ in range(n):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(keys))


def monthly_totals(rows, today: date, n: int = 12) -> list[dict]:
    keys = month_keys(today, n)
    sums = {k: 0.0 for k in keys}
    for r in rows:
        k = to_ist_date(r[0]).strftime("%Y-%m")
        if k in sums:
            sums[k] += float(r[1])
    return [{"month": k, "litres": round(sums[k], 2)} for k in keys]


def by_purpose(rows) -> list[dict]:
    """rows: (datetime, litres, data_source, purpose)."""
    sums: dict = defaultdict(float)
    for r in rows:
        sums[r[3]] += float(r[1])
    return sorted(({"purpose": k, "litres": round(v, 2)} for k, v in sums.items()), key=lambda x: -x["litres"])


# ---------- report periods ----------
def period_bounds(period: str, ref: date) -> tuple[date, date]:
    if period == "daily":
        return ref, ref
    if period == "weekly":                       # Monday..Sunday containing ref
        start = ref - timedelta(days=ref.weekday())
        return start, start + timedelta(days=6)
    if period == "monthly":
        return ref.replace(day=1), ref.replace(day=calendar.monthrange(ref.year, ref.month)[1])
    if period == "annual":                       # calendar year
        return date(ref.year, 1, 1), date(ref.year, 12, 31)
    raise ValueError("period must be daily, weekly, monthly or annual")


def summarize_report(period: str, start: date, end: date, collections, utilizations) -> dict:
    """Rows: one per day (daily/weekly/monthly) or per month (annual). Each row splits litres by data class."""
    bucket_of = (lambda d: d.strftime("%Y-%m")) if period == "annual" else (lambda d: d.isoformat())
    if period == "annual":
        labels = [f"{start.year}-{m:02d}" for m in range(1, 13)]
    else:
        labels = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    table = {l: {"c": dict.fromkeys(CLASSES, 0.0), "u": dict.fromkeys(CLASSES, 0.0)} for l in labels}
    for side, rows in (("c", collections), ("u", utilizations)):
        for r in rows:
            d = to_ist_date(r[0])
            if start <= d <= end:
                table[bucket_of(d)][side][classify(r[2])] += float(r[1])
    cols = ["Period"] + [f"Collected - {c}" for c in CLASSES] + [f"Utilized - {c}" for c in CLASSES]
    rows_out, tot = [], [0.0] * 6
    for l in labels:
        vals = [table[l]["c"][c] for c in CLASSES] + [table[l]["u"][c] for c in CLASSES]
        rows_out.append([l] + [round(v, 2) for v in vals])
        tot = [a + b for a, b in zip(tot, vals)]
    return {"columns": cols, "rows": rows_out, "totals_row": ["Total"] + [round(v, 2) for v in tot]}
