import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import asyncpg
from fastapi import HTTPException

from hackathon.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS challenge_attempts (
    thread_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    data JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS challenge_attempts_user ON challenge_attempts(user_id, created_at DESC);
"""


@asynccontextmanager
async def connection():
    settings = get_settings()
    conn = await asyncpg.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,
    )
    try:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(79202603)")
            await conn.execute(SCHEMA)
        yield conn
    finally:
        await conn.close()


def decode(row) -> dict:
    return {
        **json.loads(row["data"]),
        "threadId": row["thread_id"],
        "userId": row["user_id"],
        "mode": row["mode"],
        "difficulty": row["difficulty"],
        "createdAt": row["created_at"].isoformat(),
        "updatedAt": row["updated_at"].isoformat(),
    }


async def list_attempts(user_id: str) -> list[dict]:
    async with connection() as conn:
        rows = await conn.fetch(
            "SELECT * FROM challenge_attempts WHERE user_id=$1 ORDER BY created_at DESC",
            user_id,
        )
    return [decode(row) for row in rows]


async def get_attempt(user_id: str, thread_id: str) -> dict:
    async with connection() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM challenge_attempts WHERE user_id=$1 AND thread_id=$2",
            user_id,
            thread_id,
        )
    if row is None:
        raise HTTPException(404, "Attempt not found")
    return decode(row)


async def claim_attempt(
    user_id: str, thread_id: str, mode: str, difficulty: str, initial: dict
) -> dict:
    async with connection() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO challenge_attempts(thread_id,user_id,mode,difficulty,data) VALUES($1,$2,$3,$4,$5::jsonb) ON CONFLICT DO NOTHING",
            thread_id,
            user_id,
            mode,
            difficulty,
            json.dumps(initial),
        )
        row = await conn.fetchrow(
            "SELECT * FROM challenge_attempts WHERE thread_id=$1 FOR UPDATE", thread_id
        )
        if row["user_id"] != user_id:
            raise HTTPException(404, "Attempt not found")
        attempt = decode(row)
        if attempt["mode"] != mode or attempt["difficulty"] != difficulty:
            raise HTTPException(
                409, "Start a new attempt when changing challenge or difficulty."
            )
        if mode != "CHALLENGE_1" and attempt.get("status") != "ready":
            raise HTTPException(
                409, "This submission has already run. Start a new attempt."
            )
        if (
            attempt.get("status") == "running"
            and (datetime.now(timezone.utc) - row["updated_at"]).total_seconds() < 900
        ):
            raise HTTPException(409, "This attempt is already running.")
        attempt["status"] = "running"
        await conn.execute(
            "UPDATE challenge_attempts SET data=$2::jsonb,updated_at=now() WHERE thread_id=$1",
            thread_id,
            json.dumps(attempt),
        )
    return attempt


async def save_attempt(attempt: dict) -> None:
    async with connection() as conn:
        await conn.execute(
            "UPDATE challenge_attempts SET data=$3::jsonb,updated_at=now() WHERE user_id=$1 AND thread_id=$2",
            attempt["userId"],
            attempt["threadId"],
            json.dumps(attempt),
        )
