"""Тесты VK-парсера. VK API замокан (httpx.MockTransport) — это НЕ проверка реального сообщества."""
from urllib.parse import parse_qs

import httpx
import pytest

from app.parsers.base import JsonlDocumentStore
from app.parsers.vk_parser import (
    VKAccessError, VKAuthError, VKParser, parse_group_ref, post_to_document,
)

GROUP = {"id": 42, "name": "Салаватстекло", "screen_name": "salavatsteklo", "members_count": 1000, "is_closed": 0}


def post(pid, text="Пост #стекло", likes=1, pinned=False):
    return {"id": pid, "owner_id": -42, "date": 1_700_000_000 + pid, "text": text, "is_pinned": int(pinned),
            "likes": {"count": likes}, "comments": {"count": 2}, "reposts": {"count": 3}, "views": {"count": 100},
            "attachments": [{"type": "photo", "photo": {"sizes": [{"width": 10, "height": 10, "url": "s"},
                                                                    {"width": 800, "height": 600, "url": "big"}]}}]}


def vk_server(wall: list[dict], errors: dict | None = None):
    log = []

    def handler(req: httpx.Request):
        method = req.url.path.rsplit("/", 1)[-1]
        params = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        log.append((method, params))
        if errors and method in errors and errors[method]:
            code = errors[method].pop(0)
            return httpx.Response(200, json={"error": {"error_code": code, "error_msg": f"err{code}"}})
        if method == "groups.getById":
            return httpx.Response(200, json={"response": {"groups": [GROUP], "profiles": []}})
        if method == "wall.get":
            off, cnt = int(params["offset"]), int(params["count"])
            return httpx.Response(200, json={"response": {"count": len(wall), "items": wall[off:off + cnt]}})
        return httpx.Response(404)

    return handler, log


def mk(handler):
    return VKParser("token", min_interval=0, transport=httpx.MockTransport(handler))


def test_parse_group_ref():
    assert parse_group_ref("https://vk.ru/salavatsteklo") == "salavatsteklo"
    assert parse_group_ref("https://m.vk.com/club123") == "123"
    assert parse_group_ref("-123") == "123"


def test_post_to_document():
    d = post_to_document(dict(post(5), text="Смотрите [club42|наш завод] #Стекло",
                              copy_history=[{"text": "Оригинал"}]), GROUP)
    assert d.url == "https://vk.ru/wall-42_5" and d.source_id == "42"
    assert "наш завод" in d.text and "[Репост]" in d.text and "Оригинал" in d.text
    assert d.metrics == {"likes": 1, "comments": 2, "reposts": 3, "views": 100}
    assert d.metadata["hashtags"] == ["стекло"] and d.metadata["attachments"][0]["url"] == "big"


def test_post_keeps_missing_metrics_distinct_from_zero():
    payload = post(5, likes=0)
    del payload["comments"], payload["reposts"], payload["views"]
    assert post_to_document(payload, GROUP).metrics == {"likes": 0, "comments": None, "reposts": None, "views": None}


async def test_pagination_incremental_and_metrics(tmp_path):
    wall = [post(i) for i in range(250, 0, -1)]  # новые первыми
    handler, log = vk_server(wall)
    store = JsonlDocumentStore(tmp_path)
    res = await mk(handler).sync("salavatsteklo", store, refresh_recent=10)
    assert res.new == 250 and sum(1 for m, _ in log if m == "wall.get") == 3

    # новые посты + изменились лайки у свежего поста
    wall = [post(252), post(251), dict(post(250), likes={"count": 99})] + wall[1:]
    handler, log = vk_server(wall)
    store = JsonlDocumentStore(tmp_path)
    res = await mk(handler).sync("salavatsteklo", store, refresh_recent=10)
    assert res.new == 2 and res.metrics_updated == 1 and res.unchanged == 7  # окно refresh_recent=10 включает 2 новых
    assert sum(1 for m, _ in log if m == "wall.get") == 1  # остановились на известных постах
    assert store.get("vk:42:250").metrics["likes"] == 99
    assert store.get_state("vk:42")["last_max_post_id"] == 252


