"""Database query functions for the Garden application, providing an async interface to interact
with archetypes, instances, sessions, posts, comments, votes, and notifications.
"""

import json
import uuid
from datetime import UTC, datetime

import aiosqlite

from .models import (
    Archetype,
    AuthorType,
    Comment,
    ContentType,
    Instance,
    Post,
    PostStatus,
    Richness,
    Session,
    Urgency,
)


def _row_to_archetype(row: aiosqlite.Row) -> Archetype:
    return Archetype(
        id=row["id"],
        name=row["name"],
        version=row["version"],
        bio=row["bio"],
        role=row["role"],
        tone=row["tone"],
        sentence_style=row["sentence_style"],
        vocabulary_level=row["vocabulary_level"],
        quirks=row["quirks"],
        example_comment=row["example_comment"],
        favors=json.loads(row["favors"]),
        dislikes=json.loads(row["dislikes"]),
        indifferent=json.loads(row["indifferent"]),
        vote_probability=row["vote_probability"],
        comment_threshold=row["comment_threshold"],
        reply_probability=row["reply_probability"],
        verbosity=row["verbosity"],
        contrarian_factor=row["contrarian_factor"],
        temperature=row["temperature"],
        max_instances=row["max_instances"],
        is_active=bool(row["is_active"]),
        new_post_bias=row["new_post_bias"] if "new_post_bias" in row.keys() else 0.0,
        created_at=row["created_at"],
    )


