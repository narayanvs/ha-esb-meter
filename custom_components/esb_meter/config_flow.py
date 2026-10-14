"""ESB account setup, reauthentication and polling options; no manual upload."""
import hashlib
import logging

_LOGGER = logging.getLogger(__name__)
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .client import ESBClient, PortalError, PortalHTTPError
from .const import DOMAIN
from .parser import HDFError, parse_text
from .session import budget


def schema(defaults=None):
    defaults = defaults or {}
    return vol.Schema({
        vol.Required("username", default=defaults.get("username", "")): str,
        vol.Required("password"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        vol.Required("mprn", default=defaults.get("mprn", "")): str,
        vol.Required("publish_statistics", default=defaults.get("publish_statistics", True)): bool,
    })


class ESBConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, data):
        data = {**data, "username": data["username"].strip(), "mprn": data["mprn"].strip()}
        if not data["mprn"].isascii() or not data["mprn"].isdigit() or len(data["mprn"]) != 11:
            raise HDFError("invalid_meter_id")
        client = ESBClient(data["username"], data["password"], data["mprn"],
                           reserve_login=budget(self.hass).reserve)
        try:
            text = await client.fetch()
            parsed = await self.hass.async_add_executor_job(parse_text, text, "Europe/Dublin")
            if parsed.meter_key != hashlib.sha256(data["mprn"].encode()).hexdigest():
                raise HDFError("different_meter")
            return data, parsed, client.export_cookies(), client.auth_path
        finally:
            await client.close()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data, parsed, cookies, auth_path = await self._validate(user_input)
            except (PortalError, HDFError) as exc:
                code = str(exc)
                if isinstance(exc, PortalHTTPError):
                    _LOGGER.warning("ESB setup failed (%s; stage=%s; HTTP=%s)", code, exc.phase, exc.status)
                else:
                    _LOGGER.warning("ESB setup failed (%s)", code)
                errors["base"] = code if code in ("invalid_auth", "browser_verification_required", "login_cooldown", "invalid_meter_id", "network_error", "login_page_changed", "missing_auth_form", "session_expired", "portal_http_error", "portal_rate_limited") else "cannot_connect"
            else:
                await self.async_set_unique_id(parsed.meter_key)
                self._abort_if_unique_id_configured()
                self.hass.data.setdefault(DOMAIN, {})[parsed.meter_key] = (parsed, cookies, auth_path)
                return self.async_create_entry(title="ESB Meter Data", data=data)
        return self.async_show_form(step_id="user", errors=errors, data_schema=schema())

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            try:
                data, parsed, cookies, auth_path = await self._validate(user_input)
                if parsed.meter_key != entry.unique_id:
                    raise HDFError("different_meter")
            except (PortalError, HDFError) as exc:
                if isinstance(exc, PortalHTTPError):
                    _LOGGER.warning("ESB reauthentication failed (stage=%s; HTTP=%s)", exc.phase, exc.status)
                errors["base"] = str(exc) if str(exc) in ("invalid_auth", "browser_verification_required", "login_cooldown", "different_meter") else "cannot_connect"
            else:
                self.hass.data.setdefault(DOMAIN, {})[parsed.meter_key] = (parsed, cookies, auth_path)
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(step_id="reauth_confirm", errors=errors, data_schema=schema(entry.data))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ESBOptionsFlow()


class ESBOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            cleaned = {k:v for k,v in user_input.items() if v not in ('',None)}
            return self.async_create_entry(title="", data=cleaned)
        comparison_fields = {}
        for direction in ('import','export'):
            key = 'comparison_'+direction
            marker = vol.Optional(key, description={'suggested_value':self.config_entry.options.get(key)})
            comparison_fields[marker] = selector.EntitySelector(selector.EntitySelectorConfig(domain='sensor',device_class='energy'))
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            **comparison_fields,
            vol.Required('comparison_absolute', default=self.config_entry.options.get('comparison_absolute',0.5)): vol.All(vol.Coerce(float),vol.Range(min=0,max=1000)),
            vol.Required('comparison_percent', default=self.config_entry.options.get('comparison_percent',5.0)): vol.All(vol.Coerce(float),vol.Range(min=0,max=100)),
            vol.Required("poll_hours", default=self.config_entry.options.get("poll_hours", 24)): vol.All(vol.Coerce(int), vol.Range(min=24, max=168)),
            vol.Required("publish_statistics", default=self.config_entry.options.get("publish_statistics", self.config_entry.data.get("publish_statistics", True))): bool,
        }))
