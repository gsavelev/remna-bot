from __future__ import annotations

from datetime import datetime
from urllib.parse import quote
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.config import RemnawaveConfig


class RemnawaveUser(BaseModel):
    """Fields used by the bot from the Remnawave 3.4.4 user response."""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(gt=0, strict=True)
    username: str
    subscription_url: str = Field(alias="subscriptionUrl", min_length=1)
    telegram_id: int | None = Field(alias="telegramId")


class UserNotFoundError(Exception):
    pass


class UsernameAlreadyExistsError(Exception):
    pass


class RemnawaveUserManager:
    def __init__(
        self, config: RemnawaveConfig, *, transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=str(config.base_url).rstrip("/") + "/api/",
            headers={"Authorization": f"Bearer {config.token}"},
            timeout=30.0,
            transport=transport,
        )

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = await self._client.request(method, path, **kwargs)
        if response.is_error:
            try:
                error = response.json()
            except ValueError:
                error = {}
            code = error.get("errorCode") if isinstance(error, dict) else None
            # A reverse-proxy 404 is not evidence that a user is missing.
            if response.status_code == 404 and code == "A025":
                raise UserNotFoundError("Remnawave user not found")
            if response.status_code == 400 and code == "A019":
                raise UsernameAlreadyExistsError("Remnawave username already exists")
        response.raise_for_status()
        return response

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
        active_internal_squads: list[UUID] | None = None,
    ) -> RemnawaveUser:
        body = {
            "username": username,
            "expireAt": expire_at.isoformat(),
            "trafficLimitBytes": traffic_limit_bytes,
            "trafficLimitStrategy": self._config.traffic_limit_strategy.value,
            "description": description,
            "tag": tag,
            "email": email,
            "telegramId": telegram_id,
            "activeInternalSquads": (
                [str(uuid) for uuid in active_internal_squads]
                if active_internal_squads is not None else None
            ),
        }
        # Optional fields such as trafficLimitBytes do not accept JSON null in v3.
        response = await self._request(
            "POST", "users", json={key: value for key, value in body.items() if value is not None},
        )
        return RemnawaveUser.model_validate(response.json()["response"])

    def default_internal_squads(self) -> list[UUID] | None:
        if self._config.default_internal_squad_uuid is None:
            return None
        return [self._config.default_internal_squad_uuid]

    async def get_user(self, user_id: int) -> RemnawaveUser:
        response = await self._request("GET", f"users/{user_id}")
        return RemnawaveUser.model_validate(response.json()["response"])

    async def get_user_by_username(self, username: str) -> RemnawaveUser:
        response = await self._request("GET", f"users/by-username/{quote(username, safe='')}")
        return RemnawaveUser.model_validate(response.json()["response"])

    async def remove_user(self, user_id: int) -> None:
        response = await self._request("DELETE", f"users/{user_id}")
        if response.status_code != 204:
            raise ValueError("Expected HTTP 204 from Remnawave user deletion")

    async def close(self) -> None:
        await self._client.aclose()
