from __future__ import annotations

import asyncio

from src.config import RemnawaveConfig, TelegramConfig
from src.database import Database
from src.handlers import RemnaTelegramBot
from src.rw_client import RemnawaveUserManager


async def main() -> None:
    remnawave_config = RemnawaveConfig.from_env()
    telegram_config = TelegramConfig.from_env()
    database = Database(telegram_config.db_path)
    manager = RemnawaveUserManager(remnawave_config)
    bot = RemnaTelegramBot(
        bot_config=telegram_config,
        database=database,
        rw_manager=manager,
    )
    try:
        await bot.run()
    finally:
        await bot.close()
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
