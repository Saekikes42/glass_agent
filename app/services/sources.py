"""Компании, источники и синхронизация источников в PostgreSQL с индексацией RAG."""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.models import AuditLog, Company, Conversation, Document, Source
from app.parsers.base import JsonlDocumentStore, ParsedDocument, SourceType, SyncResult, UpsertStatus, utcnow
from app.rag.embeddings import Embedder
from app.rag.index import index_documents

log = logging.getLogger(__name__)
STALE_SYNC_S = 15 * 60  # «syncing» дольше 15 минут считается зависшим (процесс был прерван)
TELEGRAM_INDEX_BATCH = 100
TELEGRAM_INDEX_TIMEOUT = 20  # ограничивает задержку обработки остальных обновлений ботом
SOURCE_TYPES = {"website", "telegram", "telegram_web", "telegram_export", "vk", "telegram_bot", "file"}


def source_sync_in_progress(src: Source) -> bool:
    return src.status == "syncing" and (datetime.now(timezone.utc) - src.updated_at).total_seconds() <= STALE_SYNC_S


def slugify(name: str) -> str:
    table = str.maketrans("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                          "abvgdeejziiklmnoprstufhccss_y_eua")
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().translate(table)).strip("-")
    return s[:60] or f"company-{uuid.uuid4().hex[:8]}"


async def create_company(session: AsyncSession, name: str, *, slug: str | None = None,
                         profile: dict[str, Any] | None = None, user_id: int | None = None,
                         settings: dict[str, Any] | None = None, default_tasks: bool = True) -> Company:
    company = Company(name=name, slug=slug or slugify(name), profile=profile or {}, settings=settings or {})
    session.add(company)
    await session.flush()
    session.add(Conversation(company_id=company.id))  # собственный постоянный AI-диалог компании
    session.add(AuditLog(company_id=company.id, user_id=user_id, action="company.create", details={"name": name}))
    await session.flush()
    if default_tasks:
        from .tasks import create_default_tasks

        await create_default_tasks(session, company.id)  # отчёт, обновление источников, сбор результатов…
    return company


def normalize_source_ref(type_: str, ref: str) -> str:
    """Один канал = один источник: @salstek, t.me/s/salstek, https://t.me/salstek/10 → https://t.me/salstek."""
    ref = ref.strip()
    if type_ == "telegram_bot":
        if not re.fullmatch(r"-100[0-9]{1,15}", ref):
            raise ValueError("Для telegram_bot укажите числовой ID канала Bot API (-100…)")
    elif type_ == "telegram_web":
        from app.parsers.telegram_parser import parse_channel_ref

        kind, value = parse_channel_ref(ref)
        if kind == "username":
            return f"https://t.me/{value.lower()}"
    return ref


async def add_source(session: AsyncSession, company_id: uuid.UUID, type_: str, ref: str, *,
                     name: str = "", is_competitor: bool = False, options: dict[str, Any] | None = None,
                     user_id: int | None = None) -> Source:
    if type_ not in SOURCE_TYPES:
        raise ValueError(f"Неизвестный тип источника: {type_}")
    ref = normalize_source_ref(type_, ref)
    if type_ == "telegram_bot":
        await session.execute(select(func.pg_advisory_xact_lock(int(ref))))
        assigned = await session.scalar(select(Source.id).where(
            Source.type == type_, Source.ref == ref, Source.company_id != company_id,
        ).limit(1))
        if assigned is not None:
            raise ValueError("Канал уже подключён к другой компании")
    existing = await session.scalar(select(Source).where(Source.company_id == company_id, Source.type == type_,
                                                         Source.ref == ref))
    if existing:
        return existing
    src = Source(company_id=company_id, type=type_, ref=ref, name=name or ref, is_competitor=is_competitor,
                 options=options or {})
    session.add(src)
    await session.flush()
    session.add(AuditLog(company_id=company_id, user_id=user_id, action="source.add",
                         details={"type": type_, "ref": ref, "is_competitor": is_competitor}))
    return src


