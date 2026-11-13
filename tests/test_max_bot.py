import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.bot.max_bot import (
    MAX_UPDATE_BYTES,
    MaxInbox,
    MaxMessenger,
    create_webhook_app,
    max_worker,
    parse_event,
    poll_updates,
    register_webhook,
)
from app.db.models import BotUpdate
from app.services.platforms import MaxAPI
from app.services.publisher import PublishError


def message(text="Подготовь публикацию", *, chat_type="dialog", mid="mid.1"):
    return {
        "update_type": "message_created",
        "message": {
            "sender": {"user_id": 19, "is_bot": False},
            "recipient": {"user_id": 99, "chat_type": chat_type},
            "body": {"mid": mid, "text": text},
        },
    }


def callback(recipient_id=19):
    return {
        "update_type": "message_callback",
        "callback": {"callback_id": "callback.1", "payload": "ap:post:review", "user": {"user_id": 19}},
        "message": {"recipient": {"user_id": recipient_id, "chat_type": "dialog"}},
    }


def test_max_accepts_only_personal_input():
    event = parse_event(message())
    assert event.user_id == "19" and event.text == "Подготовь публикацию"
    assert event.key == parse_event(message("Изменённый текст")).key
    assert parse_event(message(chat_type="chat")) is None
    assert parse_event(message(chat_type="channel")) is None
    assert parse_event(message("\x00")) is None
    assert parse_event(callback()).callback_id == "callback.1"
    assert parse_event(callback(20)) is None
    assert parse_event({"update_type": "bot_started", "timestamp": 10, "user": {"user_id": 19}}).text == "/start"


async def test_max_send_splits_and_attaches_controls(tmp_path):
    requests = []

    def transport(req):
        import json

        assert req.headers["Authorization"] == "test-token"
        if req.url.path == "/uploads":
            return httpx.Response(200, json={"url": "https://iu.oneme.ru/upload"})
        if req.url.path == "/upload":
            assert b'name="data"' in req.content
            return httpx.Response(200, json={"photos": {"p": {"token": "fake-photo"}}})
        requests.append((req.url.path, dict(req.url.params), json.loads(req.content)))
        return httpx.Response(200, json={"success": True})

    image = tmp_path / "post.png"
    image.write_bytes(b"test-image")
    messenger = MaxMessenger(MaxAPI("test-token", transport=httpx.MockTransport(transport)))
    await messenger.send("19", "я" * 4001, buttons=[[("Согласовать", "ap:post:token")]], image=image)
    await messenger.answer("callback.1", "Согласовано")
    assert requests[0] == ("/messages", {"user_id": "19"}, {"text": "я" * 4000})
    _, _, final = requests[1]
    assert final["text"] == "я"
    assert final["attachments"] == [
        {"type": "image", "payload": {"token": "fake-photo"}},
        {"type": "inline_keyboard", "payload": {"buttons": [[{
            "type": "callback", "text": "Согласовать", "payload": "ap:post:token",
        }]]}},
    ]
    assert requests[2] == ("/answers", {"callback_id": "callback.1"}, {"notification": "Согласовано"})


async def test_max_webhook_auth_before_enqueue_and_body_limit():
    inbox = AsyncMock()
    app = create_webhook_app(inbox, "fake-secret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/max/webhook", json=message())).status_code == 403
        headers = {"X-Max-Bot-Api-Secret": "fake-secret"}
        assert (await client.post("/max/webhook", content=b"{" * (MAX_UPDATE_BYTES + 1),
                                  headers=headers)).status_code == 413
        assert (await client.post("/max/webhook", content=b"{", headers=headers)).status_code == 400
        inbox.enqueue.assert_not_called()
        assert (await client.post("/max/webhook", json=message(), headers=headers)).status_code == 200
        inbox.enqueue.assert_awaited_once_with([parse_event(message())])
        inbox.enqueue.side_effect = TimeoutError()
        assert (await client.post("/max/webhook", json=message(), headers=headers)).status_code == 503


