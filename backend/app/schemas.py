"""Pydantic models: validate everything that comes in, and shape everything that goes out."""
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["admin", "staff", "resident"]
Status = Literal["pending", "active", "disabled"]
InputType = Literal["litres", "cm", "percent"]
Shape = Literal["cylindrical", "rectangular", "irregular"]
DataSource = Literal["manual_physical", "manual_declared", "water_meter", "calculated_estimate"]
Purpose = Literal["garden_irrigation", "common_area_cleaning", "vehicle_washing", "other"]
CollectionSource = Literal["rooftop", "surface_runoff", "other"]
Category = Literal["filter_cleaning", "tank_inspection", "catchment_cleaning", "pipe_inspection", "general"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- people & society ----
class SocietyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    address: str | None = Field(default=None, max_length=300)


class JoinRequest(BaseModel):
    invite_code: str = Field(min_length=4, max_length=32)


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=30)


class SocietyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    address: str | None = Field(default=None, max_length=300)
    catchment_area_m2: float | None = Field(default=None, gt=0, le=1_000_000)
    runoff_coefficient: float | None = Field(default=None, gt=0, le=1)
    water_cost_per_kl: float | None = Field(default=None, ge=0, le=10_000)
    reading_interval_days: int | None = Field(default=None, ge=1, le=90)
    discrepancy_threshold_pct: float | None = Field(default=None, ge=0, le=100)


class SocietyOut(ORM):
    id: uuid.UUID
    name: str
    address: str | None
    city: str
    catchment_area_m2: float | None
    runoff_coefficient: float
    water_cost_per_kl: float | None
    reading_interval_days: int
    discrepancy_threshold_pct: float


class MemberOut(ORM):
    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    email: str | None
    role: Role
    status: Status


class MemberUpdate(BaseModel):
    role: Role | None = None
    status: Status | None = None


