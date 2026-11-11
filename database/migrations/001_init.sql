-- AquaTrack schema v1 (PostgreSQL / Supabase). Run once in Supabase > SQL Editor.
-- Design notes:
--  * Text + CHECK constraints are used instead of PG enums so values are easy to change later.
--  * Every society-owned table carries society_id so tenant isolation is a simple filter.
--  * Records are never hard-deleted: corrections are edits (is_voided = true invalidates) plus audit_logs.
--  * The backend connects with the database owner role (bypasses RLS). RLS is enabled with NO policies,
--    so Supabase's public REST API (anon / authenticated keys) can read nothing.

create or replace function set_updated_at() returns trigger as $$
begin new.updated_at = now(); return new; end;
$$ language plpgsql;

-- profiles.id equals the Supabase auth.users id (the "sub" claim of the JWT)
create table profiles (
  id          uuid primary key,
  full_name   text not null,
  email       text,
  phone       text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table societies (
  id                        uuid primary key default gen_random_uuid(),
  name                      text not null,
  address                   text,
  city                      text not null default 'Mira-Bhayandar',
  invite_code               text not null unique,
  catchment_area_m2         numeric(12,2) check (catchment_area_m2 > 0),
  runoff_coefficient        numeric(4,3) not null default 0.800 check (runoff_coefficient > 0 and runoff_coefficient <= 1),
  water_cost_per_kl         numeric(10,2) check (water_cost_per_kl >= 0),
  reading_interval_days     integer not null default 7 check (reading_interval_days > 0),
  discrepancy_threshold_pct numeric(5,2) not null default 10 check (discrepancy_threshold_pct >= 0),
  created_by                uuid references profiles(id),
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now()
);

-- One society per user in this version (unique user_id). The role lives HERE, never in the JWT.
create table society_memberships (
  id          uuid primary key default gen_random_uuid(),
  society_id  uuid not null references societies(id) on delete cascade,
  user_id     uuid not null unique references profiles(id) on delete cascade,
  role        text not null check (role in ('admin','staff','resident')),
  status      text not null default 'pending' check (status in ('pending','active','disabled')),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index on society_memberships (society_id, role, status);

create table tanks (
  id                  uuid primary key default gen_random_uuid(),
  society_id          uuid not null references societies(id) on delete cascade,
  name                text not null,
  capacity_litres     numeric(12,2) not null check (capacity_litres > 0),
  shape               text not null default 'cylindrical' check (shape in ('cylindrical','rectangular','irregular')),
  total_height_cm     numeric(8,2) check (total_height_cm > 0),
  default_input_unit  text not null default 'litres' check (default_input_unit in ('litres','cm','percent')),
  is_active           boolean not null default true,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (society_id, name)
);

-- Strapping table: measured height (cm) -> volume (litres). Linear interpolation between points.
create table tank_calibrations (
  id             uuid primary key default gen_random_uuid(),
  tank_id        uuid not null references tanks(id) on delete cascade,
  height_cm      numeric(8,2) not null check (height_cm >= 0),
  volume_litres  numeric(12,2) not null check (volume_litres >= 0),
  unique (tank_id, height_cm)
);

-- data_source values: manual_physical | manual_declared | water_meter | calculated_estimate
create table water_readings (
  id             uuid primary key default gen_random_uuid(),
  society_id     uuid not null references societies(id) on delete cascade,
  tank_id        uuid not null references tanks(id) on delete cascade,
  measured_at    timestamptz not null,
  input_type     text not null check (input_type in ('litres','cm','percent')),
  input_value    numeric(12,2) not null check (input_value >= 0),
  volume_litres  numeric(12,2) not null check (volume_litres >= 0),
  data_source    text not null default 'manual_physical' check (data_source = 'manual_physical'),
  remarks        text,
  recorded_by    uuid not null references profiles(id),
  is_voided      boolean not null default false,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (tank_id, measured_at)
);
create index on water_readings (society_id, measured_at desc);

create table water_collections (
  id                  uuid primary key default gen_random_uuid(),
  society_id          uuid not null references societies(id) on delete cascade,
  tank_id             uuid not null references tanks(id) on delete cascade,
  collected_at        timestamptz not null,
  volume_litres       numeric(12,2) not null check (volume_litres >= 0),
  source              text not null default 'rooftop' check (source in ('rooftop','surface_runoff','other')),
  data_source         text not null check (data_source in ('manual_physical','manual_declared','water_meter','calculated_estimate')),
  rainfall_mm         numeric(8,2) check (rainfall_mm >= 0),
  catchment_area_m2   numeric(12,2) check (catchment_area_m2 > 0),
  runoff_coefficient  numeric(4,3) check (runoff_coefficient > 0 and runoff_coefficient <= 1),
  remarks             text,
  recorded_by         uuid not null references profiles(id),
  is_voided           boolean not null default false,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  check (data_source <> 'calculated_estimate' or rainfall_mm is not null)
);
create index on water_collections (society_id, collected_at desc);

create table water_utilizations (
  id             uuid primary key default gen_random_uuid(),
  society_id     uuid not null references societies(id) on delete cascade,
  tank_id        uuid not null references tanks(id) on delete cascade,
  used_at        timestamptz not null,
  volume_litres  numeric(12,2) not null check (volume_litres >= 0),
  purpose        text not null check (purpose in ('garden_irrigation','common_area_cleaning','vehicle_washing','other')),
  data_source    text not null check (data_source in ('manual_physical','manual_declared','water_meter','calculated_estimate')),
  meter_start    numeric(14,3) check (meter_start >= 0),
  meter_end      numeric(14,3) check (meter_end >= 0),
  meter_unit     text check (meter_unit in ('litres','kl')),
  remarks        text,
  recorded_by    uuid not null references profiles(id),
  is_voided      boolean not null default false,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  check (meter_end is null or meter_start is null or meter_end >= meter_start)
);
create index on water_utilizations (society_id, used_at desc);

-- A schedule row is the CURRENT state of one (possibly recurring) task. Completions go to maintenance_history.
create table maintenance_schedules (
  id                  uuid primary key default gen_random_uuid(),
  society_id          uuid not null references societies(id) on delete cascade,
  category            text not null check (category in ('filter_cleaning','tank_inspection','catchment_cleaning','pipe_inspection','general')),
  title               text not null,
  description         text,
  assigned_to         uuid references profiles(id) on delete set null,
  due_date            date not null,
  interval_days       integer check (interval_days > 0),
  remind_before_days  integer not null default 7 check (remind_before_days between 0 and 60),
  status              text not null default 'pending' check (status in ('pending','completed','cancelled')),
  created_by          uuid references profiles(id),
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);
create index on maintenance_schedules (society_id, status, due_date);

create table maintenance_history (
  id             uuid primary key default gen_random_uuid(),
  society_id     uuid not null references societies(id) on delete cascade,
  schedule_id    uuid not null references maintenance_schedules(id) on delete cascade,
  due_date_was   date not null,
  completed_on   date not null,
  completed_by   uuid not null references profiles(id),
  notes          text,
  created_at     timestamptz not null default now()
);
create index on maintenance_history (society_id, completed_on desc);

create table notifications (
  id          uuid primary key default gen_random_uuid(),
  society_id  uuid not null references societies(id) on delete cascade,
  user_id     uuid not null references profiles(id) on delete cascade,
  type        text not null check (type in ('reading_overdue','maintenance_due','maintenance_overdue','new_reading','balance_discrepancy','monthly_report')),
  title       text not null,
  message     text not null,
  dedupe_key  text not null,
  read_at     timestamptz,
  created_at  timestamptz not null default now(),
  unique (user_id, dedupe_key)   -- makes alert generation idempotent
);
create index on notifications (user_id, read_at, created_at desc);

create table audit_logs (
  id          uuid primary key default gen_random_uuid(),
  society_id  uuid not null references societies(id) on delete cascade,
  actor_id    uuid references profiles(id) on delete set null,
  table_name  text not null,
  record_id   uuid not null,
  action      text not null check (action in ('create','update','void')),
  old_values  jsonb,
  new_values  jsonb,
  created_at  timestamptz not null default now()
);
create index on audit_logs (society_id, created_at desc);
create index on audit_logs (table_name, record_id);

do $$
declare t text;
begin
  foreach t in array array['profiles','societies','society_memberships','tanks','water_readings',
                           'water_collections','water_utilizations','maintenance_schedules'] loop
    execute format('create trigger trg_%s_updated before update on %I for each row execute function set_updated_at()', t, t);
  end loop;
  foreach t in array array['profiles','societies','society_memberships','tanks','tank_calibrations','water_readings',
                           'water_collections','water_utilizations','maintenance_schedules','maintenance_history',
                           'notifications','audit_logs'] loop
    execute format('alter table %I enable row level security', t);
  end loop;
end $$;
