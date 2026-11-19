"""The Aqara client: signing, headers, sign-in and errors."""

from __future__ import annotations

from base64 import b64decode

import pytest

from custom_components.fp2_sleep.api import (
    APP_VERSION,
    REGIONS,
    AqaraAuthError,
    AqaraClient,
    AqaraConnectionError,
    AqaraError,
    password_md5,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import DID, PASSWORD, md5


def make_client(hass, **kwargs) -> AqaraClient:
    return AqaraClient(
        async_get_clientsession(hass), "USA", "me@example.com", password_md5(PASSWORD), "PHONE-1", **kwargs
    )


async def test_requests_are_signed_like_the_app(hass, aqara, aioclient_mock) -> None:
    sessions = []
    client = make_client(hass, on_session=lambda token, user: sessions.append((token, user)))
    await client.query(DID, ["heartrate_value"])
    assert sessions == [("token-1", "user-1")]
    (_, _, login_body, login_headers), (_, _, query_body, headers) = aioclient_mock.mock_calls
    appid, appkey = REGIONS["USA"]["appid"], REGIONS["USA"]["appkey"]
    # Sign-in: no token in the signature.
    assert login_headers["Sign"] == md5(
        f"Appid={appid}&Nonce={login_headers['Nonce']}&Time={login_headers['Time']}&{login_body}&{appkey}"
    )
    assert "Token" not in login_headers
    # Signed-in request: token in the signature and headers.
    assert headers["Sign"] == md5(
        f"Appid={appid}&Nonce={headers['Nonce']}&Time={headers['Time']}&Token=token-1&{query_body}&{appkey}"
    )
    assert headers["Token"] == "token-1" and headers["Userid"] == "user-1"
    # The same app-like headers on every request, with one stable phone id.
    for sent in (login_headers, headers):
        assert sent["App-Version"] == APP_VERSION
        assert sent["Sys-Type"] == "1"
        assert sent["PhoneId"] == "PHONE-1"
        assert sent["Area"] == "USA"


async def test_sign_in_sends_the_encrypted_password_hash(hass, aqara) -> None:
    await make_client(hass).login()
    body = aqara.requests[0][3]
    assert body["account"] == "me@example.com" and body["encryptType"] == 2
    assert len(b64decode(body["password"])) == 128  # RSA-1024 block
    assert PASSWORD not in str(body) and password_md5(PASSWORD) not in str(body)


async def test_an_expired_session_signs_in_again_once(hass, aqara) -> None:
    client = make_client(hass, token="old", user_id="user-1")
    aqara.expire_next_query = True
    items = await client.query(DID, ["heartrate_value"])
    assert items[0]["value"] == "66"
    assert aqara.logins == 1 and client.token == "token-1"


async def test_errors_are_classified(hass, aqara) -> None:
    aqara.password_ok = False
    with pytest.raises(AqaraAuthError):
        await make_client(hass).login()
    aqara.password_ok = True
    aqara.login_code = 106
    with pytest.raises(AqaraError) as err:
        await make_client(hass).login()
    assert not isinstance(err.value, AqaraAuthError) and err.value.code == 106
    aqara.login_code = 0
    aqara.query_code = 755
    with pytest.raises(AqaraError) as err:
        await make_client(hass).query(DID, ["lux"])
    assert err.value.code == 755
    aqara.down = True
    with pytest.raises(AqaraConnectionError):
        await make_client(hass).login()


async def test_device_list(hass, aqara) -> None:
    devices = await make_client(hass).devices()
    assert [d["model"] for d in devices] == ["lumi.motion.agl001", "lumi.gateway.acn01"]
    assert aqara.requests[-1][:2] == ("GET", "/app/v1.0/lumi/app/position/device/query")
