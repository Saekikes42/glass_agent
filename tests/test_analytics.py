from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.db.models import Document, Post
from app.services.analytics import format_effectiveness, post_analytics, report_text


@pytest.mark.parametrize(("platform", "target", "url", "key", "meta", "wrong_key"), [
    ("telegram", "-100123", "https://t.me/c/123/5", "telegram:123:album8",
     {"message_ids": [4, 5]}, "telegram:999:album8"),
    ("vk", "-123", "https://vk.com/wall-123_5", "vk:123:5",
     {}, "vk:999:5"),
])
async def test_post_analytics_matches_platform_channel_and_message(platform, target, url, key, meta, wrong_key):
    def doc(ext_key, views):
        return Document(external_key=ext_key, meta=meta, metrics={"views": views},
                        fetched_at=datetime(2026, 11, 13, tzinfo=timezone.utc))

    post = Post(id=uuid4(), company_id=uuid4(), status="published", platform=platform,
                target_chat_id=target, external_message_id="5", external_url=url)
    session = AsyncMock()
    (await session.scalars()).all = lambda: [doc(wrong_key, 90), doc(key, None), doc(key, 0)]
    result = await post_analytics(session, post)
    assert result["metrics"] == {"views": 0}
    assert result["collected_at"] == "2026-11-13T00:00:00+00:00"


async def test_post_analytics_unsupported_platform_does_not_query_telegram():
    post = Post(id=uuid4(), company_id=uuid4(), status="published", platform="max", external_message_id="mid.123")
    session = AsyncMock()
    result = await post_analytics(session, post)
    assert result["metrics"] is None and "max" in result["note"]
    session.scalars.assert_not_called()


def test_format_effectiveness_does_not_treat_reactions_as_views():
    docs = [Document(metrics=metrics) for metrics in (
        {"views": 10}, {"reactions_total": 90}, {"views": 30},
    )]
    result = format_effectiveness(docs)
    assert result["metric"] == "views"
    assert result["media"] == [{"value": "текст", "posts": 2, "avg": 20}]


def test_report_renders_available_metrics_without_views():
    report = report_text("Компания", {
        "days": 7, "channels": {}, "published_by_system": [
            {"topic": "ВК", "platform": "vk", "metrics": {"views": None, "likes": 0}},
            {"topic": "Телеграм", "platform": "telegram", "metrics": {"reactions_total": 12}},
            {"topic": "Новый пост", "platform": "telegram", "metrics": None},
        ],
    })
    assert "ВК (vk) — 0 отм. «Нравится»" in report
    assert "Телеграм (telegram) — 12 реакц." in report
    assert "Новый пост (telegram) — показатели ещё не собраны" in report
