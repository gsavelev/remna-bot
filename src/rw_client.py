from __future__ import annotations

from datetime import datetime

from remnawave import RemnawaveSDK
from remnawave.models import (
    CreateUserRequestDto,
    CreateUserResponseDto,
    DeleteUserResponseDto,
    GetUserByUsernameResponseDto,
    GetUserByUuidResponseDto,
)

from src.config import RemnawaveConfig


class RemnawaveUserManager:
    def __init__(self, config: RemnawaveConfig) -> None:
        self._sdk = RemnawaveSDK(
            base_url=str(config.base_url),
            token=config.token,
        )

    async def add_user(
        self,
        *,
        username: str,
        expire_at: datetime,
        traffic_limit_bytes: int | None = None,
        description: str | None = None,
        tag: str | None = None,
        email: str | None = None,
        telegram_id: int | None = None,
    ) -> CreateUserResponseDto:
        request = CreateUserRequestDto(
            username=username,
            expire_at=expire_at,
            traffic_limit_bytes=traffic_limit_bytes,
            description=description,
            tag=tag,
            email=email,
            telegram_id=telegram_id,
        )
        return await self._sdk.users.create_user(body=request)

    async def get_user(
        self,
        uuid: str,
    ) -> GetUserByUuidResponseDto:
        return await self._sdk.users.get_user_by_uuid(uuid=uuid)

    async def get_user_by_username(
        self,
        username: str,
    ) -> GetUserByUsernameResponseDto:
        return await self._sdk.users.get_user_by_username(username=username)
    
    async def remove_user(self, uuid: str) -> DeleteUserResponseDto:
        return await self._sdk.users.delete_user(uuid=uuid)

    async def remove_user_by_username(
        self,
        username: str,
    ) -> DeleteUserResponseDto:
        user = await self.get_user_by_username(username)
        return await self.remove_user(str(user.uuid))
