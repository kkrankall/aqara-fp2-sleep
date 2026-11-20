"""Sleep readings and night totals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import LIGHT_LUX, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    ATTR_BREATHING,
    ATTR_HEART_RATE,
    ATTR_LUX,
    ATTR_MOVEMENT,
    ATTR_SLEEP_STATE,
    MODES,
    STAGES,
)
from .coordinator import FP2ConfigEntry, FP2Coordinator, FP2Data, mode_code
from .entity import FP2Entity
from .night import code_string
from .score import WEIGHTS

# Nights listed in the sleep score's `nights` attribute, for charts.
SCORE_HISTORY_NIGHTS = 30


def _number(raw: Any) -> float | int | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return int(value) if value.is_integer() else value


@dataclass(frozen=True, kw_only=True)
class FP2SensorDescription(SensorEntityDescription):
    value: Callable[[FP2Data], Any]
    # The Aqara reading whose own time goes in the measured_at attribute.
    measured: str | None = None
    attributes: Callable[[FP2Data], dict[str, Any]] | None = None


def _reading(attr: str) -> Callable[[FP2Data], Any]:
    return lambda data: _number(data.values.get(attr))


def _night_hours(key: str) -> Callable[[FP2Data], Any]:
    return lambda data: data.night.hours[key] if data.night else None


def _stage(data: FP2Data) -> str | None:
    return STAGES.get(code_string(data.values.get(ATTR_SLEEP_STATE)) or "")


def _night_attributes(data: FP2Data) -> dict[str, Any]:
    night = data.night
    if night is None:
        return {}
    return {
        "night_start": night.start.isoformat(),
        "awakenings": night.awakenings,
        # The night's stage changes by Aqara's own times, for the card's
        # timeline. Not recorded (see _unrecorded_attributes).
        "stages": [
            {"time": time.isoformat(timespec="seconds"), "stage": STAGES.get(code, code)}
            for time, code in night.stages
        ],
    }


def _score(data: FP2Data) -> int | None:
    latest = data.sleep.latest if data.sleep else None
    return latest.score if latest else None


def _score_attributes(data: FP2Data) -> dict[str, Any]:
    latest = data.sleep.latest if data.sleep else None
    if latest is None:
        return {}
    facts = latest.facts
    return {
        "night_start": facts["start"],
        "date": latest.date.isoformat(),
        "age_months": latest.age_months,
        "recommended_min_h": latest.low,
        "recommended_max_h": latest.high,
        "asleep_h": facts["asleep_h"],
        "sleep_start": facts["sleep_start"],
        "sleep_end": facts["sleep_end"],
        "efficiency": round(facts["efficiency"] * 100),
        "wake_ups": facts["wake_ups"],
        "settle_min": facts["settle_min"],
        "bedtime_shift_min": latest.bedtime_shift_min,
        "points": latest.points,
        "points_possible": {key: WEIGHTS[key] for key in latest.points},
        # Recent nights for charts. Not recorded (see _unrecorded_attributes).
        "nights": [
            {
                "date": night.date.isoformat(),
                "score": night.score,
                "asleep_h": night.facts["asleep_h"],
                "min_h": night.low,
                "max_h": night.high,
            }
            for night in data.sleep.nights[-SCORE_HISTORY_NIGHTS:]
        ],
    }


def _debt_attributes(data: FP2Data) -> dict[str, Any]:
    latest = data.sleep.latest if data.sleep else None
    if latest is None:
        return {}
    return {"recommended_min_h": latest.low}


NIGHT_HOURS = dict(
    native_unit_of_measurement=UnitOfTime.HOURS,
    device_class=SensorDeviceClass.DURATION,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=2,
)

SENSORS: tuple[FP2SensorDescription, ...] = (
    FP2SensorDescription(
        key="heart_rate",
        translation_key="heart_rate",
        native_unit_of_measurement="bpm",
        state_class=SensorStateClass.MEASUREMENT,
        value=_reading(ATTR_HEART_RATE),
        measured=ATTR_HEART_RATE,
    ),
    FP2SensorDescription(
        key="respiration_rate",
        translation_key="respiration_rate",
        native_unit_of_measurement="br/min",
        state_class=SensorStateClass.MEASUREMENT,
        value=_reading(ATTR_BREATHING),
        measured=ATTR_BREATHING,
    ),
    FP2SensorDescription(
        key="body_movement",
        translation_key="body_movement",
        state_class=SensorStateClass.MEASUREMENT,
        value=_reading(ATTR_MOVEMENT),
        measured=ATTR_MOVEMENT,
    ),
    FP2SensorDescription(
        key="sleep_stage",
        translation_key="sleep_stage",
        device_class=SensorDeviceClass.ENUM,
        options=list(STAGES.values()),
        value=_stage,
        measured=ATTR_SLEEP_STATE,
    ),
    FP2SensorDescription(
        # Aqara's raw code, as the SleepRadar app published it.
        key="sleep_state",
        translation_key="sleep_state",
        value=lambda data: code_string(data.values.get(ATTR_SLEEP_STATE)),
        measured=ATTR_SLEEP_STATE,
        entity_registry_enabled_default=False,
    ),
    FP2SensorDescription(
        key="illuminance",
        device_class=SensorDeviceClass.ILLUMINANCE,
        native_unit_of_measurement=LIGHT_LUX,
        state_class=SensorStateClass.MEASUREMENT,
        value=_reading(ATTR_LUX),
        measured=ATTR_LUX,
    ),
    FP2SensorDescription(
        key="night_asleep",
        translation_key="night_asleep",
        value=_night_hours("asleep"),
        attributes=_night_attributes,
        **NIGHT_HOURS,
    ),
    FP2SensorDescription(
        key="night_light_sleep",
        translation_key="night_light_sleep",
        value=_night_hours("light"),
        **NIGHT_HOURS,
    ),
    FP2SensorDescription(
        key="night_deep_sleep",
        translation_key="night_deep_sleep",
        value=_night_hours("deep"),
        **NIGHT_HOURS,
    ),
    FP2SensorDescription(
        key="night_rem_sleep",
        translation_key="night_rem_sleep",
        value=_night_hours("rem"),
        **NIGHT_HOURS,
    ),
    FP2SensorDescription(
        key="fell_asleep",
        translation_key="fell_asleep",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda data: data.night.sleep_start if data.night else None,
    ),
    FP2SensorDescription(
        key="woke_up",
        translation_key="woke_up",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda data: data.night.sleep_end if data.night else None,
    ),
    FP2SensorDescription(
        key="sleep_score",
        translation_key="sleep_score",
        state_class=SensorStateClass.MEASUREMENT,
        value=_score,
        attributes=_score_attributes,
    ),
    FP2SensorDescription(
        key="sleep_debt",
        translation_key="sleep_debt",
        value=lambda data: data.sleep.debt if data.sleep else None,
        attributes=_debt_attributes,
        **NIGHT_HOURS,
    ),
    FP2SensorDescription(
        key="fp2_mode",
        translation_key="fp2_mode",
        device_class=SensorDeviceClass.ENUM,
        options=list(MODES.values()),
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda data: MODES.get(mode_code(data) or ""),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: FP2ConfigEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    add_entities(FP2Sensor(coordinator, description) for description in SENSORS)


class FP2Sensor(FP2Entity, SensorEntity):
    entity_description: FP2SensorDescription
    _unrecorded_attributes = frozenset({"stages", "nights"})

    def __init__(self, coordinator: FP2Coordinator, description: FP2SensorDescription) -> None:
        super().__init__(coordinator, description, "sensor")

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        description = self.entity_description
        attributes: dict[str, Any] = {}
        if description.measured:
            measured = self.coordinator.data.measured_at.get(description.measured)
            # When the FP2 took the reading; HA's own time is when it arrived.
            attributes["measured_at"] = measured.isoformat(timespec="seconds") if measured else None
        if description.attributes:
            attributes.update(description.attributes(self.coordinator.data))
        return attributes or None
