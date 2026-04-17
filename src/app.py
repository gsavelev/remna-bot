from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from src.config import RemnawaveConfig
from src.rw_client import RemnawaveUserManager


async def main() -> None:
    config = RemnawaveConfig.from_env()
    manager = RemnawaveUserManager(config)

    user = await manager.add_user(
        username="test_user",
        expire_at=datetime.now(UTC) + timedelta(days=30),
        traffic_limit_bytes=50 * 1024 * 1024 * 1024,
        description="Created from the Remnawave SDK wrapper",
    )
    print(f"Created user: {user.username}, UUID: {user.uuid}, with sub url: {user.subscription_url} ")

    user = await manager.remove_user_by_username("test_user")
    print(f"Deleted user: {user.is_deleted}")


if __name__ == "__main__":
    asyncio.run(main())
