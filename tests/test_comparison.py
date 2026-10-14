from datetime import datetime,timedelta,timezone
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from support import module

c=module('comparison')

def rows(day,value=1):
    start,end=c.day_window(day)
    result=[{'start':start.timestamp()-3600,'sum':0,'change':None}]
    for i in range(int((end-start).total_seconds()/3600)):
        result.append({'start':start.timestamp()+3600*i,'sum':(i+1)*value,'change':value})
    return result

class ComparisonTests(unittest.TestCase):
    def test_matching_complete_day(self):
        result=c.compare_day('2026-10-12',24,rows('2026-10-12'))
        self.assertEqual(result['status'],'matched')
        self.assertEqual(result['difference_kwh'],0)

    def test_signed_difference_and_anomaly(self):
        result=c.compare_day('2026-10-12',20,rows('2026-10-12'))
        self.assertEqual(result['status'],'anomaly')
        self.assertEqual(result['difference_kwh'],4)
        self.assertEqual(result['difference_percent'],20)
        self.assertLess(c.compare_day('2026-10-12',30,rows('2026-10-12'))['difference_kwh'],0)

    def test_both_thresholds_required(self):
        self.assertEqual(c.compare_day('2026-10-12',24,rows('2026-10-12',1.02))['status'],'matched')
        self.assertEqual(c.compare_day('2026-10-12',240,rows('2026-10-12',10.1))['status'],'matched')

    def test_no_boundary_or_missing_hour_is_not_anomaly(self):
        self.assertEqual(c.compare_day('2026-10-12',20,rows('2026-10-12')[1:])['status'],'incomplete_reference')
        self.assertEqual(c.compare_day('2026-10-12',20,rows('2026-10-12')[:-1])['status'],'incomplete_reference')

    def test_negative_duplicate_and_nonfinite_are_incomplete(self):
        for value in (-1,float('nan'),float('inf'),None):
            data=rows('2026-10-12');data[1]['change']=value
            self.assertEqual(c.compare_day('2026-10-12',24,data)['status'],'incomplete_reference')
        data=rows('2026-10-12');data.append(data[1])
        self.assertEqual(c.compare_day('2026-10-12',24,data)['status'],'incomplete_reference')

    def test_zero_esb_day_has_no_invented_percentage(self):
        result=c.compare_day('2026-10-12',0,rows('2026-10-12'))
        self.assertIsNone(result['difference_percent'])
        self.assertEqual(result['status'],'anomaly')

    def test_irish_dst_days(self):
        for day,count in [('2026-03-29',23),('2025-10-26',25)]:
            result=c.compare_day(day,count,rows(day))
            self.assertEqual(result['status'],'matched')
            self.assertEqual(result['reference_hours'],count)

class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self,executor):
        tree=ast.parse((Path(__file__).parents[1]/'custom_components/esb_meter/reconciliation.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef))
        ns={'CoreState':SimpleNamespace(running='running'),'compare_day':c.compare_day,'day_window':c.day_window,
            'timedelta':timedelta,'get_instance':lambda hass:SimpleNamespace(async_add_executor_job=executor),
            'statistics_during_period':object(),'_LOGGER':SimpleNamespace(warning=lambda *args:None)}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'adapter-under-test','exec'),ns)
        return ns['async_comparison']

    async def test_no_recorder_query_during_startup(self):
        executor=AsyncMock();fn=self.adapter(executor)
        data={'import':{'latest_complete_day':'2026-10-12','latest_day_kwh':24}}
        result=await fn(SimpleNamespace(state='starting'),{'comparison_import':'sensor.reference'},data)
        self.assertEqual(result['import']['status'],'waiting_for_startup')
        executor.assert_not_awaited()

    async def test_request_normalizes_energy_units_to_kwh(self):
        executor=AsyncMock(return_value={'sensor.reference':rows('2026-10-12')});fn=self.adapter(executor)
        result=await fn(SimpleNamespace(state='running'),{'comparison_import':'sensor.reference'},
            {'import':{'latest_complete_day':'2026-10-12','latest_day_kwh':24}})
        self.assertEqual(result['import']['status'],'matched')
        args=executor.await_args.args
        self.assertEqual(args[-2],{'energy':'kWh'})
        self.assertEqual(args[-1],{'sum','change'})
        self.assertEqual(result['export']['status'],'not_configured')

    async def test_reference_query_failure_does_not_fail_meter(self):
        executor=AsyncMock(side_effect=RuntimeError('synthetic database issue'));fn=self.adapter(executor)
        result=await fn(SimpleNamespace(state='running'),{'comparison_import':'sensor.reference'},
            {'import':{'latest_complete_day':'2026-10-12','latest_day_kwh':24}})
        self.assertEqual(result['import']['status'],'reference_error')
