from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from json import JSONDecodeError
from typing import Any
from urllib.parse import urlparse

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    CopyTextButton,
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
_DELETE_USER_CALLBACK = "delete_user_by_username"
_HAPP_DOWNLOAD_URL = "https://www.happ.su/main/ru"
_HAPP_CRYPTO_API_URL = "https://crypto.happ.su/api-v2.php"


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
        self._awaiting_delete_username: set[int] = set()
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
            self._handle_delete_user_button,
            F.data == _DELETE_USER_CALLBACK,
        )
        self._router.message.register(self._handle_delete_username_input, F.text)

    async def _handle_start(self, message: Message) -> None:
        user = self._require_user(message)
        if not await self._ensure_access(message, user):
            return
        subscription_url = await self._ensure_subscription(user)
        happ_subscription_url = await self._happ_crypto_subscription_url(subscription_url)
        keyboard_rows = [
            [InlineKeyboardButton(text="скачать приложение", url=_HAPP_DOWNLOAD_URL)],
            [InlineKeyboardButton(
                text="скопировать подписку",
                copy_text=CopyTextButton(text=happ_subscription_url),
            )],
        ]
        if self._is_admin(user.id):
            keyboard_rows.append(
                [InlineKeyboardButton(text="удалить пользователя", callback_data=_DELETE_USER_CALLBACK)],
            )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=keyboard_rows,
        )
        await message.answer(
            "1. скачай и установи приложение\n2. скопирую и вставь в него подписку\n3. включи vpn",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )

    async def _handle_delete_user_button(self, callback: CallbackQuery) -> None:
        user = callback.from_user
        if not await self._ensure_user_access(user):
            await callback.answer("service forbidden", show_alert=True)
            return
        if not self._is_admin(user.id):
            await callback.answer("admin permissions required", show_alert=True)
            return

        self._awaiting_delete_username.add(user.id)
        await callback.answer()
        if callback.message is not None:
            await callback.message.answer("send the username")

    async def _handle_delete_username_input(self, message: Message) -> None:
        user = self._require_user(message)
        if user.id not in self._awaiting_delete_username:
            return
        self._awaiting_delete_username.discard(user.id)

        if not await self._ensure_access(message, user):
            return
        if not self._is_admin(user.id):
            await message.answer("admin permissions required")
            return

        username = (message.text or "").strip().lstrip("@")
        if not username:
            await message.answer("username is required")
            return

        result = await self._rw_manager.remove_user_by_username(username)
        await self._db.delete_subscription_by_username(username)
        is_deleted = bool(getattr(result, "is_deleted", False))
        await message.answer("user deleted" if is_deleted else "user was not deleted")

    async def _happ_crypto_subscription_url(self, subscription_url: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                _HAPP_CRYPTO_API_URL,
                json={"url": subscription_url},
            )
        response.raise_for_status()
        crypto_url = self._extract_happ_crypto_url(response)
        if crypto_url is None:
            raise RuntimeError("Happ crypto API returned no encrypted URL")
        return crypto_url

    @staticmethod
    def _extract_happ_crypto_url(response: httpx.Response) -> str | None:
        text = response.text.strip()
        try:
            payload: Any = response.json()
        except JSONDecodeError:
            payload = text
        return RemnaTelegramBot._find_happ_crypto_url(payload)

    @staticmethod
    def _find_happ_crypto_url(value: Any) -> str | None:
        if isinstance(value, str):
            stripped = value.strip().strip('"')
            if stripped.startswith(("happ://crypt", "happ://crypto")):
                return stripped
            return None
        if isinstance(value, dict):
            for key in ("url", "link", "result", "data", "encrypted_url", "encryptedLink"):
                crypto_url = RemnaTelegramBot._find_happ_crypto_url(value.get(key))
                if crypto_url is not None:
                    return crypto_url
            for item in value.values():
                crypto_url = RemnaTelegramBot._find_happ_crypto_url(item)
                if crypto_url is not None:
                    return crypto_url
        if isinstance(value, list):
            for item in value:
                crypto_url = RemnaTelegramBot._find_happ_crypto_url(item)
                if crypto_url is not None:
                    return crypto_url
        return None

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
            await message.answer("service forbidden")
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
        existing = await self._db.get_subscription_by_tg_id(user.id)
        if existing is not None:
            try:
                remote_user = await self._rw_manager.get_user(existing.uuid)
            except Exception:
                remote_user = None
            if remote_user is not None:
                subscription_url = getattr(remote_user, "subscription_url", None)
                if isinstance(subscription_url, str) and subscription_url:
                    await self._db.upsert_subscription(
                        user_tg_id=user.id,
                        uuid=existing.uuid,
                        username=existing.username,
                        path=self._extract_path(subscription_url),
                    )
                    return subscription_url

        username = self._build_subscription_username(user)
        try:
            remna_user = await self._rw_manager.add_user(
                username=username,
                expire_at=datetime.now(UTC) + timedelta(days=self._config.subscription_expire_days),
                traffic_limit_bytes=self._traffic_limit_bytes(),
                telegram_id=user.id,
                description=f"Telegram user {user.full_name}",
                active_internal_squads=self._rw_manager.default_internal_squads(),
            )
        except Exception:
            remna_user = await self._rw_manager.get_user_by_username(username)

        subscription_url = str(getattr(remna_user, "subscription_url", ""))
        await self._db.upsert_subscription(
            user_tg_id=user.id,
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

    def _is_admin(self, tg_user_id: int) -> bool:
        return tg_user_id in self._config.admin_ids

    @staticmethod
    def _build_subscription_username(user: TelegramUser) -> str:
        if user.username:
            base = user.username.lower()
        else:
            normalized = re.sub(r"[^a-z0-9]+", "_", user.full_name.lower())
            base = normalized.strip("_") or "telegram_user"
        base = re.sub(r"[^a-z0-9_]", "_", base)
        username = f"{base}_{user.id}"
        if len(username) > _REMNA_USERNAME_MAX_LENGTH:
            return f"telegram_user_{user.id}"
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
    def _require_user(message: Message) -> TelegramUser:
        if message.from_user is None:
            raise ValueError("Message has no sender")
        return message.from_user
