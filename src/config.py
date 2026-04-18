from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from pydantic import AnyUrl, BaseModel, Field


class RemnawaveConfig(BaseModel):
    base_url: AnyUrl
    token: str = Field(min_length=1)
    default_internal_squad_uuid: UUID | None = None

    model_config = {
        "extra": "forbid",
        "validate_default": True,
    }

    @classmethod
    def from_env(cls) -> "RemnawaveConfig":
        data: dict[str, Any] = {
            "base_url": os.getenv("REMNAWAVE_URL"),
            "token": os.getenv("REMNAWAVE_TOKEN"),
            "default_internal_squad_uuid": os.getenv("REMNAWAVE_DEFAULT_INTERNAL_SQUAD_UUID") or None,
        }
        return cls.model_validate(data)


class TelegramConfig(BaseModel):
    bot_token: str = Field(min_length=1)
    chat_id: int
    admin_ids: tuple[int, ...] = ()
    db_path: str = "bot.db"
    poll_timeout_seconds: int = Field(default=30, ge=1, le=120)
    subscription_expire_days: int = Field(default=30, ge=1)
    traffic_limit_gb: int | None = Field(default=None, ge=1)

    model_config = {
        "extra": "forbid",
        "validate_default": True,
    }

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        admin_ids_raw = os.getenv("TG_ADMIN_IDS", "")
        data: dict[str, Any] = {
            "bot_token": os.getenv("TG_BOT_TOKEN"),
            "chat_id": os.getenv("TG_CHAT_ID"),
            "admin_ids": tuple(
                int(item.strip())
                for item in admin_ids_raw.split(",")
                if item.strip()
            ),
            "db_path": os.getenv("DB_PATH", "bot.db"),
            "poll_timeout_seconds": os.getenv("TG_POLL_TIMEOUT_SECONDS", 30),
            "subscription_expire_days": os.getenv("SUBSCRIPTION_EXPIRE_DAYS", 30),
            "traffic_limit_gb": os.getenv("TRAFFIC_LIMIT_GB") or None,
        }
        return cls.model_validate(data)
