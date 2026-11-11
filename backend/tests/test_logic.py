"""Unit tests for the pure logic (no database or web framework needed): python -m unittest discover -s tests"""
import time
import unittest
from datetime import date, datetime, timedelta, timezone

import jwt

from app.core import security
from app.services import alerts, analytics, calculations as calc, reporting

IST = analytics.IST


class CalibrationTests(unittest.TestCase):
    TABLE = [(0, 0), (50, 400), (100, 1500), (150, 3500)]   # irregular: volume per cm grows with height

    def test_irregular_tank_is_not_proportional(self):
        v = calc.volume_from_input("cm", 75, 3500, "irregular", 150, self.TABLE)
        self.assertEqual(v, 950.0)                              # halfway between 400 and 1500
        self.assertNotEqual(v, 3500 * 75 / 150)                 # the naive linear answer would be 1750

    def test_irregular_without_calibration_rejected(self):
        with self.assertRaises(calc.CalculationError):
            calc.volume_from_input("cm", 75, 3500, "irregular", 150, None)

    def test_regular_tank_linear_ok(self):
        self.assertEqual(calc.volume_from_input("cm", 75, 3000, "cylindrical", 150), 1500.0)

    def test_no_extrapolation(self):
        with self.assertRaises(calc.CalculationError):
            calc.volume_from_input("cm", 160, 3500, "irregular", 150, self.TABLE)

    def test_percent_and_litres(self):
        self.assertEqual(calc.volume_from_input("percent", 40, 5000), 2000.0)
        self.assertEqual(calc.volume_from_input("litres", 1234.5, 5000), 1234.5)

    def test_over_capacity_and_invalid(self):
        for args in (("litres", 6000, 5000), ("percent", 101, 5000), ("litres", -1, 5000)):
            with self.assertRaises(calc.CalculationError):
                calc.volume_from_input(*args)

    def test_validate_calibration(self):
        self.assertEqual(calc.validate_calibration(self.TABLE, 3500)[0], (0.0, 0.0))
        for bad in ([(0, 0)], [(0, 0), (0, 5)], [(0, 0), (10, 100), (20, 50)], [(0, 0), (10, 9999)]):
            with self.assertRaises(calc.CalculationError):
                calc.validate_calibration(bad, 3500)


class CollectionAndMeterTests(unittest.TestCase):
    def test_rainfall_formula(self):
        self.assertEqual(calc.estimate_collection(50, 400, 0.8), 16000.0)   # 50mm x 400m2 x 0.8

    def test_rainfall_validation(self):
        for args in ((-1, 100, 0.8), (10, 0, 0.8), (10, 100, 0), (10, 100, 1.2)):
            with self.assertRaises(calc.CalculationError):
                calc.estimate_collection(*args)

    def test_meter(self):
        self.assertEqual(calc.meter_consumption(120.5, 123.0, "kl"), 2500.0)
        self.assertEqual(calc.meter_consumption(100, 350, "litres"), 250.0)
        with self.assertRaises(calc.CalculationError):
            calc.meter_consumption(10, 5, "kl")


class BalanceTests(unittest.TestCase):
    def test_discrepancy_not_consumption(self):
        b = calc.water_balance(2000, 1000, 500, 2300, 5000, 10)   # expected 2500, actual 2300
        self.assertEqual(b["expected_closing_litres"], 2500.0)
        self.assertEqual(b["discrepancy_litres"], -200.0)
        self.assertEqual(b["direction"], "actual_lower")
        self.assertFalse(b["flagged"])                            # 4% of capacity
        self.assertIn("not counted as consumption", b["interpretation"])

    def test_flagged_and_warnings(self):
        b = calc.water_balance(100, 0, 900, 0, 5000, 10)
        self.assertTrue(any("incomplete" in w for w in b["warnings"]))
        self.assertTrue(calc.water_balance(0, 0, 0, 1000, 5000, 10)["flagged"])      # 20% gap
        self.assertEqual(calc.water_balance(1, 2, 1, 2, 5000)["direction"], "matches")


