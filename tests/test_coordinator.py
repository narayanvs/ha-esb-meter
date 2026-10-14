"""Exercise coordinator code with isolated HA/recorder adapters, without a live DB."""
import ast
import asyncio
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import logging
from pathlib import Path
import random
import re
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from support import module

history = module('history')
parser = module('parser')
client = module('client')

class HAError(Exception): pass
class UpdateFailed(Exception):
    def __init__(self, message, **kwargs): super().__init__(message)
class Base:
    def __init__(self,hass,*args,**kwargs): self.hass=hass
class Store:
    def __init__(self,hass,version,key): self.hass=hass;self.key=key
    async def async_load(self): return copy.deepcopy(self.hass.storage.get(self.key))
    async def async_save(self,value): self.hass.storage[self.key]=copy.deepcopy(value)
class FakeClient:
    def __init__(self,*args,**kwargs): pass
    async def close(self): pass
    async def fetch(self): raise AssertionError('Pending data must not cause a download')
class Hass:
    def __init__(self): self.storage={};self.data={}
    async def async_add_executor_job(self,fn,*args): return fn(*args)

# Execute the actual class with dependency adapters; no duplicated coordinator logic.
tree=ast.parse((Path(__file__).parents[1]/'custom_components/esb_meter/coordinator.py').read_text())
node=next(n for n in tree.body if isinstance(n,ast.ClassDef))
ns=dict(asyncio=asyncio,datetime=datetime,timedelta=timedelta,timezone=timezone,
        hashlib=hashlib,random=random,time=time,DataUpdateCoordinator=Base,
        _LOGGER=logging.getLogger('test'),DOMAIN='esb_meter',Store=Store,
        ESBClient=FakeClient,ConfigEntryAuthFailed=HAError,HomeAssistantError=HAError,
        UpdateFailed=UpdateFailed,InvalidAuth=client.InvalidAuth,PortalError=client.PortalError,
        HDFError=parser.HDFError,Parsed=parser.Parsed,parse_text=parser.parse_text,
        retry_delay=module('policy').retry_delay,budget=lambda hass:SimpleNamespace(reserve=None),
        decode=history.decode,encode=history.encode,overview=history.overview,plan=history.plan,
        StatisticMeanType=SimpleNamespace(NONE=0))
exec(compile(ast.Module(body=[node],type_ignores=[]),'coordinator-under-test','exec'),ns)
async def no_comparison(*args): return {}
ns['async_comparison']=no_comparison
Coordinator=ns['ESBCoordinator']

class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def entry(self):
        return SimpleNamespace(entry_id='01TESTABC',unique_id='synthetic-key',options={},
            data={'username':'test@example.invalid','password':'synthetic','mprn':'10000000000'})

    async def test_uppercase_config_entry_produces_valid_chunked_statistic_ids(self):
        collected=[]
        def publish(hass,metadata,rows):
            self.assertRegex(metadata['statistic_id'],r'^esb_meter:[a-z0-9_]+$')
            self.assertEqual(metadata['source'],'esb_meter')
            collected.append((metadata,rows))
        ns['async_add_external_statistics']=publish
        recorder=SimpleNamespace(async_block_till_done=AsyncMock())
        ns['get_instance']=lambda hass:recorder
        co=Coordinator(Hass(),self.entry())
        rows=[{'start':datetime(2026,10,1,tzinfo=timezone.utc),'sum':1}]*501
        await co._async_publish({'import':rows,'export':[]})
        self.assertEqual([len(r) for _,r in collected],[500,1])
        recorder.async_block_till_done.assert_not_awaited()

    async def test_recorder_failure_retains_download_across_restart(self):
        hass=Hass();entry=self.entry();co=Coordinator(hass,entry)
        row=parser.Reading('import',datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)-timedelta(days=1),Decimal(1))
        parsed=parser.Parsed(hashlib.sha256(entry.data['mprn'].encode()).hexdigest(),(row,),0)
        hass.data={'esb_meter':{entry.unique_id:(parsed,[],'password_login')}}
        co._async_publish=AsyncMock(side_effect=HAError('synthetic recorder failure'))
        with self.assertRaisesRegex(UpdateFailed,'statistics_error'):
            await co._async_update_data()
        self.assertEqual(co.pending,(row,))
        restarted=Coordinator(hass,entry)
        await restarted.async_restore()
        restarted._async_publish=AsyncMock()
        result=await restarted._async_update_data()
        self.assertEqual(result['status'],'ok')
        self.assertEqual(restarted.readings,(row,))
        self.assertFalse(restarted.pending)
        restarted._async_publish.assert_awaited_once()

    async def test_beta5_initial_failure_cooldown_recovery(self):
        hass=Hass();entry=self.entry()
        hass.storage['esb_meter.'+entry.entry_id]={'readings':[],'status':'starting','next_fetch':time.time()+21600}
        co=Coordinator(hass,entry)
        await co.async_restore()
        self.assertEqual(co.next_fetch,0)

    async def test_initial_import_does_not_wait_for_recorder_readiness(self):
        async def blocked():
            await asyncio.Event().wait()
        recorder=SimpleNamespace(async_block_till_done=blocked)
        ns['get_instance']=lambda hass:recorder
        queued=[]
        ns['async_add_external_statistics']=lambda hass,metadata,rows:queued.extend(rows)
        co=Coordinator(Hass(),self.entry())
        rows=[{'start':datetime(2026,10,1,tzinfo=timezone.utc),'sum':1}]*1200
        await asyncio.wait_for(co._async_publish({'import':rows}),timeout=1)
        self.assertEqual(len(queued),1200)
