"""Сборка контекста модели для конкретной компании.

Контекст = правила + профиль компании + долговременная память + текущая задача +
последние действия + сводки старых диалогов + релевантные фрагменты RAG +
последние сообщения диалога. Всё берётся из PostgreSQL по company_id,
определённому сервером.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Company, Conversation, Message
from app.rag.index import HybridSearcher, SearchHit
from app.services import memory as mem

from . import tools as tools_mod
from .connectors.base import AIMessage

AGENT_RULES = """Ты — Glass Agent, AI-маркетолог компании «{name}». Отвечай по-русски, по делу.

Правила:
1. Используй только данные этой компании: профиль, память, базу знаний и результаты инструментов.
2. Не выдумывай статистику, цены, сроки и факты. Если данных нет — скажи об этом и предложи, как их получить.
3. Ссылайся на источники из базы знаний в виде [n] с адресом. Используй номера из материалов
   текущего запроса и результатов его инструментов. Номера в старых ответах относятся к старым запросам.
4. Данные конкурентов явно помечай как данные конкурентов.
5. Посты публикуются только после согласования человеком — сам ничего не публикуешь.
6. {reference_rule}

Протокол ответа — СТРОГО один JSON-объект без текста вокруг:
- вызвать инструмент: {{"tool": "<имя>", "args": {{...}}}}
- финальный ответ: {{"answer": "<текст для пользователя>", "remember": [{{"category": "<brand_style|goal|preference|constraint|decision|fact|audience>", "content": "<факт>"}}], "current_task": "<текущая задача или пусто>"}}
В "remember" добавляй только новые устойчивые факты, решения и предпочтения, которые пользователь подтвердил.
За один ответ — один вызов инструмента. Результат инструмента придёт следующим сообщением.

Инструменты:
{catalog}"""

_FOLLOWUP = re.compile(
    r"\b(?:о како[мй]|о ч[её]м)\b.{0,120}\bспрашивал[аи]?\b|"
    r"\bповтори(?:те)?\s+(?:(?:предыдущий|прошлый|тот)\s+)?ответ\b|"
    r"\b(?:тот же|ту же|то же|том же|того же|этом же|вышеупомянут\w*|предыдущ\w+ ответ\w*)\b|"
    r"\b(?:этот|этом|того|этого|том)\s+(?:продукт\w*|стекл\w*|материал\w*|товар\w*)\b",
    re.IGNORECASE,
)


@dataclass
class BuiltContext:
    instructions: str
    messages: list[AIMessage]
    memory: dict[str, Any] = field(default_factory=dict)


def _section(title: str, body: str) -> str:
    return f"\n\n## {title}\n{body}" if body.strip() else ""


async def build_context(session: AsyncSession, company: Company, conv: Conversation, user_text: str,
                        searcher: HybridSearcher, cfg: mem.MemoryConfig | None = None,
                        rag_limit: int = 5, *, current_message_id: int,
                        ctx: tools_mod.ToolContext) -> BuiltContext:
    cfg = cfg or mem.MemoryConfig()
    memory = await mem.load_memory(session, company.id, cfg)
    recent_q = select(Message).where(Message.conversation_id == conv.id, Message.company_id == company.id,
                                     Message.id <= current_message_id)
    recent = list(reversed((await session.scalars(recent_q.order_by(Message.id.desc()).limit(cfg.recent_messages))).all()))
    search_text = user_text.strip()
    if ctx.reference_text:
        search_text = f"{ctx.reference_text[:1000]}\n{search_text}"
    elif _FOLLOWUP.search(search_text):
        previous = next((m.content.strip() for m in reversed(recent) if m.role == "user" and m.content
                         and m.id < current_message_id and not _FOLLOWUP.search(m.content)), "")
        if previous:
            search_text = f"{previous[:300]}\n{search_text}"
    hits: list[SearchHit] = []
    if search_text:
        hits = await searcher.search(session, company.id, search_text, limit=rag_limit, competitor=False)
    instr = AGENT_RULES.format(name=company.name, catalog=tools_mod.catalog(),
                              reference_rule=tools_mod.REFERENCE_RULE)
    if company.profile:
        instr += _section("Профиль компании", json.dumps(company.profile, ensure_ascii=False, indent=1))
    instr += _section("Долговременная память", "\n".join(f"- [{m['category']}] {m['content']}"
                                                         for m in memory["long_term"]))
    if memory["current_task"]:
        instr += _section("Текущая задача", memory["current_task"])
    instr += _section("Последние действия", "\n".join(f"- {m['at'][:16] if m['at'] else ''} {m['content']}"
                                                     for m in memory["episodic"]))
    instr += _section("Сводки прошлых диалогов", "\n\n".join(s["summary"] for s in memory["summaries"]))
    instr += _section("Фрагменты базы знаний по запросу", "\n".join(
        f"[{ctx.cite(h.url, h.title, h.source_type)}] {h.title} — {h.url}\n{h.text[:800]}" for h in hits))

    messages = [AIMessage(m.role, ctx.reference_prompt() if ctx.reference_text and m.id == current_message_id
                          else m.content)
                for m in recent if m.role in ("user", "assistant") and m.content]
    return BuiltContext(instructions=instr, messages=messages, memory=memory)
