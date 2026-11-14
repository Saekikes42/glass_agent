"""Telegram-бот: настройка компании без кода, поручения AI (ТЕСТОВАЯ модель), согласование, доработка,
комментарии из группы обсуждения. Bot API замокан."""
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.ai.connectors.base import MockConnector
from app.ai.connectors.dev_mock import DevMockConnector
from app.ai.orchestrator import ChatReply, Orchestrator
from app.bot.bot import GlassBot
from app.core.config import get_settings
from app.db.models import AuditLog, Company, CompanyMember, InboxItem, Post, User
from app.rag.index import HybridSearcher
from app.services import posts as P
from app.services.publisher import LogNotifier, TelegramBotAPI, send_approval_preview
from app.services.sources import create_company

from .conftest import HashEmbedder


class Fake:
    def __init__(self):
        self.calls = []

    def __call__(self, req):
        body = json.loads(req.content or b"{}") if req.headers.get("content-type", "").startswith("application/json") \
            else {}
        self.calls.append((req.url.path.rsplit("/", 1)[-1], body))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 555}})

    def texts(self):
        return [d.get("text", "") for m, d in self.calls if m == "sendMessage"]

    def answers(self):
        return [d["text"] for m, d in self.calls if m == "answerCallbackQuery"]

    def review_button(self, action):
        return next(button["callback_data"] for method, body in reversed(self.calls)
                    if method == "sendMessage" for row in body.get("reply_markup", {}).get("inline_keyboard", [])
                    for button in row if button["callback_data"].startswith(action + ":"))


def msg(uid, text, chat_type="private", chat_id=None, **extra):
    return {"message": {"message_id": 7, "date": 1700000000, "chat": {"id": chat_id or uid, "type": chat_type},
                        "from": {"id": uid, "first_name": "U"}, "text": text, **extra}}


def cb(uid, data, n="1"):
    return {"callback_query": {"id": n, "from": {"id": uid}, "data": data, "message": {"chat": {"id": uid}}}}


@pytest.fixture
def admin(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "admin_telegram_ids", "1")
    monkeypatch.setattr(s, "data_dir", tmp_path)


def make_bot(sm, connector):
    fake = Fake()
    orch = Orchestrator(connector, HybridSearcher(HashEmbedder()), notifier=LogNotifier())
    return GlassBot(TelegramBotAPI("1:T", transport=httpx.MockTransport(fake)), sm, orch), fake


async def test_admin_setup_without_code(pg, admin):
    _, sm = pg
    bot, fake = make_bot(sm, MockConnector())
    await bot.handle(msg(5, "/newcompany Тест"))
    assert "администратор" in fake.texts()[-1]
    await bot.handle(msg(1, "/newcompany Вторая компания"))
    await bot.handle(msg(1, "/setchannel telegram @test_channel"))
    await bot.handle(msg(1, "/addsource website https://example.com конкурент"))
    await bot.handle(msg(1, "/invite 5 approver"))
    async with sm() as s:
        c = await s.scalar(select(Company))
        assert c.settings["channels"]["telegram"] == "@test_channel"
        roles = (await s.execute(select(User.telegram_id, CompanyMember.role).join(
            CompanyMember, CompanyMember.user_id == User.id))).all()
        assert set(roles) == {(1, "owner"), (5, "approver")}
    await bot.handle(msg(5, "/companies"))
    assert "Вторая компания (approver)" in fake.texts()[-1]
    await bot.handle(msg(5, "/tasks"))
    assert "Еженедельный отчёт" in fake.texts()[-1]


