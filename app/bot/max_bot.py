"""Личный бот MAX и очередь входящих событий в PostgreSQL."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.inbox import BotInbox
from app.bot.social import Buttons, SocialBot, SocialNotifier
from app.services.platforms import MaxAPI
from app.services.publisher import PublishError

log = logging.getLogger(__name__)
UPDATE_TYPES = ("bot_started", "message_created", "message_callback")
MAX_TEXT = 4000
MAX_UPDATE_BYTES = 128 * 1024
WEBHOOK_DB_TIMEOUT = 15
SHUTDOWN_TIMEOUT = 30


class MaxMessenger:
    def __init__(self, api: MaxAPI):
        self.api = api
        self._send_lock = asyncio.Lock()
        self._next_send_at = 0.0

    async def _send(self, external_id: str, body: dict[str, Any]) -> None:
        for attempt in range(4):
            try:
                # Защита от превышения двух отправок в секунду в один диалог.
                async with self._send_lock:
                    loop = asyncio.get_running_loop()
                    await asyncio.sleep(max(0.0, self._next_send_at - loop.time()))
                    self._next_send_at = loop.time() + 0.51
                    await self.api.request("POST", "/messages", params={"user_id": int(external_id)}, json=body)
                return
            except PublishError as exc:
                if exc.uncertain or attempt == 3:
                    raise
                if getattr(exc, "code", None) == "attachment.not.ready":
                    await asyncio.sleep(2 ** attempt)
                elif exc.retry_after is not None:
                    await asyncio.sleep(exc.retry_after)
                else:
                    raise

    async def send(self, external_id: str, text: str, *,
                   buttons: Buttons | None = None, image: Path | None = None) -> None:
        attachments = []
        if image is not None:
            token = await self.api.upload_image(image)
            attachments.append({"type": "image", "payload": {"token": token}})
        if buttons:
            keyboard = [[{"type": "callback", "text": label, "payload": payload} for label, payload in row]
                        for row in buttons]
            attachments.append({"type": "inline_keyboard", "payload": {"buttons": keyboard}})
        for offset in range(0, len(text) or 1, MAX_TEXT):
            body: dict[str, Any] = {"text": text[offset:offset + MAX_TEXT]}
            if attachments and offset + MAX_TEXT >= len(text):
                body["attachments"] = attachments
            await self._send(external_id, body)

    async def answer(self, callback_id: str, notification: str) -> None:
        await self.api.request("POST", "/answers", params={"callback_id": callback_id},
                               json={"notification": notification})


class MaxNotifier(SocialNotifier):
    def __init__(self, api: MaxAPI):
        super().__init__("max", MaxMessenger(api))


@dataclass(frozen=True)
class MaxEvent:
    key: str
    user_id: str
    text: str
    callback_id: str | None = None


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _user_id(user: Any) -> str | None:
    user = _object(user)
    value = user.get("user_id")
    if type(value) is not int or value <= 0 or user.get("is_bot"):
        return None
    return str(value)


def parse_event(update: Any) -> MaxEvent | None:
    update = _object(update)
    kind = update.get("update_type")
    message = _object(update.get("message"))
    recipient = _object(message.get("recipient"))
    if kind == "bot_started":
        user_id = _user_id(update.get("user"))
        stamp = update.get("timestamp")
        if not user_id or type(stamp) is not int:
            return None
        return MaxEvent(f"start:{user_id}:{stamp}", user_id, "/start")
    if recipient.get("chat_type") != "dialog":
        return None
    if kind == "message_created":
        user_id = _user_id(message.get("sender"))
        body = _object(message.get("body"))
        ref, value = body.get("mid"), body.get("text")
        callback_id = None
    elif kind == "message_callback":
        callback = _object(update.get("callback"))
        user_id = _user_id(callback.get("user"))
        if str(recipient.get("user_id")) != user_id:
            return None
        ref = callback_id = callback.get("callback_id")
        value = callback.get("payload")
    else:
        return None
    if not user_id or not isinstance(ref, str) or not ref.strip() or not isinstance(value, str) or not value.strip():
        return None
    try:
        digest = hashlib.sha256(ref.encode()).hexdigest()
        value.encode()
    except UnicodeError:
        return None
    # PostgreSQL JSONB не поддерживает нулевой символ.
    if "\x00" in ref or "\x00" in value:
        return None
    return MaxEvent(f"{kind}:{digest}", user_id, value, callback_id)


class MaxInbox:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], bot: SocialBot, messenger: MaxMessenger):
        self.bot = bot
        self.messenger = messenger
        self.queue = BotInbox(sessions, "max", 713025482, self._dispatch)

    async def enqueue(self, events: list[MaxEvent], *, checkpoint: tuple[str, str] | None = None) -> None:
        await self.queue.enqueue([(event.key, asdict(event)) for event in events], checkpoint=checkpoint)

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        event = MaxEvent(**payload)
        if event.callback_id is None:
            await self.bot.handle_message(event.user_id, event.text)
            return
        result = await self.bot.handle_action(event.user_id, event.text)
        if result:
            try:
                await self.messenger.answer(event.callback_id, result)
            except PublishError as exc:
                # Действие уже выполнено, повторять его для ответа на callback нельзя.
                if exc.permanent:
                    await self.messenger.send(event.user_id, result)
                else:
                    log.warning("MAX не принял подтверждение нажатия кнопки")

@asynccontextmanager
async def max_worker(inbox: MaxInbox):
    stop = asyncio.Event()
    worker = asyncio.create_task(inbox.queue.run(stop))
    try:
        yield stop
    finally:
        stop.set()
        try:
            await asyncio.wait_for(worker, SHUTDOWN_TIMEOUT)
        except TimeoutError:
            log.warning("Обработка события MAX прервана. После истечения блокировки проверьте её результат")


def validate_webhook_secret(secret: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{5,256}", secret):
        raise ValueError("MAX_WEBHOOK_SECRET: от 5 до 256 символов A–Z, a–z, 0–9, '_' или '-'")


async def register_webhook(api: MaxAPI, url: str, secret: str) -> None:
    validate_webhook_secret(secret)
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.port is not None or parsed.username
            or parsed.password or parsed.fragment):
        raise ValueError("Нужен публичный HTTPS URL вебхука на порту 443 без указания порта")
    result = await api.request("POST", "/subscriptions",
                               json={"url": url, "secret": secret, "update_types": list(UPDATE_TYPES)})
    if not isinstance(result, dict) or result.get("success") is not True:
        raise PublishError("MAX не подтвердил подписку", permanent=True)


def create_webhook_app(inbox: MaxInbox, secret: str, *,
                       on_start: Callable[[], Awaitable[None]] | None = None) -> FastAPI:
    validate_webhook_secret(secret)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if on_start is not None:
            await on_start()
        async with max_worker(inbox):
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/max/webhook")
    async def webhook(request: Request):
        provided = request.headers.get("X-Max-Bot-Api-Secret", "")
        if not hmac.compare_digest(provided.encode(), secret.encode()):
            raise HTTPException(403, "Неверный секрет вебхука MAX")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_UPDATE_BYTES:
                raise HTTPException(413, "Обновление MAX слишком велико")
            body.extend(chunk)
        try:
            update = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(400, "Ожидается JSON MAX") from exc
        if event := parse_event(update):
            try:
                async with asyncio.timeout(WEBHOOK_DB_TIMEOUT):
                    await inbox.enqueue([event])
            except (SQLAlchemyError, OSError, TimeoutError) as exc:
                log.warning("Не удалось сохранить событие MAX: %s", type(exc).__name__)
                raise HTTPException(503, "Очередь MAX временно недоступна") from exc
        return {"ok": True}

    return app


async def poll_updates(api: MaxAPI, inbox: MaxInbox, stop: asyncio.Event) -> None:
    subscriptions = await api.request("GET", "/subscriptions")
    if subscriptions.get("subscriptions"):
        raise ValueError("У бота есть подписка Webhook. Для тестового опроса сначала отключите её в MAX.")
    bot_id = (await api.request("GET", "/me")).get("user_id")
    if type(bot_id) is not int or bot_id <= 0:
        raise PublishError("MAX не вернул ID бота", permanent=True)
    checkpoint_key = str(bot_id)
    marker = await inbox.queue.get_checkpoint(checkpoint_key)
    while not stop.is_set():
        params = {"timeout": 25}
        if marker is not None:
            params["marker"] = marker
        try:
            result = await api.request("GET", "/updates", params=params)
            events = [event for update in result["updates"] if (event := parse_event(update))]
            next_marker = result.get("marker")
            committed_marker = str(next_marker) if type(next_marker) is int else marker
            await inbox.enqueue(events, checkpoint=(checkpoint_key, committed_marker)
                                if committed_marker is not None else None)
            marker = committed_marker
        except PublishError as exc:
            if exc.permanent:
                raise
            await asyncio.sleep(exc.retry_after or 5)
        except (SQLAlchemyError, OSError) as exc:
            log.warning("Не удалось сохранить события опроса MAX: %s", type(exc).__name__)
            await asyncio.sleep(5)
