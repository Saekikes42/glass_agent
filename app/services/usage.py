"""Учёт расхода вызовов модели и дневной лимит на компанию (контроль расходов, запрет несанкционированных трат).

Лимит: settings.ai_daily_call_limit компании, иначе AI_DAILY_CALL_LIMIT (0 = без лимита).
Подписка ChatGPT Plus оплачивается фиксированно, но её 5-часовое окно общее для всех
компаний администратора — лимит не даёт одной компании выбрать его целиком.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.ai.connectors.base import AIError, AIResult
from app.core.config import get_settings
from app.db.models import AIUsage, Company


class CompanyQuotaExceeded(AIError):
    code = "AI_COMPANY_QUOTA_EXCEEDED"


def _today() -> date:
    return datetime.now(timezone.utc).date()


async def calls_today(session: AsyncSession, company_id: uuid.UUID, *, day: date | None = None) -> int:
    return int(await session.scalar(select(func.coalesce(func.sum(AIUsage.calls), 0)).where(
        AIUsage.company_id == company_id, AIUsage.day == (day or _today()))) or 0)


async def daily_limit(session: AsyncSession, company_id: uuid.UUID) -> int:
    company = await session.get(Company, company_id)
    value = (company.settings or {}).get("ai_daily_call_limit") if company else None
    return int(value) if value is not None else get_settings().ai_daily_call_limit


def _independent_session(session: AsyncSession) -> AsyncSession:
    bind = session.bind
    return AsyncSession(bind.engine if isinstance(bind, AsyncConnection) else bind)


async def reserve_call(session: AsyncSession, company_id: uuid.UUID, provider: str) -> int:
    """Атомарно учитывает попытку до вызова модели, независимо от транзакции чата."""
    async with _independent_session(session) as meter, meter.begin():
        # FK AIUsage → Company не должен зависнуть, если другая транзакция меняет компанию.
        await meter.execute(text("SET LOCAL lock_timeout = '2s'"))
        # Отдельное пространство advisory lock, без блокировки строки Company.
        await meter.execute(select(func.pg_advisory_xact_lock(713025482, func.hashtext(str(company_id)))))
        day = _today()
        limit = await daily_limit(meter, company_id)
        if limit and await calls_today(meter, company_id, day=day) >= limit:
            raise CompanyQuotaExceeded(f"Дневной лимит вызовов AI для компании исчерпан ({limit}). "
                                       "Изменить: settings.ai_daily_call_limit", reason="company_daily_limit")
        stmt = insert(AIUsage).values(company_id=company_id, day=day, provider=provider, calls=1)
        stmt = stmt.on_conflict_do_update(index_elements=[AIUsage.company_id, AIUsage.day, AIUsage.provider],
                                          set_={"calls": AIUsage.calls + 1})
        return await meter.scalar(stmt.returning(AIUsage.id))


async def record_tokens(session: AsyncSession, usage_id: int, res: AIResult) -> None:
    usage = res.usage or {}
    if not usage:
        return
    async with _independent_session(session) as meter, meter.begin():
        await meter.execute(text("SET LOCAL lock_timeout = '2s'"))
        await meter.execute(update(AIUsage).where(AIUsage.id == usage_id).values(
            input_tokens=AIUsage.input_tokens + int(usage.get("input_tokens") or 0),
            output_tokens=AIUsage.output_tokens + int(usage.get("output_tokens") or 0),
        ))


async def report(session: AsyncSession, company_id: uuid.UUID | None = None, days: int = 30) -> list[dict[str, Any]]:
    since = _today() - timedelta(days=days - 1)
    q = select(AIUsage.company_id, Company.name, AIUsage.provider, func.sum(AIUsage.calls),
               func.sum(AIUsage.input_tokens), func.sum(AIUsage.output_tokens)).join(
        Company, Company.id == AIUsage.company_id, isouter=True).where(AIUsage.day >= since)
    if company_id:
        q = q.where(AIUsage.company_id == company_id)
    rows = (await session.execute(q.group_by(AIUsage.company_id, Company.name, AIUsage.provider))).all()
    return [{"company_id": str(cid) if cid else None, "company": name, "provider": prov, "calls": int(c or 0),
             "input_tokens": int(i or 0), "output_tokens": int(o or 0), "days": days} for cid, name, prov, c, i, o in rows]
