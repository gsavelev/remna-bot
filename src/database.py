from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(AsyncAttrs, DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    tg_username: Mapped[str | None] = mapped_column(String, nullable=True)
    tg_name: Mapped[str] = mapped_column(String)
    is_chat_member: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )

    subscription: Mapped["Subscription | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_tg_id: Mapped[int] = mapped_column(ForeignKey("users.tg_id", ondelete="CASCADE"), unique=True)
    uuid: Mapped[str] = mapped_column(String, unique=True)
    username: Mapped[str] = mapped_column(String, unique=True)
    path: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )

    user: Mapped[User] = relationship(back_populates="subscription")


class Database:
    def __init__(self, path: str) -> None:
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine: AsyncEngine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            future=True,
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
        )

    async def initialize(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def upsert_user(
        self,
        *,
        tg_id: int,
        tg_username: str | None,
        tg_name: str,
        is_chat_member: bool,
        is_admin: bool,
    ) -> User:
        async with self._session_factory() as session:
            user = await session.scalar(select(User).where(User.tg_id == tg_id))
            if user is None:
                user = User(
                    tg_id=tg_id,
                    tg_username=tg_username,
                    tg_name=tg_name,
                    is_chat_member=is_chat_member,
                    is_admin=is_admin,
                )
                session.add(user)
            else:
                user.tg_username = tg_username
                user.tg_name = tg_name
                user.is_chat_member = is_chat_member
                user.is_admin = is_admin
                user.updated_at = _utcnow()
            await session.commit()
            return user

    async def get_user(self, tg_id: int) -> User | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(User)
                .options(selectinload(User.subscription))
                .where(User.tg_id == tg_id)
            )

    async def get_subscription_by_tg_id(self, tg_id: int) -> Subscription | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(Subscription).where(Subscription.user_tg_id == tg_id)
            )

    async def upsert_subscription(
        self,
        *,
        user_tg_id: int,
        uuid: str,
        username: str,
        path: str,
    ) -> Subscription:
        async with self._session_factory() as session:
            subscription = await session.scalar(
                select(Subscription).where(Subscription.user_tg_id == user_tg_id)
            )
            if subscription is None:
                subscription = Subscription(
                    user_tg_id=user_tg_id,
                    uuid=uuid,
                    username=username,
                    path=path,
                )
                session.add(subscription)
            else:
                subscription.uuid = uuid
                subscription.username = username
                subscription.path = path
                subscription.updated_at = _utcnow()
            await session.commit()
            return subscription

    async def delete_subscription_by_uuid(self, uuid: str) -> bool:
        async with self._session_factory() as session:
            subscription = await session.scalar(
                select(Subscription).where(Subscription.uuid == uuid)
            )
            if subscription is None:
                return False
            await session.delete(subscription)
            await session.commit()
            return True

    async def delete_subscription_by_username(self, username: str) -> bool:
        async with self._session_factory() as session:
            subscription = await session.scalar(
                select(Subscription).where(Subscription.username == username)
            )
            if subscription is None:
                return False
            await session.delete(subscription)
            await session.commit()
            return True

    async def close(self) -> None:
        await self._engine.dispose()
