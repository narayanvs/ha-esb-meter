"""Daily retrieval, bounded ledger and original-timestamp recorder imports."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import random
import time

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import ESBClient, InvalidAuth, PortalError
from .const import DOMAIN
from .history import decode, encode, overview, plan
from .parser import HDFError, Parsed, parse_text
from .policy import retry_delay
from .session import budget
from .reconciliation import async_comparison

_LOGGER = logging.getLogger(__name__)


class ESBCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(hours=1))
        self.entry = entry
        self.readings = ()
        self.base = {"import": "0", "export": "0"}
        self.anchor = None
        self.cookies = []
        self.next_fetch = 0
        self.last_attempt = 0
        self.last_success = None
        self.status = "starting"
        self.auth_path = "not_connected"
        self.corrections = 0
        self.published = False
        self.pending = ()
        self.store = Store(hass, 2, f"{DOMAIN}.{entry.entry_id}")
        self.import_lock = asyncio.Lock()
        self.cached_summary = None

    async def async_restore(self):
        saved = await self.store.async_load()
        if saved:
            self.readings = await self.hass.async_add_executor_job(decode, saved["readings"])
            self.base = saved.get("base", self.base)
            self.anchor = datetime.fromisoformat(saved["anchor"]) if saved.get("anchor") else None
            self.pending = await self.hass.async_add_executor_job(decode, saved.get("pending", []))
            self.cookies = saved.get("cookies", [])
            self.next_fetch = saved.get("next_fetch", 0)
            self.last_attempt = saved.get("last_attempt", 0)
            self.last_success = saved.get("last_success")
            self.published = saved.get("published", False)
            self.auth_path = saved.get("auth_path", "cached_session")
            self.status = saved.get("status", "cached")
            # Recover beta.5 initial imports that failed before accepting any history.
            if not self.readings and not self.last_success and self.status == "starting":
                self.next_fetch = 0
            if self.readings:
                self.cached_summary = await self._overview()

    async def _overview(self):
        result = await self.hass.async_add_executor_job(overview, self.readings, datetime.now(timezone.utc))
        result.update(status=self.status, last_success=self.last_success,
                      next_fetch=self.next_fetch, corrections=self.corrections,
                      auth_path=self.auth_path)
        result["comparison"] = await async_comparison(self.hass, self.entry.options, result)
        return result

    async def _save(self):
        rows = await self.hass.async_add_executor_job(encode, self.readings)
        pending = await self.hass.async_add_executor_job(encode, self.pending)
        await self.store.async_save({"readings": rows, "pending": pending, "auth_path": self.auth_path, "base": self.base,
            "anchor": self.anchor.isoformat() if self.anchor else None,
            "cookies": self.cookies, "next_fetch": self.next_fetch,
            "last_attempt": self.last_attempt, "last_success": self.last_success,
            "status": self.status, "published": self.published})

    async def async_manual_refresh(self):
        # Manual refresh can advance the daily job, but never defeat failure backoff.
        if self.status == "ok" and time.time()-self.last_attempt >= 3600:
            self.next_fetch = 0
        await self.async_request_refresh()

    async def _async_update_data(self):
        async with self.import_lock:
            prefetched = self.hass.data.get(DOMAIN, {}).pop(self.entry.unique_id, None)
            if not prefetched and self.pending:
                key = hashlib.sha256(self.entry.data["mprn"].encode()).hexdigest()
                prefetched = (Parsed(key, self.pending, 0), self.cookies, self.auth_path)
            if not prefetched and time.time() < self.next_fetch:
                if self.readings:
                    return await self._overview()
                raise UpdateFailed(self.status, retry_after=max(60, self.next_fetch-time.time()))
            client = ESBClient(self.entry.data["username"], self.entry.data["password"],
                               self.entry.data["mprn"], cookies=self.cookies,
                               reserve_login=budget(self.hass).reserve)
            try:
                self.last_attempt = time.time()
                # Persist cadence before network access so a restart cannot loop logins.
                self.next_fetch = self.last_attempt + 15*60
                self.status = "fetching"
                await self._save()
                if prefetched:
                    parsed, self.cookies, self.auth_path = prefetched
                else:
                    text = await client.fetch()
                    parsed = await self.hass.async_add_executor_job(parse_text, text, "Europe/Dublin")
                    self.cookies = client.export_cookies()
                    self.auth_path = client.auth_path
                expected = hashlib.sha256(self.entry.data["mprn"].encode()).hexdigest()
                if parsed.meter_key != expected:
                    raise HDFError("different_meter")
                # Save accepted download separately before touching recorder.
                self.pending = parsed.readings
                await self._save()
                now = datetime.now(timezone.utc)
                retained, base, anchor, stats, corrections = await self.hass.async_add_executor_job(
                    plan, self.readings, parsed.readings, self.base, self.anchor, now)
                publish = self.entry.options.get("publish_statistics", self.entry.data.get("publish_statistics", True))
                if publish:
                    # First enabling statistics replays all locally retained observations.
                    if not self.published and self.readings:
                        _, _, _, stats, _ = await self.hass.async_add_executor_job(
                            plan, (), (*self.readings, *parsed.readings), self.base, self.anchor, now)
                    await self._async_publish(stats)
                self.pending = ()
                self.published = publish
                self.readings, self.base, self.anchor = retained, base, anchor
                self.corrections = corrections
                self.status = "ok"
                self.last_success = now.isoformat()
                hours = self.entry.options.get("poll_hours", 24)
                self.next_fetch = time.time() + hours*3600 + random.uniform(0, 900)
                await self._save()
                self.cached_summary = await self._overview()
                return self.cached_summary
            except (PortalError, HDFError) as exc:
                self.status = str(exc)
                self.next_fetch = time.time() + retry_delay(self.status)
                await self._save()
                if isinstance(exc, InvalidAuth):
                    raise ConfigEntryAuthFailed("ESB credentials were rejected") from None
                if self.readings:
                    return await self._overview()
                raise UpdateFailed(self.status, retry_after=retry_delay(self.status)) from None
            except HomeAssistantError:
                self.status = "statistics_error"
                self.next_fetch = time.time() + 15*60
                await self._save()
                raise UpdateFailed("statistics_error", retry_after=15*60) from None
            finally:
                await client.close()

    async def _async_publish(self, series):
        for direction, rows in series.items():
            metadata = {
                "mean_type": StatisticMeanType.NONE, "has_sum": True,
                "name": f"ESB {direction.title()}", "source": DOMAIN,
                "statistic_id": f"{DOMAIN}:{self.entry.entry_id.lower()}_{direction}",
                "unit_of_measurement": "kWh", "unit_class": "energy",
            }
            for offset in range(0, len(rows), 500):
                async_add_external_statistics(self.hass, metadata, rows[offset:offset+500])
                # Recorder imports are queued. Never drain its queue during entry
                # setup: recorder may itself be waiting for HA startup to finish.
                await asyncio.sleep(0)
