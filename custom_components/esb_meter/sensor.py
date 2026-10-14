"""Dated summaries and freshness; historical accounting uses external statistics."""
from datetime import datetime, timezone
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory
from .const import DOMAIN

DIRECTIONS = ("import", "export")


async def async_setup_entry(hass, entry, async_add_entities):
    keys = [(d, key) for d in DIRECTIONS for key in ("kwh", "latest_day_kwh", "month_kwh")]
    keys += [(None, k) for k in ("latest", "last_success", "next_fetch", "lag_hours", "status", "missing", "partial", "corrections")]
    entities = [ESBSensor(entry,direction,key) for direction,key in keys]
    entities += [ESBComparisonSensor(entry,direction,key) for direction in DIRECTIONS for key in ('status','difference_kwh','difference_percent')]
    async_add_entities(entities)


class ESBSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, entry, direction, key):
        super().__init__(entry.runtime_data)
        self.direction = direction
        self.key = key
        self._attr_unique_id = f"{entry.entry_id}_{direction or 'data'}_{key}"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title}
        if direction:
            self._attr_name = direction.title() + " " + {"kwh": "retained history", "latest_day_kwh": "latest complete day", "month_kwh": "month to latest reading"}[key]
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = "kWh"
            self._attr_suggested_display_precision = 3
        else:
            self._attr_name = {"latest": "Latest interval end", "last_success": "Last successful download",
                "next_fetch": "Next download", "lag_hours": "Data lag", "status": "Connection status",
                "missing": "Missing intervals", "partial": "Partial hours", "corrections": "Corrected intervals"}[key]
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            if key in ("latest", "last_success", "next_fetch"):
                self._attr_device_class = SensorDeviceClass.TIMESTAMP
            elif key == "lag_hours":
                self._attr_device_class = SensorDeviceClass.DURATION
                self._attr_native_unit_of_measurement = "h"

    @property
    def available(self):
        # Stale readings remain visible; timestamps and connection status disclose age.
        return self.coordinator.data is not None

    @property
    def native_value(self):
        data = self.coordinator.data
        if not data:
            return None
        if self.direction:
            section = data[self.direction]
            return section.get(self.key) if section.get("available") else None
        if self.key == "last_success":
            return datetime.fromisoformat(data[self.key]) if data.get(self.key) else None
        if self.key == "next_fetch":
            return datetime.fromtimestamp(data[self.key], timezone.utc) if data.get(self.key) else None
        if self.key in ("missing", "partial"):
            field = "missing_intervals" if self.key == "missing" else "partial_hours"
            return sum(data[d].get(field, 0) for d in DIRECTIONS)
        if self.key == "status":
            return self.coordinator.status
        return data.get(self.key)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        if self.direction:
            section = data.get(self.direction, {})
            return {"coverage_end": data.get("latest").isoformat() if data.get("latest") else None,
                    "latest_complete_day": section.get("latest_complete_day"),
                    "missing_intervals": section.get("missing_intervals", 0)}
        return None


class ESBComparisonSensor(CoordinatorEntity, SensorEntity):
    """Optional reference comparison; status distinguishes gaps from anomalies."""
    _attr_has_entity_name = True

    def __init__(self,entry,direction,key):
        super().__init__(entry.runtime_data)
        self.direction=direction
        self.key=key
        self._attr_unique_id=f'{entry.entry_id}_{direction}_comparison_{key}'
        self._attr_device_info={'identifiers':{(DOMAIN,entry.entry_id)},'name':entry.title}
        self._attr_name=direction.title()+' comparison '+{'status':'status','difference_kwh':'difference','difference_percent':'difference percent'}[key]
        if key=='difference_kwh':
            self._attr_native_unit_of_measurement='kWh'
            self._attr_suggested_display_precision=3
        elif key=='difference_percent':
            self._attr_native_unit_of_measurement='%'
            self._attr_suggested_display_precision=2

    @property
    def available(self):
        return self.coordinator.data is not None

    @property
    def native_value(self):
        result=(self.coordinator.data or {}).get('comparison',{}).get(self.direction,{})
        return result.get(self.key,'not_configured' if self.key=='status' else None)

    @property
    def extra_state_attributes(self):
        return (self.coordinator.data or {}).get('comparison',{}).get(self.direction,{})
