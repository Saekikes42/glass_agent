import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.ai.connectors.base import MockConnector
from app.ai.orchestrator import Orchestrator
from app.bot.bot import GlassBot
from app.db.models import Chunk, Document, Source
from app.parsers.base import JsonlDocumentStore
from app.rag.index import HybridSearcher
from app.services.sources import add_source, create_company, import_jsonl, sync_source

from .conftest import HashEmbedder


async def test_existing_archive_import_is_idempotent_and_preserves_checkpoints(pg):
    _, sm = pg
    embedder = HashEmbedder()
    archive = Path(__file__).resolve().parents[1] / "data" / "salavatsteklo"
    expected = len(JsonlDocumentStore(archive))
    async with sm() as session, session.begin():
        company = await create_company(session, "Салаватстекло", slug="sal", default_tasks=False)
        await add_source(session, company.id, "telegram_web", "@salstek")

    first = await import_jsonl(sm, company.id, archive, embedder)
    assert first["new"] == expected and first["chunks"] > 0
    async with sm() as session, session.begin():
        source = await session.scalar(select(Source).where(Source.type == "telegram_web"))
        assert source.ref == "https://t.me/salstek"
        state = source.sync_state["telegram_web:salstek"]
        assert state["last_message_id"] == 1143
        source.sync_state = {"telegram_web:salstek": {**state, "last_message_id": 1144}}
        assert await session.scalar(select(func.count()).select_from(Source)) == 2
        assert await session.scalar(select(func.count()).select_from(Document)) == expected
        assert await session.scalar(select(func.count()).select_from(Chunk)) == first["chunks"]

    second = await import_jsonl(sm, company.id, archive, embedder)
    assert second == {"new": 0, "updated": 0, "unchanged": expected, "chunks": 0}
    async with sm() as session:
        source = await session.scalar(select(Source).where(Source.type == "telegram_web"))
        assert source.sync_state["telegram_web:salstek"]["last_message_id"] == 1144


async def test_bot_routes_only_bound_channel_and_deduplicates_album_updates(pg):
    _, sm = pg
    embedder = HashEmbedder()
    first_chat = -1001234567890
    second_chat = -1001234567891
    async with sm() as session, session.begin():
        first = await create_company(session, "Первая", slug="first", default_tasks=False)
        second = await create_company(session, "Вторая", slug="second", default_tasks=False)
        first_source = await add_source(session, first.id, "telegram_bot", str(first_chat))
        await add_source(session, second.id, "telegram_bot", str(second_chat))
        with pytest.raises(ValueError, match="другой компании"):
            await add_source(session, second.id, "telegram_bot", str(first_chat))

    bot = GlassBot(None, sm, Orchestrator(MockConnector(), HybridSearcher(embedder)))

    def post(channel_id, message_id, caption, **extra):
        return {"chat": {"id": channel_id, "type": "channel"}, "message_id": message_id,
                "date": 1700000000, "caption": caption, **extra}

    part1 = post(first_chat, 1, "Первая часть альбома", photo=[], media_group_id="abc")
    part2 = post(first_chat, 2, "Вторая часть альбома", photo=[], media_group_id="abc")
    await bot.handle({"channel_post": part1})
    await bot.handle({"channel_post": part2})
    await bot.handle({"channel_post": part2})
    await bot.handle({"edited_channel_post": {**part1, "caption": "Исправленная часть альбома", "edit_date": 1700000010}})
    await bot.handle({"channel_post": post(second_chat, 3, "Пост второй компании")})
    await bot.handle({"channel_post": post(-1001234567899, 4, "Пост неподключённого канала")})

    result = await sync_source(sm, first_source.id, embedder)
    assert not result.errors and result.notes
    async with sm() as session:
        rows = (await session.scalars(select(Document).order_by(Document.url))).all()
        assert [row.company_id for row in rows] == [first.id, second.id]
        assert rows[0].text == "Исправленная часть альбома\n\nВторая часть альбома"
        assert rows[0].meta["message_ids"] == [1, 2] and rows[0].meta["media_count"] == 2
        assert rows[0].url.endswith("/1")
        assert rows[1].text == "Пост второй компании"
        assert await session.scalar(select(func.count()).select_from(Chunk)) == 2


async def test_bot_keeps_raw_post_if_rag_fails_and_sync_can_retry(pg):
    class UnavailableEmbedder(HashEmbedder):
        async def embed_passages(self, texts):
            raise RuntimeError("Временный сбой модели эмбеддингов")

    _, sm = pg
    chat_id = -1001234567890
    async with sm() as session, session.begin():
        company = await create_company(session, "Стекло", default_tasks=False)
        src = await add_source(session, company.id, "telegram_bot", str(chat_id))

    bot = GlassBot(None, sm, Orchestrator(MockConnector(), HybridSearcher(UnavailableEmbedder())))
    await bot.handle({"channel_post": {
        "chat": {"id": chat_id, "type": "channel"}, "message_id": 1,
        "date": 1700000000, "text": "Пост сохранится, даже если индекс временно недоступен",
    }})

    async with sm() as session:
        source = await session.get(Source, src.id)
        doc = await session.scalar(select(Document).where(Document.source_id == src.id))
        assert source.status == "error" and "повторена" in source.last_error
        assert doc.text.startswith("Пост сохранится") and doc.indexed_hash is None

    result = await sync_source(sm, src.id, HashEmbedder(), company_id=company.id)
    assert not result.errors
    async with sm() as session:
        source = await session.get(Source, src.id)
        doc = await session.scalar(select(Document).where(Document.source_id == src.id))
        assert source.status == "ok" and source.last_error is None
        assert doc.indexed_hash == doc.content_hash
        assert await session.scalar(select(func.count()).select_from(Chunk)) == 1


async def test_polling_acknowledges_channel_update_only_after_durable_write(monkeypatch):
    stop = asyncio.Event()
    offsets = []
    received = []

    class Api:
        async def call(self, method, params, *, timeout):
            assert method == "getUpdates" and "channel_post" in params["allowed_updates"]
            offsets.append(params["offset"])
            if len(offsets) == 3:
                stop.set()
                return []
            return [{"update_id": 1, "channel_post": {}}, {"update_id": 2, "message": {}}]

    async def handle(update):
        received.append(update["update_id"])
        if len(received) == 1:
            raise RuntimeError("Запись в PostgreSQL временно недоступна")

    async def sleep(seconds):
        assert seconds == 5

    bot = GlassBot(Api(), None, None)
    monkeypatch.setattr(bot, "handle", handle)
    monkeypatch.setattr("app.bot.bot.asyncio.sleep", sleep)
    await bot.run(stop)
    assert offsets == [0, 0, 3]
    assert received == [1, 1, 2]
