"""Zero-IoT water maths. Pure Python (no database, no web framework) so it is easy to test and explain.

Nothing here "detects" water: every function turns numbers a person typed in into other numbers.
"""
from datetime import date, timedelta

LITRES_PER_KL = 1000.0
REGULAR_SHAPES = ("cylindrical", "rectangular")


class CalculationError(ValueError):
    """Raised for input that cannot produce a trustworthy number. The message is shown to the user."""


def _r(x: float) -> float:
    return round(float(x), 2)


# ---------- calibration ----------
def validate_calibration(points: list[tuple[float, float]], capacity: float) -> list[tuple[float, float]]:
    pts = sorted((float(h), float(v)) for h, v in points)
    if len(pts) < 2:
        raise CalculationError("A calibration table needs at least two points.")
    heights = [h for h, _ in pts]
    if len(set(heights)) != len(heights):
        raise CalculationError("Each height may appear only once in the calibration table.")
    if any(h < 0 or v < 0 for h, v in pts):
        raise CalculationError("Heights and volumes cannot be negative.")
    for (_, v1), (_, v2) in zip(pts, pts[1:]):
        if v2 < v1:
            raise CalculationError("Volume must not decrease as height increases.")
    if pts[-1][1] > capacity + 0.01:
        raise CalculationError("Calibration volume exceeds the tank capacity.")
    return pts


def interpolate(points: list[tuple[float, float]], height: float) -> float:
    """Linear interpolation between neighbouring calibration points (never extrapolates)."""
    pts = sorted(points)
    if len(pts) < 2:
        raise CalculationError("This tank has no calibration table.")
    low, high = pts[0][0], pts[-1][0]
    if height < low or height > high:
        raise CalculationError(f"{height:g} cm is outside the calibrated range ({low:g}-{high:g} cm). "
                               "Extend the calibration table or check the reading.")
    for (h1, v1), (h2, v2) in zip(pts, pts[1:]):
        if h1 <= height <= h2:
            return v1 if h2 == h1 else v1 + (v2 - v1) * (height - h1) / (h2 - h1)
    raise CalculationError("Could not interpolate this height.")  # unreachable, defensive


# ---------- tank reading -> litres ----------
def volume_from_input(input_type: str, value: float, capacity: float, shape: str = "cylindrical",
                      total_height_cm: float | None = None,
                      calibration: list[tuple[float, float]] | None = None) -> float:
    if value < 0:
        raise CalculationError("A reading cannot be negative.")
    if input_type == "litres":
        volume = value
    elif input_type == "percent":
        if value > 100:
            raise CalculationError("Percentage cannot exceed 100.")
        volume = capacity * value / 100.0
    elif input_type == "cm":
        if calibration and len(calibration) >= 2:
            volume = interpolate(calibration, value)
        elif shape in REGULAR_SHAPES and total_height_cm:
            if value > total_height_cm:
                raise CalculationError("Water height exceeds the tank's total height.")
            # Valid only because a regular tank has the same cross-section at every height.
            volume = capacity * value / total_height_cm
        else:
            raise CalculationError("This tank needs a calibration table before centimetre readings can be converted. "
                                   "Irregular tanks cannot use a simple height-to-volume ratio.")
    else:
        raise CalculationError("Unknown measurement type.")
    if volume > capacity + 0.01:
        raise CalculationError(f"That reading ({_r(volume)} L) is more than the tank capacity ({_r(capacity)} L).")
    return _r(min(volume, capacity))


# ---------- collection & meter ----------
def estimate_collection(rainfall_mm: float, catchment_area_m2: float, runoff_coefficient: float) -> float:
    """Litres = rainfall (mm) x area (m2) x runoff coefficient. 1 mm of rain on 1 m2 is 1 litre."""
    if rainfall_mm < 0:
        raise CalculationError("Rainfall cannot be negative.")
    if catchment_area_m2 <= 0:
        raise CalculationError("Catchment area must be greater than zero.")
    if not 0 < runoff_coefficient <= 1:
        raise CalculationError("Runoff coefficient must be between 0 and 1.")
    return _r(rainfall_mm * catchment_area_m2 * runoff_coefficient)


def meter_consumption(start: float, end: float, unit: str) -> float:
    if unit not in ("litres", "kl"):
        raise CalculationError("Meter unit must be litres or kl.")
    if start < 0 or end < 0:
        raise CalculationError("Meter readings cannot be negative.")
    if end < start:
        raise CalculationError("The new meter reading is lower than the previous one. "
                               "Meter roll-over is not handled - please correct the readings.")
    return _r((end - start) * (LITRES_PER_KL if unit == "kl" else 1.0))


# ---------- water balance ----------
def water_balance(opening: float, collected: float, utilized: float, actual_closing: float,
                  capacity: float, threshold_pct: float = 10.0) -> dict:
    """Opening + collected - utilized = expected closing. The gap to the physical reading is reported as an
    UNEXPLAINED discrepancy; it is never labelled as consumption (it could equally be unrecorded inflow,
    overflow, leakage or a measurement error)."""
    expected = opening + collected - utilized
    discrepancy = actual_closing - expected
    pct = abs(discrepancy) / capacity * 100.0 if capacity else 0.0
    if abs(discrepancy) < 0.005:
        direction = "matches"
    else:
        direction = "actual_higher" if discrepancy > 0 else "actual_lower"
    warnings = []
    if expected < 0:
        warnings.append("Recorded utilization is larger than the water that could have been available - records may be incomplete.")
    if expected > capacity:
        warnings.append("Expected stock is above tank capacity - water may have overflowed.")
    return {
        "opening_litres": _r(opening), "collected_litres": _r(collected), "utilized_litres": _r(utilized),
        "expected_closing_litres": _r(expected), "actual_closing_litres": _r(actual_closing),
        "discrepancy_litres": _r(discrepancy), "discrepancy_pct_of_capacity": _r(pct),
        "direction": direction, "flagged": pct > threshold_pct, "warnings": warnings,
        "interpretation": "Cause of any difference is unknown; it is not counted as consumption.",
    }


# ---------- maintenance ----------
def maintenance_state(due: date, today: date, remind_before_days: int = 7) -> str:
    if due < today:
        return "overdue"
    return "due_soon" if (due - today).days <= remind_before_days else "scheduled"


def next_due_date(completed_on: date, interval_days: int) -> date:
    """Recurring tasks restart from the day the work was actually done."""
    return completed_on + timedelta(days=interval_days)
