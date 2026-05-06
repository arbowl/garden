import asyncio
import logging
import math
import random
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore

from avatars.session import AvatarSession
from config import settings
from curator.curator import curate_all, synthesize_board_post, write_editorial_for_instance
from db.connection import get_db
from db.queries import (
    archive_board_post,
    archive_old_posts,
    get_active_board_post,
    get_active_instances,
    get_curated_posts_for_recalc,
    update_engagement_scores,
    update_hot_scores,
)
from ingest.fetcher import fetch_all
from ingest.sources import load_sources
from llm.client import OllamaClient
from ranking.hot_rank import compute_score
from web.broadcaster import broadcaster

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()


async def fetch_job() -> None:
    logger.info("[scheduler] fetch_job start")
    db = await get_db()
    sources = load_sources(settings.sources_path)
    results = await fetch_all(db, sources)
    new_count = sum(r.new for r in results)
    if new_count:
        logger.info("[scheduler] fetched %d new posts, curating", new_count)
        client = OllamaClient(base_url=settings.ollama_url, model=settings.ollama_disciplined_model)
        await curate_all(db, client, sources, settings.curator_threshold)
    logger.info("[scheduler] fetch_job done")


async def avatar_job() -> None:
    logger.info("[scheduler] avatar_job start")
    db = await get_db()
    instances = await get_active_instances(db)
    if not instances:
        logger.info("[scheduler] no active instances, skipping")
        return
    now = datetime.now(timezone.utc)

    def _staleness_weight(last_session: str | None) -> float:
        if not last_session:
            return 1 + math.sqrt(168)  # treat never-run as ~1 week stale
        try:
            last = datetime.fromisoformat(last_session).replace(tzinfo=timezone.utc)
        except ValueError:
            return 1.0
        hours = max(0.0, (now - last).total_seconds() / 3600)
        return 1 + math.sqrt(hours)

    weights = [_staleness_weight(inst.last_session) for inst in instances]
    instance = random.choices(instances, weights=weights, k=1)[0]
    logger.info("[scheduler] running session for %s (%s)", instance.name, instance.id)
    await broadcaster.publish({"type": "session_start", "instance_name": instance.name})
    disciplined = OllamaClient(
        base_url=settings.ollama_url, model=settings.ollama_disciplined_model
    )
    creative = OllamaClient(base_url=settings.ollama_url, model=settings.ollama_creative_model)
    session = AvatarSession(
        db, disciplined, creative, instance.id, max_seconds=settings.max_session_seconds
    )
    await session.run()
    await broadcaster.publish({"type": "session_end", "instance_name": instance.name})
    logger.info("[scheduler] avatar_job done")


async def recalc_job() -> None:
    db = await get_db()
    await update_engagement_scores(db)
    posts = await get_curated_posts_for_recalc(db)
    if not posts:
        return
    scores = [
        (
            compute_score(
                p["default_score"] + max(0, p["vote_count"]) + p["engagement_score"],
                p["last_activity"],
                settings.hot_rank_gravity,
            ),
            p["id"],
        )
        for p in posts
    ]
    await update_hot_scores(db, scores)


async def expire_job() -> None:
    db = await get_db()
    count = await archive_old_posts(db, days=2)
    if count:
        logger.info("[scheduler] expire_job archived %d old post(s)", count)
    async with db.execute(
        "UPDATE mentions SET resolved = 1 WHERE resolved = 0 AND created_at < "
        "datetime('now', '-1 day')"
    ) as cur:
        expired = cur.rowcount
    await db.commit()
    if expired:
        logger.info("[scheduler] expire_job expired %d stale mention(s)", expired)


async def editorial_job() -> None:
    logger.info("[scheduler] editorial_job start")
    db = await get_db()
    instances = await get_active_instances(db)
    if not instances:
        return
    creative = OllamaClient(base_url=settings.ollama_url, model=settings.ollama_creative_model)
    await asyncio.gather(
        *[write_editorial_for_instance(db, creative, instance.id) for instance in instances]
    )
    logger.info("[scheduler] editorial_job done; %d instance(s) processed", len(instances))


async def board_synthesis_job() -> None:
    logger.info("[scheduler] board_synthesis_job start")
    db = await get_db()
    active = await get_active_board_post(db)
    if active:
        await archive_board_post(db, active.id)
        logger.info("[scheduler] archived board post %d", active.id)
    client = OllamaClient(base_url=settings.ollama_url, model=settings.ollama_disciplined_model)
    post_id = await synthesize_board_post(db, client)
    if post_id:
        logger.info("[scheduler] new board post: %d", post_id)
    else:
        logger.error("[scheduler] board synthesis failed")
    logger.info("[scheduler] board_synthesis_job done")


def start_scheduler() -> None:
    _scheduler.add_job(
        fetch_job,
        "interval",
        minutes=settings.fetch_interval_minutes,
        id="fetch",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        avatar_job,
        "interval",
        minutes=settings.avatar_session_interval_minutes,
        id="avatar",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        recalc_job,
        "interval",
        minutes=5,
        id="recalc",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        expire_job,
        "interval",
        hours=6,
        id="expire",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        board_synthesis_job,
        "cron",
        hour=1,
        minute=0,
        id="board_synthesis",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        editorial_job,
        "cron",
        hour=2,
        minute=0,
        id="editorial",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "[scheduler] started; fetch every %dm, avatar every %dm",
        settings.fetch_interval_minutes,
        settings.avatar_session_interval_minutes,
    )


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
