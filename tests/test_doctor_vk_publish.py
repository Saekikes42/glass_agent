from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.db import session
from app.services import secrets
from scripts import doctor


@pytest.mark.parametrize(("company_token", "env_token", "scope", "group", "ok", "message"), [
    ("company-key", "env-key", 8196, {"can_post": 1, "admin_level": 3}, True, "право photos есть"),
    (None, "env-key", 8192, {"can_post": 1, "admin_level": 2}, True, "право photos отсутствует"),
    (None, None, 8196, None, False, "нет пользовательского токена"),
    ("company-key", "env-key", 4, None, False, "нет обязательного права wall"),
    ("company-key", None, 8196, {"can_post": 1, "admin_level": 1}, False, "не подтвердил право"),
    ("company-key", None, 8196, {"can_post": 0, "admin_level": 3}, False, "не подтвердил право"),
    ("company-key", "env-key", None, None, False, "код VK 5"),
])
async def test_vk_publish_preflight(monkeypatch, capsys, company_token, env_token, scope, group, ok, message):
    settings = get_settings()
    monkeypatch.setattr(settings, "vk_api_base", "https://vk.example/method")
    monkeypatch.setattr(settings, "vk_publish_token", SecretStr(env_token) if env_token else None)
    monkeypatch.setattr(settings, "vk_access_token", SecretStr("read-key"))
    monkeypatch.setattr(settings, "vk_bot_token", SecretStr("bot-key"))
    if company_token:
        monkeypatch.setattr(settings, "glass_secret_key", SecretStr("configured"))
    company = SimpleNamespace(id="company-id", settings={"channels": {"vk": "https://vk.ru/club123"}})

    class CompanySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def scalar(self, query):
            assert list(query.compile().params.values()) == ["chosen-company"]
            return company

        async def get(self, model, key):
            assert model.__name__ == "CompanySecret"
            assert key == ("company-id", "vk_token")
            return None

    async def get_secret(db, company_id, name):
        assert isinstance(db, CompanySession)
        assert (company_id, name) == ("company-id", "vk_token")
        return company_token

    monkeypatch.setattr(session, "get_sessionmaker", lambda: CompanySession)
    monkeypatch.setattr(secrets, "get_secret", get_secret)

    calls = []

    def vk(request):
        method = request.url.path.rsplit("/", 1)[-1]
        params = parse_qs(request.content.decode())
        assert params["access_token"] == [company_token or env_token]
        assert not request.url.query
        calls.append(method)
        if method == "account.getAppPermissions":
            if scope is None:
                return httpx.Response(200, json={"error": {
                    "error_code": 5, "error_msg": "https://internal.example/?token=company-key",
                }})
            return httpx.Response(200, json={"response": scope})
        assert method == "groups.getById"
        assert params["group_id"] == ["123"]
        assert params["fields"] == ["can_post,admin_level"]
        return httpx.Response(200, json={"response": {"groups": [{"id": 123, **group}]}})

    async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: async_client(transport=httpx.MockTransport(vk), **kw))
    args = SimpleNamespace(vk_publish_company="chosen-company", live=True, model=False)
    assert await doctor.main_async(args) == (0 if ok else 1)
    expected_calls = ["account.getAppPermissions"] if company_token or env_token else []
    if group:
        expected_calls.append("groups.getById")
    assert calls == expected_calls
    output = capsys.readouterr().out
    assert message in output
    assert all(secret not in output for secret in ("read-key", "bot-key", "company-key", "env-key", "internal.example"))
