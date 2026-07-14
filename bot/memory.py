from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


_MAX_FACTS_PER_USER = 50


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class MemoryStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._db: aiosqlite.Connection | None = None

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self.database_path)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL")
        return self._db

    async def initialize(self) -> None:
        db = await self._get_db()
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

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def upsert_user(
        self,
        telegram_user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> None:
        now = self._now()
        db = await self._get_db()
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

        db = await self._get_db()
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
        db = await self._get_db()
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
        db = await self._get_db()
        await db.execute(
            "DELETE FROM messages WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        await db.commit()

    async def add_user_fact(self, telegram_user_id: int, fact: str) -> None:
        db = await self._get_db()
        await db.execute(
            """
            INSERT INTO user_facts (telegram_user_id, fact, created_at)
            VALUES (?, ?, ?)
            """,
            (telegram_user_id, fact.strip(), self._now()),
        )

        # Keep only the most recent facts per user to prevent unbounded growth.
        await db.execute(
            """
            DELETE FROM user_facts
            WHERE telegram_user_id = ?
            AND id NOT IN (
                SELECT id FROM user_facts
                WHERE telegram_user_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
            """,
            (telegram_user_id, telegram_user_id, _MAX_FACTS_PER_USER),
        )
        await db.commit()

    async def get_user_facts(self, telegram_user_id: int) -> list[str]:
        db = await self._get_db()
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
        db = await self._get_db()
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

    async def get_last_message_role(self, telegram_user_id: int) -> str | None:
        """Return the role of the most recent message for this user, or None."""
        db = await self._get_db()
        cursor = await db.execute(
            """
            SELECT role FROM messages
            WHERE telegram_user_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (telegram_user_id,),
        )
        row = await cursor.fetchone()
        return row["role"] if row else None

    async def get_all_user_ids(self) -> list[int]:
        db = await self._get_db()
        cursor = await db.execute("SELECT telegram_user_id FROM users")
        rows = await cursor.fetchall()
        return [row["telegram_user_id"] for row in rows]

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
