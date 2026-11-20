"""Diagnostics: settings and the latest readings, with identifiers removed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_ACCOUNT, CONF_BIRTH_DATE, CONF_DEVICE_ID, CONF_PASSWORD_MD5, CONF_PHONE_ID
from .coordinator import FP2ConfigEntry

TO_REDACT = {
    CONF_ACCOUNT,
    CONF_BIRTH_DATE,
    CONF_PASSWORD_MD5,
    CONF_PHONE_ID,
    CONF_DEVICE_ID,
    "unique_id",
    "title",
    "did",
    "parentDeviceId",
    "positionId",
    "positionName",
    "deviceName",
    "originalName",
    "real1",
    "real2",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: FP2ConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    night = data.night if data else None
    latest = data.sleep.latest if data and data.sleep else None
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "region": coordinator.client.region,
        "device": async_redact_data(coordinator.device, TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "update_interval": str(coordinator.update_interval),
        "presence_entity": coordinator.presence_entity,
        "values": data.values if data else None,
        "measured_at": {k: v.isoformat() if v else None for k, v in data.measured_at.items()} if data else None,
        "night": {
            "start": night.start.isoformat(),
            "hours": night.hours,
            "stage_changes": len(night.stages),
            "awakenings": night.awakenings,
        }
        if night
        else None,
        "night_log": len(coordinator.night_log),
        "latest_score": {
            "score": latest.score,
            "points": latest.points,
            "age_band_h": [latest.low, latest.high],
        }
        if latest
        else None,
    }
