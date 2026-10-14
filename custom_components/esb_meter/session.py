"""Shared persisted authentication budget across entries and setup flows."""
import asyncio
import time
from homeassistant.helpers.storage import Store
from .client import PortalError
from .const import DOMAIN
from .policy import LOGIN_WINDOW, login_allowed


class LoginBudget:
    def __init__(self, hass):
        self.store = Store(hass, 1, f"{DOMAIN}.login_budget")
        self.lock = asyncio.Lock()

    async def reserve(self):
        async with self.lock:
            now = time.time()
            attempts = (await self.store.async_load() or {}).get("attempts", [])
            if not login_allowed(attempts, now):
                raise PortalError("login_cooldown")
            # Persist before sending credentials, including failed attempts.
            await self.store.async_save({"attempts": [t for t in attempts if t > now-LOGIN_WINDOW] + [now]})


def budget(hass):
    return hass.data.setdefault(DOMAIN, {}).setdefault("login_budget", LoginBudget(hass))
