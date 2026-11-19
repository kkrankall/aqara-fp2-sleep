"""Import past nights' totals into long-term statistics.

This reads each past night's stage changes from Aqara's history log and
writes the hourly statistics the night-total sensors would have recorded,
so charts of past nights start filled in. Hours that already have
statistics are left as they are.

Aqara keeps roughly a week of history. A night is imported only when the
log also holds stage changes from the 24 hours before it, which shows the
whole night is still there; otherwise the oldest night could be cut short
and read as a short night.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SLEEP_STATE_RESOURCE
from .coordinator import FP2Coordinator
from .night import TOTALS, NightStages, hourly_values, night_start

# Night-total entity key -> TOTALS key.
NIGHT_SENSORS = {
    "night_asleep": "asleep",
    "night_light_sleep": "light",
    "night_deep_sleep": "deep",
    "night_rem_sleep": "rem",
}
MAX_NIGHTS = 30
# Read this far before each night to prove the log covers all of it.
COVERAGE_PROBE = timedelta(hours=24)
# Larger windows than live polling: a night has a few hundred changes at most,
# and a full page is split anyway.
IMPORT_CHUNK_S = 12 * 3600


def _entity_ids(hass: HomeAssistant, coordinator: FP2Coordinator) -> dict[str, str]:
    """Night-total key -> entity id, as registered (the user may rename)."""
    registry = er.async_get(hass)
    found = {}
    for key in NIGHT_SENSORS:
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{coordinator.did}_{key}")
        if entity_id:
            found[key] = entity_id
    return found


async def _existing_hours(hass: HomeAssistant, ids: set[str], start: datetime, end: datetime) -> dict[str, set[float]]:
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, ids, "hour", None, {"mean"}
    )
    found: dict[str, set[float]] = {statistic_id: set() for statistic_id in ids}
    for statistic_id, items in rows.items():
        for item in items:
            when = item["start"]
            found[statistic_id].add(when.timestamp() if isinstance(when, datetime) else float(when))
    return found


async def import_past_nights(
    hass: HomeAssistant, coordinator: FP2Coordinator, nights: int, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Import up to `nights` past nights; returns what was done per night."""
    now = now or dt_util.now()
    ids = _entity_ids(hass, coordinator)
    if not ids:
        return []
    tonight = night_start(now)
    report = []
    for back in range(min(nights, MAX_NIGHTS), 0, -1):
        start = night_start(tonight - timedelta(days=back) + timedelta(hours=1))
        end = start + timedelta(days=1)  # wall-clock: 18:00 the next day
        entry: dict[str, Any] = {"night": start.date().isoformat()}
        report.append(entry)

        async def read(start_ms: int, end_ms: int) -> list[dict[str, Any]]:
            return await coordinator.client.history_log(
                coordinator.did, [SLEEP_STATE_RESOURCE], start_ms, end_ms
            )

        stages = NightStages()
        if not await stages.read_window(
            read, (start - COVERAGE_PROBE).timestamp(), end.timestamp(), IMPORT_CHUNK_S
        ):
            entry["skipped"] = "Aqara's history came back incomplete"
            continue
        begin = start.timestamp()
        covered = any(when < begin for when in stages.events)
        stages.events = {when: code for when, code in stages.events.items() if when >= begin}
        if not stages.events:
            entry["skipped"] = "no stage changes in Aqara's history"
            continue
        if not covered:
            entry["skipped"] = "Aqara's history may not cover the whole night"
            continue

        existing = await _existing_hours(hass, set(ids.values()), start, end)
        imported = 0
        for key, entity_id in ids.items():
            rows = hourly_values(stages.events, start.timestamp(), end.timestamp(), TOTALS[NIGHT_SENSORS[key]])
            entry[f"{NIGHT_SENSORS[key]}_h"] = rows[-1][3] if rows else 0.0
            new = [
                StatisticData(start=dt_util.utc_from_timestamp(hour), mean=mean, min=low, max=high)
                for hour, mean, low, high in rows
                if hour not in existing.get(entity_id, set())
            ]
            if not new:
                continue
            metadata = StatisticMetaData(
                source="recorder",
                statistic_id=entity_id,
                name=None,
                unit_of_measurement="h",
                unit_class="duration",
                mean_type=StatisticMeanType.ARITHMETIC,
                has_sum=False,
            )
            async_import_statistics(hass, metadata, new)
            imported += len(new)
        entry["hours_imported"] = imported
    return report
