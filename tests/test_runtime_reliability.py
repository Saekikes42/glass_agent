"""Concurrent conversation creation and enforced inference time budgets."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.ai.connectors.base import AIMessage, AIResult, AITemporarilyUnavailable, MockConnector
from app.ai.orchestrator import Orchestrator
from app.ai.tools import REGISTRY, Budget, BudgetExceeded, Tool, ToolContext
from app.db.models import AuditLog, Company, Conversation, ScheduledTask, Source
from app.services import memory, tasks, usage
from app.services.sources import add_source, create_company


async def test_concurrent_first_messages_share_conversation(pg):
    _, sm = pg
    async with sm() as session, session.begin():
        company = await create_company(session, "Concurrent", slug="concurrent")

    async def get_or_create():
        async with sm() as session, session.begin():
            return (await memory.get_conversation(session, company.id)).id

    ids = await asyncio.gather(*(get_or_create() for _ in range(4)))
    assert len(set(ids)) == 1
    async with sm() as session:
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 1


async def test_inference_deadline_cancels_slow_model(monkeypatch):
    cancelled = asyncio.Event()

    class SlowConnector(MockConnector):
        async def complete(self, *args, **kwargs):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

    async def reserve_call(*args):
        return 1

    monkeypatch.setattr(usage, "reserve_call", reserve_call)
    context = ToolContext(session=None, company_id=None, connector=SlowConnector(),
                          searcher=None, budget=Budget(max_seconds=0.05))
    with pytest.raises(BudgetExceeded, match="время"):
        await context.complete([])
    assert cancelled.is_set()
    assert context.budget.llm_calls == 1


async def test_concurrent_ai_quota_survives_rollback(pg):
    _, sm = pg
    async with sm() as session, session.begin():
        company = await create_company(session, "Квота", settings={"ai_daily_call_limit": 1})

    entered = asyncio.Event()
    release = asyncio.Event()

    class Connector(MockConnector):
        async def complete(self, messages, *, instructions=None):
            self.calls.append(messages)
            entered.set()
            await release.wait()
            return AIResult(text="Готово", model="test", provider=self.name, usage={"input_tokens": 3})

    connector = Connector()

    async def call_and_rollback():
        async with sm() as session:
            context = ToolContext(session, company.id, connector, None)
            try:
                return await context.complete([AIMessage("user", "Проверка")])
            finally:
                await session.rollback()

    first = asyncio.create_task(call_and_rollback())
    try:
        await asyncio.wait_for(entered.wait(), timeout=4)
        with pytest.raises(usage.CompanyQuotaExceeded):
            await asyncio.wait_for(call_and_rollback(), timeout=4)
    finally:
        release.set()
        await first
    assert len(connector.calls) == 1
    async with sm() as session:
        rows = await usage.report(session, company.id)
        assert rows[0]["calls"] == 1 and rows[0]["input_tokens"] == 3


async def test_company_lock_blocks_inference_without_hanging(pg):
    _, sm = pg
    async with sm() as session, session.begin():
        company = await create_company(session, "Блокировка")

    connector = MockConnector()
    async with sm() as session, session.begin():
        await session.get(Company, company.id, with_for_update=True)
        context = ToolContext(session, company.id, connector, None)
        with pytest.raises(AITemporarilyUnavailable, match="учесть вызов"):
            await asyncio.wait_for(context.complete([AIMessage("user", "Проверка")]), timeout=5)
    assert not connector.calls


async def test_scheduled_sync_retries_stale_sources_without_touching_live_workers(pg, monkeypatch):
    _, sm = pg
    async with sm() as session, session.begin():
        company = await create_company(session, "Зависший источник")
        stale = await add_source(session, company.id, "website", "https://stale.example/")
        active = await add_source(session, company.id, "website", "https://active.example/")
        stale.status = active.status = "syncing"
        stale.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)

    from app.services import sources
    from app.parsers.base import SourceType, SyncResult

    entered = asyncio.Event()
    release = asyncio.Event()

    async def parse(src, store):
        entered.set()
        await release.wait()
        return SyncResult(source_type=SourceType.WEBSITE, source_id=src.ref)

    monkeypatch.setattr(sources, "_run_parser", parse)
    task = ScheduledTask(company_id=company.id, kind="sync_sources", title="Обновление", params={})
    worker = asyncio.create_task(tasks._run_sync(sm, task, tasks.TaskDeps(notifier=None)))
    try:
        await asyncio.wait_for(entered.wait(), timeout=4)
        extra, _ = await tasks._run_sync(sm, task, tasks.TaskDeps(notifier=None))
        assert not extra["sources"]
    finally:
        release.set()
        result, _ = await worker
    assert set(result["sources"]) == {str(stale.id)}
    async with sm() as session:
        assert (await session.get(Source, stale.id)).status == "ok"
        assert (await session.get(Source, active.id)).status == "syncing"


async def test_agent_deadline_applies_to_initial_context(pg, monkeypatch):
    _, sm = pg
    async with sm() as session, session.begin():
        company = await create_company(session, "Лимит подготовки контекста")

    cancelled = asyncio.Event()

    async def slow_context(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    monkeypatch.setattr("app.ai.orchestrator.build_context", slow_context)
    connector = MockConnector()
    orch = Orchestrator(connector, None, max_seconds=0.05)
    async with sm() as session:
        result = await asyncio.wait_for(orch.chat(session, company.id, "Проверка"), timeout=4)
    assert result.stopped_reason == "budget" and cancelled.is_set()
    assert result.message_id and result.user_message_id and not connector.calls


async def test_timed_out_agent_tool_rolls_back_partial_changes(pg, monkeypatch):
    _, sm = pg
    async with sm() as session, session.begin():
        company = await create_company(session, "Лимит инструмента")

    entered = asyncio.Event()

    async def slow_tool(ctx):
        ctx.session.add(AuditLog(company_id=company.id, action="test.timed_out_tool"))
        await ctx.session.flush()
        ctx.created_post_ids.append("partial")
        entered.set()
        await asyncio.sleep(10)

    monkeypatch.setitem(REGISTRY, "slow_test", Tool("slow_test", "Проверка", {}, slow_tool))
    connector = MockConnector(['{"tool": "slow_test"}'])
    from app.rag.index import HybridSearcher
    from .conftest import HashEmbedder

    orch = Orchestrator(connector, HybridSearcher(HashEmbedder()), max_seconds=0.75)
    async with sm() as session:
        result = await asyncio.wait_for(orch.chat(session, company.id, "Проверка"), timeout=4)
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "test.timed_out_tool",
        )) == 0
    assert entered.is_set() and result.stopped_reason == "budget"
    assert not result.created_post_ids and len(connector.calls) == 1