# --------------------------------------------------------------------------- хранилище для парсеров


def _row_to_parsed(row: Document) -> ParsedDocument:
    st, sid, ext = row.external_key.split(":", 2)
    return ParsedDocument(source_type=SourceType(st), source_id=sid, external_id=ext, url=row.url, title=row.title,
                          text=row.text, published_at=row.published_at, edited_at=row.edited_at,
                          fetched_at=row.fetched_at or datetime.now(timezone.utc), content_hash=row.content_hash,
                          metrics=row.metrics or {}, metadata=row.meta or {})


class PgSourceStore:
    """Снимок документов одного источника в памяти с интерфейсом JsonlDocumentStore.

    Парсер работает с ним синхронно (сеть долгая — транзакцию не держим),
    затем `flush()` записывает изменения в PostgreSQL одной транзакцией."""

    def __init__(self, source: Source, rows: list[Document]):
        self.source_id = source.id
        self.company_id = source.company_id
        self.is_competitor = source.is_competitor
        self._docs: dict[str, ParsedDocument] = {r.external_key: _row_to_parsed(r) for r in rows}
        self._dirty: dict[str, None] = {}  # dict сохраняет порядок записи (от старых к новым)
        self._metrics_only: dict[str, None] = {}
        self._removed: set[str] = set()
        self.state: dict[str, Any] = dict(source.sync_state or {})

    def __len__(self) -> int:
        return len(self._docs)

    def get(self, key: str) -> ParsedDocument | None:
        return self._docs.get(key)

    def all(self, source_type: SourceType | None = None, source_id: str | None = None) -> list[ParsedDocument]:
        return [d for d in self._docs.values() if (source_type is None or d.source_type == source_type)
                and (source_id is None or d.source_id == source_id)]

    def upsert(self, doc: ParsedDocument) -> UpsertStatus:
        old = self._docs.get(doc.key)
        self._removed.discard(doc.key)
        if old is None or doc.differs_from(old):
            self._docs[doc.key] = doc
            self._dirty[doc.key] = None
            return UpsertStatus.NEW if old is None else UpsertStatus.UPDATED
        if old.metrics != doc.metrics:
            old.metrics = doc.metrics
            old.fetched_at = doc.fetched_at
            self._metrics_only[doc.key] = None
            return UpsertStatus.METRICS
        return UpsertStatus.UNCHANGED

    def remove(self, keys) -> int:
        n = 0
        for k in keys:
            if self._docs.pop(k, None) is not None:
                self._removed.add(k)
                self._dirty.pop(k, None)
                n += 1
        return n

    def find_by_hash(self, source_type: SourceType, source_id: str, digest: str) -> ParsedDocument | None:
        return next((d for d in self._docs.values() if d.source_type == source_type and d.source_id == source_id
                     and d.content_hash == digest), None)

    def get_state(self, name: str) -> dict[str, Any]:
        return dict(self.state.get(name, {}))

    def set_state(self, name: str, state: dict[str, Any]) -> None:
        self.state[name] = state

    def save(self) -> None:  # запись выполняет flush()
        pass

    async def flush(self, session: AsyncSession) -> list[Document]:
        keys = list(self._dirty) + [k for k in self._metrics_only if k not in self._dirty]
        existing = {r.external_key: r for r in (await session.scalars(
            select(Document).where(Document.source_id == self.source_id, Document.external_key.in_(keys))
        )).all()} if keys else {}
        changed: list[Document] = []
        for key in keys:
            d = self._docs[key]
            row = existing.get(key)
            if row is None:
                row = Document(company_id=self.company_id, source_id=self.source_id, external_key=key,
                               is_competitor=self.is_competitor)
                session.add(row)
            row.source_type = d.source_type.value
            row.url, row.title, row.text = d.url, d.title, d.text
            row.content_hash = d.content_hash
            row.published_at, row.edited_at, row.fetched_at = d.published_at, d.edited_at, d.fetched_at
            row.metrics, row.meta = d.metrics, d.metadata
            if key in self._dirty:
                changed.append(row)
        if self._removed:
            await session.execute(delete(Document).where(Document.source_id == self.source_id,
                                                         Document.external_key.in_(self._removed)))
        await session.flush()
        return changed


