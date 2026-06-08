from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class MemoryStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (telegram_user_id) REFERENCES users (telegram_user_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    fact TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (telegram_user_id) REFERENCES users (telegram_user_id)
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_user_created
                ON messages (telegram_user_id, created_at)
                """
            )
            await db.commit()

    async def upsert_user(
        self,
        telegram_user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> None:
        now = self._now()
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO users (
                    telegram_user_id,
                    username,
                    first_name,
                    last_name,
                    first_seen_at,
                    last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    last_seen_at = excluded.last_seen_at
                """,
                (telegram_user_id, username, first_name, last_name, now, now),
            )
            await db.commit()

    async def add_message(self, telegram_user_id: int, role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError(f"Unsupported role: {role}")

        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO messages (telegram_user_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (telegram_user_id, role, content, self._now()),
            )
            await db.commit()

    async def get_recent_messages(
        self,
        telegram_user_id: int,
        limit: int,
    ) -> list[ChatMessage]:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT role, content
                FROM messages
                WHERE telegram_user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (telegram_user_id, limit),
            )
            rows = await cursor.fetchall()

        return [
            ChatMessage(role=row["role"], content=row["content"])
            for row in reversed(rows)
        ]

    async def clear_user_history(self, telegram_user_id: int) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                "DELETE FROM messages WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )
            await db.commit()

    async def add_user_fact(self, telegram_user_id: int, fact: str) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO user_facts (telegram_user_id, fact, created_at)
                VALUES (?, ?, ?)
                """,
                (telegram_user_id, fact.strip(), self._now()),
            )
            await db.commit()

    async def get_user_facts(self, telegram_user_id: int) -> list[str]:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT fact FROM user_facts
                WHERE telegram_user_id = ?
                ORDER BY id ASC
                """,
                (telegram_user_id,),
            )
            rows = await cursor.fetchall()
        return [row["fact"] for row in rows]

    async def get_last_user_message_time(self, telegram_user_id: int) -> str | None:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT created_at FROM messages
                WHERE telegram_user_id = ? AND role = 'user'
                ORDER BY id DESC LIMIT 1
                """,
                (telegram_user_id,),
            )
            row = await cursor.fetchone()
        return row["created_at"] if row else None

    async def get_all_user_ids(self) -> list[int]:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT telegram_user_id FROM users")
            rows = await cursor.fetchall()
        return [row["telegram_user_id"] for row in rows]

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
