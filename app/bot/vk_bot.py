"""Интерфейс Glass Agent в личных сообщениях сообщества ВКонтакте."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from app.bot.inbox import BotInbox
from app.bot.social import Buttons, SocialBot, SocialNotifier
from app.parsers.vk_parser import VKAccessError, VKAuthError, VKError, VKParser
from app.services.publisher import PublishError

log = logging.getLogger(__name__)
MESSAGE_LIMIT = 4000
VK_INBOX_LOCK = 713025483


def _https_url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise PublishError("VK вернул некорректный URL", permanent=True)
    return value


class VKMessenger:
    def __init__(self, token: SecretStr | str, *, api_version: str = "5.199",
                 api_base: str = "https://api.vk.ru/method", transport: httpx.AsyncBaseTransport | None = None):
        self._api = VKParser(token, api_version=api_version, api_base=api_base, max_retries=1,
                             min_interval=0 if transport else 0.34)
        self._transport = transport
        self._api_lock = asyncio.Lock()

    async def call(self, method: str, **params: Any) -> Any:
        async with self._api_lock, httpx.AsyncClient(timeout=30, transport=self._transport) as client:
            try:
                return await self._api.call(client, method, **params)
            except (VKError, ValueError, KeyError, TypeError) as exc:
                code = exc.code if isinstance(exc, VKError) else -1
                # Ошибки API могут содержать URL или параметры запроса.
                permanent = isinstance(exc, VKAuthError) or (
                    method == "groups.getLongPollServer" and (isinstance(exc, VKAccessError) or code in (100, 1000))
                )
                raise PublishError(f"VK {method}: ошибка {code}", permanent=permanent) from None

    async def _upload_image(self, peer_id: int, path: Path) -> str:
        try:
            server = await self.call("photos.getMessagesUploadServer", peer_id=peer_id)
            async with httpx.AsyncClient(timeout=60, transport=self._transport) as client:
                with path.open("rb") as image:
                    response = await client.post(_https_url(server["upload_url"]), files={"photo": image})
                response.raise_for_status()
            data = response.json()
            result = await self.call("photos.saveMessagesPhoto",
                                     photo=data["photo"], server=data["server"], hash=data["hash"])
            photo = result[0]
            attachment = f"photo{photo['owner_id']}_{photo['id']}"
            if photo.get("access_key"):
                attachment += f"_{photo['access_key']}"
            return attachment
        except (OSError, httpx.HTTPError, TypeError, ValueError, KeyError, IndexError):
            raise PublishError("VK: не удалось загрузить изображение в сообщения") from None

    async def send(self, external_id: str, text: str, *,
                   buttons: Buttons | None = None, image: Path | None = None) -> None:
        peer_id = int(external_id)
        attachment = await self._upload_image(peer_id, image) if image is not None else None
        keyboard = None
        if buttons:
            keyboard = json.dumps({"inline": True, "buttons": [
                [{"action": {"type": "callback", "label": label,
                             "payload": json.dumps({"glass": payload}, separators=(",", ":"))},
                  "color": "secondary"} for label, payload in row] for row in buttons
            ]}, ensure_ascii=False)
        chunks = [text[i:i + MESSAGE_LIMIT] for i in range(0, len(text), MESSAGE_LIMIT)] or ["…"]
        for i, chunk in enumerate(chunks):
            await self.call("messages.send", peer_id=peer_id, random_id=secrets.randbelow(2**31 - 1) + 1,
                            message=chunk, attachment=attachment if i == 0 else None,
                            keyboard=keyboard if i == len(chunks) - 1 else None)

    async def poll(self, server: str, key: str, ts: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=35, transport=self._transport) as client:
                result = await client.get(_https_url(server), params={"act": "a_check", "key": key,
                                                                    "ts": ts, "wait": 25})
                result.raise_for_status()
            body = result.json()
            if not isinstance(body, dict):
                raise ValueError("invalid response")
            return body
        except (httpx.HTTPError, ValueError) as exc:
            raise PublishError(f"VK Long Poll: {type(exc).__name__}") from None


class VKNotifier(SocialNotifier):
    def __init__(self, messenger: VKMessenger):
        super().__init__("vk", messenger)


class VKBot:
    def __init__(self, messenger: VKMessenger, bot: SocialBot, group_id: int):
        self.messenger = messenger
        self.bot = bot
        self.group_id = group_id

    def parse_event(self, update: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        if update.get("group_id") != self.group_id:
            return None
        obj = update.get("object") or {}
        if not isinstance(obj, dict):
            return None
        kind = update.get("type")
        if kind == "message_new":
            message = obj.get("message") or {}
            if not isinstance(message, dict):
                return None
            author = message.get("from_id")
            text = message.get("text")
            if not (type(author) is int and author > 0 and message.get("peer_id") == author
                    and not message.get("out") and isinstance(text, str) and text.strip() and "\x00" not in text):
                return None
            message_id = message.get("conversation_message_id") or message.get("id")
            if type(message_id) is int and message_id > 0:
                return f"{self.group_id}:msg:{author}:{message_id}", {"external_id": str(author), "text": text}
        elif kind == "message_event":
            author = obj.get("user_id")
            event_id = obj.get("event_id")
            payload = obj.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    return None
            if (type(author) is int and author > 0 and obj.get("peer_id") == author
                    and isinstance(event_id, str) and event_id and "\x00" not in event_id and isinstance(payload, dict)
                    and isinstance(payload.get("glass"), str) and "\x00" not in payload["glass"]):
                digest = hashlib.sha256(event_id.encode()).hexdigest()
                return f"{self.group_id}:cb:{author}:{digest}", {"external_id": str(author), "event_id": event_id,
                                                                "action": payload["glass"]}
        return None

    async def handle(self, update: dict[str, Any]) -> None:
        event = self.parse_event(update)
        if event is not None:
            await self.dispatch(event[1])

    async def dispatch(self, payload: dict[str, Any]) -> None:
        external_id = payload["external_id"]
        if "event_id" not in payload:
            await self.bot.handle_message(external_id, payload["text"])
            return
        try:
            author = int(external_id)
            await self.messenger.call("messages.sendMessageEventAnswer", event_id=payload["event_id"],
                                      user_id=author, peer_id=author)
        except PublishError as exc:
            log.warning("VK: не удалось подтвердить нажатие кнопки: %s", exc)
        note = await self.bot.handle_action(external_id, payload["action"])
        await self.messenger.send(external_id, note)

    async def run(self, stop: asyncio.Event | None = None, *, inbox: BotInbox | None = None) -> None:
        stop = stop or asyncio.Event()
        group_key = str(self.group_id)
        ts = await inbox.get_checkpoint(group_key) if inbox is not None else None
        connection = None
        while not stop.is_set():
            try:
                if connection is None:
                    connection = await self.messenger.call("groups.getLongPollServer", group_id=self.group_id)
                    if not isinstance(connection, dict) or not all(connection.get(key) for key in ("server", "key", "ts")):
                        connection = None
                        raise PublishError("VK вернул неполные данные сервера Long Poll")
                    if ts is None:
                        ts = str(connection["ts"])
                body = await self.messenger.poll(connection["server"], connection["key"], ts)
                failure = body.get("failed")
                if failure == 1:
                    next_ts = str(body["ts"]) if body.get("ts") else None
                    if next_ts is not None and inbox is not None:
                        await inbox.enqueue([], checkpoint=(group_key, next_ts))
                    ts = next_ts
                    if ts is None:
                        connection = None
                elif failure in (2, 3):
                    connection = None
                    if failure == 3:
                        ts = None
                elif failure is not None:
                    raise PublishError(f"VK Long Poll: код ошибки {failure}", permanent=True)
                else:
                    updates = body.get("updates")
                    if not isinstance(updates, list) or not body.get("ts"):
                        raise PublishError("VK вернул неполный пакет событий Long Poll")
                    next_ts = str(body["ts"])
                    if inbox is not None:
                        events = [event for update in updates if isinstance(update, dict)
                                  if (event := self.parse_event(update)) is not None]
                        await inbox.enqueue(events, checkpoint=(group_key, next_ts))
                    else:
                        for update in updates:
                            if isinstance(update, dict):
                                await self.handle(update)
                    ts = next_ts
            except (PublishError, SQLAlchemyError, OSError) as exc:
                if isinstance(exc, PublishError) and exc.permanent:
                    raise
                log.warning("Опрос VK временно недоступен: %s", type(exc).__name__)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=3)
                except TimeoutError:
                    continue