def _row_to_instance(row: aiosqlite.Row) -> Instance:
    return Instance(
        id=row["id"],
        archetype_id=row["archetype_id"],
        archetype_version=row["archetype_version"],
        name=row["name"],
        drift_vector=json.loads(row["drift_vector"]),
        memory=json.loads(row["memory"]),
        session_count=row["session_count"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        last_session=row["last_session"],
        mood=row["mood"],
        new_post_bias=row["new_post_bias"] if "new_post_bias" in row.keys() else 0.0,
    )


def _row_to_session(row: aiosqlite.Row) -> Session:
    return Session(
        id=row["id"],
        instance_id=row["instance_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        phase=row["phase"],
        posts_triaged=row["posts_triaged"],
        posts_engaged=row["posts_engaged"],
        comments_made=row["comments_made"],
        votes_cast=row["votes_cast"],
        llm_calls=row["llm_calls"],
        summary=row["summary"],
        error=row["error"],
    )


async def get_all_archetypes(db: aiosqlite.Connection) -> list[Archetype]:
    async with db.execute("SELECT * FROM archetypes ORDER BY created_at DESC") as cur:
        rows = await cur.fetchall()
    return [_row_to_archetype(r) for r in rows]


async def get_archetype(db: aiosqlite.Connection, archetype_id: int) -> Archetype | None:
    async with db.execute("SELECT * FROM archetypes WHERE id = ?", (archetype_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_archetype(row) if row else None


async def create_archetype(
    db: aiosqlite.Connection,
    name: str,
    bio: str,
    role: str,
    tone: str | None,
    sentence_style: str | None,
    vocabulary_level: str | None,
    quirks: str | None,
    example_comment: str | None,
    favors: list[str],
    dislikes: list[str],
    indifferent: list[str],
    vote_probability: float,
    comment_threshold: float,
    reply_probability: float,
    verbosity: str,
    contrarian_factor: float,
    temperature: float,
    max_instances: int,
    new_post_bias: float = 0.0,
) -> int:
    async with db.execute(
        """INSERT INTO archetypes
           (name, bio, role, tone, sentence_style, vocabulary_level, quirks, example_comment,
            favors, dislikes, indifferent, vote_probability, comment_threshold, reply_probability,
            verbosity, contrarian_factor, temperature, max_instances, new_post_bias)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            name,
            bio,
            role,
            tone,
            sentence_style,
            vocabulary_level,
            quirks,
            example_comment,
            json.dumps(favors),
            json.dumps(dislikes),
            json.dumps(indifferent),
            vote_probability,
            comment_threshold,
            reply_probability,
            verbosity,
            contrarian_factor,
            temperature,
            max_instances,
            new_post_bias,
        ),
    ) as cur:
        archetype_id = cur.lastrowid
    await db.commit()
    return archetype_id  # type: ignore[return-value]


async def get_instances_for_archetype(
    db: aiosqlite.Connection, archetype_id: int
) -> list[Instance]:
    async with db.execute(
        "SELECT * FROM instances WHERE archetype_id = ? ORDER BY created_at DESC",
        (archetype_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_instance(r) for r in rows]


async def get_active_instances(db: aiosqlite.Connection) -> list[Instance]:
    async with db.execute("SELECT * FROM instances WHERE is_active = 1") as cur:
        rows = await cur.fetchall()
    return [_row_to_instance(r) for r in rows]


async def get_curated_posts_for_recalc(db: aiosqlite.Connection) -> list[dict]:
    async with db.execute(
        "SELECT id, default_score, vote_count, engagement_score, last_activity FROM posts WHERE "
        "status = 'curated'"
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "default_score": r["default_score"],
            "vote_count": r["vote_count"],
            "engagement_score": r["engagement_score"],
            "last_activity": r["last_activity"],
        }
        for r in rows
    ]


async def update_engagement_scores(db: aiosqlite.Connection) -> None:
    """Recompute engagement_score for all curated posts from their comment depths.

    Formula per comment: ROUND(MIN(depth + 1, 6) * (1 + COALESCE(relevance_score, 0)))
    Depth is capped at 5 (level 6) so deep threads don't generate unbounded boosts.
    Posts with no relevance_score (board, editorial) use a 1× multiplier.
    """
    await db.execute(
        """
        UPDATE posts
        SET engagement_score = (
            SELECT COALESCE(
                SUM(ROUND(MIN(c.depth + 1, 6) * (1.0 + COALESCE(posts.relevance_score, 0.0)))),
                0.0
            )
            FROM comments c
            WHERE c.post_id = posts.id
        )
        WHERE status = 'curated'
        """
    )
    await db.commit()


async def close_stale_sessions(db: aiosqlite.Connection) -> int:
    async with db.execute(
        "UPDATE sessions SET ended_at = datetime('now'), error = 'interrupted' WHERE ended_at IS "
        "NULL"
    ) as cur:
        count = cur.rowcount
    await db.commit()
    return count


async def get_activity_status(db: aiosqlite.Connection) -> dict:
    """Returns current running session or last completed session info."""
    async with db.execute(
        """SELECT s.instance_id, i.name as instance_name, s.started_at, s.ended_at
           FROM sessions s JOIN instances i ON s.instance_id = i.id
           WHERE s.ended_at IS NULL ORDER BY s.started_at DESC LIMIT 1"""
    ) as cur:
        running = await cur.fetchone()
    if running:
        return {"state": "running", "instance_name": running["instance_name"]}
    async with db.execute(
        """SELECT s.instance_id, i.name as instance_name, s.ended_at
           FROM sessions s JOIN instances i ON s.instance_id = i.id
           WHERE s.ended_at IS NOT NULL ORDER BY s.ended_at DESC LIMIT 1"""
    ) as cur:
        last = await cur.fetchone()
    if last:
        return {
            "state": "idle",
            "instance_name": last["instance_name"],
            "ended_at": last["ended_at"],
        }
    return {"state": "idle", "instance_name": None, "ended_at": None}


async def get_all_instances(db: aiosqlite.Connection) -> list[Instance]:
    async with db.execute("SELECT * FROM instances ORDER BY last_session DESC NULLS LAST") as cur:
        rows = await cur.fetchall()
    return [_row_to_instance(r) for r in rows]


async def get_instance(db: aiosqlite.Connection, instance_id: str) -> Instance | None:
    async with db.execute("SELECT * FROM instances WHERE id = ?", (instance_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_instance(row) if row else None


async def create_instance(
    db: aiosqlite.Connection,
    archetype_id: int,
    archetype_version: int,
    name: str,
    drift_vector: dict,
    memory: dict,
    new_post_bias: float = 0.0,
) -> str:
    instance_id = str(uuid.uuid4())[:8]
    await db.execute(
        """INSERT INTO instances
           (id, archetype_id, archetype_version, name, drift_vector, memory, new_post_bias)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            instance_id,
            archetype_id,
            archetype_version,
            name,
            json.dumps(drift_vector),
            json.dumps(memory),
            new_post_bias,
        ),
    )
    await db.commit()
    return instance_id


async def update_instance(
    db: aiosqlite.Connection,
    instance_id: str,
    name: str,
    mood: str | None,
    is_active: bool,
    new_post_bias: float = 0.0,
) -> None:
    await db.execute(
        "UPDATE instances SET name = ?, mood = ?, is_active = ?, new_post_bias = ? WHERE id = ?",
        (name, mood or None, int(is_active), new_post_bias, instance_id),
    )
    await db.commit()


async def delete_instance(db: aiosqlite.Connection, instance_id: str) -> None:
    # Find all comments by this instance and their full descendant trees
    async with db.execute(
        """
        WITH RECURSIVE descendants(id, post_id) AS (
            SELECT id, post_id FROM comments
             WHERE author_id = ? AND author_type = 'avatar'
            UNION ALL
            SELECT c.id, c.post_id FROM comments c
              JOIN descendants d ON c.parent_comment_id = d.id
        )
        SELECT id, post_id FROM descendants
        """,
        (instance_id,),
    ) as cur:
        rows = await cur.fetchall()

    if rows:
        ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(ids))
        await db.execute(f"DELETE FROM votes WHERE comment_id IN ({placeholders})", ids)
        await db.execute(f"DELETE FROM mentions WHERE comment_id IN ({placeholders})", ids)
        await db.execute(f"DELETE FROM notifications WHERE comment_id IN ({placeholders})", ids)
        await db.execute(f"DELETE FROM comments WHERE id IN ({placeholders})", ids)
        # Recount comment_count per affected post from remaining comments
        affected_posts = {r["post_id"] for r in rows}
        for post_id in affected_posts:
            await db.execute(
                "UPDATE posts SET comment_count = (SELECT COUNT(*) FROM comments WHERE post_id = ?)"
                 " WHERE id = ?",
                (post_id, post_id),
            )

    await db.execute(
        "DELETE FROM votes WHERE voter_id = ? AND voter_type = 'avatar'", (instance_id,)
    )
    await db.execute("DELETE FROM mentions WHERE instance_id = ?", (instance_id,))
    await db.execute("DELETE FROM sessions WHERE instance_id = ?", (instance_id,))
    await db.execute("DELETE FROM editorials WHERE instance_id = ?", (instance_id,))
    await db.execute("DELETE FROM instances WHERE id = ?", (instance_id,))
    await db.commit()


async def update_archetype(
    db: aiosqlite.Connection,
    archetype_id: int,
    name: str,
    bio: str,
    role: str,
    tone: str | None,
    sentence_style: str | None,
    vocabulary_level: str | None,
    quirks: str | None,
    example_comment: str | None,
    favors: list[str],
    dislikes: list[str],
    indifferent: list[str],
    vote_probability: float,
    comment_threshold: float,
    reply_probability: float,
    verbosity: str,
    contrarian_factor: float,
    temperature: float,
    max_instances: int,
    is_active: bool,
    new_post_bias: float = 0.0,
) -> None:
    await db.execute(
        """UPDATE archetypes SET
               name = ?, bio = ?, role = ?, tone = ?, sentence_style = ?,
               vocabulary_level = ?, quirks = ?, example_comment = ?,
               favors = ?, dislikes = ?, indifferent = ?,
               vote_probability = ?, comment_threshold = ?, reply_probability = ?,
               verbosity = ?, contrarian_factor = ?, temperature = ?,
               max_instances = ?, is_active = ?, new_post_bias = ?,
               version = version + 1
           WHERE id = ?""",
        (
            name,
            bio,
            role,
            tone,
            sentence_style,
            vocabulary_level,
            quirks,
            example_comment,
            json.dumps(favors),
            json.dumps(dislikes),
            json.dumps(indifferent),
            vote_probability,
            comment_threshold,
            reply_probability,
            verbosity,
            contrarian_factor,
            temperature,
            max_instances,
            int(is_active),
            new_post_bias,
            archetype_id,
        ),
    )
    await db.commit()


async def update_instance_post_session(
    db: aiosqlite.Connection,
    instance_id: str,
    memory: dict,
    drift_vector: dict,
    mood: str | None,
) -> None:
    await db.execute(
        """UPDATE instances SET
               memory = ?,
               drift_vector = ?,
               mood = ?,
               session_count = session_count + 1,
               last_session = datetime('now')
           WHERE id = ?""",
        (json.dumps(memory), json.dumps(drift_vector), mood, instance_id),
    )
    await db.commit()


async def insert_session(db: aiosqlite.Connection, instance_id: str) -> int:
    async with db.execute("INSERT INTO sessions (instance_id) VALUES (?)", (instance_id,)) as cur:
        session_id = cur.lastrowid
    await db.commit()
    return session_id  # type: ignore[return-value]


async def update_session(
    db: aiosqlite.Connection,
    session_id: int,
    phase: str | None = None,
    posts_triaged: int | None = None,
    posts_engaged: int | None = None,
    comments_made: int | None = None,
    votes_cast: int | None = None,
    llm_calls: int | None = None,
    summary: str | None = None,
    error: str | None = None,
    ended: bool = False,
) -> None:
    sets: list[str] = []
    params: list = []
    if phase is not None:
        sets.append("phase = ?")
        params.append(phase)
    if posts_triaged is not None:
        sets.append("posts_triaged = ?")
        params.append(posts_triaged)
    if posts_engaged is not None:
        sets.append("posts_engaged = ?")
        params.append(posts_engaged)
    if comments_made is not None:
        sets.append("comments_made = ?")
        params.append(comments_made)
    if votes_cast is not None:
        sets.append("votes_cast = ?")
        params.append(votes_cast)
    if llm_calls is not None:
        sets.append("llm_calls = ?")
        params.append(llm_calls)
    if summary is not None:
        sets.append("summary = ?")
        params.append(summary)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    if ended:
        sets.append("ended_at = datetime('now')")
    if not sets:
        return
    params.append(session_id)
    await db.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params)
    await db.commit()


async def get_recent_sessions(db: aiosqlite.Connection, limit: int = 20) -> list[Session]:
    async with db.execute(
        "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_session(r) for r in rows]


async def get_last_session_per_instance(db: aiosqlite.Connection) -> dict[str, Session]:
    async with db.execute(
        """
        SELECT s.*
        FROM sessions s
        INNER JOIN (
            SELECT instance_id, MAX(started_at) AS latest
            FROM sessions
            GROUP BY instance_id
        ) t ON s.instance_id = t.instance_id AND s.started_at = t.latest
        """
    ) as cur:
        rows = await cur.fetchall()
    return {r["instance_id"]: _row_to_session(r) for r in rows}


async def get_pending_replies(
    db: aiosqlite.Connection,
    instance_id: str,
    limit: int = 5,
    max_depth: int = 8,
) -> list[Comment]:
    """Comments that reply to this instance's comments, not yet replied to by this instance."""
    async with db.execute(
        """
        SELECT c.*
        FROM comments c
        JOIN comments mine ON c.parent_comment_id = mine.id
        WHERE mine.author_id = ?
          AND mine.author_type = 'avatar'
          AND (c.author_id IS NULL OR c.author_id != ?)
          AND c.depth < ?
          AND NOT EXISTS (
              SELECT 1 FROM comments reply
              WHERE reply.parent_comment_id = c.id
                AND reply.author_id = ?
                AND reply.author_type = 'avatar'
          )
        ORDER BY c.created_at DESC
        LIMIT ?
        """,
        (instance_id, instance_id, max_depth, instance_id, limit),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_comment(r) for r in rows]


async def count_instance_comments_on_post(
    db: aiosqlite.Connection,
    instance_id: str,
    post_id: int,
) -> int:
    async with db.execute(
        "SELECT COUNT(*) FROM comments WHERE author_id = ? AND author_type = 'avatar' "
        "AND post_id = ?",
        (instance_id, post_id),
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


def _row_to_post(row: aiosqlite.Row) -> Post:
    return Post(
        id=row["id"],
        url=row["url"],
        title=row["title"],
        source_name=row["source_name"],
        status=PostStatus(row["status"]),
        hot_score=row["hot_score"],
        vote_count=row["vote_count"],
        comment_count=row["comment_count"],
        content_type=ContentType(row["content_type"]),
        created_at=row["created_at"],
        last_activity=row["last_activity"],
        raw_content=row["raw_content"],
        full_text=row["full_text"],
        summary=row["summary"],
        word_count=row["word_count"],
        extraction_ok=bool(row["extraction_ok"]),
        relevance_score=row["relevance_score"],
        urgency=Urgency(row["urgency"]) if row["urgency"] else None,
        richness=Richness(row["richness"]) if row["richness"] else None,
        tags=json.loads(row["tags"]),
        default_score=row["default_score"],
        engagement_score=row["engagement_score"] if "engagement_score" in row.keys() else 0.0,
    )


def _row_to_comment(row: aiosqlite.Row) -> Comment:
    return Comment(
        id=row["id"],
        post_id=row["post_id"],
        parent_comment_id=row["parent_comment_id"],
        author_type=AuthorType(row["author_type"]),
        author_id=row["author_id"],
        author_name=row["author_name"],
        body=row["body"],
        depth=row["depth"],
        vote_count=row["vote_count"],
        created_at=row["created_at"],
        edited_at=row["edited_at"] if "edited_at" in row.keys() else None,
    )


async def insert_raw_post(
    db: aiosqlite.Connection,
    url: str,
    title: str,
    source_name: str,
    raw_content: str | None = None,
) -> int | None:
    """Insert a raw post. Returns the new row id, or None if the URL already exists."""
    async with db.execute(
        "INSERT OR IGNORE INTO posts (url, title, source_name, raw_content) VALUES (?, ?, ?, ?)",
        (url, title, source_name, raw_content),
    ) as cursor:
        if cursor.rowcount > 0:
            await db.commit()
            return cursor.lastrowid
    return None


async def get_posts_by_status(
    db: aiosqlite.Connection,
    status: PostStatus,
    limit: int = 100,
) -> list[Post]:
    async with db.execute(
        "SELECT * FROM posts WHERE status = ? ORDER BY created_at DESC LIMIT ?",
        (status.value, limit),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_post(row) for row in rows]


async def get_new_posts(db: aiosqlite.Connection, limit: int = 50, offset: int = 0) -> list[Post]:
    async with db.execute(
        "SELECT * FROM posts WHERE status NOT IN ('rejected', 'archived') ORDER BY created_at DESC "
        "LIMIT ? OFFSET ?",
        (limit, offset),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_post(row) for row in rows]


async def archive_old_posts(db: aiosqlite.Connection, days: int = 1) -> int:
    async with db.execute(
        """UPDATE posts SET status = 'archived'
           WHERE status = 'curated'
             AND source_name != 'board'
             AND created_at <= datetime('now', ?)
             AND last_activity <= datetime('now', '-12 hours')""",
        (f"-{days} days",),
    ) as cur:
        count = cur.rowcount
    await db.commit()
    return count


async def get_avatar_commented_post_ids(db: aiosqlite.Connection, avatar_id: int | str) -> set[int]:
    async with db.execute(
        "SELECT DISTINCT post_id FROM comments WHERE author_id = ? AND author_type = ?",
        (avatar_id, AuthorType.AVATAR.value),
    ) as cursor:
        rows = await cursor.fetchall()
    return {row[0] for row in rows}


async def get_avatar_top_level_commented_post_ids(
    db: aiosqlite.Connection, avatar_id: int | str
) -> set[int]:
    async with db.execute(
        "SELECT DISTINCT post_id FROM comments WHERE author_id = ? AND author_type = ? "
        "AND parent_comment_id IS NULL",
        (avatar_id, AuthorType.AVATAR.value),
    ) as cursor:
        rows = await cursor.fetchall()
    return {row[0] for row in rows}


async def get_top_posts(
    db: aiosqlite.Connection,
    limit: int = 25,
    offset: int = 0,
    since_interval: str | None = "-1 day",
) -> list[Post]:
    if since_interval is not None:
        async with db.execute(
            """
            SELECT * FROM posts
            WHERE status NOT IN ('rejected', 'archived')
              AND created_at >= datetime('now', ?)
            ORDER BY engagement_score DESC
            LIMIT ? OFFSET ?
            """,
            (since_interval, limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
    else:
        async with db.execute(
            """
            SELECT * FROM posts
            WHERE status NOT IN ('rejected', 'archived')
            ORDER BY engagement_score DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
    return [_row_to_post(row) for row in rows]


async def get_hot_posts(
    db: aiosqlite.Connection, limit: int = 25, max_per_source: int = 5, offset: int = 0
) -> list[Post]:
    async with db.execute(
        """
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY source_name ORDER BY hot_score DESC) AS rn
            FROM posts WHERE status = 'curated'
        )
        WHERE rn <= ?
        ORDER BY hot_score DESC
        LIMIT ? OFFSET ?
        """,
        (max_per_source, limit, offset),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_post(row) for row in rows]


async def get_comment(db: aiosqlite.Connection, comment_id: int) -> Comment | None:
    async with db.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)) as cursor:
        row = await cursor.fetchone()
    return _row_to_comment(row) if row else None


async def get_post(db: aiosqlite.Connection, post_id: int) -> Post | None:
    async with db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)) as cursor:
        row = await cursor.fetchone()
    return _row_to_post(row) if row else None


async def get_comments_for_post(db: aiosqlite.Connection, post_id: int) -> list[Comment]:
    async with db.execute(
        "SELECT * FROM comments WHERE post_id = ? ORDER BY created_at ASC",
        (post_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_comment(row) for row in rows]


async def count_posts_by_status(db: aiosqlite.Connection) -> dict[str, int]:
    async with db.execute("SELECT status, COUNT(*) as n FROM posts GROUP BY status") as cursor:
        rows = await cursor.fetchall()
    return {row["status"]: row["n"] for row in rows}


async def update_post_curated(
    db: aiosqlite.Connection,
    post_id: int,
    relevance_score: float,
    urgency: str,
    richness: str,
    tags: list[str],
    default_score: float,
    full_text: str | None,
    summary: str | None,
    word_count: int,
    extraction_ok: bool,
) -> None:
    await db.execute(
        """
        UPDATE posts SET
            status = 'curated',
            relevance_score = ?,
            urgency = ?,
            richness = ?,
            tags = ?,
            default_score = ?,
            hot_score = ?,
            full_text = ?,
            summary = ?,
            word_count = ?,
            extraction_ok = ?
        WHERE id = ?
        """,
        (
            relevance_score,
            urgency,
            richness,
            json.dumps(tags),
            default_score,
            default_score,
            full_text,
            summary,
            word_count,
            int(extraction_ok),
            post_id,
        ),
    )
    await db.commit()


async def update_post_rejected(db: aiosqlite.Connection, post_id: int) -> None:
    await db.execute("UPDATE posts SET status = 'rejected' WHERE id = ?", (post_id,))
    await db.commit()


async def update_hot_scores(db: aiosqlite.Connection, scores: list[tuple[float, int]]) -> None:
    await db.executemany("UPDATE posts SET hot_score = ? WHERE id = ?", scores)
    await db.commit()


async def insert_comment(
    db: aiosqlite.Connection,
    post_id: int,
    author_type: AuthorType,
    author_name: str,
    body: str,
    parent_comment_id: int | None = None,
    author_id: str | None = None,
) -> int | None:
    async with db.execute("SELECT status FROM posts WHERE id = ?", (post_id,)) as cur:
        row = await cur.fetchone()
    if row and row["status"] == "archived":
        return None

    depth = 0
    if parent_comment_id is not None:
        async with db.execute(
            "SELECT depth FROM comments WHERE id = ?", (parent_comment_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                depth = row["depth"] + 1

    async with db.execute(
        """
        INSERT INTO comments (post_id, parent_comment_id, author_type, author_id, author_name, body, depth)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (post_id, parent_comment_id, author_type.value, author_id, author_name, body, depth),
    ) as cursor:
        comment_id = cursor.lastrowid

    await db.execute(
        "UPDATE posts SET comment_count = comment_count + 1, last_activity = datetime('now') WHERE "
        "id = ?",
        (post_id,),
    )
    await db.commit()
    return comment_id


_VOTE_WEIGHT = 0.3
_REL_MAX = 5.0


async def upsert_relationship(
    db: aiosqlite.Connection,
    subject_id: str,
    object_id: str,
    vote_delta: int,
) -> None:
    if subject_id == object_id:
        return
    async with db.execute(
        "SELECT score FROM relationships WHERE subject_id = ? AND object_id = ?",
        (subject_id, object_id),
    ) as cur:
        row = await cur.fetchone()
    current = float(row["score"]) if row else 0.0
    effective_delta = vote_delta * _VOTE_WEIGHT * (1.0 - abs(current) / _REL_MAX)
    new_score = max(-_REL_MAX, min(_REL_MAX, current + effective_delta))
    await db.execute(
        """
        INSERT INTO relationships (subject_id, object_id, score, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(subject_id, object_id) DO UPDATE SET
            score = excluded.score,
            updated_at = datetime('now')
        """,
        (subject_id, object_id, new_score),
    )


async def _apply_comment_vote_relationship(
    db: aiosqlite.Connection,
    voter_id: str,
    comment_id: int,
    delta: int,
) -> None:
    async with db.execute(
        "SELECT author_type, author_id FROM comments WHERE id = ?",
        (comment_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return
    author_type = row["author_type"]
    author_id = row["author_id"]
    if author_type == "avatar":
        if author_id is None or author_id == voter_id:
            return
        object_id = author_id
    else:
        object_id = "you"
    await upsert_relationship(db, voter_id, object_id, delta)


async def insert_vote(
    db: aiosqlite.Connection,
    voter_type: AuthorType,
    direction: int,
    post_id: int | None = None,
    comment_id: int | None = None,
    voter_id: str | None = None,
    reason: str | None = None,
) -> bool:
    """Insert or update a vote. Returns False if the vote was a no-op (same direction already
    cast).
    """
    if voter_id is not None:
        if post_id is not None:
            async with db.execute(
                "SELECT id, direction FROM votes WHERE post_id = ? AND voter_type = ? AND "
                "voter_id = ?",
                (post_id, voter_type.value, voter_id),
            ) as cur:
                existing = await cur.fetchone()
        else:
            async with db.execute(
                "SELECT id, direction FROM votes WHERE comment_id = ? AND voter_type = ? AND "
                "voter_id = ?",
                (comment_id, voter_type.value, voter_id),
            ) as cur:
                existing = await cur.fetchone()

        if existing is not None:
            if existing["direction"] == direction:
                return False
            delta = direction - existing["direction"]
            await db.execute(
                "UPDATE votes SET direction = ?, reason = ? WHERE id = ?",
                (direction, reason, existing["id"]),
            )
            if post_id is not None:
                await db.execute(
                    "UPDATE posts SET vote_count = vote_count + ?, last_activity = datetime('now') "
                    "WHERE id = ?",
                    (delta, post_id),
                )
            if comment_id is not None:
                await db.execute(
                    "UPDATE comments SET vote_count = vote_count + ? WHERE id = ?",
                    (delta, comment_id),
                )
                await _apply_comment_vote_relationship(db, voter_id, comment_id, delta)
            await db.commit()
            return True

    await db.execute(
        """
        INSERT INTO votes (post_id, comment_id, voter_type, voter_id, direction, reason)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (post_id, comment_id, voter_type.value, voter_id, direction, reason),
    )
    if post_id is not None:
        await db.execute(
            "UPDATE posts SET vote_count = vote_count + ?, last_activity = datetime('now') "
            "WHERE id = ?",
            (direction, post_id),
        )
    if comment_id is not None:
        await db.execute(
            "UPDATE comments SET vote_count = vote_count + ? WHERE id = ?",
            (direction, comment_id),
        )
        if voter_id is not None:
            await _apply_comment_vote_relationship(db, voter_id, comment_id, direction)
    await db.commit()
    return True


async def insert_notification(
    db: aiosqlite.Connection,
    avatar_name: str,
    post_id: int,
    post_title: str,
    comment_id: int,
    body: str,
) -> int:
    async with db.execute(
        """
        INSERT INTO notifications (avatar_name, post_id, post_title, comment_id, body)
        VALUES (?, ?, ?, ?, ?)
        """,
        (avatar_name, post_id, post_title, comment_id, body),
    ) as cur:
        row_id = cur.lastrowid
    await db.commit()
    return row_id  # type: ignore[return-value]


async def get_notifications(db: aiosqlite.Connection, limit: int = 100) -> list[dict]:
    async with db.execute(
        """
        SELECT n.*, c.author_id AS avatar_id
        FROM notifications n
        LEFT JOIN comments c ON c.id = n.comment_id
        ORDER BY n.created_at DESC LIMIT ?
        """,
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def count_unread_notifications(db: aiosqlite.Connection) -> int:
    async with db.execute("SELECT COUNT(*) FROM notifications WHERE read = 0") as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def mark_all_notifications_read(db: aiosqlite.Connection) -> None:
    await db.execute("UPDATE notifications SET read = 1 WHERE read = 0")
    await db.commit()


async def mark_notification_read(db: aiosqlite.Connection, notification_id: int) -> None:
    await db.execute("UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,))
    await db.commit()


async def get_instance_sessions(
    db: aiosqlite.Connection, instance_id: str, limit: int = 20
) -> list[Session]:
    async with db.execute(
        "SELECT * FROM sessions WHERE instance_id = ? ORDER BY started_at DESC LIMIT ?",
        (instance_id, limit),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_session(r) for r in rows]


async def get_instance_comments(
    db: aiosqlite.Connection, instance_id: str, limit: int = 25, offset: int = 0
) -> list[dict]:
    async with db.execute(
        """
        SELECT c.id, c.post_id, c.body, c.vote_count, c.created_at, c.depth,
               p.title as post_title
        FROM comments c
        JOIN posts p ON p.id = c.post_id
        WHERE c.author_id = ? AND c.author_type = 'avatar'
        ORDER BY c.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (instance_id, limit, offset),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def count_instance_comments(db: aiosqlite.Connection, instance_id: str) -> int:
    async with db.execute(
        "SELECT COUNT(*) FROM comments WHERE author_id = ? AND author_type = 'avatar'",
        (instance_id,),
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def sum_instance_comment_votes(db: aiosqlite.Connection, instance_id: str) -> int:
    async with db.execute(
        "SELECT COALESCE(SUM(vote_count), 0) FROM comments WHERE author_id = ? AND "
        "author_type = 'avatar'",
        (instance_id,),
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def get_instance_votes(
    db: aiosqlite.Connection, instance_id: str, limit: int = 25, offset: int = 0
) -> list[dict]:
    async with db.execute(
        """
        SELECT v.id, v.direction, v.reason, v.created_at,
               v.post_id, v.comment_id,
               p.title as post_title,
               c.body as comment_body,
               c.author_name as comment_author,
               cp.id as comment_post_id,
               cp.title as comment_post_title
        FROM votes v
        LEFT JOIN posts p ON p.id = v.post_id
        LEFT JOIN comments c ON c.id = v.comment_id
        LEFT JOIN posts cp ON cp.id = c.post_id
        WHERE v.voter_id = ? AND v.voter_type = 'avatar'
        ORDER BY v.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (instance_id, limit, offset),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def count_instance_votes(db: aiosqlite.Connection, instance_id: str) -> int:
    async with db.execute(
        "SELECT COUNT(*) FROM votes WHERE voter_id = ? AND voter_type = 'avatar'",
        (instance_id,),
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def get_human_comments(
    db: aiosqlite.Connection, limit: int = 25, offset: int = 0
) -> list[dict]:
    async with db.execute(
        """
        SELECT c.id, c.post_id, c.body, c.vote_count, c.created_at,
               p.title as post_title,
               EXISTS (
                   SELECT 1 FROM comments r
                   WHERE r.parent_comment_id = c.id AND r.author_type = 'avatar'
               ) as has_reply
        FROM comments c
        JOIN posts p ON p.id = c.post_id
        WHERE c.author_type = 'human'
        ORDER BY c.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def count_human_comments(db: aiosqlite.Connection) -> int:
    async with db.execute(
        "SELECT COUNT(*) FROM comments WHERE author_type = 'human'",
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def sum_human_comment_votes(db: aiosqlite.Connection) -> int:
    async with db.execute(
        "SELECT COALESCE(SUM(vote_count), 0) FROM comments WHERE author_type = 'human'",
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def get_new_comments(db: aiosqlite.Connection, limit: int = 10) -> list[dict]:
    async with db.execute(
        """
        SELECT c.id, c.post_id, c.author_name, c.author_type, c.author_id, c.body, c.vote_count, 
        c.created_at,
               p.title as post_title
        FROM comments c
        JOIN posts p ON p.id = c.post_id
        WHERE p.status NOT IN ('rejected')
        ORDER BY c.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_hot_comments(db: aiosqlite.Connection, pool_size: int = 100) -> list[dict]:
    async with db.execute(
        """
        SELECT c.id, c.post_id, c.author_name, c.author_type, c.author_id, c.body, c.vote_count,
        c.created_at,
               p.title as post_title
        FROM comments c
        JOIN posts p ON p.id = c.post_id
        WHERE p.status NOT IN ('rejected')
          AND c.created_at >= datetime('now', '-30 days')
        ORDER BY c.vote_count DESC, c.created_at DESC
        LIMIT ?
        """,
        (pool_size,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_human_votes(db: aiosqlite.Connection, limit: int = 25, offset: int = 0) -> list[dict]:
    async with db.execute(
        """
        SELECT v.id, v.direction, v.created_at,
               v.post_id, v.comment_id,
               p.title as post_title,
               c.body as comment_body,
               c.author_name as comment_author
        FROM votes v
        LEFT JOIN posts p ON p.id = v.post_id
        LEFT JOIN comments c ON c.id = v.comment_id
        WHERE v.voter_type = 'human'
        ORDER BY v.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def count_human_votes(db: aiosqlite.Connection) -> int:
    async with db.execute(
        "SELECT COUNT(*) FROM votes WHERE voter_type = 'human'",
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def insert_mention(
    db: aiosqlite.Connection,
    comment_id: int,
    instance_id: str,
    post_id: int,
) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO mentions (comment_id, instance_id, post_id) VALUES (?, ?, ?)",
        (comment_id, instance_id, post_id),
    )
    await db.commit()


async def get_mentions_for_post(
    db: aiosqlite.Connection,
    post_id: int,
) -> dict[int, dict[str, str]]:
    """Returns {comment_id: {lower_name: instance_id}} for all mentions on a post."""
    async with db.execute(
        """
        SELECT m.comment_id, lower(i.name) as name, i.id as instance_id
        FROM mentions m JOIN instances i ON m.instance_id = i.id
        WHERE m.post_id = ?
        """,
        (post_id,),
    ) as cur:
        rows = await cur.fetchall()
    result: dict[int, dict[str, str]] = {}
    for r in rows:
        result.setdefault(r["comment_id"], {})[r["name"]] = r["instance_id"]
    return result


async def get_unresolved_mention_posts_for_instance(
    db: aiosqlite.Connection,
    instance_id: str,
) -> list["Post"]:
    async with db.execute(
        """
        SELECT DISTINCT p.*
        FROM mentions m JOIN posts p ON m.post_id = p.id
        WHERE m.instance_id = ? AND m.resolved = 0
          AND p.status = 'curated'
        ORDER BY m.created_at DESC
        """,
        (instance_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_post(r) for r in rows]


async def resolve_mentions_for_instance(
    db: aiosqlite.Connection,
    instance_id: str,
    post_ids: list[int],
) -> None:
    if not post_ids:
        return
    placeholders = ",".join("?" * len(post_ids))
    await db.execute(
        f"UPDATE mentions SET resolved = 1 WHERE instance_id = ? AND post_id IN ({placeholders})",
        (instance_id, *post_ids),
    )
    await db.commit()


async def insert_editorial(
    db: aiosqlite.Connection,
    instance_id: str,
    body: str,
    mood: str | None,
    date: str,
) -> int:
    async with db.execute(
        "INSERT INTO editorials (instance_id, body, mood, date) VALUES (?, ?, ?, ?)",
        (instance_id, body, mood, date),
    ) as cur:
        row_id = cur.lastrowid
    await db.commit()
    return row_id  # type: ignore[return-value]


async def get_editorials_for_instance(
    db: aiosqlite.Connection,
    instance_id: str,
    limit: int = 5,
    offset: int = 0,
) -> list[dict]:
    async with db.execute(
        "SELECT * FROM editorials WHERE instance_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (instance_id, limit, offset),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def count_editorials_for_instance(db: aiosqlite.Connection, instance_id: str) -> int:
    async with db.execute(
        "SELECT COUNT(*) FROM editorials WHERE instance_id = ?", (instance_id,)
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def has_editorial_for_date(db: aiosqlite.Connection, instance_id: str, date: str) -> bool:
    async with db.execute(
        "SELECT 1 FROM editorials WHERE instance_id = ? AND date = ? LIMIT 1",
        (instance_id, date),
    ) as cur:
        return await cur.fetchone() is not None


async def get_instances_without_any_editorial(db: aiosqlite.Connection) -> list[str]:
    async with db.execute(
        """
        SELECT i.id FROM instances i
        WHERE i.is_active = 1
          AND NOT EXISTS (SELECT 1 FROM editorials e WHERE e.instance_id = i.id)
        """
    ) as cur:
        rows = await cur.fetchall()
    return [r[0] for r in rows]


async def get_sessions_for_editorial(
    db: aiosqlite.Connection,
    instance_id: str,
    hours: int = 24,
) -> list[dict]:
    async with db.execute(
        """
        SELECT summary, posts_engaged, comments_made, votes_cast
        FROM sessions
        WHERE instance_id = ?
          AND ended_at IS NOT NULL
          AND ended_at >= datetime('now', ?)
          AND summary IS NOT NULL
        ORDER BY ended_at DESC LIMIT 10
        """,
        (instance_id, f"-{hours} hours"),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_active_board_post(db: aiosqlite.Connection) -> "Post | None":
    async with db.execute(
        "SELECT * FROM posts WHERE source_name = 'board' AND status = 'curated' ORDER BY "
        "created_at DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    return _row_to_post(row) if row else None


async def get_board_posts(db: aiosqlite.Connection, limit: int = 30) -> list["Post"]:
    async with db.execute(
        "SELECT * FROM posts WHERE source_name = 'board' ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_post(r) for r in rows]


async def archive_board_post(db: aiosqlite.Connection, post_id: int) -> None:
    await db.execute(
        "UPDATE posts SET status = 'archived' WHERE id = ? AND source_name = 'board'",
        (post_id,),
    )
    await db.commit()


async def insert_board_post(db: aiosqlite.Connection, title: str, body: str) -> int:
    url = f"garden://board/{datetime.now(UTC).isoformat()}"
    async with db.execute(
        """
        INSERT INTO posts
            (url, title, source_name, status, content_type,
             full_text, summary, word_count, extraction_ok,
             relevance_score, urgency, richness, tags, default_score, hot_score)
        VALUES (?, ?, 'board', 'curated', 'board',
                ?, ?, ?, 1,
                1.0, 'high', 'full_text', '[]', 10.0, 10.0)
        """,
        (url, title, body, body[:300], len(body.split())),
    ) as cur:
        post_id = cur.lastrowid
    await db.commit()
    return post_id  # type: ignore[return-value]


async def get_synthesis_context(db: aiosqlite.Connection, hours: int = 24) -> dict:
    interval = f"-{hours} hours"
    async with db.execute(
        """
        SELECT s.summary, s.posts_engaged, s.comments_made, i.name as instance_name
        FROM sessions s JOIN instances i ON s.instance_id = i.id
        WHERE s.ended_at >= datetime('now', ?) AND s.summary IS NOT NULL
        ORDER BY s.ended_at DESC LIMIT 20
        """,
        (interval,),
    ) as cur:
        sessions = [dict(r) for r in await cur.fetchall()]

    async with db.execute(
        """
        SELECT id, title, vote_count, comment_count, tags
        FROM posts
        WHERE status = 'curated' AND source_name != 'board'
          AND created_at >= datetime('now', ?)
        ORDER BY comment_count DESC, vote_count DESC LIMIT 10
        """,
        (interval,),
    ) as cur:
        posts = [dict(r) for r in await cur.fetchall()]

    async with db.execute(
        """
        SELECT c.body, c.author_name, c.vote_count, p.title as post_title
        FROM comments c JOIN posts p ON c.post_id = p.id
        WHERE c.created_at >= datetime('now', ?) AND c.vote_count > 0
          AND p.source_name != 'board'
        ORDER BY c.vote_count DESC LIMIT 15
        """,
        (interval,),
    ) as cur:
        hot_comments = [dict(r) for r in await cur.fetchall()]

    return {"sessions": sessions, "posts": posts, "hot_comments": hot_comments}


async def update_comment_body(
    db: aiosqlite.Connection,
    comment_id: int,
    body: str,
) -> bool:
    async with db.execute(
        "UPDATE comments SET body = ?, edited_at = datetime('now') WHERE id = ? AND author_type "
        "= 'human'",
        (body, comment_id),
    ) as cur:
        updated = cur.rowcount > 0
    if updated:
        await db.commit()
    return updated


async def delete_comment_cascade(
    db: aiosqlite.Connection,
    comment_id: int,
) -> bool:
    """Delete a comment and all its descendants, decrement post comment_count."""
    async with db.execute(
        """
        WITH RECURSIVE descendants(id) AS (
            SELECT id FROM comments WHERE id = ?
            UNION ALL
            SELECT c.id FROM comments c JOIN descendants d ON c.parent_comment_id = d.id
        )
        SELECT id FROM descendants
        """,
        (comment_id,),
    ) as cur:
        ids = [r["id"] for r in await cur.fetchall()]
    if not ids:
        return False

    async with db.execute("SELECT post_id FROM comments WHERE id = ?", (comment_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        return False
    post_id = row["post_id"]

    placeholders = ",".join("?" * len(ids))
    await db.execute(f"DELETE FROM votes WHERE comment_id IN ({placeholders})", ids)
    await db.execute(f"DELETE FROM mentions WHERE comment_id IN ({placeholders})", ids)
    await db.execute(f"DELETE FROM notifications WHERE comment_id IN ({placeholders})", ids)
    await db.execute(f"DELETE FROM comments WHERE id IN ({placeholders})", ids)
    await db.execute(
        "UPDATE posts SET comment_count = MAX(0, comment_count - ?) WHERE id = ?",
        (len(ids), post_id),
    )
    await db.commit()
    return True


# ── Relationship queries ──────────────────────────────────────────────────────

_REL_LABELS = [
    (4.0, "a close friend"),
    (2.0, "someone they like"),
    (1.0, "someone they're fond of"),
    (-4.0, "their nemesis"),
    (-2.0, "someone they dislike"),
    (-1.0, "someone they're wary of"),
]


def _rel_label(score: float) -> str:
    for threshold, label in _REL_LABELS:
        if threshold > 0 and score >= threshold:
            return label
        if threshold < 0 and score <= threshold:
            return label
    return "someone they're wary of"


async def get_relationships_for_prompt(
    db: aiosqlite.Connection,
    instance_id: str,
) -> list[dict]:
    """Relationships used for system prompt injection (abs >= 1.0), sorted by magnitude."""
    async with db.execute(
        """
        SELECT r.object_id, r.score,
               COALESCE(i.name, 'the human user') as object_name
        FROM relationships r
        LEFT JOIN instances i ON i.id = r.object_id
        WHERE r.subject_id = ? AND ABS(r.score) >= 1.0
        ORDER BY ABS(r.score) DESC
        """,
        (instance_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_all_relationship_scores(
    db: aiosqlite.Connection,
    instance_id: str,
) -> dict[str, float]:
    """All outgoing relationship scores, keyed by object_id. No threshold filter."""
    async with db.execute(
        "SELECT object_id, score FROM relationships WHERE subject_id = ?",
        (instance_id,),
    ) as cur:
        rows = await cur.fetchall()
    return {row["object_id"]: float(row["score"]) for row in rows}


async def get_profile_relationships(
    db: aiosqlite.Connection,
    instance_id: str,
) -> dict:
    """Best/worst outgoing and incoming relationships for profile display (abs >= 1.0)."""
    async with db.execute(
        """
        SELECT r.object_id, r.score,
               COALESCE(i.name, 'you') as object_name,
               i.id as instance_id
        FROM relationships r
        LEFT JOIN instances i ON i.id = r.object_id
        WHERE r.subject_id = ? AND ABS(r.score) >= 1.0
        ORDER BY ABS(r.score) DESC
        """,
        (instance_id,),
    ) as cur:
        out_rows = [dict(r) for r in await cur.fetchall()]
    async with db.execute(
        """
        SELECT r.subject_id, r.score,
               COALESCE(i.name, 'you') as subject_name,
               i.id as instance_id
        FROM relationships r
        LEFT JOIN instances i ON i.id = r.subject_id
        WHERE r.object_id = ? AND ABS(r.score) >= 1.0
        ORDER BY ABS(r.score) DESC
        """,
        (instance_id,),
    ) as cur:
        in_rows = [dict(r) for r in await cur.fetchall()]
    return {
        "best": next((r for r in out_rows if r["score"] > 0), None),
        "worst": next((r for r in out_rows if r["score"] < 0), None),
        "friend_of": [r for r in in_rows if r["score"] > 0],
        "rival_to": [r for r in in_rows if r["score"] < 0],
    }
