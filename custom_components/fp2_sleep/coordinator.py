"""Polls one FP2's sleep readings from Aqara."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AqaraAuthError, AqaraClient, AqaraConnectionError, AqaraError
from .const import (
    ATTR_MODE,
    ATTR_ONLINE,
    ATTR_SLEEP_STATE,
    CONF_DEVICE_ID,
    DEVICE_REFRESH,
    DOMAIN,
    FAST_HOURS_END,
    FAST_INTERVAL,
    LOGGER,
    NIGHT_REFRESH,
    NIGHT_START_HOUR,
    ONLINE_CODE,
    OFFLINE_CODE,
    QUERY_ATTRS,
    SLEEP_MODE,
    SLEEP_STATE_RESOURCE,
    SLOW_INTERVAL,
)
from .night import NightStages, NightSummary, aqara_time, code_string, night_start

type FP2ConfigEntry = ConfigEntry[FP2Coordinator]

STORE_VERSION = 1


@dataclass
class FP2Data:
    """One poll's view of the FP2."""

    values: dict[str, Any] = field(default_factory=dict)          # attr -> raw value
    measured_at: dict[str, datetime | None] = field(default_factory=dict)
    night: NightSummary | None = None


def mode_code(data: FP2Data) -> str | None:
    return code_string(data.values.get(ATTR_MODE))


def online(data: FP2Data) -> bool | None:
    code = code_string(data.values.get(ATTR_ONLINE))
    return {ONLINE_CODE: True, OFFLINE_CODE: False}.get(code) if code else None


class FP2Coordinator(DataUpdateCoordinator[FP2Data]):
    config_entry: FP2ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: FP2ConfigEntry, client: AqaraClient) -> None:
        super().__init__(
            hass, LOGGER, config_entry=entry, name=DOMAIN, update_interval=FAST_INTERVAL
        )
        self.client = client
        self.did: str = entry.data[CONF_DEVICE_ID]
        # The FP2's entry in Aqara's device list (name, firmware, room).
        self.device: dict[str, Any] = {}
        self._device_read: datetime | None = None
        self._night = NightStages()
        self._night_read: datetime | None = None
        self._night_summary: NightSummary | None = None
        self.store: Store[dict[str, str]] = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")

    async def _async_setup(self) -> None:
        saved = await self.store.async_load() or {}
        if saved.get("token") and saved.get("user_id"):
            self.client.token = saved["token"]
            self.client.user_id = saved["user_id"]

    def save_session(self, token: str, user_id: str) -> None:
        """Keep the session across restarts, so HA doesn't sign in each start."""
        self.hass.async_create_task(self.store.async_save({"token": token, "user_id": user_id}))

    async def _async_update_data(self) -> FP2Data:
        now = dt_util.now()
        try:
            items = await self.client.query(self.did, QUERY_ATTRS)
        except AqaraAuthError as err:
            raise ConfigEntryAuthFailed(f"Aqara refused the sign-in: {err}") from err
        except AqaraConnectionError as err:
            raise UpdateFailed(f"No answer from Aqara: {err}") from err
        except AqaraError as err:
            raise UpdateFailed(f"Aqara refused the request (code {err.code}): {err}") from err

        data = FP2Data()
        for item in items:
            attr = item.get("attr")
            if isinstance(attr, str):
                data.values[attr] = item.get("value")
                data.measured_at[attr] = aqara_time(item.get("timeStamp"))

        await self._refresh_night(now)
        data.night = self._night_summary
        await self._refresh_device(now)
        self._update_mode_issue(mode_code(data))

        stage = code_string(data.values.get(ATTR_SLEEP_STATE))
        in_bed = stage not in (None, "0")
        night = now.hour >= NIGHT_START_HOUR or now.hour < FAST_HOURS_END
        self.update_interval = FAST_INTERVAL if night or in_bed else SLOW_INTERVAL
        return data

    async def _refresh_night(self, now: datetime) -> None:
        """Re-read the night's stage changes every NIGHT_REFRESH, and at once
        when a new night starts. A failed read keeps the last summary."""
        new_night = night_start(now) != self._night.start
        if not new_night and self._night_read and now - self._night_read < NIGHT_REFRESH:
            return
        self._night_read = now

        async def read(start_ms: int, end_ms: int) -> list[dict[str, Any]]:
            return await self.client.history_log(self.did, [SLEEP_STATE_RESOURCE], start_ms, end_ms)

        try:
            await self._night.update(read, now)
        except AqaraAuthError as err:
            raise ConfigEntryAuthFailed(f"Aqara refused the sign-in: {err}") from err
        except AqaraError as err:
            LOGGER.debug("Night stages not read: %s", err)
        summary = self._night.summary()
        if summary is not None or (
            self._night_summary is not None and self._night_summary.start != self._night.start
        ):
            # Keep the last good summary through a failed read, but never
            # carry one into the next night.
            self._night_summary = summary

    async def _refresh_device(self, now: datetime) -> None:
        if self._device_read and now - self._device_read < DEVICE_REFRESH:
            return
        self._device_read = now
        try:
            devices = await self.client.devices()
        except AqaraAuthError as err:
            raise ConfigEntryAuthFailed(f"Aqara refused the sign-in: {err}") from err
        except AqaraError as err:
            LOGGER.debug("Device list not read: %s", err)
            return
        device = next((d for d in devices if d.get("did") == self.did), None)
        if device is None:
            return
        changed = {k: device.get(k) for k in ("firmwareVersion", "deviceName")} != {
            k: self.device.get(k) for k in ("firmwareVersion", "deviceName")
        }
        self.device = device
        if changed and self.data is not None:
            self._update_device_registry()

    def _update_device_registry(self) -> None:
        registry = dr.async_get(self.hass)
        lookup = getattr(registry, "async_get_device_by_identifier", None)  # HA 2026.10+
        entry = (
            lookup((DOMAIN, self.did), self.config_entry.entry_id)
            if lookup
            else registry.async_get_device(identifiers={(DOMAIN, self.did)})
        )
        if entry and self.device.get("firmwareVersion"):
            registry.async_update_device(entry.id, sw_version=str(self.device["firmwareVersion"]))

    def _update_mode_issue(self, mode: str | None) -> None:
        issue_id = f"not_sleep_mode_{self.config_entry.entry_id}"
        if mode is None:
            return
        if mode == SLEEP_MODE:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="not_sleep_mode",
            translation_placeholders={"name": self.config_entry.title},
        )
