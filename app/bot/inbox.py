"""Очередь событий социальных ботов с дедупликацией в PostgreSQL."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import BotUpdate

log = logging.getLogger(__name__)
PROCESSING_TIMEOUT = timedelta(minutes=10)
DISPATCH_TIMEOUT = 9 * 60


class BotInbox:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], platform: str, advisory_lock: int,
                 dispatch: Callable[[dict[str, Any]], Awaitable[None]]):
        self.sessions = sessions
        self.platform = platform
        self.advisory_lock = advisory_lock
        self.dispatch = dispatch

    async def get_checkpoint(self, key: str) -> str | None:
        async with self.sessions() as session:
            payload = await session.scalar(select(BotUpdate.payload).where(
                BotUpdate.platform == self.platform, BotUpdate.event_key == f"cursor:{key}",
                BotUpdate.status == "checkpoint",
            ))
        return payload["cursor"] if payload else None

    async def enqueue(self, events: list[tuple[str, dict[str, Any]]], *, checkpoint: tuple[str, str] | None = None
                      ) -> None:
        if not events and checkpoint is None:
            return
        async with self.sessions.begin() as session:
            if events:
                rows = [{"platform": self.platform, "event_key": key, "payload": payload} for key, payload in events]
                await session.execute(insert(BotUpdate).values(rows).on_conflict_do_nothing(
                    index_elements=[BotUpdate.platform, BotUpdate.event_key]))
            if checkpoint is not None:
                key, cursor = checkpoint
                stmt = insert(BotUpdate).values(platform=self.platform, event_key=f"cursor:{key}",
                                                payload={"cursor": cursor}, status="checkpoint")
                await session.execute(stmt.on_conflict_do_update(
                    index_elements=[BotUpdate.platform, BotUpdate.event_key],
                    set_={"payload": stmt.excluded.payload, "updated_at": func.now()},
                ))

    async def _claim(self) -> tuple[int, int, dict[str, Any]] | None:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            await session.execute(select(func.pg_advisory_xact_lock(self.advisory_lock)))
            interrupted = await session.execute(update(BotUpdate).where(
                BotUpdate.platform == self.platform, BotUpdate.status == "processing",
                BotUpdate.locked_at <= now - PROCESSING_TIMEOUT,
            ).values(status="failed", locked_at=None, last_error="worker_interrupted", payload={}))
            if interrupted.rowcount:
                log.warning("Воркер %s прервал обработку %s событий. Проверьте результат в диалоге или канале.",
                            self.platform, interrupted.rowcount)
            active = await session.scalar(select(BotUpdate.id).where(
                BotUpdate.platform == self.platform, BotUpdate.status == "processing").limit(1))
            if active is not None:
                return None
            row = await session.scalar(select(BotUpdate).where(
                BotUpdate.platform == self.platform, BotUpdate.status == "pending",
                BotUpdate.available_at <= now,
            ).order_by(BotUpdate.id).limit(1).with_for_update())
            if row is None:
                return None
            row.status, row.locked_at = "processing", now
            row.attempts += 1
            return row.id, row.attempts, row.payload

    async def process_next(self) -> bool:
        claim = await self._claim()
        if claim is None:
            return False
        row_id, attempt, payload = claim
        status, error = "done", None
        try:
            async with asyncio.timeout(DISPATCH_TIMEOUT):
                await self.dispatch(payload)
        except Exception as exc:
            # Диалог мог быть записан до сбоя отправки. Повторять его автоматически нельзя.
            status, error = "failed", type(exc).__name__
            log.warning("Событие %s %s не обработано: %s", self.platform, row_id, error)
        async with self.sessions.begin() as session:
            row = await session.scalar(select(BotUpdate).where(
                BotUpdate.id == row_id, BotUpdate.status == "processing", BotUpdate.attempts == attempt
            ).with_for_update())
            if row is not None:
                row.status, row.last_error, row.locked_at, row.payload = status, error, None, {}
        return True

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                if await self.process_next():
                    continue
            except (SQLAlchemyError, OSError) as exc:
                log.warning("Воркер %s временно недоступен: %s", self.platform, type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
