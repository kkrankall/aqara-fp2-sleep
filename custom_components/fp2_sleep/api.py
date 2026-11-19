"""Client for Aqara's app API, the one the Aqara Home app itself uses.

Requests are signed with the Aqara Home app's public app id and key for the
region: Sign = md5("Appid=..&Nonce=..&Time=..[&Token=..][&body]&appkey").
The sign-in sends md5(password), RSA-encrypted with the app's public key.
"""

from __future__ import annotations

import asyncio
from base64 import b64encode
from collections.abc import Callable
import hashlib
import json
import time
from typing import Any
import uuid

import aiohttp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .const import LOGGER

# Aqara Home app constants: public, the same in every copy of the app. The
# EU pair differs; the rest share one.
REGIONS: dict[str, dict[str, str]] = {
    "USA": {
        "server": "https://aiot-rpc-usa.aqara.com",
        "appid": "94549908487478b220992a70",
        "appkey": "euGhPe2rcmxwculATNj45eEtnd50zp0I",
    },
    "EU": {
        "server": "https://rpc-ger.aqara.com",
        "appid": "7be1984f0556276133336839",
        "appkey": "Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3",
    },
    "CN": {
        "server": "https://aiot-rpc.aqara.cn",
        "appid": "94549908487478b220992a70",
        "appkey": "euGhPe2rcmxwculATNj45eEtnd50zp0I",
    },
    "RU": {
        "server": "https://rpc-ru.aqara.com",
        "appid": "94549908487478b220992a70",
        "appkey": "euGhPe2rcmxwculATNj45eEtnd50zp0I",
    },
    "KR": {
        "server": "https://rpc-kr.aqara.com",
        "appid": "94549908487478b220992a70",
        "appkey": "euGhPe2rcmxwculATNj45eEtnd50zp0I",
    },
}

PUBLIC_KEY = (
    b"-----BEGIN PUBLIC KEY-----\n"
    b"MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCG46slB57013JJs4Vvj5cVyMpR\n"
    b"9b+B2F+YJU6qhBEYbiEmIdWpFPpOuBikDs2FcPS19MiWq1IrmxJtkICGurqImRUt\n"
    b"4lP688IWlEmqHfSxSRf2+aH0cH8VWZ2OaZn5DWSIHIPBF2kxM71q8stmoYiV0oZs\n"
    b"rZzBHsMuBwA4LQdxBwIDAQAB\n"
    b"-----END PUBLIC KEY-----"
)

# The Aqara Home app version whose requests this client mirrors.
APP_VERSION = "6.4.5"
SYS_TYPE_IOS = "1"

LOGIN_PATH = "/app/v1.0/lumi/user/login"
QUERY_PATH = "/app/v1.0/lumi/res/query"
HISTORY_LOG_PATH = "/app/v1.0/lumi/res/history/log"
DEVICE_LIST_PATH = "/app/v1.0/lumi/app/position/device/query"
DEVICE_DETAIL_PATH = "/app/v1.0/lumi/dev/query/detail"

HISTORY_PAGE_SIZE = 300

CODE_OK = 0
CODE_INVALID_SIGN = 106
CODE_TOKEN_EXPIRED = 108
CODE_NO_PERMISSION = 755
# "Aqara did not answer": this client's own -1 plus server-side HTTP codes.
TRANSIENT_HTTP = frozenset({429, 500, 502, 503, 504})

TIMEOUT = aiohttp.ClientTimeout(total=30)


