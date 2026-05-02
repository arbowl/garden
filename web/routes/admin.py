import logging

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from avatars.schema import DriftVector, Memory
from avatars.session import AvatarSession
from config import settings
from db.connection import get_db
from db.queries import (
    count_posts_by_status,
    create_archetype,
    create_instance,
    get_all_archetypes,
    get_all_instances,
    get_archetype,
    get_instance,
    get_instances_for_archetype,
    get_recent_sessions,
)
from ingest.fetcher import fetch_source
from ingest.sources import Source, load_sources, save_sources
from llm.client import OllamaClient
from web.templating import templates

router = APIRouter(prefix="/admin")
logger = logging.getLogger(__name__)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = await get_db()
    post_counts = await count_posts_by_status(db)
    archetypes = await get_all_archetypes(db)
    instances = await get_all_instances(db)
    sessions = await get_recent_sessions(db, limit=10)
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "post_counts": post_counts,
            "archetypes": archetypes,
            "instances": instances,
            "sessions": sessions,
        },
    )


@router.get("/archetypes/new", response_class=HTMLResponse)
async def archetype_new_form(request: Request):
    return templates.TemplateResponse(request, "admin/archetype_form.html", {"archetype": None})


@router.post("/archetypes")
async def archetype_create(
    request: Request,
    name: str = Form(...),
    role: str = Form(...),
    bio: str = Form(...),
    tone: str = Form(""),
    sentence_style: str = Form(""),
    vocabulary_level: str = Form(""),
    quirks: str = Form(""),
    example_comment: str = Form(""),
    favors: str = Form(""),
    dislikes: str = Form(""),
    vote_probability: float = Form(0.7),
    comment_threshold: float = Form(0.5),
    reply_probability: float = Form(0.6),
    verbosity: str = Form("medium"),
    contrarian_factor: float = Form(0.1),
    temperature: float = Form(0.7),
    max_instances: int = Form(1),
):
    def _split(s: str) -> list[str]:
        return [x.strip() for x in s.split(",") if x.strip()]

    db = await get_db()
    archetype_id = await create_archetype(
        db,
        name=name,
        bio=bio,
        role=role,
        tone=tone or None,
        sentence_style=sentence_style or None,
        vocabulary_level=vocabulary_level or None,
        quirks=quirks or None,
        example_comment=example_comment or None,
        favors=_split(favors),
        dislikes=_split(dislikes),
        indifferent=[],
        vote_probability=vote_probability,
        comment_threshold=comment_threshold,
        reply_probability=reply_probability,
        verbosity=verbosity,
        contrarian_factor=contrarian_factor,
        temperature=temperature,
        max_instances=max_instances,
    )
    return RedirectResponse(url=f"/admin/archetypes/{archetype_id}", status_code=303)


@router.get("/archetypes/{archetype_id}", response_class=HTMLResponse)
async def archetype_detail(request: Request, archetype_id: int):
    db = await get_db()
    archetype = await get_archetype(db, archetype_id)
    if not archetype:
        raise HTTPException(status_code=404, detail="Archetype not found")
    instances = await get_instances_for_archetype(db, archetype_id)
    return templates.TemplateResponse(
        request,
        "admin/archetype_detail.html",
        {
            "archetype": archetype,
            "instances": instances,
        },
    )


@router.post("/archetypes/{archetype_id}/spawn")
async def archetype_spawn(
    archetype_id: int,
    name: str = Form(...),
):
    db = await get_db()
    archetype = await get_archetype(db, archetype_id)
    if not archetype:
        raise HTTPException(status_code=404, detail="Archetype not found")
    instance_id = await create_instance(
        db,
        archetype_id=archetype_id,
        archetype_version=archetype.version,
        name=name,
        drift_vector=DriftVector.random_seed().to_dict(),
        memory=Memory().to_dict(),
    )
    logger.info("Spawned instance %s (%s) from archetype %d", instance_id, name, archetype_id)
    return RedirectResponse(url=f"/admin/archetypes/{archetype_id}", status_code=303)


async def _run_session_bg(instance_id: str) -> None:
    db = await get_db()
    disciplined = OllamaClient(
        base_url=settings.ollama_url, model=settings.ollama_disciplined_model
    )
    creative = OllamaClient(base_url=settings.ollama_url, model=settings.ollama_creative_model)
    session = AvatarSession(
        db, disciplined, creative, instance_id, max_seconds=settings.max_session_seconds
    )
    await session.run()


@router.post("/instances/{instance_id}/run-session")
async def run_session(instance_id: str, background_tasks: BackgroundTasks):
    db = await get_db()
    instance = await get_instance(db, instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    background_tasks.add_task(_run_session_bg, instance_id)
    logger.info("Queued session for instance %s", instance_id)
    return RedirectResponse(url="/admin", status_code=303)


# ── Sources ──────────────────────────────────────────────────────────────────


@router.get("/sources", response_class=HTMLResponse)
async def sources_list(request: Request):
    sources = load_sources(settings.sources_path)
    return templates.TemplateResponse(request, "admin/sources.html", {"sources": sources})


@router.post("/sources/add")
async def sources_add(
    name: str = Form(...),
    url: str = Form(...),
    type: str = Form("rss"),
    tags: str = Form(""),
    trust_level: float = Form(0.5),
):
    sources = load_sources(settings.sources_path)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    sources.append(Source(name=name, url=url, type=type, tags=tag_list, trust_level=trust_level))
    save_sources(settings.sources_path, sources)
    return RedirectResponse(url="/admin/sources", status_code=303)


@router.post("/sources/delete")
async def sources_delete(index: int = Form(...)):
    sources = load_sources(settings.sources_path)
    if 0 <= index < len(sources):
        sources.pop(index)
        save_sources(settings.sources_path, sources)
    return RedirectResponse(url="/admin/sources", status_code=303)


@router.post("/sources/fetch")
async def sources_fetch(index: int = Form(...)):
    sources = load_sources(settings.sources_path)
    if index < 0 or index >= len(sources):
        raise HTTPException(status_code=404)
    db = await get_db()
    import httpx

    async with httpx.AsyncClient(follow_redirects=True) as client:
        result = await fetch_source(db, sources[index], client)
    logger.info(
        "Manual fetch %s: fetched=%d new=%d errors=%d",
        result.source_name,
        result.fetched,
        result.new,
        result.errors,
    )
    return RedirectResponse(
        url=f"/admin/sources?fetched={result.source_name}&new={result.new}", status_code=303
    )
