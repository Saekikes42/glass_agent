"""Полный сценарий входа Sign in with ChatGPT (используется CLI и REST API).

Redirect идёт на loopback 127.0.0.1, поэтому вход через API работает, когда
браузер администратора и backend на одной машине (локальная установка). Для
своей VM: войти локально и перенести зашифрованный файл учётных данных (см. docs).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from .oauth import (
    DYNAMIC_CLIENT_ID, REQUIRED_SCOPE, AuthAttempt, CallbackResult, LoopbackCallbackServer, OAuthError, build_authorization_url,
    exchange_code, load_or_create_host_id, parse_callback, validate_id_token,
)
from .token_refresh import TokenManager
from .token_storage import ChatGPTCredentials

log = logging.getLogger(__name__)


async def finish_login(cb: CallbackResult, attempt: AuthAttempt, host_id: str, tm: TokenManager,
                       client: httpx.AsyncClient | None = None) -> ChatGPTCredentials:
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        body = await exchange_code(client, client_id=cb.client_id, code=cb.code, attempt=attempt)
        id_token = body.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise OAuthError("OpenAI не вернул id_token", error="invalid_id_token")
        claims = await validate_id_token(client, id_token, client_id=cb.client_id, nonce=attempt.nonce)
    finally:
        if own:
            await client.aclose()
    creds = ChatGPTCredentials.from_token_response(body, client_id=cb.client_id, host_id=host_id, claims=claims)
    if {"offline_access", REQUIRED_SCOPE}.intersection(creds.scopes) and not creds.refresh_token:
        raise OAuthError("OpenAI не вернул refresh token", error="missing_refresh_token")
    async with tm.locked():
        current = tm.load()
        if not attempt.is_registration and (
            current is None or current.client_id != cb.client_id
            or (current.subject is not None and current.subject != creds.subject)
        ):
            raise OAuthError("Аккаунт ChatGPT изменился во время входа", error="account_mismatch")
        tm.storage.save(creds)
    return creds


@dataclass
class LoginSession:
    url: str
    redirect_uri: str
    status: str = "waiting"  # waiting | connected | error
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {"status": self.status, "authorization_url": self.url if self.status == "waiting" else None,
                "redirect_uri": self.redirect_uri, "error": self.error}


async def start_login(tm: TokenManager, *, host_id_path, agent_name: str, port: int,
                      timeout: float = 300) -> LoginSession:
    """Поднимает loopback-сервер, возвращает ссылку входа; обмен кода — в фоне."""
    host_id = load_or_create_host_id(host_id_path)
    prev = tm.load()
    client_id = prev.client_id if prev else DYNAMIC_CLIENT_ID
    server = LoopbackCallbackServer(port)
    await server.__aenter__()  # слушать до открытия браузера (требование документации)
    attempt = AuthAttempt(client_id=client_id, redirect_uri=server.redirect_uri)
    url = build_authorization_url(attempt, host_id=host_id, agent_name=agent_name,
                                  login_hint=prev.email if prev else None,
                                  prompt="consent" if prev and REQUIRED_SCOPE not in prev.scopes else None)
    sess = LoginSession(url=url, redirect_uri=server.redirect_uri)

    async def wait() -> None:
        try:
            query = await server.wait(timeout)
            cb = parse_callback(query, attempt)
            creds = await finish_login(cb, attempt, host_id, tm)
            if REQUIRED_SCOPE in creds.scopes:
                sess.status = "connected"
            else:
                sess.status = "error"
                sess.error = f"AI_CONNECTION_UNAVAILABLE: разрешение {REQUIRED_SCOPE} не выдано"
        except asyncio.TimeoutError:
            sess.status, sess.error = "error", "Время ожидания входа истекло"
        except OAuthError as e:
            sess.status, sess.error = "error", str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("Ошибка входа ChatGPT")
            sess.status, sess.error = "error", f"{type(e).__name__}: {e}"
        finally:
            await server.__aexit__(None, None, None)

    sess.task = asyncio.create_task(wait())
    return sess
