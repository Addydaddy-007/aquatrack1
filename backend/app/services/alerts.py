"""Rules that decide WHEN an in-app notification is needed (pure; the database part is in notifications.py).
These only look at schedules and dates people entered - they never claim to detect blocked filters or failures."""
from datetime import date, datetime, timedelta

from app.services.calculations import maintenance_state


def maintenance_alert(title: str, due: date, today: date, remind_before_days: int):
    """Returns (type, notification title, message, key_kind) or None when nothing is needed yet."""
    state = maintenance_state(due, today, remind_before_days)
    if state == "overdue":
        days = (today - due).days
        return ("maintenance_overdue", f"Overdue: {title}",
                f"'{title}' was due on {due.isoformat()} ({days} day{'s' if days != 1 else ''} ago).", "overdue")
    if state == "due_soon":
        days = (due - today).days
        when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
        return ("maintenance_due", f"Upcoming: {title}", f"'{title}' is due {when} ({due.isoformat()}).", "due")
    return None


def reading_overdue_alert(tank_name: str, last_reading_at: datetime | None, tank_created_at: datetime,
                          now: datetime, interval_days: int):
    """Returns (title, message, key_suffix) when a tank reading is overdue, else None."""
    reference = last_reading_at or tank_created_at
    if now - reference <= timedelta(days=interval_days):
        return None
    if last_reading_at is None:
        return (f"No reading yet: {tank_name}", f"No water level has been recorded for '{tank_name}' yet.", "none")
    days = (now - last_reading_at).days
    return (f"Reading overdue: {tank_name}",
            f"The last recorded level for '{tank_name}' was {days} days ago (reading interval is {interval_days} days).",
            last_reading_at.date().isoformat())
