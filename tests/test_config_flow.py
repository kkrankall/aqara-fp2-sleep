"""Setting up: sign in, pick the FP2, sign in again."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fp2_sleep.api import password_md5
from custom_components.fp2_sleep.config_flow import default_region
from custom_components.fp2_sleep.const import (
    CONF_ACCOUNT,
    CONF_DEVICE_ID,
    CONF_PASSWORD_MD5,
    CONF_PHONE_ID,
    CONF_REGION,
    DOMAIN,
)

from .conftest import DID, OTHER_DID, PASSWORD

SIGN_IN = {CONF_ACCOUNT: " me@example.com ", CONF_PASSWORD: PASSWORD, CONF_REGION: "usa"}


async def start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_one_fp2_is_set_up_straight_away(hass, aqara) -> None:
    result = await start(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SIGN_IN)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bedroom Sleep Monitor"
    data = result["data"]
    assert data[CONF_ACCOUNT] == "me@example.com"
    assert data[CONF_PASSWORD_MD5] == password_md5(PASSWORD)
    assert PASSWORD not in str(data)
    assert data[CONF_DEVICE_ID] == DID and data[CONF_REGION] == "USA" and len(data[CONF_PHONE_ID]) == 36
    assert result["result"].unique_id == DID


async def test_wrong_password_shows_an_error(hass, aqara) -> None:
    aqara.password_ok = False
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SIGN_IN)
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "invalid_auth"}
    aqara.down = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SIGN_IN)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_several_fp2s_offer_a_choice(hass, aqara) -> None:
    aqara.devices.append(
        {"did": OTHER_DID, "deviceName": "Office FP2", "model": "lumi.motion.agl001", "positionName": "Office"}
    )
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SIGN_IN)
    assert result["step_id"] == "device"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_DEVICE_ID: OTHER_DID})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "Office FP2"


async def test_no_fp2_in_the_list_asks_for_its_id(hass, aqara) -> None:
    aqara.devices = [d for d in aqara.devices if d["did"] != DID]
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SIGN_IN)
    assert result["step_id"] == "manual"
    aqara.query_code = 755
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_DEVICE_ID: "01:23:45:67:89:AB"})
    assert result["errors"] == {"base": "no_permission"}
    aqara.query_code = 0
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_DEVICE_ID: "01:23:45:67:89:AB"})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"][CONF_DEVICE_ID] == DID


async def test_an_fp2_set_up_already_is_refused(hass, aqara) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=DID, data={}).add_to_hass(hass)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SIGN_IN)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_reauth_keeps_the_phone_id(hass, aqara) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DID,
        title="Bedroom Sleep Monitor",
        data={
            CONF_ACCOUNT: "me@example.com",
            CONF_PASSWORD_MD5: password_md5("old"),
            CONF_REGION: "USA",
            CONF_PHONE_ID: "PHONE-1",
            CONF_DEVICE_ID: DID,
        },
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    aqara.password_ok = False
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "wrong"})
    assert result["errors"] == {"base": "invalid_auth"}
    aqara.password_ok = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: PASSWORD})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD_MD5] == password_md5(PASSWORD)
    assert entry.data[CONF_PHONE_ID] == "PHONE-1"


def test_default_region_follows_the_country() -> None:
    assert default_region("US") == "USA"
    assert default_region("de") == "EU"
    assert default_region("GB") == "EU"
    assert default_region("CN") == "CN"
    assert default_region("KR") == "KR"
    assert default_region(None) == "USA"
