"""No credentials, cookies, entry IDs, meter fingerprints or raw usage data."""
async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    return {
        "connection_status": coordinator.status,
        "last_update_success": coordinator.last_update_success,
        "time_basis": "Europe/Dublin",
        "statistics_enabled": entry.options.get("publish_statistics", entry.data.get("publish_statistics", True)),
        "poll_hours": entry.options.get("poll_hours", 24),
        "retention_days": 730,
        "interval_count": len(coordinator.readings),
        "corrections": coordinator.corrections,
        "auth_path": coordinator.auth_path,
        "missing_intervals": sum(data.get(d, {}).get("missing_intervals", 0) for d in ("import", "export")),
    }
