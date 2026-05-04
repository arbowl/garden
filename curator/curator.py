"""Main curation logic: how we use the LLM to curate posts, write editorials, and synthesize board
posts.
"""

import logging
from datetime import UTC, datetime

import aiosqlite

from avatars.schema import DriftVector, Memory, build_system_prompt
from db.models import Post, PostStatus
from db.queries import (
    get_archetype,
    get_instance,
    get_posts_by_status,
    get_sessions_for_editorial,
    get_synthesis_context,
    has_editorial_for_date,
    insert_board_post,
    insert_editorial,
    update_post_curated,
    update_post_rejected,
)
from ingest.extractor import extract_content
from ingest.sources import Source
from llm.client import OllamaClient
from llm.parsing import parse_curate_response, parse_editorial_response, parse_synthesis_response
from llm.prompts import (
    build_curator_prompt,
    build_curator_retry_prompt,
    build_editorial_prompt,
    build_synthesis_prompt,
)

logger = logging.getLogger(__name__)


async def _curate_post(
    db: aiosqlite.Connection,
    client: OllamaClient,
    post: Post,
    source: Source | None,
    threshold: float,
) -> None:
    source_name = source.name if source else post.source_name
    source_tags = source.tags if source else []
    trust_level = source.trust_level if source else 0.5

    extracted = await extract_content(post.url, fallback_summary=post.raw_content)
    content_snippet = extracted.full_text or extracted.summary or post.title

    system, user = build_curator_prompt(post.title, source_name, source_tags, content_snippet)
    try:
        raw = await client.chat(system, user)
    except Exception as e:
        logger.error("LLM call failed for post %d (%s): %s", post.id, post.title[:60], e)
        return

    parsed = parse_curate_response(raw)

    if parsed is None:
        logger.warning("Retrying curation for post %d", post.id)
        system, user = build_curator_retry_prompt(
            post.title, source_name, source_tags, content_snippet
        )
        try:
            raw = await client.chat(system, user)
        except Exception as e:
            logger.error("Retry LLM call failed for post %d: %s", post.id, e)
            return
        parsed = parse_curate_response(raw)

    if parsed is None:
        logger.error("Could not parse curate response for post %d, skipping", post.id)
        return

    default_score = parsed.default_score * trust_level

    if parsed.relevance_score < threshold:
        logger.debug(
            "Rejected post %d (score=%.2f): %s", post.id, parsed.relevance_score, post.title[:60]
        )
        await update_post_rejected(db, post.id)
        return

    await update_post_curated(
        db,
        post_id=post.id,
        relevance_score=parsed.relevance_score,
        urgency=parsed.urgency,
        richness=parsed.richness,
        tags=parsed.tags,
        default_score=default_score,
        full_text=extracted.full_text or None,
        summary=extracted.summary or None,
        word_count=extracted.word_count,
        extraction_ok=extracted.extraction_ok,
    )
    logger.info(
        "Curated post %d (rel=%.2f score=%.1f): %s",
        post.id,
        parsed.relevance_score,
        default_score,
        post.title[:60],
    )


async def write_editorial_for_instance(
    db: aiosqlite.Connection,
    creative: OllamaClient,
    instance_id: str,
    hours: int = 24,
) -> int | None:
    """Generate and store a daily editorial for one instance. Returns editorial id or None."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if await has_editorial_for_date(db, instance_id, today):
        return None

    instance = await get_instance(db, instance_id)
    if not instance:
        return None
    archetype = await get_archetype(db, instance.archetype_id)
    if not archetype:
        return None

    drift = DriftVector.from_dict(instance.drift_vector)
    memory = Memory.from_dict(instance.memory)
    system_prompt = build_system_prompt(archetype, drift, memory)

    sessions = await get_sessions_for_editorial(db, instance_id, hours=hours)
    date_str = datetime.now(UTC).strftime("%A, %B %-d")
    system, user = build_editorial_prompt(system_prompt, sessions, instance.name, date_str)

    try:
        raw = await creative.chat(system, user, temperature=archetype.temperature)
    except Exception as e:
        logger.error("editorial LLM call failed for %s: %s", instance_id, e)
        return None

    result = parse_editorial_response(raw)
    if not result:
        logger.error("editorial parse failed for %s", instance_id)
        return None

    editorial_id = await insert_editorial(db, instance_id, result.body, result.mood, today)
    logger.info("editorial written for %s (%s): %s", instance.name, today, result.mood)
    return editorial_id


async def synthesize_board_post(db: aiosqlite.Connection, client: OllamaClient) -> int | None:
    """Ask the LLM to synthesize today's activity into a board post. Returns new post_id or None."""
    context = await get_synthesis_context(db)
    system, user = build_synthesis_prompt(**context)
    try:
        raw = await client.chat(system, user, temperature=0.8)
    except Exception as e:
        logger.error("synthesis LLM call failed: %s", e)
        return None
    result = parse_synthesis_response(raw)
    if not result:
        logger.error("synthesis response parse failed")
        return None
    post_id = await insert_board_post(db, result.title, result.body)
    logger.info("board post created: %d; %s", post_id, result.title[:80])
    return post_id


async def curate_all(
    db: aiosqlite.Connection,
    client: OllamaClient,
    sources: list[Source],
    threshold: float,
) -> tuple[int, int, int]:
    """Curate all raw posts. Returns (curated, rejected, failed) counts."""
    source_map = {s.name: s for s in sources}
    raw_posts = await get_posts_by_status(db, PostStatus.RAW)

    if not raw_posts:
        logger.info("No raw posts to curate")
        return 0, 0, 0

    logger.info("Curating %d raw posts", len(raw_posts))

    before_counts: dict[str, int] = {}
    from db.queries import count_posts_by_status

    before_counts = await count_posts_by_status(db)

    for post in raw_posts:
        source = source_map.get(post.source_name)
        await _curate_post(db, client, post, source, threshold)

    after_counts = await count_posts_by_status(db)
    curated = after_counts.get("curated", 0) - before_counts.get("curated", 0)
    rejected = after_counts.get("rejected", 0) - before_counts.get("rejected", 0)
    still_raw = after_counts.get("raw", 0)
    failed = len(raw_posts) - curated - rejected
    logger.info(
        "Curation complete: +%d curated, +%d rejected, %d failed/skipped, %d raw remaining",
        curated,
        rejected,
        failed,
        still_raw,
    )
    return curated, rejected, failed
