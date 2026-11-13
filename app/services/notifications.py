"""Уведомления в подключённые площадки; сбой одной доставки не блокирует остальные."""
import logging

from app.core.config import get_settings
from app.services.publisher import LogNotifier, TelegramBotAPI, TelegramNotifier

log = logging.getLogger(__name__)


class MultiNotifier:
    def __init__(self, notifiers):
        self.notifiers = tuple(notifiers)

    async def _dispatch(self, method, *args):
        for notifier in self.notifiers:
            try:
                await getattr(notifier, method)(*args)
            except Exception as exc:
                # Не логируем параметры запроса/токены сторонних API.
                log.warning("Уведомление %s через %s: %s", method, type(notifier).__name__, type(exc).__name__)

    async def notify(self, session, company_id, text):
        await self._dispatch("notify", session, company_id, text)

    async def notify_approval(self, session, post):
        await self._dispatch("notify_approval", session, post)

    async def notify_admins(self, text):
        await self._dispatch("notify_admins", text)


def get_notifier(*, telegram_token=None):
    settings = get_settings()
    notifiers = []
    token = telegram_token if telegram_token is not None else settings.tg_bot_token
    if token:
        notifiers.append(TelegramNotifier(TelegramBotAPI(token)))
    if settings.vk_bot_token and settings.vk_bot_group_id:
        from app.bot.vk_bot import VKMessenger, VKNotifier
        notifiers.append(VKNotifier(VKMessenger(settings.vk_bot_token, api_version=settings.vk_api_version,
                                               api_base=settings.vk_api_base)))
    if settings.max_bot_token:
        from app.bot.max_bot import MaxNotifier
        from app.services.platforms import MaxAPI
        notifiers.append(MaxNotifier(MaxAPI(settings.max_bot_token.get_secret_value(),
                                           api_base=settings.max_api_base)))
    return MultiNotifier(notifiers) if notifiers else LogNotifier()
