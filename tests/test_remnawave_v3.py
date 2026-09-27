import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx

from src.config import RemnawaveConfig, TrafficLimitStrategy
from src.database import Database
from src.handlers import RemnaTelegramBot
from src.rw_client import (
    RemnawaveUser, RemnawaveUserManager, UserNotFoundError, UsernameAlreadyExistsError,
)


def remote(**overrides):
    return RemnawaveUser.model_validate({
        "id": 123, "username": "saved_name", "subscriptionUrl": "https://sub.example/abc",
        "telegramId": 42, **overrides,
    })


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def manager(self, handler):
        manager = RemnawaveUserManager(
            RemnawaveConfig(base_url="https://panel.example/", token="test-token"),
            transport=httpx.MockTransport(handler),
        )
        self.addAsyncCleanup(manager.close)
        return manager

    async def test_create_uses_v3_payload_and_response(self):
        def handler(request):
            self.assertEqual(str(request.url), "https://panel.example/api/users")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            body = json.loads(request.content)
            self.assertEqual(body["telegramId"], 42)
            self.assertEqual(body["trafficLimitStrategy"], "NO_RESET")
            self.assertEqual(body["activeInternalSquads"], ["00000000-0000-4000-8000-000000000001"])
            self.assertNotIn("trafficLimitBytes", body)
            self.assertNotIn("description", body)
            self.assertNotIn("uuid", body)
            self.assertEqual(body["expireAt"], "2030-01-01T00:00:00+00:00")
            return httpx.Response(201, json={"response": remote().model_dump(by_alias=True)})
        manager = await self.manager(handler)
        user = await manager.add_user(
            username="saved_name", expire_at=datetime(2030, 1, 1, tzinfo=UTC), telegram_id=42,
            active_internal_squads=[UUID("00000000-0000-4000-8000-000000000001")],
        )
        self.assertEqual(user.id, 123)

    async def test_numeric_lookup_username_lookup_and_empty_delete(self):
        requests = []
        def handler(request):
            requests.append((request.method, request.url.path))
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, json={"response": remote().model_dump(by_alias=True)})
        manager = await self.manager(handler)
        await manager.get_user(123)
        await manager.get_user_by_username("saved_name")
        self.assertIsNone(await manager.remove_user(123))
        self.assertEqual(requests, [
            ("GET", "/api/users/123"), ("GET", "/api/users/by-username/saved_name"),
            ("DELETE", "/api/users/123"),
        ])

    async def test_specific_errors_only(self):
        for status, payload, expected in [
            (404, {"errorCode": "A025"}, UserNotFoundError),
            (400, {"errorCode": "A019"}, UsernameAlreadyExistsError),
            (404, {"message": "Cannot GET /api/users/123"}, httpx.HTTPStatusError),
            (401, {"errorCode": "A025"}, httpx.HTTPStatusError),
            (403, {}, httpx.HTTPStatusError),
            (500, {}, httpx.HTTPStatusError),
        ]:
            with self.subTest(status=status, payload=payload):
                manager = await self.manager(lambda r: httpx.Response(status, json=payload))
                with self.assertRaises(expected):
                    await manager.get_user(123)

    async def test_proxy_html_error_is_preserved(self):
        manager = await self.manager(lambda r: httpx.Response(502, text="Bad Gateway"))
        with self.assertRaises(httpx.HTTPStatusError):
            await manager.get_user(123)

    async def test_delete_does_not_accept_old_response(self):
        manager = await self.manager(lambda r: httpx.Response(200, json={"isDeleted": True}))
        with self.assertRaises(ValueError):
            await manager.remove_user(123)

    async def test_rolling_reset_strategy(self):
        config = RemnawaveConfig(base_url="https://panel.example", token="test", traffic_limit_strategy="MONTH_ROLLING")
        self.assertEqual(config.traffic_limit_strategy, TrafficLimitStrategy.MONTH_ROLLING)


class SubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "bot.db"
        # The actual 2.x schema, including the NOT NULL UUID and unique constraints.
        with sqlite3.connect(self.path) as connection:
            connection.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, tg_id INTEGER UNIQUE, tg_username VARCHAR,
                    tg_name VARCHAR NOT NULL, is_chat_member BOOLEAN, is_admin BOOLEAN,
                    created_at DATETIME, updated_at DATETIME
                );
                CREATE TABLE subscriptions (
                    id INTEGER PRIMARY KEY, user_tg_id INTEGER NOT NULL UNIQUE REFERENCES users(tg_id),
                    uuid VARCHAR NOT NULL UNIQUE, username VARCHAR NOT NULL UNIQUE,
                    path VARCHAR NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
                );
                INSERT INTO users VALUES (1, 42, 'oldname', 'Name', 1, 0, '2026-01-01', '2026-01-02');
                INSERT INTO subscriptions VALUES (
                    9, 42, 'old-user-uuid', 'saved_name', '/abc', '2026-01-01', '2026-01-02'
                );
            ''')
        self.db = Database(str(self.path))
        self.addAsyncCleanup(self.db.close)
        await self.db.initialize()
        self.manager = SimpleNamespace(
            get_user=AsyncMock(return_value=remote()),
            get_user_by_username=AsyncMock(return_value=remote()),
            add_user=AsyncMock(return_value=remote()),
            remove_user=AsyncMock(return_value=None),
            default_internal_squads=lambda: None,
        )
        self.bot = RemnaTelegramBot.__new__(RemnaTelegramBot)
        self.bot._db = self.db
        self.bot._rw_manager = self.manager
        self.bot._config = SimpleNamespace(subscription_expire_days=30, traffic_limit_gb=None)
        self.bot._check_chat_membership_for_revision = AsyncMock(return_value=False)
        self.bot._get_required_chat_name_for_revision = AsyncMock(return_value="chat")
        self.bot._notify_subscription_deleted = AsyncMock()

    async def ensure(self):
        return await self.bot._ensure_subscription_for_user(tg_id=42, tg_username="newname", tg_name="Name")

    async def test_migration_is_idempotent_and_preserves_data(self):
        await self.db.initialize()
        sub = await self.db.get_subscription_by_tg_id(42)
        self.assertEqual((sub.id, sub.legacy_uuid, sub.username, sub.path), (9, "old-user-uuid", "saved_name", "/abc"))
        self.assertIsNone(sub.remnawave_id)
        self.assertEqual(sub.created_at, datetime(2026, 1, 1))
        self.assertEqual(sub.updated_at, datetime(2026, 1, 2))
        self.assertEqual(len(await self.db.list_users_with_subscriptions()), 1)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    async def test_legacy_resolves_saved_username_then_uses_id(self):
        self.assertEqual(await self.ensure(), "https://sub.example/abc")
        self.manager.get_user_by_username.assert_awaited_once_with("saved_name")
        sub = await self.db.get_subscription_by_tg_id(42)
        self.assertEqual(sub.remnawave_id, 123)
        self.assertEqual(sub.legacy_uuid, "old-user-uuid")
        await self.ensure()
        self.manager.get_user.assert_awaited_once_with(123)
        self.manager.add_user.assert_not_awaited()

    async def test_missing_legacy_username_does_not_create_or_delete(self):
        self.manager.get_user_by_username.side_effect = UserNotFoundError()
        with self.assertRaisesRegex(ValueError, "missing or renamed"):
            await self.ensure()
        with self.assertLogs("src.handlers", level="ERROR"):
            await self.bot._revise_subscriptions()
        self.manager.add_user.assert_not_awaited()
        self.manager.remove_user.assert_not_awaited()
        self.assertIsNotNone(await self.db.get_subscription_by_tg_id(42))

    async def test_transport_failure_does_not_create_or_delete(self):
        self.manager.get_user_by_username.side_effect = httpx.ConnectError("offline")
        with self.assertRaises(httpx.ConnectError):
            await self.ensure()
        with self.assertLogs("src.handlers", level="ERROR"):
            await self.bot._revise_subscriptions()
        self.manager.add_user.assert_not_awaited()
        self.manager.remove_user.assert_not_awaited()
        self.assertIsNotNone(await self.db.get_subscription_by_tg_id(42))

    async def test_wrong_owner_is_never_adopted_or_deleted(self):
        self.manager.get_user_by_username.return_value = remote(telegramId=99)
        with self.assertRaisesRegex(ValueError, "ownership mismatch"):
            await self.ensure()
        with self.assertLogs("src.handlers", level="ERROR"):
            await self.bot._revise_subscriptions()
        self.manager.remove_user.assert_not_awaited()
        self.assertIsNone((await self.db.get_subscription_by_tg_id(42)).remnawave_id)

    async def test_legacy_without_telegram_id_requires_matching_path(self):
        self.manager.get_user_by_username.return_value = remote(telegramId=None, subscriptionUrl="https://new.example/abc")
        self.assertEqual(await self.ensure(), "https://new.example/abc")
        self.manager.get_user.return_value = remote(telegramId=None, subscriptionUrl="https://sub.example/different")
        with self.assertRaisesRegex(ValueError, "ownership mismatch"):
            await self.ensure()

    async def test_revision_resolves_legacy_and_accepts_no_content(self):
        await self.bot._revise_subscriptions()
        self.manager.remove_user.assert_awaited_once_with(123)
        self.assertIsNone(await self.db.get_subscription_by_tg_id(42))
        self.bot._notify_subscription_deleted.assert_awaited_once_with(42, "chat")

    async def test_revision_keeps_local_row_on_delete_failure(self):
        self.manager.remove_user.side_effect = httpx.ConnectError("offline")
        with self.assertLogs("src.handlers", level="ERROR"):
            await self.bot._revise_subscriptions()
        self.assertIsNotNone(await self.db.get_subscription_by_tg_id(42))
        self.bot._notify_subscription_deleted.assert_not_awaited()

    async def test_known_missing_id_cleanup(self):
        await self.ensure()
        self.manager.get_user.side_effect = UserNotFoundError()
        await self.bot._revise_subscriptions()
        self.assertIsNone(await self.db.get_subscription_by_tg_id(42))
        self.manager.remove_user.assert_not_awaited()

    async def test_missing_known_id_recreates_using_saved_name(self):
        await self.ensure()
        self.manager.get_user.side_effect = UserNotFoundError()
        self.manager.add_user.return_value = remote(id=456)
        await self.ensure()
        self.assertEqual(self.manager.add_user.call_args.kwargs["username"], "saved_name")
        self.assertEqual((await self.db.get_subscription_by_tg_id(42)).remnawave_id, 456)

    async def test_new_subscription_does_not_require_legacy_uuid(self):
        await self.db.delete_subscription_by_tg_id(42)
        await self.ensure()
        sub = await self.db.get_subscription_by_tg_id(42)
        self.assertEqual(sub.remnawave_id, 123)
        self.assertIsNone(sub.legacy_uuid)

    async def test_duplicate_username_checks_owner(self):
        await self.db.delete_subscription_by_tg_id(42)
        self.manager.add_user.side_effect = UsernameAlreadyExistsError()
        self.manager.get_user_by_username.return_value = remote(telegramId=99)
        with self.assertRaisesRegex(ValueError, "ownership mismatch"):
            await self.ensure()
        self.assertIsNone(await self.db.get_subscription_by_tg_id(42))

    async def test_create_failure_is_not_masked_by_lookup(self):
        await self.db.delete_subscription_by_tg_id(42)
        self.manager.add_user.side_effect = httpx.ConnectError("offline")
        with self.assertRaises(httpx.ConnectError):
            await self.ensure()
        self.manager.get_user_by_username.assert_not_awaited()

    async def test_fresh_database(self):
        fresh = Database(str(Path(self.temp.name) / "fresh.db"))
        self.addAsyncCleanup(fresh.close)
        await fresh.initialize()
        await fresh.initialize()
        await fresh.upsert_user(tg_id=7, tg_username=None, tg_name="Test", is_chat_member=True, is_admin=False)
        await fresh.upsert_subscription(user_tg_id=7, remnawave_id=5, username="test", path="/test")
        self.assertEqual((await fresh.get_subscription_by_tg_id(7)).remnawave_id, 5)


if __name__ == "__main__":
    unittest.main()
