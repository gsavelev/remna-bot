from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User as TelegramUser,
)
from aiogram.utils.token import validate_token

from src.config import TelegramConfig
from src.database import Database, User
from src.rw_client import RemnawaveUserManager

_MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}
_REMNA_USERNAME_MAX_LENGTH = 36
_ADD_USER_CALLBACK = "add_user"
_DELETE_USER_CALLBACK = "delete_user_by_username"
_HAPP_DOWNLOAD_URL = "https://www.happ.su/main/ru"


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
        self._awaiting_add_identifier: set[int] = set()
        self._awaiting_delete_identifier: set[int] = set()
        self._register_routes()

    async def run(self) -> None:
        await self._db.initialize()
        self._dispatcher.include_router(self._router)
        await self._bot.delete_webhook(drop_pending_updates=False)
        await self._dispatcher.start_polling(
            self._bot,
            polling_timeout=self._config.poll_timeout_seconds,
        )

    async def close(self) -> None:
        await self._bot.session.close()

    def _register_routes(self) -> None:
        self._router.message.filter(F.chat.type == "private")
        self._router.message.register(self._handle_start, Command("start"))
        self._router.callback_query.register(
            self._handle_add_user_button,
            F.data == _ADD_USER_CALLBACK,
        )
        self._router.callback_query.register(
            self._handle_delete_user_button,
            F.data == _DELETE_USER_CALLBACK,
        )
        self._router.message.register(self._handle_admin_text_input, F.text)

    async def _handle_start(self, message: Message) -> None:
        user = self._require_user(message)
        if not await self._ensure_access(message, user):
            return
        subscription_url = await self._ensure_subscription(user)
        keyboard_rows = [
            [InlineKeyboardButton(text="скачать приложение", url=_HAPP_DOWNLOAD_URL)],
        ]
        if self._is_admin(user.id):
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text="добавить пользователя",
                        callback_data=_ADD_USER_CALLBACK,
                        style="success",
                    ),
                ],
            )
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text="удалить пользователя",
                        callback_data=_DELETE_USER_CALLBACK,
                        style="danger",
                    ),
                ],
            )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=keyboard_rows,
        )
        await message.answer(
            "1\\. скачай и установи приложение\n"
            "2\\. скопируй и вставь в него ссылку\n\n"
            f"`{self._escape_markdown_code(subscription_url)}`\n\n"
            "3\\. включи vpn \\(обычно круглой кнопкой\\)",
            reply_markup=keyboard,
            disable_web_page_preview=True,
            parse_mode="MarkdownV2",
        )

    async def _handle_add_user_button(self, callback: CallbackQuery) -> None:
        user = callback.from_user
        if not await self._ensure_user_access(user):
            await callback.answer("denied", show_alert=True)
            return
        if not self._is_admin(user.id):
            await callback.answer("admin permissions required", show_alert=True)
            return

        self._awaiting_add_identifier.add(user.id)
        self._awaiting_delete_identifier.discard(user.id)
        await callback.answer()
        if callback.message is not None:
            await callback.message.answer("send tg_id or tg_id @username")

    async def _handle_delete_user_button(self, callback: CallbackQuery) -> None:
        user = callback.from_user
        if not await self._ensure_user_access(user):
            await callback.answer("denied", show_alert=True)
            return
        if not self._is_admin(user.id):
            await callback.answer("admin permissions required", show_alert=True)
            return

        self._awaiting_delete_identifier.add(user.id)
        self._awaiting_add_identifier.discard(user.id)
        await callback.answer()
        if callback.message is not None:
            await callback.message.answer("send tg_username or tg_id")

    async def _handle_admin_text_input(self, message: Message) -> None:
        user = self._require_user(message)
        if user.id in self._awaiting_add_identifier:
            self._awaiting_add_identifier.discard(user.id)
            await self._handle_add_user_identifier_input(message, user)
            return
        if user.id in self._awaiting_delete_identifier:
            self._awaiting_delete_identifier.discard(user.id)
            await self._handle_delete_user_identifier_input(message, user)

    async def _handle_add_user_identifier_input(self, message: Message, user: TelegramUser) -> None:
        if not await self._ensure_access(message, user):
            return
        if not self._is_admin(user.id):
            await message.answer("admin permissions required")
            return

        identifier = (message.text or "").strip()
        if not identifier:
            await message.answer("tg_id is required")
            return

        target_user = await self._get_or_create_user_for_admin_add(identifier)
        if target_user is None:
            await message.answer("send tg_id or tg_id @username")
            return

        subscription_url = await self._ensure_subscription_for_user(
            tg_id=target_user.tg_id,
            tg_username=target_user.tg_username,
            tg_name=target_user.tg_name,
        )
        await message.answer("user added")
        await message.answer(
            f"`{self._escape_markdown_code(subscription_url)}`",
            disable_web_page_preview=True,
            parse_mode="MarkdownV2",
        )

    async def _handle_delete_user_identifier_input(self, message: Message, user: TelegramUser) -> None:
        if not await self._ensure_access(message, user):
            return
        if not self._is_admin(user.id):
            await message.answer("admin permissions required")
            return

        identifier = (message.text or "").strip()
        if not identifier:
            await message.answer("tg_username or tg_id is required")
            return

        target_user = await self._find_user_by_telegram_identifier(identifier)
        if target_user is None:
            await message.answer("user not found")
            return
        if target_user.subscription is None:
            await message.answer("subscription not found")
            return

        result = await self._rw_manager.remove_user(target_user.subscription.uuid)
        is_deleted = bool(getattr(result, "is_deleted", False))
        if is_deleted:
            await self._db.delete_subscription_by_tg_id(target_user.tg_id)
        await message.answer("user deleted" if is_deleted else "user was not deleted")

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

    async def _find_user_by_telegram_identifier(self, identifier: str) -> User | None:
        normalized_identifier = identifier.strip()
        if normalized_identifier.startswith("@"):
            normalized_identifier = normalized_identifier[1:]
        if normalized_identifier.isdigit():
            return await self._db.get_user(int(normalized_identifier))
        return await self._db.get_user_by_tg_username(normalized_identifier)

    async def _get_or_create_user_for_admin_add(self, identifier: str) -> User | None:
        tg_id, tg_username = self._parse_admin_add_identifier(identifier)
        if tg_id is None:
            return await self._find_user_by_telegram_identifier(identifier)
        existing = await self._db.get_user(tg_id)
        if existing is not None:
            tg_username = tg_username or existing.tg_username
            tg_name = existing.tg_name
            is_chat_member = existing.is_chat_member
            is_admin = existing.is_admin
        else:
            tg_name = tg_username or "telegram_user"
            is_chat_member = False
            is_admin = self._is_admin(tg_id)
        return await self._db.upsert_user(
            tg_id=tg_id,
            tg_username=tg_username,
            tg_name=tg_name,
            is_chat_member=is_chat_member,
            is_admin=is_admin,
        )

    @staticmethod
    def _parse_admin_add_identifier(identifier: str) -> tuple[int | None, str | None]:
        parts = identifier.strip().split()
        if not parts or not parts[0].isdigit():
            return None, None
        tg_username = None
        if len(parts) > 1:
            tg_username = parts[1].strip().lstrip("@") or None
        return int(parts[0]), tg_username

    async def _is_chat_member(self, tg_user_id: int) -> bool:
        try:
            member = await self._bot.get_chat_member(self._config.chat_id, tg_user_id)
        except Exception:
            return False
        return member.status in _MEMBER_STATUSES

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
