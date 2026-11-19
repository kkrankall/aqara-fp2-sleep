"""The integration end to end against the fake Aqara server."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.fp2_sleep.api import password_md5
from custom_components.fp2_sleep.const import (
    CONF_ACCOUNT,
    CONF_DEVICE_ID,
    CONF_PASSWORD_MD5,
    CONF_PHONE_ID,
    CONF_REGION,
    DOMAIN,
)
from custom_components.fp2_sleep.diagnostics import async_get_config_entry_diagnostics

from .conftest import DID, PASSWORD, ms

TZ = ZoneInfo("America/New_York")


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 18 if hour >= 18 else 19, hour, minute, tzinfo=TZ)


@pytest.fixture
async def entry(hass: HomeAssistant, aqara, freezer: FrozenDateTimeFactory) -> MockConfigEntry:
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to(at(3))
    aqara.stage_log = [(ms(at(20, 30)), 4), (ms(at(23, 40)), 5), (ms(at(0, 32)), 4), (ms(at(2, 0)), 2), (ms(at(2, 10)), 4)]
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DID,
        title="Bedroom Sleep Monitor",
        data={
            CONF_ACCOUNT: "me@example.com",
            CONF_PASSWORD_MD5: password_md5(PASSWORD),
            CONF_REGION: "USA",
            CONF_PHONE_ID: "PHONE-1",
            CONF_DEVICE_ID: DID,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_entities_keep_sleepradars_ids(hass: HomeAssistant, entry) -> None:
    assert entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    ids = sorted(e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id))
    assert ids == sorted(
        [
            "binary_sensor.aqara_fp2_sleep_connection_problem",
            "binary_sensor.aqara_fp2_sleep_fp2_online",
            "sensor.aqara_fp2_sleep_body_movement",
            "sensor.aqara_fp2_sleep_fell_asleep",
            "sensor.aqara_fp2_sleep_fp2_mode",
            "sensor.aqara_fp2_sleep_heart_rate",
            "sensor.aqara_fp2_sleep_illuminance",
            "sensor.aqara_fp2_sleep_night_asleep",
            "sensor.aqara_fp2_sleep_night_deep_sleep",
            "sensor.aqara_fp2_sleep_night_light_sleep",
            "sensor.aqara_fp2_sleep_night_rem_sleep",
            "sensor.aqara_fp2_sleep_respiration_rate",
            "sensor.aqara_fp2_sleep_sleep_stage",
            "sensor.aqara_fp2_sleep_sleep_state",
            "sensor.aqara_fp2_sleep_woke_up",
        ]
    )
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, DID), entry.entry_id)
    assert device.name == "Bedroom Sleep Monitor"
    assert device.sw_version == "1.3.2_0002.0098"
    assert device.manufacturer == "Aqara"


async def test_readings_and_night_totals(hass: HomeAssistant, entry) -> None:
    heart = hass.states.get("sensor.aqara_fp2_sleep_heart_rate")
    assert heart.state == "66" and heart.attributes["unit_of_measurement"] == "bpm"
    assert heart.attributes["measured_at"].startswith("2025-11-20T00:53")
    assert hass.states.get("sensor.aqara_fp2_sleep_respiration_rate").state == "16"
    assert hass.states.get("sensor.aqara_fp2_sleep_sleep_stage").state == "light"
    assert hass.states.get("sensor.aqara_fp2_sleep_fp2_mode").state == "sleep_monitoring"
    assert hass.states.get("binary_sensor.aqara_fp2_sleep_fp2_online").state == "on"
    assert hass.states.get("binary_sensor.aqara_fp2_sleep_connection_problem").state == "off"
    asleep = hass.states.get("sensor.aqara_fp2_sleep_night_asleep")
    assert asleep.state == "5.5" and asleep.attributes["unit_of_measurement"] == "h"
    assert asleep.attributes["awakenings"] == 1
    assert [s["stage"] for s in asleep.attributes["stages"]] == ["light", "deep", "light", "awake", "light"]
    assert hass.states.get("sensor.aqara_fp2_sleep_night_deep_sleep").state == "0.867"
    assert hass.states.get("sensor.aqara_fp2_sleep_night_light_sleep").state == "4.633"
    assert hass.states.get("sensor.aqara_fp2_sleep_night_rem_sleep").state == "0.0"
    fell = hass.states.get("sensor.aqara_fp2_sleep_fell_asleep").state
    assert datetime.fromisoformat(fell) == at(20, 30)
    assert hass.states.get("sensor.aqara_fp2_sleep_woke_up").state == "unknown"


async def test_night_refresh_is_throttled(hass: HomeAssistant, entry, aqara, freezer) -> None:
    reads = len(aqara.history_requests)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(aqara.history_requests) == reads  # night re-read every 5 min
    aqara.stage_log.append((ms(at(3, 1)), 0))
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(aqara.history_requests) == reads + 1  # just the last hour
    assert datetime.fromisoformat(hass.states.get("sensor.aqara_fp2_sleep_woke_up").state) == at(3, 1)


async def test_outage_flags_a_problem_and_recovers(hass: HomeAssistant, entry, aqara, freezer) -> None:
    aqara.down = True
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.aqara_fp2_sleep_heart_rate").state == "unavailable"
    problem = hass.states.get("binary_sensor.aqara_fp2_sleep_connection_problem")
    assert problem.state == "on" and "No answer from Aqara" in problem.attributes["cause"]
    aqara.down = False
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.aqara_fp2_sleep_heart_rate").state == "66"
    assert hass.states.get("binary_sensor.aqara_fp2_sleep_connection_problem").state == "off"


async def test_daytime_polls_slow_down(hass: HomeAssistant, entry, aqara, freezer) -> None:
    coordinator = entry.runtime_data
    assert coordinator.update_interval == timedelta(seconds=60)
    aqara.readings["sleep_state"] = (0, aqara.readings["sleep_state"][1])
    freezer.move_to(at(13))
    await coordinator.async_refresh()
    assert coordinator.update_interval == timedelta(minutes=5)


async def test_wrong_mode_raises_a_repair(hass: HomeAssistant, entry, aqara, freezer) -> None:
    aqara.readings["set_device_mode4"] = (3, 0)
    await entry.runtime_data.async_refresh()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"not_sleep_mode_{entry.entry_id}")
    assert issue is not None and issue.translation_key == "not_sleep_mode"
    assert hass.states.get("sensor.aqara_fp2_sleep_fp2_mode").state == "zone_detection"
    aqara.readings["set_device_mode4"] = (9, 0)
    await entry.runtime_data.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"not_sleep_mode_{entry.entry_id}") is None


async def test_rejected_sign_in_starts_reauth(hass: HomeAssistant, entry, aqara) -> None:
    aqara.expire_next_query = True
    aqara.password_ok = False
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_session_is_saved_and_reused(hass: HomeAssistant, entry, aqara, hass_storage) -> None:
    await hass.async_block_till_done()
    assert aqara.logins == 1
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert aqara.logins == 1  # the saved token is used after a restart


async def test_diagnostics_hide_identifiers(hass: HomeAssistant, entry) -> None:
    result = await async_get_config_entry_diagnostics(hass, entry)
    text = str(result)
    for secret in (DID, "me@example.com", password_md5(PASSWORD), "PHONE-1", "Bedroom", "real2.1"):
        assert secret not in text
    assert result["night"]["hours"]["asleep"] == 5.5
    assert result["device"]["firmwareVersion"] == "1.3.2_0002.0098"


async def test_unload(hass: HomeAssistant, entry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_new_firmware_updates_the_device(hass: HomeAssistant, entry, aqara, freezer) -> None:
    aqara.devices[0]["firmwareVersion"] = "1.3.3_0002.0100"
    freezer.tick(timedelta(hours=6, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, DID), entry.entry_id)
    assert device.sw_version == "1.3.3_0002.0100"
