from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from support import module

p = module("parser")
h = module("history")
policy = module("policy")
UTC = timezone.utc


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 14, tzinfo=UTC)
        self.start = self.now-timedelta(days=2)
        self.rows = tuple(p.Reading("import", self.start+timedelta(minutes=30*i), Decimal(1)) for i in range(48))

    def test_absent_export_is_not_zero(self):
        result = h.overview(self.rows, self.now)
        self.assertFalse(result["export"]["available"])
        self.assertIsNone(result["export"]["latest_day_kwh"])

    def test_latest_day_requires_complete_coverage(self):
        # Construct a complete Irish-local summer day (UTC start is previous 23:00).
        rows = tuple(p.Reading("import", self.start-timedelta(hours=1)+timedelta(minutes=30*i), Decimal(1)) for i in range(48))
        self.assertEqual(h.overview(rows, self.now)["import"]["latest_day_kwh"], 48)
        self.assertIsNone(h.overview(rows[:-1], self.now)["import"]["latest_day_kwh"])

    def test_no_replay_for_unchanged_file(self):
        result = h.plan(self.rows, self.rows, {}, None, self.now)
        self.assertFalse(result[3]["import"])

    def test_correction_replays_subsequent_cumulative_values(self):
        corrected = (p.Reading("import", self.rows[2].start, Decimal(3)),)
        result = h.plan(self.rows, corrected, {}, None, self.now)
        self.assertEqual(result[4], 1)
        self.assertEqual(result[3]["import"][-1]["sum"], 50)
        self.assertEqual(result[3]["import"][0]["start"], self.rows[2].start.replace(minute=0))

    def test_retention_preserves_cumulative_anchor(self):
        old = p.Reading("import", self.now-timedelta(days=800), Decimal(10))
        result = h.plan((), (old, *self.rows), {}, None, self.now)
        retained, base, anchor, stats, _ = result
        self.assertNotIn(old, retained)
        self.assertEqual(base["import"], "10")
        self.assertEqual(stats["import"][-1]["sum"], 58)
        later = p.Reading("import", self.now, Decimal(2))
        again = h.plan(retained, (old, later), base, anchor, self.now)
        self.assertEqual(again[3]["import"][-1]["sum"], 60)

    def test_storage_roundtrip(self):
        self.assertEqual(h.decode(h.encode(self.rows)), self.rows)

    def test_login_budget_persists_sliding_window_semantics(self):
        self.assertTrue(policy.login_allowed([], 100000))
        self.assertFalse(policy.login_allowed([99990], 100000))
        self.assertTrue(policy.login_allowed([99800, 99900], 100000))
        self.assertFalse(policy.login_allowed([99700, 99800, 99900], 100000))
        self.assertTrue(policy.login_allowed([1, 2], 100000))

    def test_challenge_backoff(self):
        self.assertEqual(policy.retry_delay("browser_verification_required"), 21600)
        self.assertEqual(policy.retry_delay("network_error"), 900)

    def test_retry_window_recovers_without_daily_lockout(self):
        self.assertFalse(policy.login_allowed([100,200,300],400))
        self.assertTrue(policy.login_allowed([100,200,300],1000))
        self.assertEqual(policy.login_wait([100,200,300],400),600)

    def test_restart_does_not_bypass_spacing(self):
        self.assertEqual(policy.login_wait([1000],1020),40)
        self.assertTrue(policy.login_allowed([1000],1060))

    def test_old_daily_budget_is_automatically_reinterpreted(self):
        self.assertTrue(policy.login_allowed([1000,2000],3000))

    def test_provider_failures_keep_longer_backoff(self):
        self.assertGreater(policy.retry_delay('browser_verification_required'),policy.retry_delay('network_error'))
        self.assertEqual(policy.retry_delay('portal_rate_limited'),3600)
