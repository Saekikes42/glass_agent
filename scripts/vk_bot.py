"""Запуск бота ВКонтакте через Bots Long Poll."""
from __future__ import annotations

import asyncio
import logging
import sys

from app.ai.factory import get_ai_connector
from app.ai.orchestrator import Orchestrator
from app.bot.inbox import BotInbox
from app.bot.social import SocialBot
from app.bot.vk_bot import VK_INBOX_LOCK, VKBot, VKMessenger
from app.core.config import get_settings
from app.db.session import get_sessionmaker, init_db
from app.rag.embeddings import get_embedder
from app.rag.index import HybridSearcher
from app.services.notifications import get_notifier
from app.services.publisher import PublishError


async def main_async() -> int:
    settings = get_settings()
    if not settings.vk_bot_token or not settings.vk_bot_group_id or settings.vk_bot_group_id <= 0:
        logging.error("Укажите VK_BOT_TOKEN и положительный VK_BOT_GROUP_ID")
        return 2
    await init_db()
    messenger = VKMessenger(settings.vk_bot_token, api_version=settings.vk_api_version,
                            api_base=settings.vk_api_base)
    orchestrator = Orchestrator(get_ai_connector(), HybridSearcher(get_embedder()),
                                max_steps=settings.agent_max_steps, max_llm_calls=settings.agent_max_llm_calls,
                                max_seconds=settings.agent_max_seconds, notifier=get_notifier())
    sessions = get_sessionmaker()
    bot = VKBot(messenger, SocialBot("vk", messenger, sessions, orchestrator), settings.vk_bot_group_id)
    inbox = BotInbox(sessions, "vk", VK_INBOX_LOCK, bot.dispatch)
    stop = asyncio.Event()
    worker = asyncio.create_task(inbox.run(stop))
    try:
        await bot.run(stop, inbox=inbox)
    except PublishError as exc:
        logging.error("%s", exc)
        return 1
    finally:
        stop.set()
        try:
            await asyncio.wait_for(worker, timeout=30)
        except TimeoutError:
            logging.warning("VK: обработка текущего события не завершилась при остановке")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # Long Poll key is in the URL.
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
