import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import AuditLog, User, UserPlatformIdentity
from app.services.identities import link_identity
from app.services.notifications import MultiNotifier, get_notifier


async def test_identity_link_is_explicit_unique_and_does_not_grant_roles(pg):
    _, sm = pg
    async with sm() as session, session.begin():
        session.add_all([User(telegram_id=123, name="Owner"), User(telegram_id=124, name="Other")])
    async with sm() as session, session.begin():
        with pytest.raises(ValueError, match="не найден"):
            await link_identity(session, "vk", "100", 999)
        identity = await link_identity(session, "vk", "00100", 123)
        assert identity.external_id == "100"
        assert (await link_identity(session, "vk", "100", 123)).id == identity.id
        with pytest.raises(ValueError, match="уже связан"):
            await link_identity(session, "vk", "100", 124)
        with pytest.raises(ValueError, match="уже связан"):
            await link_identity(session, "vk", "200", 123)
        second = await link_identity(session, "max", "100", 124)
        assert second.user_id != identity.user_id
    async with sm() as session:
        assert len((await session.scalars(select(UserPlatformIdentity))).all()) == 2
        assert len((await session.scalars(select(AuditLog).where(AuditLog.action == "identity.link"))).all()) == 2
        assert not any(u.is_admin for u in (await session.scalars(select(User))).all())


async def test_notification_failure_does_not_prevent_other_platform_delivery():
    delivered = []

    class Failing:
        async def notify(self, *args):
            raise RuntimeError("unavailable")

    class Working:
        async def notify(self, *args):
            delivered.append(args)

    await MultiNotifier([Failing(), Working()]).notify(None, "company", "report")
    assert delivered == [(None, "company", "report")]


async def test_notifier_constructs_only_configured_channels_and_unwraps_max_token(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "tg_bot_token", SecretStr("test_tg"))
    monkeypatch.setattr(settings, "vk_bot_token", SecretStr("test_vk"))
    monkeypatch.setattr(settings, "vk_bot_group_id", 100)
    monkeypatch.setattr(settings, "max_bot_token", SecretStr("test_max"))
    notifier = get_notifier()
    assert [type(x).__name__ for x in notifier.notifiers] == ["TelegramNotifier", "VKNotifier", "MaxNotifier"]
    assert notifier.notifiers[2].messenger.api._token == "test_max"