async def test_chat_approval_and_revision(pg, admin):
    _, sm = pg
    async with sm() as s, s.begin():
        c = await create_company(s, "Салаватстекло", slug="sal", settings={"channels": {"telegram": "@test_channel"}})
        owner, viewer = User(telegram_id=10, prefs={}), User(telegram_id=20, prefs={})
        s.add_all([owner, viewer])
        await s.flush()
        s.add_all([CompanyMember(company_id=c.id, user_id=owner.id, role="owner"),
                   CompanyMember(company_id=c.id, user_id=viewer.id, role="viewer")])
        post = await P.create_draft(s, c.id, text="Пост")
        await P.submit(s, post)
        pid = post.id
    bot, fake = make_bot(sm, MockConnector([json.dumps({"answer": "Привет из агента"})]))
    await bot.handle(msg(10, "Привет"))  # одна компания — выбирается автоматически
    assert fake.texts()[-1] == "[ТЕСТОВАЯ МОДЕЛЬ] Привет из агента"
    await bot.handle(msg(10, "/posts"))
    assert "На согласование · Telegram" in fake.texts()[-1]

    old_approval = fake.review_button("pn")
    await bot.handle(cb(20, fake.review_button("ap")))
    assert fake.answers()[-1] == "Недостаточно прав"
    # на доработку: кнопка → комментарий → revision (AI недоступен в моке без ответов → сообщение об ошибке)
    await bot.handle(cb(10, fake.review_button("rv")))
    assert "что доработать" in fake.texts()[-1]
    bot.orchestrator = None
    await bot.handle(msg(10, "Добавь цену"))
    async with sm() as s:
        p = await s.get(Post, pid)
        assert p.status == "revision" and p.review_comment == "Добавь цену"
        await P.edit_content(s, p, text="Пост с ценой")
        await P.submit(s, p)
        await s.commit()
    await bot.handle(cb(10, old_approval, "2"))
    assert "Материал изменился" in fake.answers()[-1]
    await bot.handle(msg(10, "/posts"))
    await bot.handle(cb(10, fake.review_button("pn"), "3"))
    assert fake.answers()[-1].startswith("Согласовано")
    async with sm() as s:
        p = await s.get(Post, pid)
        assert p.status == "scheduled" and P.is_approved_content(p)


async def test_publish_now_requires_channel_and_duplicate_review_reports_queue(pg, admin):
    _, sm = pg
    async with sm() as s, s.begin():
        company = await create_company(s, "Тестовая компания", slug="demo")
        post = await P.create_draft(s, company.id, text="Пост для тестового канала")
        await P.submit(s, post)
        pid = post.id

    bot, fake = make_bot(sm, MockConnector())
    await bot.handle(msg(1, "/posts"))
    no_channel_pn = fake.review_button("pn")
    await bot.handle(cb(1, no_channel_pn))
    assert "Канал публикации не настроен" in fake.answers()[-1]
    async with sm() as s:
        post = await s.get(Post, pid)
        assert post.status == P.PENDING
        assert post.approved_at is None and post.scheduled_at is None

    await bot.handle(msg(1, "/setchannel telegram @test_channel", message_id=8))
    await bot.handle(msg(1, "/posts", message_id=9))
    assert "Канал: @test_channel" in fake.texts()[-1]
    fresh_pn, fresh_ap = fake.review_button("pn"), fake.review_button("ap")
    assert fresh_pn != no_channel_pn
    await bot.handle(cb(1, fresh_pn, "2"))
    assert fake.answers()[-1].startswith("Согласовано")
    async with sm() as s:
        post = await s.get(Post, pid)
        assert post.status == P.SCHEDULED and P.is_approved_content(post)
        assert post.meta["approval_target"] == "@test_channel"
        approved_at, scheduled_at = post.approved_at, post.scheduled_at

    for callback_id, button in [("3", fresh_ap), ("4", fresh_pn)]:
        await bot.handle(cb(1, button, callback_id))
        assert "Пост уже в очереди на публикацию" in fake.answers()[-1]

    async with sm() as s:
        post = await s.get(Post, pid)
        assert post.status == P.SCHEDULED
        assert (post.approved_at, post.scheduled_at) == (approved_at, scheduled_at)
        actions = (await s.scalars(select(AuditLog.action).where(
            AuditLog.details["post_id"].as_string() == str(pid),
            AuditLog.action.in_(["post.approve", "post.schedule"]),
        ))).all()
        assert sorted(actions) == ["post.approve", "post.schedule"]


