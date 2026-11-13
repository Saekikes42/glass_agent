"""Личный MAX-бот, регистрация вебхука и тестовый опрос событий."""
from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn

from app.ai.factory import get_ai_connector
from app.ai.orchestrator import Orchestrator
from app.bot.max_bot import (
    MaxInbox,
    MaxMessenger,
    create_webhook_app,
    max_worker,
    poll_updates,
    register_webhook,
)
from app.bot.social import SocialBot
from app.core.config import get_settings
from app.db.session import get_sessionmaker, init_db
from app.rag.embeddings import get_embedder
from app.rag.index import HybridSearcher
from app.services.platforms import MaxAPI
from app.services.publisher import PublishError

log = logging.getLogger(__name__)


def _inbox(api: MaxAPI) -> MaxInbox:
    from app.services.notifications import get_notifier

    settings = get_settings()
    messenger = MaxMessenger(api)
    sessions = get_sessionmaker()
    reranker = None
    if settings.reranker_model:
        from sentence_transformers import CrossEncoder

        reranker = CrossEncoder(settings.reranker_model)
    orchestrator = Orchestrator(
        get_ai_connector(), HybridSearcher(get_embedder(), reranker), notifier=get_notifier(),
        max_steps=settings.agent_max_steps, max_llm_calls=settings.agent_max_llm_calls,
        max_seconds=settings.agent_max_seconds,
    )
    return MaxInbox(sessions, SocialBot("max", messenger, sessions, orchestrator), messenger)


async def _poll(api: MaxAPI, inbox: MaxInbox) -> None:
    await init_db()
    async with max_worker(inbox) as stop:
        await poll_updates(api, inbox, stop)


async def _check(api: MaxAPI) -> None:
    bot = await api.request("GET", "/me")
    subscriptions = await api.request("GET", "/subscriptions")
    print(f"MAX API доступен. Бот: {bot.get('username') or bot.get('first_name') or bot.get('user_id')}")
    print(f"Подписок webhook: {len(subscriptions.get('subscriptions') or [])}")


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Личный MAX-бот Glass Agent")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--register", metavar="HTTPS_URL", help="Подписать бота на события по HTTPS")
    mode.add_argument("--check", action="store_true", help="Проверить токен и число подписок")
    mode.add_argument("--poll", action="store_true", help="Тестовый опрос событий, если webhook не включён")
    parser.add_argument("--host", default="127.0.0.1", help="Локальный адрес обработчика webhook")
    parser.add_argument("--port", type=int, default=settings.max_bot_port, help="Локальный порт обработчика")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = settings.max_bot_token.get_secret_value() if settings.max_bot_token else ""
    secret = settings.max_webhook_secret.get_secret_value() if settings.max_webhook_secret else ""
    try:
        api = MaxAPI(token, api_base=settings.max_api_base)
        if args.check:
            asyncio.run(_check(api))
        elif args.register:
            asyncio.run(register_webhook(api, args.register, secret))
            print("Подписка MAX на webhook подтверждена.")
        elif args.poll:
            asyncio.run(_poll(api, _inbox(api)))
        else:
            app = create_webhook_app(_inbox(api), secret, on_start=init_db)
            uvicorn.run(app, host=args.host, port=args.port)
    except (ValueError, PublishError) as exc:
        log.error("MAX: %s", exc)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
