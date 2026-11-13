"""Выдача действующего access token с безопасным обновлением.

Refresh token ротируется при каждом обновлении, поэтому обновления
сериализуются: asyncio.Lock внутри процесса + файловая блокировка
между процессами (API, бот, планировщик). После захвата блокировки
учётные данные перечитываются — их мог уже обновить другой процесс.
"""
from __future__ import annotations

import asyncio
import errno
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, BinaryIO, Callable

import httpx

from ..connectors.base import AIConnectionUnavailable
from .oauth import REAUTH_ERRORS, REQUIRED_SCOPE, OAuthError, refresh_tokens
from .token_storage import ChatGPTCredentials, EncryptedTokenStorage

if os.name == "nt":
    import msvcrt
else:
    import fcntl

log = logging.getLogger(__name__)
REFRESH_MARGIN = timedelta(minutes=5)


def _try_lock(handle: BinaryIO) -> bool:
    try:
        if os.name == "nt":
            # Windows допускает блокировку байта за концом пустого файла.
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise
    return True


def _check_scope(creds: ChatGPTCredentials) -> None:
    if REQUIRED_SCOPE not in creds.scopes:
        raise AIConnectionUnavailable(
            f"Нет разрешения {REQUIRED_SCOPE} на использование подписки ChatGPT", reason="scope_missing")


class TokenManager:
    def __init__(self, storage: EncryptedTokenStorage,
                 client_factory: Callable[[], httpx.AsyncClient] | None = None):
        self.storage = storage
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=30))
        self._lock = asyncio.Lock()

    def load(self) -> ChatGPTCredentials | None:
        return self.storage.load()

    @staticmethod
    def _needs_refresh(creds: ChatGPTCredentials) -> bool:
        now = datetime.now(timezone.utc)
        if creds.earliest_refresh_at and now < creds.earliest_refresh_at and now < creds.expires_at:
            return False  # сервер просит не обновлять раньше earliest_refresh_at, пока токен действует
        return now >= creds.expires_at - REFRESH_MARGIN

    @asynccontextmanager
    async def locked(self) -> AsyncIterator[None]:
        """Один процесс меняет токены одновременно. Закрытие файла снимает блокировку."""
        async with self._lock:
            path = self.storage.lock_path
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("a+b") as handle:
                while not _try_lock(handle):
                    await asyncio.sleep(0.05)
                try:
                    yield
                finally:
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    async def get_credentials(self, *, force_refresh: bool = False,
                              rejected_access_token: str | None = None) -> ChatGPTCredentials:
        creds = self.storage.load()
        if creds is None:
            raise AIConnectionUnavailable("Аккаунт ChatGPT Plus не подключён", reason="not_connected")
        _check_scope(creds)
        if not force_refresh and not self._needs_refresh(creds):
            return creds
        rejected_access_token = rejected_access_token or creds.access_token
        async with self.locked():
            creds = self.storage.load()
            if creds is None:
                raise AIConnectionUnavailable("Аккаунт ChatGPT Plus отключён", reason="not_connected")
            _check_scope(creds)
            if (not force_refresh or rejected_access_token != creds.access_token) and not self._needs_refresh(creds):
                return creds
            return await self._refresh(creds)

    async def _refresh(self, creds: ChatGPTCredentials) -> ChatGPTCredentials:
        async with self._client_factory() as client:
            try:
                body = await refresh_tokens(client, client_id=creds.client_id, refresh_token=creds.refresh_token)
            except OAuthError as e:
                if e.error in REAUTH_ERRORS:
                    self.storage.clear()
                    raise AIConnectionUnavailable(
                        "Авторизация ChatGPT истекла или отозвана — войдите заново", reason="reauth_required") from e
                if e.error == "invalid_client":
                    raise AIConnectionUnavailable("Неверный client_id приложения", reason="invalid_client") from e
                raise AIConnectionUnavailable(f"Не удалось обновить токен: {e}", reason="refresh_failed") from e
            except httpx.TransportError as e:
                raise AIConnectionUnavailable(f"Сеть недоступна при обновлении токена: {e}",
                                              reason="network") from e
        if not body.get("refresh_token"):
            raise AIConnectionUnavailable("Ответ OpenAI не содержит новый refresh token", reason="refresh_failed")
        new = ChatGPTCredentials.from_token_response(body, client_id=creds.client_id,
                                                     host_id=creds.ext_agent_host_id, previous=creds)
        self.storage.save(new)
        _check_scope(new)
        log.info("Токен ChatGPT обновлён, действует до %s", new.expires_at.isoformat())
        return new