# --------------------------------------------------------------------------- синхронизация


async def ingest_telegram_channel_post(sessionmaker: async_sessionmaker[AsyncSession], post: dict[str, Any],
                                       embedder: Embedder) -> None:
    """Один обработчик Bot API, привязка входящих постов к проверенному ID канала."""
    from app.parsers.telegram_parser import bot_post_to_document

    chat = post.get("chat") or {}
    if chat.get("type") != "channel":
        return
    ref = str(chat.get("id", ""))
    async with sessionmaker() as session, session.begin():
        sources = (await session.scalars(select(Source).where(
            Source.type == "telegram_bot", Source.ref == ref,
        ).limit(2).with_for_update())).all()
        if not sources:
            return
        if len(sources) != 1:
            log.error("Telegram-канал %s подключён к нескольким компаниям", ref)
            return
        src = sources[0]
        doc = bot_post_to_document(post)
        if doc is None:
            return
        row = await session.scalar(select(Document).where(Document.source_id == src.id,
                                                         Document.external_key == doc.key))
        store = PgSourceStore(src, [row] if row is not None else [])
        if doc.external_id.startswith("album") and row is not None:
            doc = bot_post_to_document(post, store)
        result = SyncResult(source_type=SourceType.TELEGRAM, source_id=doc.source_id, finished_at=utcnow())
        result.count(store.upsert(doc))
        await store.flush(session)
        source_id = src.id
        src.status, src.last_error = "pending", None
        src.last_sync_at, src.last_result = result.finished_at, result.model_dump(mode="json")
    await _index_telegram_pending(sessionmaker, source_id, embedder)


async def _index_telegram_pending(sessionmaker: async_sessionmaker[AsyncSession], source_id: uuid.UUID,
                                  embedder: Embedder) -> SyncResult:
    """Повторяет индексацию уже сохранённых постов без чтения обновлений Telegram."""
    result = SyncResult(source_type=SourceType.TELEGRAM, source_id=str(source_id),
                        notes=["Новые посты сохраняет основной обработчик Telegram-бота"])
    try:
        async with sessionmaker() as session, session.begin():
            src = await session.get(Source, source_id, with_for_update=True)
            if src is None:
                raise LookupError("Источник не найден")
            result.source_id = src.ref
            docs = (await session.scalars(select(Document).where(
                Document.source_id == source_id, Document.indexed_hash.is_distinct_from(Document.content_hash),
            ).order_by(Document.created_at).limit(TELEGRAM_INDEX_BATCH + 1))).all()
            more_pending = len(docs) > TELEGRAM_INDEX_BATCH
            docs = docs[:TELEGRAM_INDEX_BATCH]
            count = await asyncio.wait_for(index_documents(session, embedder, docs), timeout=TELEGRAM_INDEX_TIMEOUT)
            if docs:
                result.notes.append(f"RAG: проиндексировано фрагментов {count} в {len(docs)} документах")
            if more_pending:
                src.status = "pending"
                result.notes.append("Оставшиеся посты будут проиндексированы при следующей синхронизации")
            elif src.last_sync_at is not None:
                src.status = "ok"
            src.last_error = None
    except Exception:
        log.exception("Индексация постов Telegram для источника %s отложена", source_id)
        reason = "Посты сохранены. Индексация RAG будет повторена"
        result.errors.append(reason)
        try:
            async with sessionmaker() as session, session.begin():
                src = await session.get(Source, source_id, with_for_update=True)
                if src is not None and await session.scalar(select(Document.id).where(
                    Document.source_id == source_id, Document.indexed_hash.is_distinct_from(Document.content_hash),
                ).limit(1)):
                    src.status, src.last_error = "error", reason
        except Exception:
            log.exception("Не удалось сохранить статус индексации для источника %s", source_id)
    result.finished_at = utcnow()
    return result


