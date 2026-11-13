"""Привязка аккаунта мессенджера не создаёт пользователя и не меняет его роли."""
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, User, UserPlatformIdentity


async def link_identity(session: AsyncSession, platform: str, external_id: str,
                        telegram_id: int) -> UserPlatformIdentity:
    if platform not in {"vk", "max"}:
        raise ValueError("Поддерживаются только vk и max")
    if not external_id.isascii() or not external_id.isdecimal() or int(external_id) <= 0 or len(external_id) > 30:
        raise ValueError("Нужен положительный числовой ID пользователя площадки")
    external_id = str(int(external_id))
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
    if user is None:
        raise ValueError("Пользователь не найден. Сначала администратор должен добавить его в компанию.")
    existing = (await session.scalars(select(UserPlatformIdentity).where(
        UserPlatformIdentity.platform == platform,
        or_(UserPlatformIdentity.external_id == external_id, UserPlatformIdentity.user_id == user.id)
    ))).all()
    if existing:
        if len(existing) == 1 and existing[0].external_id == external_id and existing[0].user_id == user.id:
            return existing[0]
        raise ValueError("Аккаунт уже связан с другим пользователем или у пользователя есть другая привязка.")
    identity = UserPlatformIdentity(platform=platform, external_id=external_id, user_id=user.id, prefs={})
    session.add(identity)
    session.add(AuditLog(action="identity.link", details={"platform": platform, "external_id": external_id,
                                                        "target_user_id": user.id}))
    await session.flush()
    return identity
