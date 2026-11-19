"""Importing past nights into long-term statistics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.fp2_sleep.const import DOMAIN
from custom_components.fp2_sleep.night import hourly_values

from .conftest import ms
from .test_init import TZ, setup_entry


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_db_url, enable_custom_integrations):
    """The recorder's database must exist before Home Assistant starts."""
    yield


def test_hourly_values_step_up_at_each_change() -> None:
    t0 = 1_800_000_000 - 1_800_000_000 % 3600
    events = {t0: "4", t0 + 1800: "5", t0 + 5400: "4"}
    rows = hourly_values(events, t0, t0 + 3 * 3600, frozenset({"4"}))
    assert rows == [(t0, 0.25, 0.0, 0.5), (t0 + 3600, 0.5, 0.5, 0.5), (t0 + 7200, 0.5, 0.5, 0.5)]
    deep = hourly_values(events, t0, t0 + 3 * 3600, frozenset({"5"}))
    assert [row[3] for row in deep] == [0.0, 1.0, 1.0]  # 01:30 -> 02:30 closes in hour 2


def night_log(day: int) -> list[tuple[int, int]]:
    """A night starting on November `day`: light 20:30, deep 23:40, light
    00:32, out of bed 06:00."""
    evening = lambda h, m: datetime(2026, 11, day, h, m, tzinfo=TZ)
    morning = lambda h, m: datetime(2026, 11, day + 1, h, m, tzinfo=TZ)
    return [(ms(evening(20, 30)), 4), (ms(evening(23, 40)), 5), (ms(morning(0, 32)), 4), (ms(morning(6, 0)), 0)]


async def daily_max(hass: HomeAssistant, entity_id: str) -> dict[str, float]:
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 11, 10, tzinfo=timezone.utc),
        None,
        {entity_id},
        "day",
        None,
        {"max"},
    )
    return {
        datetime.fromtimestamp(row["start"], TZ).date().isoformat(): row["max"]
        for row in rows.get(entity_id, [])
    }


async def test_import_fills_past_nights(recorder_mock, hass: HomeAssistant, aqara, freezer) -> None:
    entry = await setup_entry(hass, aqara, freezer)
    aqara.stage_log += night_log(15) + night_log(16) + night_log(17)

    result = await hass.services.async_call(
        DOMAIN, "import_history", {"nights": 5}, blocking=True, return_response=True
    )
    nights = {night["night"]: night for night in result["entries"][0]["nights"]}
    assert result["entries"][0]["entry"] == entry.title
    assert nights["2026-11-13"]["skipped"] == "no stage changes in Aqara's history"
    # The oldest night with data has nothing before it, so it might be cut short.
    assert nights["2026-11-15"]["skipped"] == "Aqara's history may not cover the whole night"
    for night in ("2026-11-16", "2026-11-17"):
        assert nights[night]["asleep_h"] == 9.5
        assert nights[night]["light_h"] == round(3 + 10 / 60 + 5 + 28 / 60, 4)
        assert nights[night]["deep_h"] == round(52 / 60, 4)
        assert nights[night]["rem_h"] == 0
        assert nights[night]["hours_imported"] == 4 * 24

    await async_wait_recording_done(hass)
    asleep = await daily_max(hass, "sensor.aqara_fp2_sleep_night_asleep")
    assert asleep["2026-11-17"] == 9.5 and asleep["2026-11-18"] == 9.5
    assert "2026-11-15" not in asleep
    deep = await daily_max(hass, "sensor.aqara_fp2_sleep_night_deep_sleep")
    assert deep["2026-11-18"] == round(52 / 60, 4)

    # A second run leaves what is there.
    aqara.stage_log = [(when, 5 if code == 4 else code) for when, code in aqara.stage_log]
    result = await hass.services.async_call(
        DOMAIN, "import_history", {"nights": 2}, blocking=True, return_response=True
    )
    assert all(n["hours_imported"] == 0 for n in result["entries"][0]["nights"])
    await async_wait_recording_done(hass)
    assert (await daily_max(hass, "sensor.aqara_fp2_sleep_night_deep_sleep"))["2026-11-18"] == round(52 / 60, 4)


async def test_import_needs_a_loaded_entry(recorder_mock, hass: HomeAssistant, aqara, freezer) -> None:
    await setup_entry(hass, aqara, freezer)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "import_history", {"config_entry_id": "nope"}, blocking=True, return_response=True
        )