async def test_foreign_wall_item_does_not_advance_cursor_and_legacy_cursor_is_repaired(tmp_path):
    foreign_post = dict(post(44809), owner_id=-17042072)
    wall = [post(358), post(357), foreign_post, post(356)]
    handler, _ = vk_server(wall)
    parser = mk(handler)
    store = JsonlDocumentStore(tmp_path)
    first = await parser.sync("salavatsteklo", store)
    assert first.new == 3 and first.skipped == 1
    assert store.get_state("vk:42")["last_max_post_id"] == 358
    assert store.get("vk:17042072:44809") is None

    store.upsert(post_to_document(foreign_post, GROUP))
    independent = post_to_document(dict(post(99), owner_id=-17042072), {"screen_name": "other_group"})
    store.upsert(independent)
    store.set_state("vk:42", {"last_max_post_id": 44809, "resume_after_id": 44808, "pending_high": 44809})
    wall.insert(0, post(359))
    repaired = await parser.sync("salavatsteklo", store, refresh_recent=1)
    assert repaired.new == 1 and repaired.skipped == 1
    assert store.get("vk:42:359") is not None
    assert store.get("vk:17042072:44809") is None and store.get(independent.key) is not None
    state = store.get_state("vk:42")
    assert state["last_max_post_id"] == 359 and not ("resume_after_id" in state or "pending_high" in state)


async def test_rate_limit_retry(tmp_path):
    handler, log = vk_server([post(1)], errors={"wall.get": [6, 6]})
    res = await mk(handler).sync("x", JsonlDocumentStore(tmp_path))
    assert res.new == 1 and not res.errors


async def test_backfill_resumes_across_runs_without_skipping_new_posts(tmp_path):
    wall = [post(i) for i in range(5, 0, -1)]
    handler, _ = vk_server(wall)
    parser = mk(handler)
    first = await parser.sync("salavatsteklo", JsonlDocumentStore(tmp_path), max_posts=2)
    state = JsonlDocumentStore(tmp_path).get_state("vk:42")
    assert first.new == 2 and state["last_max_post_id"] == 0 and state["resume_after_id"] == 4

    wall.insert(0, post(6))
    for _ in range(3):
        await parser.sync("salavatsteklo", JsonlDocumentStore(tmp_path), max_posts=2)
    store = JsonlDocumentStore(tmp_path)
    assert {doc.external_id for doc in store.all()} == {str(i) for i in range(1, 7)}
    assert store.get_state("vk:42")["last_max_post_id"] == 6


async def test_pinned_post_does_not_exhaust_backfill_budget(tmp_path):
    handler, _ = vk_server([post(1, pinned=True), post(3), post(2)])
    parser = mk(handler)
    await parser.sync("salavatsteklo", JsonlDocumentStore(tmp_path), max_posts=1)
    store = JsonlDocumentStore(tmp_path)
    assert {d.external_id for d in store.all()} == {"1", "3"}
    await parser.sync("salavatsteklo", store, max_posts=1)
    assert {d.external_id for d in store.all()} == {"1", "2", "3"}
    assert store.get_state("vk:42")["last_max_post_id"] == 3


@pytest.mark.parametrize("code,exc", [(5, VKAuthError), (15, VKAccessError), (30, VKAccessError)])
async def test_auth_and_access_errors(tmp_path, code, exc):
    handler, _ = vk_server([], errors={"groups.getById": [code]})
    with pytest.raises(exc):
        await mk(handler).sync("x", JsonlDocumentStore(tmp_path))


async def test_daily_limit_stops_gracefully(tmp_path):
    handler, _ = vk_server([post(1)], errors={"wall.get": [29]})
    res = await mk(handler).sync("x", JsonlDocumentStore(tmp_path))
    assert res.errors and "29" in res.errors[0]


def test_token_required():
    with pytest.raises(VKAuthError):
        VKParser(None)
