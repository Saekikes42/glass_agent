"""OAuth Sign in with ChatGPT и хранение токенов. Сервер OpenAI ЗАМОКАН — реальный вход не проверяется."""
import asyncio
import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet

from app.ai.auth.flow import finish_login
from app.ai.auth.oauth import (
    DYNAMIC_CLIENT_ID, REQUIRED_SCOPE, AuthAttempt, ConsentDeclined, OAuthError, RegistrationIncomplete,
    build_authorization_url, load_or_create_host_id, make_pkce, parse_callback,
)
from app.ai.auth.token_refresh import TokenManager
from app.ai.auth.token_storage import ChatGPTCredentials, EncryptedTokenStorage
from app.ai.connectors.base import AIConnectionUnavailable

SCOPES = ["chatgpt.tokens.use.direct", "email", "offline_access", "openid", "profile", "resource.invoke"]


def creds(**kw):
    base = dict(client_id="oaiapp_123", ext_agent_host_id="urn:uuid:x", access_token="AT1", refresh_token="RT1",
                scopes=SCOPES, email="a@b.c")
    base.update(kw)
    return ChatGPTCredentials(**base)


def test_pkce_s256():
    import base64, hashlib
    v, c = make_pkce()
    assert 43 <= len(v) <= 128
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def test_authorization_url_registration():
    a = AuthAttempt(client_id=DYNAMIC_CLIENT_ID, redirect_uri="http://127.0.0.1:1455/auth/callback")
    q = {k: v[0] for k, v in parse_qs(urlsplit(build_authorization_url(a, host_id="urn:uuid:h", agent_name="Glass Agent")).query).items()}
    assert q["client_id"] == DYNAMIC_CLIENT_ID and q["agent_name_hint"] == "Glass Agent"
    assert q["code_challenge_method"] == "S256" and q["resource"] == "https://api.openai.com/v1"
    assert REQUIRED_SCOPE in q["scope"].split() and "offline_access" in q["scope"].split()
    assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["ext_agent_host_id"] == "urn:uuid:h"
    b = AuthAttempt(client_id="oaiapp_1", redirect_uri=a.redirect_uri)
    assert "agent_name_hint" not in build_authorization_url(b, host_id="h", agent_name="x")


def test_callback_parsing():
    a = AuthAttempt(client_id=DYNAMIC_CLIENT_ID, redirect_uri="http://127.0.0.1:1/auth/callback")
    with pytest.raises(OAuthError):
        parse_callback("code=c&state=WRONG&client_id=oaiapp_1", a)
    with pytest.raises(ConsentDeclined):
        parse_callback(f"error=access_denied&state={a.state}", a)
    with pytest.raises(RegistrationIncomplete):
        parse_callback(f"code=c&state={a.state}", a)
    r = parse_callback(f"code=c&state={a.state}&client_id=oaiapp_9&scope=openid+chatgpt.tokens.use.direct", a)
    assert r.client_id == "oaiapp_9" and r.code == "c"
    returning = AuthAttempt(client_id="oaiapp_9", redirect_uri=a.redirect_uri)
    assert parse_callback(f"code=c&state={returning.state}", returning).client_id == "oaiapp_9"
    with pytest.raises(OAuthError, match="client_id"):
        parse_callback(f"code=c&state={returning.state}&client_id=oaiapp_OTHER", returning)


def assert_private(path):
    if os.name == "nt":
        from pathlib import Path

        system32 = Path(os.environ["SystemRoot"]) / "System32"
        user = subprocess.check_output([str(system32 / "whoami.exe")]).strip().lower()
        output = subprocess.check_output([str(system32 / "icacls.exe"), str(path)])
        entries = [line for line in output.lower().splitlines() if b":(" in line]
        assert len(entries) == 1 and entries[0].rstrip().endswith(user + b":(f)")
    else:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_host_id_stable(tmp_path):
    p = tmp_path / "host"
    a = load_or_create_host_id(p)
    assert a.startswith("urn:uuid:") and load_or_create_host_id(p) == a
    assert_private(p)


def test_storage_encrypted_0600(tmp_path):
    st = EncryptedTokenStorage(tmp_path / "c.enc", Fernet(Fernet.generate_key()))
    st.save(creds(access_token="SECRET_ACCESS"))
    raw = (tmp_path / "c.enc").read_bytes()
    assert b"SECRET_ACCESS" not in raw and b"RT1" not in raw
    assert_private(tmp_path / "c.enc")
    assert st.load().access_token == "SECRET_ACCESS"
    assert "access_token" not in str(st.load().public_view())


