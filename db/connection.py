"""Database connection management and initialization."""

from pathlib import Path

import aiosqlite

_db: aiosqlite.Connection | None = None


async def init_db(db_path: str) -> aiosqlite.Connection:
    global _db
    _db = await aiosqlite.connect(db_path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA busy_timeout=5000")
    await _db.execute("PRAGMA foreign_keys=ON")
    schema = (Path(__file__).parent / "schema.sql").read_text()
    await _db.executescript(schema)
    async with _db.execute("PRAGMA table_info(comments)") as cur:
        cols = {row["name"] async for row in cur}
    if "edited_at" not in cols:
        await _db.execute("ALTER TABLE comments ADD COLUMN edited_at TEXT")

    async with _db.execute("PRAGMA table_info(instances)") as cur:
        cols = {row["name"] async for row in cur}
    if "new_post_bias" not in cols:
        await _db.execute(
            "ALTER TABLE instances ADD COLUMN new_post_bias REAL NOT NULL DEFAULT 0.0"
        )

    async with _db.execute("PRAGMA table_info(archetypes)") as cur:
        cols = {row["name"] async for row in cur}
    if "new_post_bias" not in cols:
        await _db.execute(
            "ALTER TABLE archetypes ADD COLUMN new_post_bias REAL NOT NULL DEFAULT 0.0"
        )

    async with _db.execute("PRAGMA table_info(posts)") as cur:
        cols = {row["name"] async for row in cur}
    if "engagement_score" not in cols:
        await _db.execute("ALTER TABLE posts ADD COLUMN engagement_score REAL NOT NULL DEFAULT 0.0")
    await _db.commit()
    return _db


async def get_db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Database not initialized; call init_db() first.")
    return _db


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None