def _jsonl_source(doc: ParsedDocument, archive: JsonlDocumentStore) -> tuple[str, str, str, dict[str, Any]]:
    """Тип и ссылка источника, исходный ключ и состояние из выгрузки парсера."""
    kind = doc.source_type.value
    if doc.source_type is SourceType.TELEGRAM:
        kind = {"web_preview": "telegram_web", "desktop_export": "telegram_export",
                "bot_api": "telegram_bot"}.get(doc.metadata.get("via"), "telegram")
    key = f"{kind}:{doc.source_id}"
    state = archive.get_state(key)
    ref = state.get("ref") or state.get("start_url") or state.get("file")
    if kind == "telegram_bot":
        ref = f"-100{doc.source_id}"
    elif not ref and kind == "telegram_web":
        ref = f"https://t.me/{doc.source_id}"
    elif not ref and kind == "website":
        url = urlsplit(doc.url)
        ref = f"{url.scheme}://{url.netloc}/"
    elif not ref and kind in {"telegram", "vk"}:
        ref = f"-100{doc.source_id}" if kind == "telegram" else doc.source_id
    if not ref:
        raise ValueError(f"В архиве отсутствует путь или ссылка для источника {key}")
    return kind, ref, key, state


async def import_jsonl(sessionmaker: async_sessionmaker[AsyncSession], company_id: uuid.UUID, path: Path,
                       embedder: Embedder) -> dict[str, int]:
    """Переносит документы и курсоры в PostgreSQL, строит индекс RAG в той же транзакции."""
    archive = JsonlDocumentStore(path)
    if not archive.docs_path.is_file():
        raise ValueError(f"Файл не найден: {archive.docs_path}")
    groups: dict[tuple[str, str, str], tuple[dict[str, Any], list[ParsedDocument]]] = {}
    for doc in archive.all():
        kind, ref, key, state = _jsonl_source(doc, archive)
        _, docs = groups.setdefault((kind, normalize_source_ref(kind, ref), key), (state, []))
        docs.append(doc)
    totals = {"new": 0, "updated": 0, "unchanged": 0, "chunks": 0}
    async with sessionmaker() as session, session.begin():
        company = await session.scalar(
            select(Company.id).where(Company.id == company_id).with_for_update(key_share=True)
        )
        if company is None:
            raise LookupError("Компания не найдена")
        for (kind, ref, key), (state, docs) in sorted(groups.items()):
            src = await add_source(session, company_id, kind, ref)
            src = await session.scalar(select(Source).where(Source.id == src.id).with_for_update().execution_options(
                populate_existing=True,
            ))
            if source_sync_in_progress(src):
                raise RuntimeError(f"Синхронизация источника {ref} уже выполняется")
            rows = (await session.scalars(select(Document).where(Document.source_id == src.id))).all()
            store = PgSourceStore(src, rows)
            for doc in docs:
                old = store.get(doc.key)
                status = store.upsert(doc) if old is None or doc.fetched_at >= old.fetched_at else UpsertStatus.UNCHANGED
                counter = "updated" if status is UpsertStatus.METRICS else status.value
                totals[counter] += 1
            if state and key not in store.state and not rows:
                store.set_state(key, state)
            changed = await store.flush(session)
            changed_ids = {row.id for row in changed}
            pending_index = [row for row in rows if row.indexed_hash != row.content_hash and row.id not in changed_ids]
            totals["chunks"] += await index_documents(session, embedder, changed + pending_index)
            src.sync_state, src.status, src.last_error = store.state, "ok", None
            src.last_sync_at = utcnow()
        session.add(AuditLog(company_id=company_id, action="source.import_jsonl", details=totals))
    return totals


