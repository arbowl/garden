from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from db.connection import get_db
from db.queries import (
    get_archetype,
    count_editorials_for_instance,
    get_editorials_for_instance,
    get_human_comments,
    get_human_votes,
    get_instance,
    get_instance_comments,
    get_instance_sessions,
    get_instance_votes,
)
from web.templating import templates

router = APIRouter(prefix="/profile")


@router.get("/avatar/{instance_id}", response_class=HTMLResponse)
async def avatar_profile(request: Request, instance_id: str, ep: int = 1):
    db = await get_db()
    instance = await get_instance(db, instance_id)
    if not instance:
        raise HTTPException(status_code=404)
    archetype = await get_archetype(db, instance.archetype_id)
    sessions = await get_instance_sessions(db, instance_id, limit=10)
    comments = await get_instance_comments(db, instance_id, limit=50)
    votes = await get_instance_votes(db, instance_id, limit=50)
    per_page = 5
    editorial_total = await count_editorials_for_instance(db, instance_id)
    editorials = await get_editorials_for_instance(
        db, instance_id, limit=per_page, offset=(ep - 1) * per_page
    )
    editorial_pages = max(1, -(-editorial_total // per_page))  # ceiling division
    return templates.TemplateResponse(
        request,
        "profile/avatar.html",
        {
            "instance": instance,
            "archetype": archetype,
            "sessions": sessions,
            "comments": comments,
            "votes": votes,
            "editorials": editorials,
            "editorial_page": ep,
            "editorial_pages": editorial_pages,
            "editorial_total": editorial_total,
        },
    )


@router.get("/you", response_class=HTMLResponse)
async def human_profile(request: Request):
    db = await get_db()
    comments = await get_human_comments(db, limit=50)
    votes = await get_human_votes(db, limit=50)
    return templates.TemplateResponse(
        request,
        "profile/you.html",
        {
            "comments": comments,
            "votes": votes,
        },
    )
