"""End-to-end API tests: role checks, society isolation, water maths through the real HTTP layer.

NOT RUN YET by the author (the build sandbox had no PostgreSQL). Run them against a THROWAWAY Postgres database
(a local Docker container or a second free Supabase project - never your real data):

    1. run database/migrations/001_init.sql on that test database
    2. export TEST_DATABASE_URL="postgresql://user:pass@host:5432/dbname"
    3. pip install -r requirements-dev.txt && pytest -v
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")


def iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


@pytest.fixture(scope="module")
def api():
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    from fastapi import Header
    from fastapi.testclient import TestClient
    from app.deps import get_claims
    from app.main import app

    app.dependency_overrides[get_claims] = lambda x_test_user=Header(...): {"sub": x_test_user, "email": f"{x_test_user}@test.dev"}
    client = TestClient(app)

    class Api:
        def call(self, who, method, path, **kw):
            return client.request(method, "/api/v1" + path, headers={"X-Test-User": who}, **kw)
    return Api()


@pytest.fixture(scope="module")
def world(api):
    ids = {k: str(uuid.uuid4()) for k in ("admin_a", "admin_b", "staff", "resident")}
    sa = api.call(ids["admin_a"], "POST", "/onboarding/society", json={"name": "Society A"}).json()
    api.call(ids["admin_b"], "POST", "/onboarding/society", json={"name": "Society B"})
    code = api.call(ids["admin_a"], "GET", "/society/invite-code").json()["invite_code"]
    for who in ("staff", "resident"):
        assert api.call(ids[who], "POST", "/onboarding/join", json={"invite_code": code}).status_code == 202
    members = {m["user_id"]: m["id"] for m in api.call(ids["admin_a"], "GET", "/society/members").json()}
    assert api.call(ids["staff"], "GET", "/tanks").status_code == 403            # still pending
    api.call(ids["admin_a"], "PATCH", f"/society/members/{members[ids['staff']]}", json={"role": "staff", "status": "active"})
    api.call(ids["admin_a"], "PATCH", f"/society/members/{members[ids['resident']]}", json={"status": "active"})
    tank = api.call(ids["admin_a"], "POST", "/tanks", json={"name": "Main", "capacity_litres": 5000}).json()
    return {**ids, "society_a": sa["id"], "tank": tank["id"]}


def test_roles_are_enforced_by_the_backend(api, world):
    assert api.call(world["staff"], "POST", "/tanks", json={"name": "X", "capacity_litres": 100}).status_code == 403
    body = {"tank_id": world["tank"], "measured_at": iso(), "input_type": "litres", "input_value": 100}
    assert api.call(world["resident"], "POST", "/water/readings", json=body).status_code == 403
    assert api.call(world["resident"], "GET", "/society/members").status_code == 403
    assert api.call(world["resident"], "GET", "/dashboard/resident").status_code == 200


def test_society_isolation(api, world):
    assert api.call(world["admin_b"], "GET", "/tanks").json() == []
    body = {"tank_id": world["tank"], "measured_at": iso(), "input_type": "litres", "input_value": 100}
    assert api.call(world["admin_b"], "POST", "/water/readings", json=body).status_code == 404
    assert api.call(world["admin_b"], "GET", "/water/history").json()["items"] == []


def test_reading_rules(api, world):
    t = world["tank"]
    r = api.call(world["staff"], "POST", "/water/readings", json={"tank_id": t, "measured_at": iso(5), "input_type": "percent", "input_value": 40})
    assert r.status_code == 201 and r.json()["volume_litres"] == 2000 and r.json()["data_source"] == "manual_physical"
    dup = api.call(world["staff"], "POST", "/water/readings", json={"tank_id": t, "measured_at": r.json()["measured_at"], "input_type": "litres", "input_value": 5})
    assert dup.status_code == 409
    over = api.call(world["staff"], "POST", "/water/readings", json={"tank_id": t, "measured_at": iso(4), "input_type": "litres", "input_value": 9999})
    assert over.status_code == 422
    future = api.call(world["staff"], "POST", "/water/readings", json={"tank_id": t, "measured_at": iso(-2), "input_type": "litres", "input_value": 5})
    assert future.status_code == 422
    cm = api.call(world["staff"], "POST", "/water/readings", json={"tank_id": t, "measured_at": iso(3), "input_type": "cm", "input_value": 10})
    assert cm.status_code == 422                                              # no height/calibration configured


def test_balance_and_audit_trail(api, world):
    t = world["tank"]
    api.call(world["staff"], "POST", "/water/collections", json={"tank_id": t, "collected_at": iso(3), "data_source": "manual_physical", "volume_litres": 500})
    api.call(world["staff"], "POST", "/water/utilizations", json={"tank_id": t, "used_at": iso(2), "purpose": "garden_irrigation", "data_source": "manual_declared", "volume_litres": 200})
    last = api.call(world["staff"], "POST", "/water/readings", json={"tank_id": t, "measured_at": iso(1), "input_type": "litres", "input_value": 2250}).json()
    bal = api.call(world["admin_a"], "GET", f"/water/balance?tank_id={t}").json()[0]
    assert bal["expected_closing_litres"] == 2300 and bal["discrepancy_litres"] == -50 and bal["direction"] == "actual_lower"
    fixed = api.call(world["staff"], "PATCH", f"/water/readings/{last['id']}", json={"input_value": 2300})
    assert fixed.status_code == 200 and fixed.json()["volume_litres"] == 2300
    trail = api.call(world["admin_a"], "GET", f"/water/audit/water_readings/{last['id']}").json()
    assert [e["action"] for e in trail] == ["create", "update"]


def test_estimate_and_meter(api, world):
    t = world["tank"]
    api.call(world["admin_a"], "PATCH", "/society", json={"catchment_area_m2": 400, "runoff_coefficient": 0.8})
    est = api.call(world["staff"], "POST", "/water/collections", json={"tank_id": t, "collected_at": iso(1), "data_source": "calculated_estimate", "rainfall_mm": 50})
    assert est.status_code == 201 and est.json()["volume_litres"] == 16000 and est.json()["data_source"] == "calculated_estimate"
    m = api.call(world["staff"], "POST", "/water/utilizations", json={"tank_id": t, "used_at": iso(1), "purpose": "common_area_cleaning", "data_source": "water_meter", "meter_start": 10, "meter_end": 10.75, "meter_unit": "kl"})
    assert m.status_code == 201 and m.json()["volume_litres"] == 750


def test_maintenance_recurrence_and_notifications(api, world):
    from datetime import date
    due = (date.today() + timedelta(days=3)).isoformat()
    staff_id = world["staff"]
    t = api.call(world["admin_a"], "POST", "/maintenance/tasks", json={"category": "filter_cleaning", "title": "Clean filters", "assigned_to": staff_id, "due_date": due, "interval_days": 30}).json()
    assert t["state"] == "due_soon"
    assert [x["title"] for x in api.call(world["staff"], "GET", "/maintenance/tasks").json()] == ["Clean filters"]
    done = api.call(world["staff"], "POST", f"/maintenance/tasks/{t['id']}/complete", json={"notes": "done"}).json()
    assert done["due_date"] == (date.today() + timedelta(days=30)).isoformat() and done["status"] == "pending"
    n = api.call(world["admin_a"], "GET", "/notifications").json()
    assert n["unread_count"] >= 1
    api.call(world["admin_a"], "POST", "/notifications/read-all")
    assert api.call(world["admin_a"], "GET", "/notifications?unread_only=true").json()["unread_count"] == 0
    assert api.call(world["resident"], "POST", f"/maintenance/tasks/{t['id']}/complete", json={}).status_code == 403
