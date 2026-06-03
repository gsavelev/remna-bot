from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User as TelegramUser,
)
from aiogram.utils.token import validate_token

from src.config import TelegramConfig
from src.database import Database
from src.rw_client import RemnawaveUserManager

_MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}
_REMNA_USERNAME_MAX_LENGTH = 36
_SUBSCRIPTION_REVISION_INTERVAL_SECONDS = 24 * 60 * 60
_LOGGER = logging.getLogger(__name__)


class RemnaTelegramBot:
    def __init__(
        self,
        *,
        bot_config: TelegramConfig,
        database: Database,
        rw_manager: RemnawaveUserManager,
    ) -> None:
        self._config = bot_config
        self._db = database
        self._rw_manager = rw_manager
        validate_token(bot_config.bot_token)
        self._bot = Bot(token=bot_config.bot_token)
        self._dispatcher = Dispatcher()
        self._router = Router()
        self._register_routes()

    async def run(self) -> None:
        await self._db.initialize()
        self._dispatcher.include_router(self._router)
        await self._bot.delete_webhook(drop_pending_updates=False)
        revision_task = asyncio.create_task(self._run_subscription_revision_loop())
        try:
            await self._dispatcher.start_polling(
                self._bot,
                polling_timeout=self._config.poll_timeout_seconds,
            )
        finally:
            revision_task.cancel()
            with suppress(asyncio.CancelledError):
                await revision_task

    async def close(self) -> None:
        await self._bot.session.close()

    def _register_routes(self) -> None:
        self._router.message.filter(F.chat.type == "private")
        self._router.message.register(self._handle_start, Command("start"))

    async def _handle_start(self, message: Message) -> None:
        user = self._require_user(message)
        if not await self._ensure_access(message, user):
            return
        subscription_url = await self._ensure_subscription(user)
        keyboard_rows = [
            [InlineKeyboardButton(text="скачать приложение", url=str(self._config.download_url))],
        ]
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=keyboard_rows,
        )
        await message.answer(
            "1\\. скачай и установи приложение\n"
            "2\\. скопируй и вставь в него ссылку\n\n"
            f"`{self._escape_markdown_code(subscription_url)}`\n\n"
            "3\\. включи vpn \\(обычно – круглой кнопкой\\)",
            reply_markup=keyboard,
            disable_web_page_preview=True,
            parse_mode="MarkdownV2",
        )

    async def _sync_user(self, user: TelegramUser) -> None:
        await self._db.upsert_user(
            tg_id=user.id,
            tg_username=user.username,
            tg_name=user.full_name,
            is_chat_member=await self._is_chat_member(user.id),
            is_admin=self._is_admin(user.id),
        )

    async def _ensure_access(self, message: Message, user: TelegramUser) -> bool:
        if not await self._ensure_user_access(user):
            await message.answer("denied")
            return False
        return True

    async def _ensure_user_access(self, user: TelegramUser) -> bool:
        is_chat_member = await self._is_chat_member(user.id)
        await self._db.upsert_user(
            tg_id=user.id,
            tg_username=user.username,
            tg_name=user.full_name,
            is_chat_member=is_chat_member,
            is_admin=self._is_admin(user.id),
        )
        return is_chat_member

    async def _ensure_subscription(self, user: TelegramUser) -> str:
        return await self._ensure_subscription_for_user(
            tg_id=user.id,
            tg_username=user.username,
            tg_name=user.full_name,
        )

    async def _ensure_subscription_for_user(
        self,
        *,
        tg_id: int,
        tg_username: str | None,
        tg_name: str,
    ) -> str:
        existing = await self._db.get_subscription_by_tg_id(tg_id)
        if existing is not None:
            try:
                remote_user = await self._rw_manager.get_user(existing.uuid)
            except Exception:
                remote_user = None
            if remote_user is not None:
                subscription_url = getattr(remote_user, "subscription_url", None)
                if isinstance(subscription_url, str) and subscription_url:
                    await self._db.upsert_subscription(
                        user_tg_id=tg_id,
                        uuid=existing.uuid,
                        username=existing.username,
                        path=self._extract_path(subscription_url),
                    )
                    return subscription_url

        username = self._build_subscription_username(
            tg_id=tg_id,
            tg_username=tg_username,
            tg_name=tg_name,
        )
        try:
            remna_user = await self._rw_manager.add_user(
                username=username,
                expire_at=datetime.now(UTC) + timedelta(days=self._config.subscription_expire_days),
                traffic_limit_bytes=self._traffic_limit_bytes(),
                telegram_id=tg_id,
                description=f"Telegram user {tg_name}",
                active_internal_squads=self._rw_manager.default_internal_squads(),
            )
        except Exception:
            remna_user = await self._rw_manager.get_user_by_username(username)

        subscription_url = str(getattr(remna_user, "subscription_url", ""))
        await self._db.upsert_subscription(
            user_tg_id=tg_id,
            uuid=str(getattr(remna_user, "uuid")),
            username=str(getattr(remna_user, "username")),
            path=self._extract_path(subscription_url),
        )
        return subscription_url

    async def _is_chat_member(self, tg_user_id: int) -> bool:
        try:
            member = await self._bot.get_chat_member(self._config.chat_id, tg_user_id)
        except Exception:
            return False
        return member.status in _MEMBER_STATUSES

    async def _run_subscription_revision_loop(self) -> None:
        while True:
            try:
                await self._revise_subscriptions()
            except Exception:
                _LOGGER.exception("Subscription revision failed")
            await asyncio.sleep(_SUBSCRIPTION_REVISION_INTERVAL_SECONDS)

    async def _revise_subscriptions(self) -> None:
        users = await self._db.list_users_with_subscriptions()
        chat_name = await self._get_required_chat_name_for_revision()
        for user in users:
            if user.subscription is None:
                continue

            is_chat_member = await self._check_chat_membership_for_revision(user.tg_id)
            if is_chat_member is not False:
                continue

            try:
                result = await self._rw_manager.remove_user(user.subscription.uuid)
            except Exception:
                _LOGGER.exception(
                    "Failed to delete Remnawave user during subscription revision: tg_id=%s uuid=%s",
                    user.tg_id,
                    user.subscription.uuid,
                )
                continue

            is_deleted = bool(getattr(result, "is_deleted", False))
            if not is_deleted:
                _LOGGER.warning(
                    "Remnawave user was not deleted during subscription revision: tg_id=%s uuid=%s",
                    user.tg_id,
                    user.subscription.uuid,
                )
                continue

            await self._db.delete_subscription_by_tg_id(user.tg_id)
            await self._db.set_user_chat_member(user.tg_id, False)
            await self._notify_subscription_deleted(user.tg_id, chat_name)
            _LOGGER.info(
                "Deleted subscription for Telegram user outside required chat: tg_id=%s uuid=%s",
                user.tg_id,
                user.subscription.uuid,
            )

    async def _check_chat_membership_for_revision(self, tg_user_id: int) -> bool | None:
        try:
            member = await self._bot.get_chat_member(self._config.chat_id, tg_user_id)
        except Exception:
            _LOGGER.exception(
                "Failed to check chat membership during subscription revision: tg_id=%s",
                tg_user_id,
            )
            return None
        return member.status in _MEMBER_STATUSES

    async def _get_required_chat_name_for_revision(self) -> str:
        try:
            chat = await self._bot.get_chat(self._config.chat_id)
        except Exception:
            _LOGGER.exception(
                "Failed to get required chat name during subscription revision: chat_id=%s",
                self._config.chat_id,
            )
            return str(self._config.chat_id)

        title = getattr(chat, "title", None)
        if isinstance(title, str) and title:
            return title

        username = getattr(chat, "username", None)
        if isinstance(username, str) and username:
            return f"@{username}"

        return str(self._config.chat_id)

    async def _notify_subscription_deleted(self, tg_user_id: int, chat_name: str) -> None:
        try:
            await self._bot.send_message(
                tg_user_id,
                "подписка удалена, так как вы более не состоите "
                f"в `{self._escape_markdown_code(chat_name)}`",
                parse_mode="MarkdownV2",
            )
        except Exception:
            _LOGGER.exception(
                "Failed to notify user about deleted subscription: tg_id=%s",
                tg_user_id,
            )

    def _is_admin(self, tg_user_id: int) -> bool:
        return tg_user_id in self._config.admin_ids

    @staticmethod
    def _build_subscription_username(
        *,
        tg_id: int,
        tg_username: str | None,
        tg_name: str,
    ) -> str:
        if tg_username:
            base = tg_username.lower()
        else:
            normalized = re.sub(r"[^a-z0-9]+", "_", tg_name.lower())
            base = normalized.strip("_") or "telegram_user"
        base = re.sub(r"[^a-z0-9_]", "_", base)
        username = f"{base}_{tg_id}"
        if len(username) > _REMNA_USERNAME_MAX_LENGTH:
            return f"telegram_user_{tg_id}"
        return username

    @staticmethod
    def _extract_path(subscription_url: str) -> str:
        if not subscription_url:
            return ""
        parsed = urlparse(subscription_url)
        return parsed.path or "/"

    def _traffic_limit_bytes(self) -> int | None:
        if self._config.traffic_limit_gb is None:
            return None
        return self._config.traffic_limit_gb * 1024 * 1024 * 1024

    @staticmethod
    def _escape_markdown_code(value: str) -> str:
        return value.replace("\\", "\\\\").replace("`", "\\`")

    @staticmethod
    def _require_user(message: Message) -> TelegramUser:
        if message.from_user is None:
            raise ValueError("Message has no sender")
        return message.from_user