# ---- tanks ----
class TankBase(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    capacity_litres: float = Field(gt=0, le=100_000_000)
    shape: Shape = "cylindrical"
    total_height_cm: float | None = Field(default=None, gt=0, le=10_000)
    default_input_unit: InputType = "litres"


class TankCreate(TankBase):
    @model_validator(mode="after")
    def _check(self):
        if self.default_input_unit == "cm" and self.shape != "irregular" and not self.total_height_cm:
            raise ValueError("total_height_cm is required for centimetre readings on a regular tank")
        return self


class TankUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    capacity_litres: float | None = Field(default=None, gt=0, le=100_000_000)
    shape: Shape | None = None
    total_height_cm: float | None = Field(default=None, gt=0, le=10_000)
    default_input_unit: InputType | None = None
    is_active: bool | None = None


class TankOut(ORM):
    id: uuid.UUID
    name: str
    capacity_litres: float
    shape: Shape
    total_height_cm: float | None
    default_input_unit: InputType
    is_active: bool


class CalibrationPoint(BaseModel):
    height_cm: float = Field(ge=0, le=10_000)
    volume_litres: float = Field(ge=0, le=100_000_000)


# ---- water records ----
class ReadingIn(BaseModel):
    tank_id: uuid.UUID
    measured_at: datetime
    input_type: InputType
    input_value: float = Field(ge=0, le=100_000_000)
    remarks: str | None = Field(default=None, max_length=500)


class ReadingUpdate(BaseModel):
    measured_at: datetime | None = None
    input_type: InputType | None = None
    input_value: float | None = Field(default=None, ge=0, le=100_000_000)
    remarks: str | None = Field(default=None, max_length=500)
    is_voided: bool | None = None


class ReadingOut(ORM):
    id: uuid.UUID
    tank_id: uuid.UUID
    tank_name: str
    measured_at: datetime
    input_type: InputType
    input_value: float
    volume_litres: float
    data_source: DataSource
    remarks: str | None
    recorded_by_name: str
    is_voided: bool


class CollectionIn(BaseModel):
    tank_id: uuid.UUID
    collected_at: datetime
    data_source: DataSource
    source: CollectionSource = "rooftop"
    volume_litres: float | None = Field(default=None, gt=0, le=100_000_000)   # not needed for estimates
    rainfall_mm: float | None = Field(default=None, ge=0, le=2000)
    catchment_area_m2: float | None = Field(default=None, gt=0, le=1_000_000)  # defaults to the society value
    runoff_coefficient: float | None = Field(default=None, gt=0, le=1)         # defaults to the society value
    remarks: str | None = Field(default=None, max_length=500)


class CollectionUpdate(BaseModel):
    collected_at: datetime | None = None
    data_source: DataSource | None = None
    source: CollectionSource | None = None
    volume_litres: float | None = Field(default=None, gt=0, le=100_000_000)
    rainfall_mm: float | None = Field(default=None, ge=0, le=2000)
    catchment_area_m2: float | None = Field(default=None, gt=0, le=1_000_000)
    runoff_coefficient: float | None = Field(default=None, gt=0, le=1)
    remarks: str | None = Field(default=None, max_length=500)
    is_voided: bool | None = None


class CollectionOut(ORM):
    id: uuid.UUID
    tank_id: uuid.UUID
    tank_name: str
    collected_at: datetime
    volume_litres: float
    source: CollectionSource
    data_source: DataSource
    rainfall_mm: float | None
    catchment_area_m2: float | None
    runoff_coefficient: float | None
    remarks: str | None
    recorded_by_name: str
    is_voided: bool


class UtilizationIn(BaseModel):
    tank_id: uuid.UUID
    used_at: datetime
    purpose: Purpose
    data_source: DataSource
    volume_litres: float | None = Field(default=None, gt=0, le=100_000_000)   # not needed for meter readings
    meter_start: float | None = Field(default=None, ge=0)
    meter_end: float | None = Field(default=None, ge=0)
    meter_unit: Literal["litres", "kl"] | None = None
    remarks: str | None = Field(default=None, max_length=500)


class UtilizationUpdate(BaseModel):
    used_at: datetime | None = None
    purpose: Purpose | None = None
    data_source: DataSource | None = None
    volume_litres: float | None = Field(default=None, gt=0, le=100_000_000)
    meter_start: float | None = Field(default=None, ge=0)
    meter_end: float | None = Field(default=None, ge=0)
    meter_unit: Literal["litres", "kl"] | None = None
    remarks: str | None = Field(default=None, max_length=500)
    is_voided: bool | None = None


class UtilizationOut(ORM):
    id: uuid.UUID
    tank_id: uuid.UUID
    tank_name: str
    used_at: datetime
    volume_litres: float
    purpose: Purpose
    data_source: DataSource
    meter_start: float | None
    meter_end: float | None
    meter_unit: str | None
    remarks: str | None
    recorded_by_name: str
    is_voided: bool


class EstimateIn(BaseModel):
    rainfall_mm: float = Field(ge=0, le=2000)
    catchment_area_m2: float | None = Field(default=None, gt=0, le=1_000_000)
    runoff_coefficient: float | None = Field(default=None, gt=0, le=1)


# ---- maintenance ----
class TaskCreate(BaseModel):
    category: Category
    title: str = Field(min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    assigned_to: uuid.UUID | None = None
    due_date: date
    interval_days: int | None = Field(default=None, ge=1, le=730)
    remind_before_days: int = Field(default=7, ge=0, le=60)


class TaskUpdate(BaseModel):
    category: Category | None = None
    title: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    assigned_to: uuid.UUID | None = None
    due_date: date | None = None
    interval_days: int | None = Field(default=None, ge=1, le=730)
    remind_before_days: int | None = Field(default=None, ge=0, le=60)
    status: Literal["pending", "cancelled"] | None = None


class TaskOut(ORM):
    id: uuid.UUID
    category: Category
    title: str
    description: str | None
    assigned_to: uuid.UUID | None
    assigned_to_name: str | None
    due_date: date
    interval_days: int | None
    remind_before_days: int
    status: str
    state: str = "scheduled"      # overdue | due_soon | scheduled | completed | cancelled (computed)


class CompleteIn(BaseModel):
    completed_on: date | None = None
    notes: str | None = Field(default=None, max_length=1000)


class HistoryOut(ORM):
    id: uuid.UUID
    schedule_id: uuid.UUID
    title: str
    category: Category
    due_date_was: date
    completed_on: date
    completed_by_name: str
    notes: str | None


class NotificationOut(ORM):
    id: uuid.UUID
    type: str
    title: str
    message: str
    read_at: datetime | None
    created_at: datetime
