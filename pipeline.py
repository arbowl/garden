"""Standalone runner: fetch → curate.

Usage:
    uv run python pipeline.py
"""

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

from config import settings
from db.connection import get_db
from ingest.fetcher import fetch_all
from ingest.sources import load_sources
from llm.client import OllamaClient
from curator.curator import curate_all


async def run_pipeline() -> None:
    sources = load_sources(settings.sources_path)
    from db.connection import init_db

    db = await init_db(settings.db_path)
    client = OllamaClient(base_url=settings.ollama_url, model=settings.ollama_disciplined_model)

    logging.getLogger(__name__).info("Fetching from %d sources...", len(sources))
    results = await fetch_all(db, sources)
    total_new = sum(r.new for r in results)
    logging.getLogger(__name__).info("Fetch complete: %d new posts", total_new)

    curated, rejected, failed = await curate_all(
        db, client, sources, threshold=settings.curator_threshold
    )
    logging.getLogger(__name__).info(
        "Pipeline done — curated=%d rejected=%d failed=%d", curated, rejected, failed
    )


if __name__ == "__main__":
    asyncio.run(run_pipeline())