class MaintenanceTests(unittest.TestCase):
    def test_states(self):
        today = date(2026, 11, 11)
        self.assertEqual(calc.maintenance_state(date(2026, 11, 10), today), "overdue")
        self.assertEqual(calc.maintenance_state(date(2026, 11, 18), today, 7), "due_soon")
        self.assertEqual(calc.maintenance_state(date(2026, 11, 19), today, 7), "scheduled")

    def test_recurrence_from_completion(self):
        self.assertEqual(calc.next_due_date(date(2026, 11, 11), 30), date(2026, 12, 11))

    def test_alert_rules(self):
        today = date(2026, 11, 11)
        self.assertEqual(alerts.maintenance_alert("Filter", date(2026, 11, 14), today, 7)[0], "maintenance_due")
        self.assertEqual(alerts.maintenance_alert("Filter", date(2026, 11, 1), today, 7)[0], "maintenance_overdue")
        self.assertIsNone(alerts.maintenance_alert("Filter", date(2026, 12, 25), today, 7))

    def test_reading_overdue(self):
        now = datetime(2026, 11, 11, tzinfo=IST)
        self.assertIsNone(alerts.reading_overdue_alert("T1", now - timedelta(days=3), now - timedelta(days=90), now, 7))
        self.assertIsNotNone(alerts.reading_overdue_alert("T1", now - timedelta(days=9), now - timedelta(days=90), now, 7))
        self.assertIsNone(alerts.reading_overdue_alert("T1", None, now - timedelta(days=2), now, 7))   # new tank
        self.assertIsNotNone(alerts.reading_overdue_alert("T1", None, now - timedelta(days=20), now, 7))


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        d = lambda y, m, day, h=12: datetime(y, m, day, h, tzinfo=IST)
        self.coll = [(d(2026, 11, 3), 1000, "manual_physical"), (d(2026, 11, 3, 15), 500, "calculated_estimate"),
                     (d(2026, 10, 20), 800, "manual_declared")]
        self.util = [(d(2026, 11, 4), 300, "water_meter", "garden_irrigation"),
                     (d(2026, 11, 5), 200, "manual_declared", "vehicle_washing"),
                     (d(2026, 11, 5, 18), 100, "manual_declared", "garden_irrigation")]

    def test_classes_never_mixed(self):
        t = analytics.totals_by_class(self.coll)
        self.assertEqual((t["measured"], t["declared"], t["estimated"], t["total"]), (1000, 800, 500, 2300))

    def test_daily_and_monthly(self):
        days = analytics.daily_totals(self.util, date(2026, 11, 1), date(2026, 11, 7))
        self.assertEqual(len(days), 7)
        self.assertEqual(days[4]["litres"], 300.0)
        months = analytics.monthly_totals(self.coll, date(2026, 11, 11), 3)
        self.assertEqual([m["month"] for m in months], ["2026-09", "2026-10", "2026-11"])
        self.assertEqual([m["litres"] for m in months], [0, 800, 1500])

    def test_month_keys_cross_year(self):
        self.assertEqual(analytics.month_keys(date(2026, 2, 1), 3), ["2025-12", "2026-01", "2026-02"])

    def test_purpose(self):
        p = analytics.by_purpose(self.util)
        self.assertEqual(p[0], {"purpose": "garden_irrigation", "litres": 400.0})

    def test_utc_late_evening_lands_on_ist_next_day(self):
        utc_dt = datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc)      # 01:30 IST on the 4th
        self.assertEqual(analytics.to_ist_date(utc_dt), date(2026, 11, 4))

    def test_period_bounds(self):
        self.assertEqual(analytics.period_bounds("weekly", date(2026, 11, 11)), (date(2026, 11, 9), date(2026, 11, 15)))
        self.assertEqual(analytics.period_bounds("monthly", date(2026, 2, 10)), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(analytics.period_bounds("annual", date(2026, 6, 1)), (date(2026, 1, 1), date(2026, 12, 31)))
        with self.assertRaises(ValueError):
            analytics.period_bounds("hourly", date(2026, 1, 1))

    def test_report_summary_and_exports(self):
        s, e = analytics.period_bounds("monthly", date(2026, 11, 11))
        rep = analytics.summarize_report("monthly", s, e, self.coll, self.util)
        self.assertEqual(len(rep["rows"]), 30)
        self.assertEqual(rep["totals_row"][1:], [1000, 0, 500, 300, 300, 0])
        annual = analytics.summarize_report("annual", *analytics.period_bounds("annual", date(2026, 11, 11)), self.coll, self.util)
        self.assertEqual(len(annual["rows"]), 12)
        rep.update(title="Monthly water report", subtitle="Test Society", notes=["Estimates are labelled."])
        csv_bytes = reporting.to_csv(rep)
        self.assertIn(b"Collected - estimated", csv_bytes)
        pdf = reporting.to_pdf(rep)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1500)


class TokenTests(unittest.TestCase):
    SECRET = "x" * 48

    def make(self, **over):
        claims = {"sub": "11111111-1111-1111-1111-111111111111", "aud": "authenticated", "exp": int(time.time()) + 600}
        claims.update(over)
        return jwt.encode(claims, self.SECRET, algorithm="HS256")

    def test_valid(self):
        self.assertEqual(security.decode_token(self.make(), jwt_secret=self.SECRET)["aud"], "authenticated")

    def test_rejections(self):
        cases = {"expired": self.make(exp=int(time.time()) - 5), "wrong audience": self.make(aud="other")}
        for name, tok in cases.items():
            with self.subTest(name), self.assertRaises(security.AuthError):
                security.decode_token(tok, jwt_secret=self.SECRET)
        with self.assertRaises(security.AuthError):
            security.decode_token(self.make(), jwt_secret="y" * 48)            # wrong secret
        with self.assertRaises(security.AuthError):
            security.decode_token("not.a.jwt", jwt_secret=self.SECRET)
        unsigned = jwt.encode({"sub": "a", "aud": "authenticated", "exp": int(time.time()) + 60}, None, algorithm="none")
        with self.assertRaises(security.AuthError):                              # alg=none must never be accepted
            security.decode_token(unsigned, jwt_secret=self.SECRET)
        no_sub = jwt.encode({"aud": "authenticated", "exp": int(time.time()) + 60}, self.SECRET, algorithm="HS256")
        with self.assertRaises(security.AuthError):
            security.decode_token(no_sub, jwt_secret=self.SECRET)


if __name__ == "__main__":
    unittest.main()
