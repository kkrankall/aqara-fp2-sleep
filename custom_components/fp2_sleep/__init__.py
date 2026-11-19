"""Aqara FP2 Sleep: sleep monitoring from an Aqara FP2, through Aqara's app API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .api import AqaraClient, AqaraError
from .const import CONF_ACCOUNT, CONF_PASSWORD_MD5, CONF_PHONE_ID, CONF_REGION, DOMAIN
from .coordinator import STORE_VERSION, FP2ConfigEntry, FP2Coordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CARD_URL = f"/{DOMAIN}/fp2-sleep-card.js"
SERVICE_IMPORT_HISTORY = "import_history"
IMPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("nights", default=30): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
        vol.Optional("config_entry_id"): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the import action; serve the sleep card and have every
    dashboard load it (custom:fp2-sleep-card)."""

    async def import_history(call: ServiceCall) -> ServiceResponse:
        if "recorder" not in hass.config.components:
            raise ServiceValidationError("Importing past nights needs Home Assistant's recorder.")
        wanted = call.data.get("config_entry_id")
        entries = [
            entry
            for entry in hass.config_entries.async_loaded_entries(DOMAIN)
            if not wanted or entry.entry_id == wanted
        ]
        if not entries:
            raise ServiceValidationError("No Aqara FP2 Sleep entry is set up and loaded.")
        from .history import import_past_nights  # needs the recorder

        result: list[dict[str, Any]] = []
        for entry in entries:
            try:
                nights = await import_past_nights(hass, entry.runtime_data, call.data["nights"])
            except AqaraError as err:
                raise HomeAssistantError(f"Aqara didn't return its history: {err}") from err
            result.append({"entry": entry.title, "nights": nights})
        return {"entries": result}

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_HISTORY,
        import_history,
        schema=IMPORT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        return True  # no web interface (tests)
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    version = (await async_get_integration(hass, DOMAIN)).version
    card = Path(__file__).parent / "frontend" / "fp2-sleep-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(card), True)])
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")  # the version makes browsers fetch each release
    return True


async def async_setup_entry(hass: HomeAssistant, entry: FP2ConfigEntry) -> bool:
    client = AqaraClient(
        async_get_clientsession(hass),
        entry.data[CONF_REGION],
        entry.data[CONF_ACCOUNT],
        entry.data[CONF_PASSWORD_MD5],
        entry.data[CONF_PHONE_ID],
    )
    coordinator = FP2Coordinator(hass, entry, client)
    client.on_session = coordinator.save_session
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: FP2ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FP2ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: FP2ConfigEntry) -> None:
    """Forget the saved Aqara session and any open repair."""
    await Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
    ir.async_delete_issue(hass, DOMAIN, f"not_sleep_mode_{entry.entry_id}")