async def test_private_forward_is_company_scoped_one_shot_context(pg, admin, monkeypatch):
    _, sm = pg
    async with sm() as s, s.begin():
        first = await create_company(s, "Первая компания", slug="first")
        await create_company(s, "Вторая компания", slug="second")

    bot, fake = make_bot(sm, MockConnector())
    chat = AsyncMock(return_value=ChatReply(message_id=1, user_message_id=2, answer="Готово",
                                          provider="mock", model="mock", is_mock=True))
    monkeypatch.setattr(bot.orchestrator, "chat", chat)
    forward_origin = {"type": "hidden_user", "date": 1700000000, "sender_user_name": "Источник"}
    await bot.handle(msg(1, "/use first"))

    # A forwarded slash command must remain source text for the selected company.
    await bot.handle(msg(1, "/use second", message_id=8, forward_origin=forward_origin,
                         entities=[{"type": "bot_command", "offset": 0, "length": 4}]))
    chat.assert_not_awaited()
    task = "Сделай черновик из поста выше"
    await bot.handle(msg(1, task, message_id=9))
    chat.assert_awaited_once()
    assert chat.await_args.args[1:] == (first.id, task)
    assert chat.await_args.kwargs["reference_text"] == "/use second"

    caption = "На выставке показали панель «Синяя призма»."
    forwarded_photo = msg(1, "", message_id=10, forward_origin=forward_origin, caption=caption,
                          photo=[{"file_id": "test-photo"}])
    forwarded_photo["message"].pop("text")
    await bot.handle(forwarded_photo)
    assert chat.await_count == 1
    await bot.handle(msg(1, task, message_id=11))
    assert chat.await_count == 2
    assert chat.await_args.args[1:] == (first.id, task)
    assert chat.await_args.kwargs["reference_text"] == caption

    before = len(fake.texts())
    await bot.handle(msg(1, task, message_id=12))
    assert chat.await_count == 2
    assert any("Не вижу, какой пост" in sent for sent in fake.texts()[before:])

    await bot.handle({"message": {**forwarded_photo["message"], "message_id": 13}})
    await bot.handle(msg(1, "/use second", message_id=14))
    before = len(fake.texts())
    await bot.handle(msg(1, task, message_id=15))
    assert chat.await_count == 2
    assert any("Не вижу, какой пост" in sent for sent in fake.texts()[before:])

    media_only = msg(1, "", message_id=16, photo=[{"file_id": "test-photo"}])
    media_only["message"].pop("text")
    before = len(fake.texts())
    await bot.handle(media_only)
    assert chat.await_count == 2
    assert any("нет текста или подписи" in sent for sent in fake.texts()[before:])

    before = len(fake.texts())
    await bot.handle({"message": {**media_only["message"], "message_id": 17, "forward_origin": forward_origin}})
    assert chat.await_count == 2
    assert any("нет текста или подписи" in sent for sent in fake.texts()[before:])

    await bot.handle(msg(1, "Расскажи о компании", message_id=18))
    assert chat.await_count == 3
    assert set(chat.await_args.kwargs) == {"user_id"}


@pytest.mark.parametrize("quote", [None, "Полная подпись"], ids=["caption", "quote"])
async def test_private_reply_uses_quote_or_media_caption_as_reference(pg, admin, monkeypatch, quote):
    _, sm = pg
    async with sm() as s, s.begin():
        company = await create_company(s, "Компания", slug="demo")

    bot, _ = make_bot(sm, MockConnector())
    chat = AsyncMock(return_value=ChatReply(message_id=1, user_message_id=2, answer="Готово",
                                          provider="mock", model="mock", is_mock=True))
    monkeypatch.setattr(bot.orchestrator, "chat", chat)
    caption = "Полная подпись к пересланной фотографии."
    reply = {"reply_to_message": {"message_id": 6, "caption": caption, "photo": [{"file_id": "test-photo"}]}}
    if quote is not None:
        reply["quote"] = {"text": quote, "position": 0}
    task = "Сделай черновик из поста выше"
    await bot.handle(msg(1, task, **reply))

    chat.assert_awaited_once()
    assert chat.await_args.args[1:] == (company.id, task)
    assert chat.await_args.kwargs["reference_text"] == (quote or caption)


