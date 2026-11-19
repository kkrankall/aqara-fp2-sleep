"""FP2 connectivity and connection health."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FP2ConfigEntry, FP2Coordinator, online
from .entity import FP2Entity

ONLINE = BinarySensorEntityDescription(
    key="fp2_online",
    translation_key="fp2_online",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)
PROBLEM = BinarySensorEntityDescription(
    key="connection_problem",
    translation_key="connection_problem",
    device_class=BinarySensorDeviceClass.PROBLEM,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: FP2ConfigEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    add_entities([FP2Online(coordinator, ONLINE), ConnectionProblem(coordinator, PROBLEM)])


class FP2Online(FP2Entity, BinarySensorEntity):
    """Whether Aqara's cloud hears from the FP2."""

    def __init__(self, coordinator: FP2Coordinator, description: BinarySensorEntityDescription) -> None:
        super().__init__(coordinator, description, "binary_sensor")

    @property
    def is_on(self) -> bool | None:
        return online(self.coordinator.data)


class ConnectionProblem(FP2Entity, BinarySensorEntity):
    """On while Home Assistant can't get readings from Aqara. Stays available
    when everything else goes unavailable, so it can drive an alert."""

    def __init__(self, coordinator: FP2Coordinator, description: BinarySensorEntityDescription) -> None:
        super().__init__(coordinator, description, "binary_sensor")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return not self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        error = self.coordinator.last_exception
        return {"cause": str(error) if error and not self.coordinator.last_update_success else None}
