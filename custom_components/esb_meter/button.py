"""Manual refresh respects authentication and error cooldowns."""
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ImportButton(entry)])


class ImportButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Refresh meter data"
    _attr_icon = "mdi:file-import"

    def __init__(self, entry):
        super().__init__(entry.runtime_data)
        self._attr_unique_id = f"{entry.entry_id}_import"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title}

    @property
    def available(self):
        # Keep retry accessible after a malformed file has been replaced.
        return True

    async def async_press(self):
        await self.coordinator.async_manual_refresh()
