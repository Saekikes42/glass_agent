import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
from sqlalchemy import select

from app.ai.connectors.base import MockConnector
from app.ai.orchestrator import Orchestrator
from app.bot.inbox import BotInbox
from app.bot.social import SocialBot
from app.bot.vk_bot import VK_INBOX_LOCK, VKBot, VKMessenger, VKNotifier
from app.db.models import BotUpdate, Company, CompanyMember, Post, User, UserPlatformIdentity
from app.rag.index import HybridSearcher
from app.services import posts as P
from app.services.sources import create_company

from .conftest import HashEmbedder


def message(author=19, peer=None, group_id=55):
    return {"group_id": group_id, "type": "message_new",
            "object": {"message": {"from_id": author, "peer_id": author if peer is None else peer,
                                   "conversation_message_id": 4, "text": "/start"}}}


async def test_vk_dispatches_only_personal_events_for_own_group():
    social = AsyncMock()
    social.handle_action.return_value = "Согласовано"
    messenger = AsyncMock()
    bot = VKBot(messenger, social, 55)
    await bot.handle(message(peer=2000000003))
    await bot.handle(message(author=-7))
    await bot.handle(message(group_id=56))
    social.handle_message.assert_not_called()

    await bot.handle(message())
    social.handle_message.assert_awaited_once_with("19", "/start")
    event = {"group_id": 55, "type": "message_event",
             "object": {"user_id": 19, "peer_id": 2000000003, "event_id": "evt", "payload": {"glass": "ap:key"}}}
    await bot.handle(event)
    social.handle_action.assert_not_called()
    event["object"]["peer_id"] = 19
    await bot.handle(event)
    social.handle_action.assert_awaited_once_with("19", "ap:key")
    messenger.call.assert_awaited_once_with("messages.sendMessageEventAnswer", event_id="evt", user_id=19, peer_id=19)
    messenger.send.assert_awaited_once_with("19", "Согласовано")


async def test_vk_sends_full_text_with_image_and_callback(tmp_path):
    sent = []

    def endpoint(request):
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "upload":
            assert b'name="photo"' in request.content
            assert b"vk-test-token" not in request.content
            return httpx.Response(200, json={"photo": "test-image", "server": 7, "hash": "test-hash"})
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        assert form["access_token"] == "vk-test-token"
        assert form["v"] == "5.199"
        if method == "photos.getMessagesUploadServer":
            assert form["peer_id"] == "19"
            response = {"upload_url": "https://img.example.test/upload"}
        elif method == "photos.saveMessagesPhoto":
            assert form["hash"] == "test-hash"
            response = [{"owner_id": -55, "id": 7, "access_key": "key"}]
        else:
            assert method == "messages.send" and form["peer_id"] == "19" and int(form["random_id"]) > 0
            sent.append(form)
            response = 22
        return httpx.Response(200, json={"response": response})

    img = tmp_path / "test.png"
    img.write_bytes(b"test-image")
    client = VKMessenger("vk-test-token", transport=httpx.MockTransport(endpoint))
    await client.send("19", "я" * 4001, image=img, buttons=[[("Согласовать", "ap:key")]])
    assert sent[0]["attachment"] == "photo-55_7_key"
    assert "".join(part["message"] for part in sent) == "я" * 4001
    assert "keyboard" not in sent[0] and "attachment" not in sent[1]
    button = json.loads(sent[1]["keyboard"])["buttons"][0][0]
    assert button["action"]["type"] == "callback"
    assert json.loads(button["action"]["payload"]) == {"glass": "ap:key"}


async def test_long_poll_refreshes_key_without_skipping_pending_cursor():
    stop = asyncio.Event()
    polls = []
    connections = []
    social = AsyncMock()

    def endpoint(request):
        if request.url.path.endswith("/groups.getLongPollServer"):
            assert parse_qs(request.content.decode())["group_id"] == ["55"]
            connections.append(request)
            key = "key1" if len(connections) == 1 else "key2"
            ts = "10" if len(connections) == 1 else "14"
            return httpx.Response(200, json={"response": {"server": "https://lp.example.test/poll", "key": key,
                                                           "ts": ts}})
        polls.append(dict(request.url.params))
        if len(polls) == 1:
            result = {"failed": 1, "ts": "11"}
        elif len(polls) == 2:
            result = {"failed": 2}
        else:
            result = {"ts": "15", "updates": [message()]}
            stop.set()
        return httpx.Response(200, json=result)

    messenger = VKMessenger("vk-test-token", transport=httpx.MockTransport(endpoint))
    await VKBot(messenger, social, 55).run(stop)
    assert [(poll["key"], poll["ts"]) for poll in polls] == [("key1", "10"), ("key1", "11"), ("key2", "11")]
    assert all(poll["act"] == "a_check" and poll["wait"] == "25" for poll in polls)
    social.handle_message.assert_awaited_once_with("19", "/start")


