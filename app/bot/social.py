"""Общий сценарий личного диалога с ботом ВКонтакте и MAX."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.connectors.base import AIError
from app.ai.orchestrator import Orchestrator
from app.api.deps import ROLE_RANK
from app.core.config import get_settings
from app.db.models import Company, CompanyMember, ScheduledTask, User, UserPlatformIdentity
from app.services import analytics, posts as P
from app.services.publisher import PublishError, approval_message

log = logging.getLogger(__name__)
Buttons = list[list[tuple[str, str]]]


class SocialMessenger(Protocol):
    async def send(self, external_id: str, text: str, *,
                   buttons: Buttons | None = None, image: Path | None = None) -> None: ...


async def send_review(messenger: SocialMessenger, external_id: str, post: P.Post, company: Company,
                      *, can_review: bool = True) -> None:
    text, keyboard = approval_message(post, company)
    image = Path(post.image_path) if post.image_path else None
    if image is not None and not image.is_file():
        raise PublishError("Изображение для согласования недоступно", permanent=True)
    buttons = [[(button["text"], button["callback_data"]) for button in row]
               for row in keyboard["inline_keyboard"]]
    await messenger.send(external_id, text, buttons=buttons if can_review else None, image=image)


class SocialNotifier:
    def __init__(self, platform: str, messenger: SocialMessenger):
        self.platform = platform
        self.messenger = messenger

    async def _recipients(self, session: AsyncSession, company_id: UUID) -> list[str]:
        return list(await session.scalars(select(UserPlatformIdentity.external_id)
                    .join(CompanyMember, CompanyMember.user_id == UserPlatformIdentity.user_id)
                    .where(CompanyMember.company_id == company_id, CompanyMember.role.in_(("owner", "approver")),
                           UserPlatformIdentity.platform == self.platform)))

    async def notify(self, session: AsyncSession, company_id: UUID, text: str) -> None:
        for external_id in await self._recipients(session, company_id):
            await self._send(external_id, text)

    async def notify_approval(self, session: AsyncSession, post: P.Post) -> None:
        if post.status != P.PENDING:
            return
        company = await session.get(Company, post.company_id)
        for external_id in await self._recipients(session, post.company_id):
            try:
                await send_review(self.messenger, external_id, post, company)
            except (PublishError, OSError) as exc:
                log.warning("Не доставлено согласование %s через %s: %s", post.id, self.platform, exc)

    async def _send(self, external_id: str, text: str) -> None:
        try:
            await self.messenger.send(external_id, text)
        except PublishError as exc:
            log.warning("Уведомление через %s не доставлено: %s", self.platform, exc)

    async def notify_admins(self, text: str) -> None:
        from app.db.session import get_sessionmaker

        async with get_sessionmaker()() as session:
            ids = (await session.scalars(select(UserPlatformIdentity.external_id).join(
                User, User.id == UserPlatformIdentity.user_id).where(
                UserPlatformIdentity.platform == self.platform,
                User.telegram_id.in_(get_settings().admin_ids)))).all()
        for external_id in ids:
            await self._send(external_id, text)


class SocialBot:
    def __init__(self, platform: str, messenger: SocialMessenger, sm: async_sessionmaker[AsyncSession],
                 orchestrator: Orchestrator | None):
        self.platform = platform
        self.messenger = messenger
        self.sm = sm
        self.orchestrator = orchestrator

    async def _identity(self, session: AsyncSession, external_id: str
                        ) -> tuple[UserPlatformIdentity, User] | None:
        row = (await session.execute(select(UserPlatformIdentity, User)
               .join(User, User.id == UserPlatformIdentity.user_id)
               .where(UserPlatformIdentity.platform == self.platform,
                      UserPlatformIdentity.external_id == external_id))).one_or_none()
        return (row[0], row[1]) if row is not None else None

    @staticmethod
    def _is_admin(user: User) -> bool:
        return user.telegram_id in get_settings().admin_ids

    async def _companies(self, session: AsyncSession, user: User) -> list[tuple[Company, str]]:
        if self._is_admin(user):
            return [(c, "owner") for c in (await session.scalars(select(Company).order_by(Company.name))).all()]
        rows = await session.execute(select(Company, CompanyMember.role)
                                     .join(CompanyMember, CompanyMember.company_id == Company.id)
                                     .where(CompanyMember.user_id == user.id).order_by(Company.name))
        return [(company, role) for company, role in rows]

    async def handle_message(self, external_id: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        command, _, arg = text.partition(" ")
        command = command.lower() if command.startswith("/") else ""
        async with self.sm() as session:
            found = await self._identity(session, external_id)
            if found is None:
                await self.messenger.send(external_id, f"Передайте администратору ваш ID для привязки: {external_id}.")
                return
            identity, user = found
            if command in ("/start", "/help"):
                await self.messenger.send(external_id, "Пишите поручения обычным текстом.\n"
                                          "/companies — список · /use <slug> — выбор · /posts — согласование\n"
                                          "/report — отчёт · /tasks — задачи")
                return
            companies = await self._companies(session, user)
            if command == "/companies":
                await self.messenger.send(external_id, "\n".join(
                    f"• {company.slug} — {company.name} ({role})" for company, role in companies
                ) or "Администратор ещё не предоставил доступ к компании.")
                return
            prefs = identity.prefs or {}
            if command == "/use":
                match = next((company for company, _ in companies if company.slug == arg.strip()), None)
                if match is None:
                    await self.messenger.send(external_id, "Компания не найдена. /companies — список.")
                    return
                identity.prefs = {**prefs, "company_id": str(match.id), "revision": None}
                await session.commit()
                await self.messenger.send(external_id, f"Выбрана компания «{match.name}».")
                return
            selected = next(((company, role) for company, role in companies
                             if str(company.id) == prefs.get("company_id")), None)
            if selected is None and len(companies) == 1:
                selected = companies[0]
            if selected is None:
                await self.messenger.send(external_id, "Выберите компанию: /use <slug> (/companies — список).")
                return
            company, role = selected
            rank = ROLE_RANK.get(role, -1)
            if command == "/posts":
                await self._show_posts(session, external_id, company, rank)
            elif command == "/report":
                days = min(365, max(1, int(arg))) if arg.isascii() and arg.isdigit() and len(arg) < 6 else 7
                report = await analytics.period_report(session, company.id, days=days)
                await self.messenger.send(external_id, analytics.report_text(company.name, report))
            elif command == "/tasks":
                rows = await session.scalars(select(ScheduledTask).where(ScheduledTask.company_id == company.id)
                                             .order_by(ScheduledTask.title))
                await self.messenger.send(external_id, "\n".join(
                    f"• {task.title} — {'включена' if task.enabled else 'выключена'}" for task in rows
                ) or "Задач пока нет.")
            elif command:
                await self.messenger.send(external_id, "Неизвестная команда. /help")
            elif prefs.get("revision"):
                await self._revise(session, identity, user, company, rank, text)
            elif rank < ROLE_RANK["editor"]:
                await self.messenger.send(external_id, "Для поручений AI нужна роль editor.")
            else:
                await self._chat(session, external_id, user.id, company.id, text)

    async def _show_posts(self, session: AsyncSession, external_id: str, company: Company, rank: int) -> None:
        pending = await P.list_posts(session, company.id, status=P.PENDING, limit=10)
        drafts = await P.list_posts(session, company.id, status=P.DRAFT, limit=5)
        if not pending and not drafts:
            await self.messenger.send(external_id, "Нет постов на согласовании и черновиков.")
        for post in pending:
            try:
                await send_review(self.messenger, external_id, post, company, can_review=rank >= ROLE_RANK["approver"])
            except (PublishError, OSError):
                await self.messenger.send(external_id, f"Не удалось показать материал «{post.topic or post.id}».")
        for post in drafts:
            buttons = [[("На согласование", f"sb:{post.id}")]] if rank >= ROLE_RANK["editor"] else None
            await self.messenger.send(external_id, f"Черновик · {post.platform}\n\n{post.text}", buttons=buttons)

    async def _chat(self, session: AsyncSession, external_id: str, user_id: int, company_id: UUID, text: str) -> None:
        if self.orchestrator is None:
            await self.messenger.send(external_id, "AI_CONNECTION_UNAVAILABLE: подключение ChatGPT не настроено.")
            return
        try:
            reply = await self.orchestrator.chat(session, company_id, text, user_id=user_id,
                                                 meta={"source": self.platform})
        except AIError as exc:
            await self.messenger.send(external_id, f"{exc.code}: {exc}")
            return
        answer = reply.answer
        if reply.is_mock and not answer.startswith("[ТЕСТОВАЯ МОДЕЛЬ]"):
            answer = "[ТЕСТОВАЯ МОДЕЛЬ] " + answer
        await self.messenger.send(external_id, answer)

    async def _revise(self, session: AsyncSession, identity: UserPlatformIdentity, user: User, company: Company,
                      rank: int, text: str) -> None:
        prefs = identity.prefs or {}
        revision = prefs["revision"]
        identity.prefs = {**prefs, "revision": None}
        external_id = identity.external_id
        try:
            if rank < ROLE_RANK["approver"]:
                raise P.PostStateError("Недостаточно прав")
            post = await P.get_post(session, company.id, revision["post_id"], for_update=True)
            P.require_current_review(post, company, revision["token"])
            await P.request_changes(session, post, user_id=user.id, comment=text)
        except (LookupError, P.PostStateError) as exc:
            await session.commit()
            await self.messenger.send(external_id, str(exc))
            return
        await session.commit()
        await self.messenger.send(external_id, "Пост возвращён на доработку.")
        if self.orchestrator is not None:
            await self._chat(session, external_id, user.id, company.id,
                             f"Доработай пост {post.id} по комментарию согласующего: «{text}». "
                             "Отправь его на повторное согласование.")

    async def handle_action(self, external_id: str, action: str) -> str:
        kind, _, target = action.partition(":")
        post_id, _, token = target.partition(":")
        if kind not in {"ap", "pn", "rv", "rj", "sb"}:
            return "Неизвестное действие"
        try:
            pid = UUID(post_id)
        except ValueError:
            return "Пост не найден"
        async with self.sm() as session:
            found = await self._identity(session, external_id)
            if found is None:
                return "Сначала привяжите аккаунт через администратора"
            identity, user = found
            post = await session.get(P.Post, pid, with_for_update=True)
            if post is None:
                return "Пост не найден"
            allowed = {company.id: (company, role) for company, role in await self._companies(session, user)}
            if post.company_id not in allowed:
                return "Пост не найден"
            company, role = allowed[post.company_id]
            min_role = "editor" if kind == "sb" else "approver"
            if ROLE_RANK.get(role, -1) < ROLE_RANK[min_role]:
                return "Недостаточно прав"
            try:
                if kind == "sb":
                    await P.submit(session, post, user_id=user.id)
                    note = "Отправлено на согласование"
                else:
                    P.require_current_review(post, company, token)
                    if kind == "rv":
                        identity.prefs = {**(identity.prefs or {}), "company_id": str(company.id),
                                          "revision": {"post_id": post_id, "token": token}}
                        note = "Напишите комментарием, что доработать"
                    elif kind == "rj":
                        await P.reject(session, post, user_id=user.id, comment=f"Отклонено через {self.platform}")
                        note = "Пост отклонён"
                    else:
                        at = datetime.now(timezone.utc) if kind == "pn" else post.proposed_at
                        await P.approve(session, post, user_id=user.id, scheduled_at=at, expected_review=token)
                        note = "Согласовано. Пост поставлен в очередь." if at else "Пост согласован."
            except P.PostStateError as exc:
                await session.rollback()
                return str(exc)
            await session.commit()
            if kind == "sb":
                notifier = getattr(self.orchestrator, "notifier", None) or SocialNotifier(self.platform, self.messenger)
                try:
                    await notifier.notify_approval(session, post)
                except Exception:
                    log.exception("Уведомление о согласовании не отправлено")
            return note
