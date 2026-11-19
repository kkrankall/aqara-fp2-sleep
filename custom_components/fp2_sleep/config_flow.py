"""Config flow: sign in with the Aqara Home account and pick the FP2."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    CODE_NO_PERMISSION,
    REGIONS,
    AqaraAuthError,
    AqaraClient,
    AqaraConnectionError,
    AqaraError,
    new_phone_id,
    password_md5,
)
from .const import (
    CONF_ACCOUNT,
    CONF_DEVICE_ID,
    CONF_PASSWORD_MD5,
    CONF_PHONE_ID,
    CONF_REGION,
    DOMAIN,
    FP2_MODEL,
    LOGGER,
    QUERY_ATTRS,
)

# Countries served by Aqara's European, Chinese, Russian and Korean servers;
# everywhere else defaults to the USA server.
EU_COUNTRIES = frozenset(
    "AD AT BE BG CH CY CZ DE DK EE ES FI FR GB GR HR HU IE IS IT LI LT LU LV MC "
    "MT NL NO PL PT RO RS SE SI SK SM UA VA".split()
)
COUNTRY_REGIONS = {"CN": "CN", "RU": "RU", "KR": "KR"}

PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
REGION_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=list(REGIONS), translation_key="region", mode=SelectSelectorMode.DROPDOWN)
)


def default_region(country: str | None) -> str:
    country = (country or "").upper()
    if country in COUNTRY_REGIONS:
        return COUNTRY_REGIONS[country]
    return "EU" if country in EU_COUNTRIES else "USA"


def _error_key(err: Exception) -> str:
    if isinstance(err, AqaraAuthError):
        return "invalid_auth"
    if isinstance(err, AqaraConnectionError):
        return "cannot_connect"
    if isinstance(err, AqaraError):
        return "no_permission" if err.code == CODE_NO_PERMISSION else "rejected"
    return "unknown"


class FP2SleepConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._client: AqaraClient | None = None
        self._fp2s: dict[str, dict[str, Any]] = {}

    def _make_client(self, data: Mapping[str, Any]) -> AqaraClient:
        return AqaraClient(
            async_get_clientsession(self.hass),
            data[CONF_REGION],
            data[CONF_ACCOUNT],
            data[CONF_PASSWORD_MD5],
            data[CONF_PHONE_ID],
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data = {
                CONF_ACCOUNT: user_input[CONF_ACCOUNT].strip(),
                CONF_PASSWORD_MD5: password_md5(user_input[CONF_PASSWORD]),
                CONF_REGION: user_input[CONF_REGION],
                CONF_PHONE_ID: new_phone_id(),
            }
            self._client = self._make_client(self._data)
            try:
                await self._client.login()
                devices = await self._client.devices()
            except Exception as err:  # noqa: BLE001 - shown as a form error
                errors["base"] = _error_key(err)
                if errors["base"] == "unknown":
                    LOGGER.exception("Unexpected error signing in to Aqara")
            else:
                configured = {entry.unique_id for entry in self._async_current_entries()}
                fp2s = {
                    str(device["did"]): device
                    for device in devices
                    if device.get("model") == FP2_MODEL and device.get("did")
                }
                self._fp2s = {did: d for did, d in fp2s.items() if did not in configured}
                if fp2s and not self._fp2s:
                    return self.async_abort(reason="already_configured")
                if len(self._fp2s) == 1:
                    return await self._create(next(iter(self._fp2s.values())))
                if self._fp2s:
                    return await self.async_step_device()
                return await self.async_step_manual()

        schema = vol.Schema(
            {
                vol.Required(CONF_ACCOUNT): TextSelector(TextSelectorConfig(autocomplete="username")),
                vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                vol.Required(
                    CONF_REGION, default=default_region(self.hass.config.country)
                ): REGION_SELECTOR,
            }
        )
        if user_input:
            user_input = {k: v for k, v in user_input.items() if k != CONF_PASSWORD}
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """More than one FP2 on the account: pick one."""
        if user_input is not None:
            return await self._create(self._fp2s[user_input[CONF_DEVICE_ID]])
        options = [
            SelectOptionDict(
                value=did,
                label=" · ".join(
                    str(part) for part in (device.get("deviceName"), device.get("positionName")) if part
                ) or did,
            )
            for did, device in self._fp2s.items()
        ]
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE_ID): SelectSelector(SelectSelectorConfig(options=options))}
            ),
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """No FP2 in the device list: enter its Aqara device id by hand."""
        errors: dict[str, str] = {}
        if user_input is not None:
            did = user_input[CONF_DEVICE_ID].strip()
            if not did.startswith("lumi"):
                did = f"lumi1.{did.replace(':', '').lower()}"
            await self.async_set_unique_id(did)
            self._abort_if_unique_id_configured()
            assert self._client is not None
            try:
                await self._client.query(did, QUERY_ATTRS)
            except Exception as err:  # noqa: BLE001 - shown as a form error
                errors["base"] = _error_key(err)
            else:
                return self.async_create_entry(
                    title="Aqara FP2", data={**self._data, CONF_DEVICE_ID: did}
                )
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE_ID): str}),
            errors=errors,
        )

    async def _create(self, device: dict[str, Any]) -> ConfigFlowResult:
        did = str(device["did"])
        await self.async_set_unique_id(did)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=str(device.get("deviceName") or "Aqara FP2"),
            data={**self._data, CONF_DEVICE_ID: did},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD_MD5: password_md5(user_input[CONF_PASSWORD])}
            try:
                await self._make_client(data).login()
            except Exception as err:  # noqa: BLE001 - shown as a form error
                errors["base"] = _error_key(err)
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD_MD5: data[CONF_PASSWORD_MD5]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"account": entry.data[CONF_ACCOUNT]},
            errors=errors,
        )
