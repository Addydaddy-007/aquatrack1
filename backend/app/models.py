"""SQLAlchemy models. They mirror database/migrations/001_init.sql (the SQL file is the source of truth for
constraints; if you change one, change the other)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text,
                        UniqueConstraint, Uuid, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Json = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


def _pk():
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


def _fk(target, nullable=False, **kw):
    return mapped_column(Uuid, ForeignKey(target, **kw), nullable=nullable)


class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    full_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Society(Base):
    __tablename__ = "societies"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str] = mapped_column(Text, default="Mira-Bhayandar")
    invite_code: Mapped[str] = mapped_column(Text, unique=True)
    catchment_area_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    runoff_coefficient: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0.800"))
    water_cost_per_kl: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    reading_interval_days: Mapped[int] = mapped_column(Integer, default=7)
    discrepancy_threshold_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("10"))
    created_by: Mapped[uuid.UUID | None] = _fk("profiles.id", nullable=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Membership(Base):
    __tablename__ = "society_memberships"
    id: Mapped[uuid.UUID] = _pk()
    society_id: Mapped[uuid.UUID] = _fk("societies.id", ondelete="CASCADE")
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("profiles.id", ondelete="CASCADE"), unique=True)
    role: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    profile: Mapped[Profile] = relationship(lazy="joined")

    @property
    def full_name(self): return self.profile.full_name

    @property
    def email(self): return self.profile.email


class Tank(Base):
    __tablename__ = "tanks"
    __table_args__ = (UniqueConstraint("society_id", "name"),)
    id: Mapped[uuid.UUID] = _pk()
    society_id: Mapped[uuid.UUID] = _fk("societies.id", ondelete="CASCADE")
    name: Mapped[str] = mapped_column(Text)
    capacity_litres: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    shape: Mapped[str] = mapped_column(String, default="cylindrical")
    total_height_cm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    default_input_unit: Mapped[str] = mapped_column(String, default="litres")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class TankCalibration(Base):
    __tablename__ = "tank_calibrations"
    __table_args__ = (UniqueConstraint("tank_id", "height_cm"),)
    id: Mapped[uuid.UUID] = _pk()
    tank_id: Mapped[uuid.UUID] = _fk("tanks.id", ondelete="CASCADE")
    height_cm: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    volume_litres: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class _WaterRecord:
    """Shared columns/helpers for readings, collections and utilizations."""
    @property
    def tank_name(self): return self.tank.name

    @property
    def recorded_by_name(self): return self.recorder.full_name


class WaterReading(_WaterRecord, Base):
    __tablename__ = "water_readings"
    __table_args__ = (UniqueConstraint("tank_id", "measured_at"),)
    id: Mapped[uuid.UUID] = _pk()
    society_id: Mapped[uuid.UUID] = _fk("societies.id", ondelete="CASCADE")
    tank_id: Mapped[uuid.UUID] = _fk("tanks.id", ondelete="CASCADE")
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input_type: Mapped[str] = mapped_column(String)
    input_value: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    volume_litres: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    data_source: Mapped[str] = mapped_column(String, default="manual_physical")
    remarks: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[uuid.UUID] = _fk("profiles.id")
    is_voided: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    tank: Mapped[Tank] = relationship(lazy="joined")
    recorder: Mapped[Profile] = relationship(lazy="joined", foreign_keys=[recorded_by])


class WaterCollection(_WaterRecord, Base):
    __tablename__ = "water_collections"
    id: Mapped[uuid.UUID] = _pk()
    society_id: Mapped[uuid.UUID] = _fk("societies.id", ondelete="CASCADE")
    tank_id: Mapped[uuid.UUID] = _fk("tanks.id", ondelete="CASCADE")
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    volume_litres: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    source: Mapped[str] = mapped_column(String, default="rooftop")
    data_source: Mapped[str] = mapped_column(String)
    rainfall_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    catchment_area_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    runoff_coefficient: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    remarks: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[uuid.UUID] = _fk("profiles.id")
    is_voided: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    tank: Mapped[Tank] = relationship(lazy="joined")
    recorder: Mapped[Profile] = relationship(lazy="joined", foreign_keys=[recorded_by])


class WaterUtilization(_WaterRecord, Base):
    __tablename__ = "water_utilizations"
    id: Mapped[uuid.UUID] = _pk()
    society_id: Mapped[uuid.UUID] = _fk("societies.id", ondelete="CASCADE")
    tank_id: Mapped[uuid.UUID] = _fk("tanks.id", ondelete="CASCADE")
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    volume_litres: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    purpose: Mapped[str] = mapped_column(String)
    data_source: Mapped[str] = mapped_column(String)
    meter_start: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    meter_end: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    meter_unit: Mapped[str | None] = mapped_column(String)
    remarks: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[uuid.UUID] = _fk("profiles.id")
    is_voided: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    tank: Mapped[Tank] = relationship(lazy="joined")
    recorder: Mapped[Profile] = relationship(lazy="joined", foreign_keys=[recorded_by])


class MaintenanceSchedule(Base):
    __tablename__ = "maintenance_schedules"
    id: Mapped[uuid.UUID] = _pk()
    society_id: Mapped[uuid.UUID] = _fk("societies.id", ondelete="CASCADE")
    category: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    assigned_to: Mapped[uuid.UUID | None] = _fk("profiles.id", nullable=True, ondelete="SET NULL")
    due_date: Mapped[date] = mapped_column(Date)
    interval_days: Mapped[int | None] = mapped_column(Integer)
    remind_before_days: Mapped[int] = mapped_column(Integer, default=7)
    status: Mapped[str] = mapped_column(String, default="pending")
    created_by: Mapped[uuid.UUID | None] = _fk("profiles.id", nullable=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    assignee: Mapped[Profile | None] = relationship(lazy="joined", foreign_keys=[assigned_to])

    @property
    def assigned_to_name(self): return self.assignee.full_name if self.assignee else None


class MaintenanceHistory(Base):
    __tablename__ = "maintenance_history"
    id: Mapped[uuid.UUID] = _pk()
    society_id: Mapped[uuid.UUID] = _fk("societies.id", ondelete="CASCADE")
    schedule_id: Mapped[uuid.UUID] = _fk("maintenance_schedules.id", ondelete="CASCADE")
    due_date_was: Mapped[date] = mapped_column(Date)
    completed_on: Mapped[date] = mapped_column(Date)
    completed_by: Mapped[uuid.UUID] = _fk("profiles.id")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    schedule: Mapped[MaintenanceSchedule] = relationship(lazy="joined")
    completer: Mapped[Profile] = relationship(lazy="joined", foreign_keys=[completed_by])

    @property
    def title(self): return self.schedule.title

    @property
    def category(self): return self.schedule.category

    @property
    def completed_by_name(self): return self.completer.full_name


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key"),)
    id: Mapped[uuid.UUID] = _pk()
    society_id: Mapped[uuid.UUID] = _fk("societies.id", ondelete="CASCADE")
    user_id: Mapped[uuid.UUID] = _fk("profiles.id", ondelete="CASCADE")
    type: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = _pk()
    society_id: Mapped[uuid.UUID] = _fk("societies.id", ondelete="CASCADE")
    actor_id: Mapped[uuid.UUID | None] = _fk("profiles.id", nullable=True, ondelete="SET NULL")
    table_name: Mapped[str] = mapped_column(Text)
    record_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String)
    old_values: Mapped[dict | None] = mapped_column(Json)
    new_values: Mapped[dict | None] = mapped_column(Json)
    created_at: Mapped[datetime] = _created()
