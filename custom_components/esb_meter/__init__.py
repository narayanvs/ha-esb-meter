"""Automatic ESB meter history integration."""
from homeassistant.helpers.start import async_at_started

from .const import PLATFORMS
from .coordinator import ESBCoordinator


async def async_setup_entry(hass, entry):
    coordinator = ESBCoordinator(hass, entry)
    await coordinator.async_restore()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if any(entry.options.get('comparison_'+d) for d in ('import','export')):
        async def refresh_comparison(hass):
            await coordinator.async_request_refresh()
        entry.async_on_unload(async_at_started(hass, refresh_comparison))
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)