async def test_max_register_requires_https_and_poll_respects_active_webhook():
    api = AsyncMock()
    with pytest.raises(ValueError, match="HTTPS"):
        await register_webhook(api, "http://public.example/max/webhook", "fake-secret")
    with pytest.raises(ValueError, match="HTTPS"):
        await register_webhook(api, "https://public.example:443/max/webhook", "fake-secret")
    api.request.assert_not_called()
    api.request.return_value = {"success": True}
    await register_webhook(api, "https://public.example/max/webhook", "fake-secret")
    api.request.assert_awaited_once_with("POST", "/subscriptions", json={
        "url": "https://public.example/max/webhook", "secret": "fake-secret",
        "update_types": ["bot_started", "message_created", "message_callback"],
    })
    api.request.return_value = {"subscriptions": [{"url": "https://public.example/max/webhook"}]}
    with pytest.raises(ValueError, match="Webhook"):
        await poll_updates(api, AsyncMock(), asyncio.Event())


async def test_max_poll_commits_checkpoint_with_events():
    api, inbox = AsyncMock(), AsyncMock()
    api.request.side_effect = [
        {"subscriptions": []}, {"user_id": 99}, {"marker": 830, "updates": [message()]},
    ]
    inbox.queue.get_checkpoint.return_value = "829"
    stop = asyncio.Event()

    async def enqueue(events, *, checkpoint):
        assert events == [parse_event(message())] and checkpoint == ("99", "830")
        stop.set()

    inbox.enqueue.side_effect = enqueue
    await poll_updates(api, inbox, stop)
    api.request.assert_awaited_with("GET", "/updates", params={"timeout": 25, "marker": "829"})


async def test_max_durable_queue_dedup_and_restart(pg):
    _, sessions = pg
    first = parse_event(message())
    second = parse_event(callback())
    assert first is not None and second is not None
    bot, messenger = AsyncMock(), AsyncMock()
    bot.handle_action.return_value = "Согласовано"
    inbox = MaxInbox(sessions, bot, messenger)
    await inbox.enqueue([first, first, second])
    abandoned = await inbox.queue._claim()
    assert abandoned[2]["key"] == first.key
    async with sessions.begin() as session:
        row = await session.get(BotUpdate, abandoned[0])
        row.locked_at = datetime.now(UTC) - timedelta(minutes=11)

    restarted = MaxInbox(sessions, bot, messenger)
    assert await restarted.queue.process_next()
    assert not await restarted.queue.process_next()
    bot.handle_message.assert_not_awaited()
    bot.handle_action.assert_awaited_once_with("19", second.text)
    messenger.answer.assert_awaited_once_with("callback.1", "Согласовано")
    await restarted.enqueue([first, second])
    async with sessions() as session:
        rows = (await session.scalars(select(BotUpdate).order_by(BotUpdate.id))).all()
    assert [(row.status, row.attempts, row.payload) for row in rows] == [("failed", 1, {}), ("done", 1, {})]
    assert rows[0].last_error == "worker_interrupted"


async def test_max_outbound_failure_does_not_rerun_workflow(pg):
    _, sessions = pg
    event = parse_event(message())
    assert event is not None
    bot, messenger = AsyncMock(), AsyncMock()
    bot.handle_message.side_effect = PublishError("send failed", retry_after=5)
    inbox = MaxInbox(sessions, bot, messenger)
    await inbox.enqueue([event])
    assert await inbox.queue.process_next()
    await inbox.enqueue([event])
    assert not await inbox.queue.process_next()
    bot.handle_message.assert_awaited_once()
    async with sessions() as session:
        row = await session.scalar(select(BotUpdate))
    assert row.status == "failed" and row.payload == {}


async def test_max_worker_shutdown_is_bounded(monkeypatch):
    started = asyncio.Event()
    blocked = asyncio.Event()

    async def run(stop):
        started.set()
        await blocked.wait()

    inbox = AsyncMock()
    inbox.queue.run.side_effect = run
    monkeypatch.setattr("app.bot.max_bot.SHUTDOWN_TIMEOUT", 0.01)
    async with asyncio.timeout(1), max_worker(inbox):
        await started.wait()