def _manager(tmp_path, handler):
    st = EncryptedTokenStorage(tmp_path / "c.enc", Fernet(Fernet.generate_key()))
    return st, TokenManager(st, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_not_connected_and_scope_missing(tmp_path):
    st, tm = _manager(tmp_path, lambda r: httpx.Response(500))
    with pytest.raises(AIConnectionUnavailable) as e:
        await tm.get_credentials()
    assert e.value.code == "AI_CONNECTION_UNAVAILABLE" and e.value.reason == "not_connected"
    st.save(creds(scopes=["openid", "email"]))
    with pytest.raises(AIConnectionUnavailable) as e:
        await tm.get_credentials()
    assert e.value.reason == "scope_missing"


async def test_refresh_rotation_serialized(tmp_path):
    calls = []

    def handler(req):
        form = parse_qs(req.content.decode())
        calls.append(form)
        assert form["grant_type"] == ["refresh_token"] and "scope" not in form
        assert form["client_id"] == ["oaiapp_123"] and form["resource"] == ["https://api.openai.com/v1"]
        return httpx.Response(200, json={"access_token": "AT2", "refresh_token": "RT2", "expires_in": 3600,
                                         "token_type": "Bearer", "scope": " ".join(SCOPES)})

    st, tm = _manager(tmp_path, handler)
    st.save(creds(saved_at=datetime.now(timezone.utc) - timedelta(minutes=58)))  # истекает через 2 минуты
    results = await asyncio.gather(*[tm.get_credentials() for _ in range(5)])
    assert len(calls) == 1  # один refresh на все параллельные запросы
    assert {r.access_token for r in results} == {"AT2"} and st.load().refresh_token == "RT2"


async def test_refresh_invalid_grant_requires_reauth(tmp_path):
    st, tm = _manager(tmp_path, lambda r: httpx.Response(400, json={"error": "refresh_token_reused"}))
    st.save(creds(saved_at=datetime.now(timezone.utc) - timedelta(hours=2)))
    with pytest.raises(AIConnectionUnavailable) as e:
        await tm.get_credentials()
    assert e.value.reason == "reauth_required" and st.load() is None


async def test_refresh_scope_revoked_and_rotated_token_saved(tmp_path):
    st, tm = _manager(tmp_path, lambda r: httpx.Response(200, json={
        "access_token": "AT2", "refresh_token": "RT2", "scope": "openid email",
    }))
    st.save(creds())
    with pytest.raises(AIConnectionUnavailable) as error:
        await tm.get_credentials(force_refresh=True)
    assert error.value.reason == "scope_missing"
    assert st.load().refresh_token == "RT2"


async def test_login_requires_id_token_and_ignores_callback_scope(tmp_path, monkeypatch):
    st, tm = _manager(tmp_path, lambda r: httpx.Response(500))
    attempt = AuthAttempt(client_id=DYNAMIC_CLIENT_ID, redirect_uri="http://127.0.0.1:1/auth/callback")
    callback = parse_callback(f"code=c&state={attempt.state}&client_id=oaiapp_9&scope={REQUIRED_SCOPE}", attempt)
    body = {"access_token": "AT1"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))) as client:
        with pytest.raises(OAuthError, match="id_token"):
            await finish_login(callback, attempt, "urn:uuid:host", tm, client)
        assert st.load() is None

        async def validate(*args, **kwargs):
            return {"sub": "verified-user"}

        monkeypatch.setattr("app.ai.auth.flow.validate_id_token", validate)
        body["id_token"] = "mock-validated"
        credentials = await finish_login(callback, attempt, "urn:uuid:host", tm, client)
    assert credentials.subject == "verified-user"
    assert REQUIRED_SCOPE not in credentials.scopes


async def test_file_lock_waiter_can_be_cancelled(tmp_path):
    st, owner = _manager(tmp_path, lambda r: httpx.Response(500))
    other = TokenManager(st)

    async def acquire():
        async with other.locked():
            return True

    async with owner.locked():
        waiter = asyncio.create_task(acquire())
        await asyncio.sleep(0.1)
        assert not waiter.done()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    assert await asyncio.wait_for(acquire(), timeout=1)