class AqaraError(Exception):
    """Aqara rejected a request; `code` is Aqara's (or the HTTP) code."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class AqaraConnectionError(AqaraError):
    """Aqara did not answer: network, timeout, busy server, unreadable reply."""


class AqaraAuthError(AqaraError):
    """Aqara refused the sign-in: wrong account, password or region."""


class AqaraSessionExpired(AqaraError):
    """The session token is no longer valid; sign in again."""


def password_md5(password: str) -> str:
    """What the sign-in needs: md5 of the password, as hex. Stored instead of
    the password itself."""
    return hashlib.md5(password.encode()).hexdigest()


def encrypt_password(md5_hex: str) -> str:
    key = serialization.load_pem_public_key(PUBLIC_KEY)
    return b64encode(key.encrypt(md5_hex.encode(), padding.PKCS1v15())).decode()


def new_phone_id() -> str:
    return str(uuid.uuid4()).upper()


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _message(res: dict[str, Any]) -> str:
    parts = [str(res.get(key)) for key in ("message", "msgDetails") if res.get(key)]
    return " ".join(parts)[:200] or "no message"


class AqaraClient:
    """One Aqara account in one region."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        region: str,
        account: str,
        password_hash: str,
        phone_id: str,
        *,
        token: str | None = None,
        user_id: str | None = None,
        on_session: Callable[[str, str], None] | None = None,
        lang: str = "en",
    ) -> None:
        if region not in REGIONS:
            raise ValueError(f"unknown region {region!r}")
        self._session = session
        self.region = region
        self._cfg = REGIONS[region]
        self._account = account
        self._password_md5 = password_hash
        self.phone_id = phone_id
        self.token = token
        self.user_id = user_id
        self.on_session = on_session
        self._lang = lang
        self._login_lock = asyncio.Lock()

    # Requests

    def _headers(self, signed_extra: str | None) -> dict[str, str]:
        nonce = _md5(str(uuid.uuid4()))
        now_ms = str(round(time.time() * 1000))
        parts = [f"Appid={self._cfg['appid']}", f"Nonce={nonce}", f"Time={now_ms}"]
        if self.token:
            parts.append(f"Token={self.token}")
        if signed_extra:
            parts.append(signed_extra)
        parts.append(self._cfg["appkey"])
        headers = {
            "Appid": self._cfg["appid"],
            "Nonce": nonce,
            "Time": now_ms,
            "Sign": _md5("&".join(parts)),
            "Area": self.region,
            "Lang": self._lang,
            "App-Version": APP_VERSION,
            "Sys-Type": SYS_TYPE_IOS,
            "PhoneId": self.phone_id,
        }
        if self.token:
            headers["Token"] = self.token
        if self.user_id:
            headers["Userid"] = self.user_id
        return headers

    async def _send(
        self, method: str, path: str, *, body: Any = None, query: str = "", sign_query: bool = False
    ) -> dict[str, Any]:
        """One request; the reply as a dict with an int `code`."""
        url = self._cfg["server"] + path + (f"?{query}" if query else "")
        data = None
        if method == "POST":
            data = json.dumps(body if body is not None else {})
            headers = self._headers(data)
            headers["Content-Type"] = "application/json; charset=utf-8"
        else:
            headers = self._headers(query if sign_query else None)
        try:
            async with self._session.request(
                method, url, data=data, headers=headers, timeout=TIMEOUT
            ) as resp:
                text = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise AqaraConnectionError(f"{type(err).__name__}: {err}", -1) from err
        try:
            res = json.loads(text)
        except ValueError:
            res = None
        if not isinstance(res, dict):
            if status >= 400:
                raise AqaraConnectionError(f"HTTP {status}", status)
            raise AqaraConnectionError("the reply was not a JSON object", -1)
        code = res.get("code")
        if not isinstance(code, int) or isinstance(code, bool):
            res["code"] = status if status >= 400 else -1
        return res

    @staticmethod
    def _check(res: dict[str, Any]) -> Any:
        code = res["code"]
        if code == CODE_OK:
            return res.get("result")
        if code == -1 or code in TRANSIENT_HTTP:
            raise AqaraConnectionError(_message(res), code)
        if code == CODE_TOKEN_EXPIRED:
            raise AqaraSessionExpired(_message(res), code)
        raise AqaraError(_message(res), code)

    async def login(self) -> None:
        """Sign in; raises AqaraAuthError when Aqara says no."""
        async with self._login_lock:
            self.token = None
            self.user_id = None
            res = await self._send(
                "POST",
                LOGIN_PATH,
                body={
                    "account": self._account,
                    "encryptType": 2,
                    "password": encrypt_password(self._password_md5),
                },
            )
            code = res["code"]
            if code == -1 or code in TRANSIENT_HTTP:
                raise AqaraConnectionError(_message(res), code)
            if code == CODE_INVALID_SIGN:
                # The app key was refused: not the user's password.
                raise AqaraError(_message(res), code)
            if code != CODE_OK:
                raise AqaraAuthError(_message(res), code)
            result = res.get("result")
            if not (isinstance(result, dict) and result.get("token") and result.get("userId")):
                raise AqaraConnectionError("the sign-in reply had no session", -1)
            self.token = str(result["token"])
            self.user_id = str(result["userId"])
            LOGGER.debug("Signed in to Aqara (%s)", self.region)
            if self.on_session:
                self.on_session(self.token, self.user_id)

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        """A signed-in request; signs in first if needed and once more when
        the session has expired."""
        if not self.token:
            await self.login()
        try:
            return self._check(await self._send(method, path, **kwargs))
        except AqaraSessionExpired:
            LOGGER.debug("Aqara session expired; signing in again")
            await self.login()
            return self._check(await self._send(method, path, **kwargs))

    # API

    async def query(self, did: str, attrs: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
        """Latest value and time of each resource (res/query)."""
        result = await self._call(
            "POST", QUERY_PATH, body={"data": [{"options": list(attrs), "subjectId": did}]}
        )
        if not isinstance(result, list):
            raise AqaraConnectionError(f"unreadable reading list ({type(result).__name__})", -1)
        return [item for item in result if isinstance(item, dict)]

    async def history_log(
        self,
        did: str,
        resource_ids: list[str],
        start_ms: int,
        end_ms: int | None = None,
        size: int = HISTORY_PAGE_SIZE,
        scan_id: str = "",
    ) -> list[dict[str, Any]]:
        """Reported values of the resources in a window, newest first."""
        body: dict[str, Any] = {
            "resourceIds": list(resource_ids),
            "scanId": scan_id,
            "size": str(size),
            "startTime": int(start_ms),
            "subjectId": did,
        }
        if end_ms is not None:
            body["endTime"] = int(end_ms)
        result = await self._call("POST", HISTORY_LOG_PATH, body=body)
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list):
            raise AqaraConnectionError("unreadable history reply", -1)
        return [entry for entry in data if isinstance(entry, dict)]

    async def devices(self) -> list[dict[str, Any]]:
        """Every device on the account (did, deviceName, model,
        firmwareVersion, positionName, state, ...)."""
        result = await self._call("GET", DEVICE_LIST_PATH)
        devices = result.get("devices") if isinstance(result, dict) else result
        if not isinstance(devices, list):
            raise AqaraConnectionError("unreadable device list", -1)
        return [device for device in devices if isinstance(device, dict)]
