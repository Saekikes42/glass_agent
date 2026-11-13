"""Защищённое хранение OAuth-токенов ChatGPT.

Токены шифруются (Fernet, AES-128-CBC + HMAC) и пишутся атомарно в файл
с доступом для владельца. Ключ — GLASS_SECRET_KEY или автоматически созданный
data/secrets/master.key. Токены не попадают во Frontend, логи и в открытом
виде в БД.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field, SecretStr


@lru_cache(maxsize=1)
def _windows_owner_sid() -> str:
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    result = subprocess.check_output([str(system32 / "whoami.exe"), "/user", "/fo", "csv", "/nh"])
    match = re.search(rb"S-1-(?:\d+-)+\d+", result)
    if match is None:
        raise RuntimeError("Не удалось определить владельца файлов учётных данных")
    return match.group().decode("ascii")


def _write_private(path: Path, data: bytes, *, replace: bool = True) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".cred", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            if os.name == "nt":
                system32 = Path(os.environ["SystemRoot"]) / "System32"
                subprocess.run([str(system32 / "icacls.exe"), str(tmp), "/inheritance:r",
                                "/grant:r", f"*{_windows_owner_sid()}:(F)"],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            for attempt in range(5):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if os.name != "nt" or attempt == 4:
                        raise
                    time.sleep(0.02)
        elif os.name == "nt":
            os.rename(tmp, path)  # Windows не заменяет существующий путь при rename.
        else:
            # Опубликовать полностью записанный ключ, не затирая ключ другого процесса.
            os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def load_fernet(secret: SecretStr | str | None, key_path: Path) -> Fernet:
    if secret:
        raw = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
        return Fernet(raw.encode())
    key_path = Path(key_path)
    if not key_path.exists():
        try:
            _write_private(key_path, Fernet.generate_key(), replace=False)
        except FileExistsError:
            pass
    return Fernet(key_path.read_bytes().strip())


class ChatGPTCredentials(BaseModel):
    """Запись учётных данных (поля по примеру из token-reference)."""

    email: str | None = None
    issuer: str = "https://auth.openai.com"
    subject: str | None = None
    client_id: str
    ext_agent_host_id: str
    id_token: str | None = None
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 3600
    scopes: list[str] = Field(default_factory=list)
    saved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    earliest_refresh_at: datetime | None = None
    plan_type: str | None = None

    @property
    def expires_at(self) -> datetime:
        return self.saved_at + timedelta(seconds=self.expires_in)

    def public_view(self) -> dict[str, Any]:
        """Безопасное представление без токенов (для API/логов)."""
        return {
            "email": self.email,
            "client_id": self.client_id[:10] + "…" if self.client_id else None,
            "scopes": self.scopes,
            "expires_at": self.expires_at.isoformat(),
            "saved_at": self.saved_at.isoformat(),
        }

    @classmethod
    def from_token_response(cls, body: dict[str, Any], *, client_id: str, host_id: str,
                            previous: "ChatGPTCredentials | None" = None,
                            claims: dict[str, Any] | None = None) -> "ChatGPTCredentials":
        scope = body.get("scope")
        scopes = sorted(scope.split()) if isinstance(scope, str) else (previous.scopes if previous else [])
        earliest = body.get("earliest_refresh_at")
        if isinstance(earliest, (int, float)):
            earliest = datetime.fromtimestamp(earliest, tz=timezone.utc)
        claims = claims or {}
        return cls(
            email=claims.get("email") or (previous.email if previous else None),
            subject=claims.get("sub") or (previous.subject if previous else None),
            client_id=client_id,
            ext_agent_host_id=host_id,
            id_token=body.get("id_token") or (previous.id_token if previous else None),
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token") or (previous.refresh_token if previous else ""),
            token_type=body.get("token_type", "Bearer"),
            expires_in=int(body.get("expires_in", 3600)),
            scopes=scopes,
            earliest_refresh_at=earliest if isinstance(earliest, datetime) else None,
        )


class EncryptedTokenStorage:
    def __init__(self, path: Path, fernet: Fernet):
        self.path = Path(path)
        self._fernet = fernet

    @property
    def lock_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".lock")

    def load(self) -> ChatGPTCredentials | None:
        try:
            raw = self._fernet.decrypt(self.path.read_bytes())
        except FileNotFoundError:
            return None
        except InvalidToken as e:
            raise RuntimeError("Не удалось расшифровать токены ChatGPT: изменился ключ шифрования") from e
        return ChatGPTCredentials.model_validate(json.loads(raw))

    def save(self, creds: ChatGPTCredentials) -> None:
        data = self._fernet.encrypt(creds.model_dump_json().encode())
        _write_private(self.path, data)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
