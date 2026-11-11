# AquaTrack - Digital Rainwater Harvesting Logbook

Zero-IoT, software-only rainwater-harvesting management for housing societies (project for Mira-Bhayandar).
Every number comes from a person's entry (tank scale, water meter, declared quantity) or a calculation - and is labelled that way.

Stack: React + Vite + Tailwind (frontend, **next stage**) - FastAPI + SQLAlchemy (backend) - Supabase PostgreSQL + Auth.
(Your PPT's "Technical Requirements" slide lists PHP/MySQL/XAMPP; this build follows your later brief instead. Mention this to your evaluators.)

## Build status (honest)
| Part | State |
|---|---|
| Database schema (`database/migrations/001_init.sql`) | Written. Not yet run on a real Postgres |
| Calculations, analytics, alert rules, CSV/PDF reports, JWT verification | Written **and unit-tested** (25 passing tests, `backend/tests/test_logic.py`) |
| FastAPI routes, models, auth/role gate, notifications | Written, syntax-checked. **Not yet run** (build sandbox had no internet, so FastAPI/SQLAlchemy/Postgres couldn't be installed) |
| API integration tests (`tests/test_api_integration.py`) | Written, not yet run - run them first (see below) |
| Frontend (landing page, dashboards, forms) | Not built yet - next stage |
| Deployment | Render config written; nothing deployed |

## Folder structure
```
database/migrations/001_init.sql     the whole schema (run in Supabase SQL editor)
backend/app/
  main.py            app + CORS + routers          core/    settings, JWT check, DB session
  deps.py            THE security gate (token -> membership -> role)
  models.py          tables as Python classes      schemas.py  input validation / output shapes
  api/               society, tanks, water, maintenance, notifications, dashboard, reports
  services/          calculations.py (water maths), analytics.py, alerts.py, balance.py, notifications.py, reporting.py
backend/tests/       test_logic.py (runs anywhere), test_api_integration.py (needs a test Postgres)
render.yaml          backend deployment
```

## How the security works (explain this in your viva)
1. Supabase Auth handles passwords and issues a signed token (JWT).
2. Every API call sends that token. `core/security.py` verifies the signature, expiry and audience.
3. `deps.py` then looks up the user's membership **in our own database** to get `society_id` and `role`. Neither is ever taken from the request or the token's editable metadata, so editing frontend data cannot make anyone an admin.
4. Every query filters by that `society_id`, so a user from society A cannot read society B (a foreign id simply returns 404).
5. Row Level Security is on for all tables with no policies, so Supabase's public API can read nothing; only this backend can.

## Setup (Windows commands; Mac/Linux in brackets)
### 1. Supabase
1. supabase.com > New project. Save the database password.
2. SQL Editor > paste all of `database/migrations/001_init.sql` > Run.
3. Authentication > Providers > Email. While testing, you may turn off "Confirm email" so sign-ups work instantly.
4. Collect: Project URL, and the **Session pooler** connection string (Project Settings > Database).

### 2. Run the backend locally
```
cd backend
python -m venv .venv
.venv\Scripts\activate           (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env           (Mac/Linux: cp .env.example .env)   <- then fill in the values
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000/docs (Swagger) and http://127.0.0.1:8000/health/db (should say "reachable").

### 3. Run the tests
```
python -m unittest tests.test_logic -v          # pure-logic tests, no database needed
```
For the API tests use a THROWAWAY database (second free Supabase project or local Docker Postgres), run the SQL file on it, then:
```
pip install -r requirements-dev.txt
set TEST_DATABASE_URL=postgresql://...             (Mac/Linux: export ...)
pytest -v
```
If any fail, send me the output and I'll fix them before building the frontend on top.

### 4. Deploy the backend on Render
Push the repo to GitHub > Render > New > Blueprint > select repo > fill the environment variables (same as `.env.example`;
set `CORS_ORIGINS` to your Vercel URL once you have it). Then open `https://<your-service>.onrender.com/health/db`.
Free instances sleep when idle (first request can take ~30 s). Optional: a free cron service (e.g. cron-job.org) can POST
`/api/v1/internal/run-checks` daily with header `X-Cron-Secret` so reminders are created even when nobody logs in.

## Domain rules implemented
- Tank readings: litres, % or cm. cm uses the tank's calibration table (linear interpolation, no extrapolation); regular tanks may use height ratio; **irregular tanks without calibration are rejected**.
- Collection estimate = rainfall (mm) x catchment area (m2) x runoff coefficient, stored with `calculated_estimate`.
- Meter usage = (new reading - previous reading), kL or litres. Roll-over is rejected, not guessed.
- Balance = opening + collected - utilized = expected closing; difference to the physical reading is an **unexplained discrepancy**, never "consumption".
- Savings = recorded harvested-water utilization, flagged `is_estimate`.
- Data classes everywhere: measured (tank scale/meter) - declared (staff stated) - estimated (calculated).
- Times are stored in UTC and bucketed by India time (IST).
- Corrections: edit or void, never delete; every change is in `audit_logs`.
