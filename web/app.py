import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from config import settings
from curator.curator import synthesize_board_post, write_editorial_for_instance
from db.connection import close_db, get_db, init_db
from db.queries import (
    close_stale_sessions,
    count_posts_by_status,
    get_active_board_post,
    get_instances_without_any_editorial,
)
from ingest.fetcher import fetch_all
from ingest.sources import load_sources
from llm.client import OllamaClient
from scheduler.jobs import start_scheduler, stop_scheduler
from web.routes import actions as actions_routes
from web.routes import admin as admin_routes
from web.routes import board as board_routes
from web.routes import events as events_routes
from web.routes import feed as feed_routes
from web.routes import inbox as inbox_routes
from web.routes import profile as profile_routes
from web.routes import sidebar as sidebar_routes

logger = logging.getLogger(__name__)


async def _background_init(db) -> None:
    if not await get_active_board_post(db):
        logger.info("[init] no active board post; synthesizing")
        client = OllamaClient(base_url=settings.ollama_url, model=settings.ollama_disciplined_model)
        await synthesize_board_post(db, client)
    instances_needing_editorial = await get_instances_without_any_editorial(db)
    if instances_needing_editorial:
        logger.info(
            "[init] writing first editorial for %d instance(s)", len(instances_needing_editorial)
        )
        creative = OllamaClient(base_url=settings.ollama_url, model=settings.ollama_creative_model)
        await asyncio.gather(
            *[
                write_editorial_for_instance(db, creative, instance_id, hours=72)
                for instance_id in instances_needing_editorial
            ]
        )
        logger.info("[init] editorials done")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(settings.db_path)
    db = await get_db()
    stale = await close_stale_sessions(db)
    if stale:
        logger.warning("Closed %d stale session(s) from previous run", stale)
    counts = await count_posts_by_status(db)
    if not counts:
        logger.info("Empty DB; running initial fetch")
        sources = load_sources(settings.sources_path)
        await fetch_all(db, sources)
    start_scheduler()
    asyncio.create_task(_background_init(db))
    yield
    stop_scheduler()
    await close_db()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(feed_routes.router)
app.include_router(actions_routes.router)
app.include_router(events_routes.router)
app.include_router(admin_routes.router)
app.include_router(inbox_routes.router)
app.include_router(profile_routes.router)
app.include_router(board_routes.router)
app.include_router(sidebar_routes.router)