async def test_private_chat_displays_exact_new_draft_and_posts_list_is_company_scoped(pg, admin):
    _, sm = pg
    async with sm() as s, s.begin():
        company = await create_company(s, "Тестовая компания", slug="demo")
        other = await create_company(s, "Другая компания", slug="other")
        old = await P.create_draft(s, company.id, text="Предыдущий черновик компании")
        foreign = await P.create_draft(s, other.id, text="Черновик другой компании")

    source = "На выставке показали панель «Синяя призма» из 36 стеклянных секций."
    text = "Новый пост о панели «Синяя призма» из 36 стеклянных секций."
    connector = MockConnector([
        json.dumps({"tool": "create_post_draft", "args": {"topic": "Устаревшая тема"}}),
        text,
        json.dumps({"answer": "Подготовил черновик."}),
    ])
    bot, fake = make_bot(sm, connector)
    await bot.handle(msg(1, "/use demo"))
    fake.calls.clear()
    await bot.handle(msg(1, "Сделай черновик из поста выше", message_id=8,
                         reply_to_message={"message_id": 6, "text": source}))
    async with sm() as s:
        new = await s.scalar(select(Post).where(Post.company_id == company.id, Post.text == text))
        assert new is not None and new.status == P.DRAFT
        assert "Синяя призма" in new.topic
    generation_prompt = connector.calls[1]["messages"][-1].content
    assert source in generation_prompt and "Устаревшая тема" not in generation_prompt
    assert fake.review_button("sb") == f"sb:{new.id}"
    assert any(text in sent for sent in fake.texts())
    assert all(str(old.id) not in sent and str(foreign.id) not in sent for sent in fake.texts())

    fake.calls.clear()
    await bot.handle(msg(1, "/posts", message_id=9))
    assert any(company.name in sent for sent in fake.texts())
    assert any(str(old.id) in sent for sent in fake.texts())
    assert any(str(new.id) in sent for sent in fake.texts())
    assert all(str(foreign.id) not in sent for sent in fake.texts())


async def test_long_approval_preview_has_image_and_full_text(tmp_path):
    from uuid import uuid4

    image = tmp_path / "test.png"
    image.write_bytes(b"test image")
    text = "🪟" * 4000 + "Конец"
    company = Company(settings={"channels": {"telegram": "@test_channel"}})
    post = Post(id=uuid4(), platform="telegram", revision=2, topic="Тест", text=text,
                image_path=str(image))
    fake = Fake()
    api = TelegramBotAPI("1:T", transport=httpx.MockTransport(fake))
    await send_approval_preview(api, 1, post, company)

    assert fake.calls[0][0] == "sendPhoto"
    assert "Канал: @test_channel" in fake.texts()[0]
    assert "".join(fake.texts()).endswith(text)
    assert all("reply_markup" not in body for _, body in fake.calls[:-1])
    assert len(fake.review_button("pn").encode()) <= 64


async def test_group_comments_to_inbox(pg, admin):
    _, sm = pg
    async with sm() as s, s.begin():
        c = await create_company(s, "К", slug="kk", settings={"channels": {"telegram_discussion": "-100500"}})
        o = User(telegram_id=10, prefs={})
        s.add(o)
        await s.flush()
        s.add(CompanyMember(company_id=c.id, user_id=o.id, role="owner"))
    bot, fake = make_bot(sm, DevMockConnector())
    await bot.handle(msg(77, "Сколько стоит триплекс?", chat_type="supergroup", chat_id=-100500))
    await bot.handle(msg(77, "автопересылка", chat_type="supergroup", chat_id=-100500, is_automatic_forward=True))
    await bot.handle(msg(77, "чужая группа", chat_type="supergroup", chat_id=-1))
    async with sm() as s:
        items = (await s.scalars(select(InboxItem))).all()
        assert [i.text for i in items] == ["Сколько стоит триплекс?"]
        iid = items[0].id
    await bot.handle(cb(10, f"id:{iid}"))
    assert fake.answers()[-1] == "Черновик готов — /inbox"
    await bot.handle(cb(10, f"is:{iid}", "2"))
    assert fake.answers()[-1] == "Ответ отправлен"
    sent = [d for m, d in fake.calls if m == "sendMessage" and d.get("chat_id") == "-100500"][0]
    assert sent["reply_parameters"] == {"message_id": 7} and sent["text"].startswith("[ТЕСТОВАЯ МОДЕЛЬ]")
