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
    CONF_BIRTH_DATE,
    CONF_PRESENCE_ENTITY,
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


ROOM = "binary_sensor.bedroom_presence"


async def setup_entry(
    hass: HomeAssistant,
    aqara,
    freezer: FrozenDateTimeFactory,
    options=None,
    when: datetime | None = None,
    log: list[tuple[int, int]] | None = None,
) -> MockConfigEntry:
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to(when or at(3))
    aqara.stage_log = log or [(ms(at(20, 30)), 4), (ms(at(23, 40)), 5), (ms(at(0, 32)), 4), (ms(at(2, 0)), 2), (ms(at(2, 10)), 4)]
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
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def entry(hass: HomeAssistant, aqara, freezer: FrozenDateTimeFactory) -> MockConfigEntry:
    return await setup_entry(hass, aqara, freezer)


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
            "sensor.aqara_fp2_sleep_sleep_debt",
            "sensor.aqara_fp2_sleep_sleep_score",
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



def queries(aqara) -> int:
    return sum(1 for _, path, _, _ in aqara.requests if path == "/app/v1.0/lumi/res/query")


async def tick(hass: HomeAssistant, freezer, delta: timedelta) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_polling_follows_the_presence_sensor(hass: HomeAssistant, aqara, freezer) -> None:
    hass.states.async_set(ROOM, "on")
    entry = await setup_entry(hass, aqara, freezer, {CONF_PRESENCE_ENTITY: ROOM})
    coordinator = entry.runtime_data
    assert coordinator.update_interval == timedelta(seconds=60)

    # He gets up: Aqara still says light sleep for a while, then out of bed.
    hass.states.async_set(ROOM, "off")
    aqara.readings["sleep_state"] = (0, aqara.readings["sleep_state"][1])
    await tick(hass, freezer, timedelta(seconds=61))
    assert coordinator.update_interval == timedelta(seconds=60)  # within the hold
    await tick(hass, freezer, timedelta(minutes=30))
    assert coordinator.update_interval == timedelta(minutes=30)  # idle

    # Idle: nothing for 29 minutes.
    before = queries(aqara)
    await tick(hass, freezer, timedelta(minutes=29))
    assert queries(aqara) == before

    # Someone walks in: read at once, then every minute.
    hass.states.async_set(ROOM, "on")
    await hass.async_block_till_done()
    assert queries(aqara) == before + 1
    assert coordinator.update_interval == timedelta(seconds=60)
    await tick(hass, freezer, timedelta(seconds=61))
    assert queries(aqara) == before + 2


async def test_an_unavailable_sensor_polls_as_if_present(hass: HomeAssistant, aqara, freezer) -> None:
    freezer.move_to(at(3))
    hass.states.async_set(ROOM, "off")
    entry = await setup_entry(hass, aqara, freezer, {CONF_PRESENCE_ENTITY: ROOM})
    aqara.readings["sleep_state"] = (0, aqara.readings["sleep_state"][1])
    await tick(hass, freezer, timedelta(minutes=31))
    assert entry.runtime_data.update_interval == timedelta(minutes=30)
    hass.states.async_set(ROOM, "unavailable")
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval == timedelta(seconds=60)


async def test_options_set_the_presence_sensor(hass: HomeAssistant, entry, aqara) -> None:
    hass.states.async_set(ROOM, "on")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_PRESENCE_ENTITY: ROOM})
    await hass.async_block_till_done()
    assert entry.options == {CONF_PRESENCE_ENTITY: ROOM}
    assert entry.runtime_data.presence_entity == ROOM  # reloaded with it
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert entry.runtime_data.presence_entity is None


async def test_sleep_score_once_the_night_is_over(hass: HomeAssistant, entry, aqara, freezer, hass_storage) -> None:
    assert hass.states.get("sensor.aqara_fp2_sleep_sleep_score").state == "unknown"  # asleep
    aqara.stage_log.append((ms(at(6, 0)), 0))
    await tick(hass, freezer, timedelta(hours=3, minutes=10))  # 06:10, up 10 minutes
    assert hass.states.get("sensor.aqara_fp2_sleep_sleep_score").state == "unknown"
    await tick(hass, freezer, timedelta(hours=1))  # 07:10
    score = hass.states.get("sensor.aqara_fp2_sleep_sleep_score")
    # 9 h 20 m asleep, 20 minutes over the adult range; nothing to settle from,
    # no week of bedtimes yet: duration and continuity only.
    assert score.state == str(round(100 * (50 * (13 - 28 / 3) / 4 + 20) / 70))
    assert score.attributes["date"] == "2026-11-19"
    assert score.attributes["recommended_min_h"] == 7 and score.attributes["recommended_max_h"] == 9
    assert score.attributes["age_months"] is None
    assert set(score.attributes["points"]) == {"duration", "continuity"}
    assert score.attributes["points_possible"] == {"duration": 50, "continuity": 20}
    assert score.attributes["nights"] == [
        {"date": "2026-11-19", "score": int(score.state), "asleep_h": round(28 / 3, 3), "min_h": 7, "max_h": 9}
    ]
    assert hass.states.get("sensor.aqara_fp2_sleep_sleep_debt").state == "0.0"
    saved = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]
    assert list(saved["nights"]) == [at(18).replace(day=18).isoformat()]
    assert saved["token"]  # the session is still there

    # A child's birth date: 9-12 h for an 8-year-old, so 9 h 20 m is enough.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {CONF_BIRTH_DATE: "2018-03-10"})
    await hass.async_block_till_done()
    score = hass.states.get("sensor.aqara_fp2_sleep_sleep_score")
    assert score.state == "100"  # the log came back from storage after the reload
    assert score.attributes["age_months"] == 104
    assert (score.attributes["recommended_min_h"], score.attributes["recommended_max_h"]) == (9, 12)

    # The next evening the score stays on last night's until tonight is over.
    await tick(hass, freezer, timedelta(hours=12))  # 19:10
    assert hass.states.get("sensor.aqara_fp2_sleep_sleep_score").state == "100"


async def test_last_night_is_caught_up_after_a_restart(hass: HomeAssistant, aqara, freezer) -> None:
    evening = datetime(2026, 11, 19, 19, 30, tzinfo=TZ)
    # Home Assistant was down through the morning: last night is read whole.
    await setup_entry(hass, aqara, freezer, when=evening, log=[(ms(at(20, 30)), 4), (ms(at(6, 0)), 0)])
    score = hass.states.get("sensor.aqara_fp2_sleep_sleep_score")
    assert score.attributes["date"] == "2026-11-19"
    assert score.attributes["asleep_h"] == 9.5
    assert hass.states.get("sensor.aqara_fp2_sleep_night_asleep").state == "0.0"  # tonight


async def test_options_reject_a_future_birth_date(hass: HomeAssistant, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_BIRTH_DATE: "2027-01-01"})
    assert result["errors"] == {CONF_BIRTH_DATE: "invalid_birth_date"}