async def test_vk_restart_resumes_pending_keeps_cursor_and_never_replays_interrupted_dispatch(pg):
    _, sessions = pg
    social, messenger = AsyncMock(), AsyncMock()
    messenger.call.return_value = {"server": "https://lp.example.test/poll", "key": "key", "ts": "10"}
    bot = VKBot(messenger, social, 55)
    inbox = BotInbox(sessions, "vk", VK_INBOX_LOCK, bot.dispatch)
    first, second = bot.parse_event(message()), bot.parse_event(message(author=23))
    await inbox.enqueue([first, first, second], checkpoint=("55", "11"))
    abandoned, _, _ = await inbox._claim()
    async with sessions.begin() as session:
        row = await session.get(BotUpdate, abandoned)
        row.locked_at = datetime.now(timezone.utc) - timedelta(minutes=11)

    restarted = BotInbox(sessions, "vk", VK_INBOX_LOCK, bot.dispatch)
    stop = asyncio.Event()

    async def poll(server, key, ts):
        assert ts == "11"
        stop.set()
        return {"ts": "12", "updates": [message(), message(author=23)]}

    messenger.poll.side_effect = poll
    await bot.run(stop, inbox=restarted)
    assert await restarted.get_checkpoint("55") == "12"
    assert await restarted.process_next()
    assert not await restarted.process_next()
    social.handle_message.assert_awaited_once_with("23", "/start")
    async with sessions() as session:
        statuses = (await session.scalars(select(BotUpdate).where(BotUpdate.status != "checkpoint")
                                          .order_by(BotUpdate.id))).all()
    assert [(row.status, row.last_error, row.payload) for row in statuses] == [
        ("failed", "worker_interrupted", {}), ("done", None, {}),
    ]


async def test_vk_linked_chat_review_and_notifications_are_company_scoped(pg, tmp_path):
    _, sessions = pg
    image = tmp_path / "preview.png"
    image.write_bytes(b"preview")
    async with sessions.begin() as session:
        first = await create_company(session, "Компания 1", slug="test-first")
        second = await create_company(session, "Компания 2", slug="test-second")
        approver = User(telegram_id=111)
        viewer = User(telegram_id=222)
        outsider = User(telegram_id=333)
        session.add_all([approver, viewer, outsider])
        await session.flush()
        session.add_all([UserPlatformIdentity(platform="vk", external_id="19", user_id=approver.id),
                         UserPlatformIdentity(platform="vk", external_id="20", user_id=viewer.id),
                         UserPlatformIdentity(platform="vk", external_id="21", user_id=outsider.id),
                         CompanyMember(company_id=first.id, user_id=approver.id, role="approver"),
                         CompanyMember(company_id=first.id, user_id=viewer.id, role="viewer"),
                         CompanyMember(company_id=second.id, user_id=outsider.id, role="owner")])
        post = await P.create_draft(session, first.id, text="Стекло и свет")
        await P.edit_content(session, post, image_path=str(image), target_chat_id="@test_channel")
        await P.submit(session, post)
        stale = P.review_token(post, first)
        pid = post.id
    messenger = AsyncMock()
    mock = MockConnector([json.dumps({"answer": "Тестовый ответ"})])
    social = SocialBot("vk", messenger, sessions, Orchestrator(mock, HybridSearcher(HashEmbedder())))

    await social.handle_message("999", "Привет")
    assert messenger.send.call_args.args == ("999", "Передайте администратору ваш ID для привязки: 999.")
    await social.handle_message("20", "Привет")
    assert "editor" in messenger.send.call_args.args[1]
    await social.handle_message("19", "/use test-first")
    await social.handle_message("19", "Привет")
    assert messenger.send.call_args.args == ("19", "[ТЕСТОВАЯ МОДЕЛЬ] Тестовый ответ")
    await social.handle_message("19", "/posts")
    review = messenger.send.call_args
    assert review.kwargs["image"] == image
    assert review.kwargs["buttons"][0][0][1] == f"ap:{pid}:{stale}"
    assert await social.handle_action("21", f"ap:{pid}:{stale}") == "Пост не найден"
    assert await social.handle_action("20", f"ap:{pid}:{stale}") == "Недостаточно прав"

    async with sessions.begin() as session:
        post = await session.get(Post, pid)
        await P.edit_content(session, post, text="Обновлённый пост")
        await P.submit(session, post)
        first = await session.get(Company, post.company_id)
        fresh = P.review_token(post, first)
    assert "изменился" in await social.handle_action("19", f"ap:{pid}:{stale}")

    messenger.reset_mock()
    async with sessions() as session:
        await VKNotifier(messenger).notify_approval(session, await session.get(Post, pid))
    messenger.send.assert_awaited_once()
    assert messenger.send.call_args.args[0] == "19"
    assert str(messenger.send.call_args.kwargs["image"]) == str(image)
    assert "Согласовано" in await social.handle_action("19", f"pn:{pid}:{fresh}")
    async with sessions() as session:
        post = await session.get(Post, pid)
        assert post.status == P.SCHEDULED and post.scheduled_at <= datetime.now(timezone.utc)
        assert await session.scalar(select(UserPlatformIdentity.prefs)
                                    .where(UserPlatformIdentity.external_id == "19")) == {
                                        "company_id": str(first.id), "revision": None,
                                    }
