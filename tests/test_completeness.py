from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import unittest
from support import module

p = module('parser')
h = module('history')

class CompletenessTests(unittest.TestCase):
    def test_dst_complete_days_use_actual_day_length(self):
        local = ZoneInfo('Europe/Dublin')
        for day, count in ((datetime(2026,3,29,tzinfo=local),46), (datetime(2025,10,26,tzinfo=local),50)):
            with self.subTest(day=day):
                start = day.astimezone(timezone.utc)
                rows = tuple(p.Reading('import',start+timedelta(minutes=30*i),Decimal('0.5')) for i in range(count))
                result = h.overview(rows, day+timedelta(days=2))
                self.assertEqual(result['import']['latest_day_kwh'],count*0.5)
                self.assertIsNone(h.overview(rows[:-1],day+timedelta(days=2))['import']['latest_day_kwh'])

    def test_missing_current_month_is_unavailable(self):
        rows = (p.Reading('import',datetime(2026,9,30,tzinfo=timezone.utc),Decimal(1)),)
        result = h.overview(rows,datetime(2026,10,14,tzinfo=timezone.utc))
        self.assertIsNone(result['import']['month_kwh'])
        self.assertTrue(result['import']['available'])

    def test_retained_correction_preserves_prior_anchor(self):
        now = datetime(2026,10,14,tzinfo=timezone.utc)
        old = p.Reading('import',now-timedelta(days=800),Decimal(10))
        recent = p.Reading('import',now-timedelta(days=2),Decimal(2))
        retained, base, anchor, _, _ = h.plan((),(old,recent),{},None,now)
        changed = p.Reading('import',recent.start,Decimal(3))
        result = h.plan(retained,(changed,),base,anchor,now)
        self.assertEqual(result[3]['import'][-1]['sum'],13)
        self.assertEqual(result[1]['import'],'10')
