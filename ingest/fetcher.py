""" "Functions for fetching and processing news posts from RSS feeds, including error handling and
logging."""

import logging
from dataclasses import dataclass

import aiosqlite
import feedparser  # type: ignore
import httpx

from db.queries import insert_raw_post
from ingest.sources import Source

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Represents the result of fetching a source, including the number of entries fetched, how many
    were new, and how many errors occurred."""

    source_name: str
    fetched: int
    new: int
    errors: int


async def _fetch_feed(client: httpx.AsyncClient, source: Source) -> list[dict]:
    try:
        response = await client.get(source.url, timeout=15.0)
        response.raise_for_status()
        feed = feedparser.parse(response.text)
        entries = []
        for entry in feed.entries:
            url = str(entry.get("link", "")).strip()
            title = str(entry.get("title", "")).strip()
            if not url or not title:
                continue
            raw_content = entry.get("summary") or entry.get("description") or None
            entries.append({"url": url, "title": title, "raw_content": raw_content})
        return entries
    except Exception as e:
        logger.warning("Failed to fetch %s: %s", source.url, e)
        return []


async def fetch_source(
    db: aiosqlite.Connection,
    source: Source,
    client: httpx.AsyncClient,
) -> FetchResult:
    """Fetch posts from a single source, insert new ones into the database, and return a summary of
    the results."""
    entries = await _fetch_feed(client, source)
    new = 0
    errors = 0
    for entry in entries:
        try:
            result = await insert_raw_post(
                db,
                url=entry["url"],
                title=entry["title"],
                source_name=source.name,
                raw_content=entry["raw_content"],
            )
            if result is not None:
                new += 1
        except Exception as e:
            logger.error("Failed to insert post from %s: %s", source.name, e)
            errors += 1
    return FetchResult(source_name=source.name, fetched=len(entries), new=new, errors=errors)


async def fetch_all(db: aiosqlite.Connection, sources: list[Source]) -> list[FetchResult]:
    results = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for source in sources:
            result = await fetch_source(db, source, client)
            logger.info(
                "%-20s fetched=%-3d new=%-3d errors=%d",
                result.source_name,
                result.fetched,
                result.new,
                result.errors,
            )
            results.append(result)
    return results