async def _run_parser(src: Source, store: PgSourceStore) -> SyncResult:
    s = get_settings()
    opts = src.options or {}
    if src.type == "website":
        from app.parsers.website_parser import WebsiteParser, WebsiteParserConfig

        cfg = WebsiteParserConfig(max_pages=int(opts.get("max_pages", 300)), max_depth=int(opts.get("max_depth", 4)),
                                  allowed_languages=tuple(opts.get("languages", ["ru"])))
        return await WebsiteParser(cfg).sync(src.ref, store)  # type: ignore[arg-type]
    if src.type == "telegram":
        from app.parsers.telegram_parser import TelethonChannelParser

        p = TelethonChannelParser(s.tg_api_id, s.tg_api_hash, s.tg_session_path)
        return await p.sync(src.ref, store, limit=opts.get("limit"), join=bool(opts.get("join")),  # type: ignore[arg-type]
                            refresh_recent=int(opts.get("refresh_recent", 50)))
    if src.type == "telegram_web":
        from app.parsers.telegram_nokey import TelegramWebPreviewParser

        return await TelegramWebPreviewParser().sync(
            src.ref, store, max_posts=int(opts.get("max_posts", 1000)),  # type: ignore[arg-type]
            initial_pages=int(opts.get("initial_pages", s.tg_web_initial_pages)))
    if src.type == "telegram_export":
        from app.parsers.telegram_nokey import TelegramExportImporter

        return await TelegramExportImporter().sync(src.ref, store)  # type: ignore[arg-type]
    if src.type == "vk":
        from app.parsers.vk_parser import VKParser

        p = VKParser(s.vk_access_token, api_version=s.vk_api_version, api_base=s.vk_api_base)
        return await p.sync(src.ref, store, max_posts=int(opts.get("max_posts", 1000)),  # type: ignore[arg-type]
                            refresh_recent=int(opts.get("refresh_recent", 100)))
    if src.type == "file":
        from app.parsers.file_parser import FileParser

        return await FileParser().sync(src.ref, store)  # type: ignore[arg-type]
    raise ValueError(f"Нет парсера для {src.type}")


async def sync_source(sessionmaker: async_sessionmaker[AsyncSession], source_id: uuid.UUID, embedder: Embedder,
                      *, company_id: uuid.UUID | None = None) -> SyncResult:
    async with sessionmaker() as session, session.begin():
        q = select(Source).where(Source.id == source_id)
        if company_id is not None:
            q = q.where(Source.company_id == company_id)
        src = await session.scalar(q.with_for_update())
        if src is None:
            raise LookupError("Источник не найден")
        if src.type != "telegram_bot":
            if source_sync_in_progress(src):
                raise RuntimeError("Синхронизация этого источника уже выполняется")
            src.status = "syncing"
            claim_at = utcnow()
            src.updated_at = claim_at
            rows = (await session.scalars(select(Document).where(Document.source_id == src.id))).all()
            store = PgSourceStore(src, list(rows))

    if src.type == "telegram_bot":
        return await _index_telegram_pending(sessionmaker, source_id, embedder)

    try:
        result = await _run_parser(src, store)
    except Exception as e:
        async with sessionmaker() as session, session.begin():
            row = await session.get(Source, source_id, with_for_update=True)
            if row is not None and row.updated_at == claim_at:
                row.status, row.last_error = "error", f"{type(e).__name__}: {e}"
        raise

    async with sessionmaker() as session, session.begin():
        row = await session.get(Source, source_id, with_for_update=True)
        if row is None or row.updated_at != claim_at:
            raise RuntimeError("Источник удалён или синхронизация уже перезапущена")
        changed = await store.flush(session)
        indexed = await index_documents(session, embedder, changed)
        result.notes.append(f"RAG: проиндексировано фрагментов {indexed} в {len(changed)} документах")
        row.sync_state = store.state
        row.status = "ok" if not result.errors else "error"
        row.last_error = "; ".join(result.errors[:5]) or None
        row.last_sync_at = datetime.now(timezone.utc)
        row.last_result = result.model_dump(mode="json")
        session.add(AuditLog(company_id=row.company_id, action="source.sync",
                             details={"source_id": str(row.id), "new": result.new, "updated": result.updated,
                                      "errors": len(result.errors)}))
    return result
