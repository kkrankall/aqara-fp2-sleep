"""A fake Aqara app API behind Home Assistant's aiohttp mocker."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from typing import Any

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.fp2_sleep.api import REGIONS

pytest_plugins = "pytest_homeassistant_custom_component"

SERVER = REGIONS["USA"]["server"]
DID = "lumi1.0123456789ab"
OTHER_DID = "lumi1.ba9876543210"
PASSWORD = "correct horse"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


class FakeAqara:
    """Answers like Aqara's USA server. Tests change the fields to steer it."""

    def __init__(self) -> None:
        self.password_ok = True
        self.login_code = 0
        self.token = "token-1"
        self.logins = 0
        self.expire_next_query = False
        self.query_code = 0
        self.down = False
        self.devices: list[dict[str, Any]] = [
            {
                "did": DID,
                "deviceName": "Bedroom Sleep Monitor",
                "model": "lumi.motion.agl001",
                "firmwareVersion": "1.3.2_0002.0098",
                "positionName": "Bedroom",
                "positionId": "real2.1",
                "state": 1,
            },
            {"did": "lumi.hub1", "deviceName": "Hub", "model": "lumi.gateway.acn01"},
        ]
        now_ms = 1_763_600_000_000
        self.readings: dict[str, Any] = {
            "heartrate_value": (66, now_ms - 6_000),
            "respiration_rate_value": (16, now_ms - 6_000),
            "sleep_state": (4, now_ms - 600_000),
            "body_movement_value": (12, now_ms - 6_000),
            "lux": (3, now_ms - 60_000),
            "set_device_mode4": (9, now_ms - 86_400_000),
            "device_offline_status": (1, now_ms - 86_400_000),
        }
        # (epoch ms, stage code) for the history log.
        self.stage_log: list[tuple[int, int]] = []
        self.history_requests: list[dict[str, Any]] = []
        self.requests: list[tuple[str, str, dict[str, str], Any]] = []

    def install(self, mock: AiohttpClientMocker) -> None:
        for method in ("get", "post"):
            mock.request(method, re.compile(re.escape(SERVER) + r"/.*"), side_effect=self.handle)

    async def handle(self, method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        body = json.loads(data) if isinstance(data, str) and data else None
        path = url.path
        self.requests.append((method.upper(), path, {}, body))
        if self.down:
            return AiohttpClientMockResponse(method, url, status=503, text="Service Unavailable")
        if path == "/app/v1.0/lumi/user/login":
            self.logins += 1
            if not self.password_ok:
                return self.reply(method, url, {"code": 810, "message": "Account or password error"})
            if self.login_code:
                return self.reply(method, url, {"code": self.login_code, "message": "nope"})
            self.token = f"token-{self.logins}"
            return self.reply(method, url, {"code": 0, "result": {"token": self.token, "userId": "user-1"}})
        if path == "/app/v1.0/lumi/res/query":
            if self.expire_next_query:
                self.expire_next_query = False
                return self.reply(method, url, {"code": 108, "message": "Token has expired"})
            if self.query_code:
                return self.reply(method, url, {"code": self.query_code, "message": "no permission"})
            options = body["data"][0]["options"]
            result = [
                {"attr": attr, "value": str(value), "timeStamp": stamp, "subjectId": body["data"][0]["subjectId"]}
                for attr, (value, stamp) in self.readings.items()
                if attr in options
            ]
            return self.reply(method, url, {"code": 0, "result": result})
        if path == "/app/v1.0/lumi/res/history/log":
            self.history_requests.append(body)
            start, end = body["startTime"], body.get("endTime", 2**62)
            size = int(body["size"])
            hits = [
                {"resourceId": "13.106.85", "value": str(code), "timeStamp": stamp, "subjectId": body["subjectId"]}
                for stamp, code in sorted(self.stage_log, reverse=True)
                if start <= stamp <= end
            ][:size]
            return self.reply(method, url, {"code": 0, "result": {"data": hits, "scanId": ""}})
        if path == "/app/v1.0/lumi/app/position/device/query":
            return self.reply(method, url, {"code": 0, "result": {"devices": self.devices}})
        return AiohttpClientMockResponse(method, url, status=404, text="Not Found")

    @staticmethod
    def reply(method: str, url: URL, payload: dict[str, Any]) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(method, url, json=payload)


@pytest.fixture
def aqara(aioclient_mock: AiohttpClientMocker) -> FakeAqara:
    fake = FakeAqara()
    fake.install(aioclient_mock)
    return fake


def md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()
