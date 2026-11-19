"""Base entity: one FP2 is one device."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ENTITY_PREFIX
from .coordinator import FP2Coordinator


class FP2Entity(CoordinatorEntity[FP2Coordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: FP2Coordinator, description: EntityDescription, platform: str) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        did = coordinator.did
        self._attr_unique_id = f"{did}_{description.key}"
        # The same ids the SleepRadar app used, so history carries over. A
        # second FP2 gets HA's usual _2 suffix.
        self.entity_id = f"{platform}.{ENTITY_PREFIX}_{description.key}"
        device = coordinator.device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, did)},
            name=device.get("deviceName") or coordinator.config_entry.title,
            manufacturer="Aqara",
            model="Presence Sensor FP2",
            model_id=device.get("model") or "lumi.motion.agl001",
            sw_version=str(device["firmwareVersion"]) if device.get("firmwareVersion") else None,
            suggested_area=device.get("positionName") or None,
        )
