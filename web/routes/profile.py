from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.connection import get_db
from db.queries import (
    count_editorials_for_instance,
    count_human_comments,
    count_human_votes,
    count_instance_comments,
    count_instance_votes,
    get_archetype,
    get_editorials_for_instance,
    get_human_comments,
    get_human_votes,
    get_instance,
    get_instance_comments,
    get_instance_sessions,
    get_instance_votes,
    get_profile_relationships,
    sum_human_comment_votes,
    sum_instance_comment_votes,
    update_instance,
)
from web.templating import templates

router = APIRouter(prefix="/profile")

_PER_PAGE = 25


@router.get("/avatar/{instance_id}", response_class=HTMLResponse)
async def avatar_profile(
    request: Request, instance_id: str, ep: int = 1, cp: int = 1, vp: int = 1, tab: str = "comments"
):
    db = await get_db()
    instance = await get_instance(db, instance_id)
    if not instance:
        raise HTTPException(status_code=404)
    archetype = await get_archetype(db, instance.archetype_id)
    sessions = await get_instance_sessions(db, instance_id, limit=10)

    comment_total = await count_instance_comments(db, instance_id)
    vote_total = await count_instance_votes(db, instance_id)
    karma = await sum_instance_comment_votes(db, instance_id)
    comments = await get_instance_comments(
        db, instance_id, limit=_PER_PAGE, offset=(cp - 1) * _PER_PAGE
    )
    votes = await get_instance_votes(db, instance_id, limit=_PER_PAGE, offset=(vp - 1) * _PER_PAGE)
    comment_pages = max(1, -(-comment_total // _PER_PAGE))
    vote_pages = max(1, -(-vote_total // _PER_PAGE))

    per_page = 5
    editorial_total = await count_editorials_for_instance(db, instance_id)
    editorials = await get_editorials_for_instance(
        db, instance_id, limit=per_page, offset=(ep - 1) * per_page
    )
    editorial_pages = max(1, -(-editorial_total // per_page))
    relationships = await get_profile_relationships(db, instance_id)
    return templates.TemplateResponse(
        request,
        "profile/avatar.html",
        {
            "instance": instance,
            "archetype": archetype,
            "sessions": sessions,
            "comments": comments,
            "comment_total": comment_total,
            "comment_page": cp,
            "comment_pages": comment_pages,
            "votes": votes,
            "vote_total": vote_total,
            "vote_page": vp,
            "vote_pages": vote_pages,
            "karma": karma,
            "editorials": editorials,
            "editorial_page": ep,
            "editorial_pages": editorial_pages,
            "editorial_total": editorial_total,
            "relationships": relationships,
            "tab": tab,
        },
    )


@router.get("/avatar/{instance_id}/edit", response_class=HTMLResponse)
async def avatar_edit_form(request: Request, instance_id: str):
    db = await get_db()
    instance = await get_instance(db, instance_id)
    if not instance:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "profile/avatar_edit.html",
        {"instance": instance},
    )


@router.post("/avatar/{instance_id}/edit")
async def avatar_edit(
    request: Request,
    instance_id: str,
    name: str = Form(...),
    mood: str = Form(""),
    is_active: str = Form(""),
    new_post_bias: float = Form(0.0),
):
    db = await get_db()
    instance = await get_instance(db, instance_id)
    if not instance:
        raise HTTPException(status_code=404)
    await update_instance(
        db,
        instance_id=instance_id,
        name=name.strip(),
        mood=mood.strip() or None,
        is_active=is_active == "on",
        new_post_bias=max(-1.0, min(1.0, new_post_bias)),
    )
    return RedirectResponse(url=f"/profile/avatar/{instance_id}", status_code=303)


@router.get("/you", response_class=HTMLResponse)
async def human_profile(request: Request, cp: int = 1, vp: int = 1, tab: str = "comments"):
    db = await get_db()
    comment_total = await count_human_comments(db)
    vote_total = await count_human_votes(db)
    karma = await sum_human_comment_votes(db)
    comments = await get_human_comments(db, limit=_PER_PAGE, offset=(cp - 1) * _PER_PAGE)
    votes = await get_human_votes(db, limit=_PER_PAGE, offset=(vp - 1) * _PER_PAGE)
    comment_pages = max(1, -(-comment_total // _PER_PAGE))
    vote_pages = max(1, -(-vote_total // _PER_PAGE))
    return templates.TemplateResponse(
        request,
        "profile/you.html",
        {
            "comments": comments,
            "comment_total": comment_total,
            "comment_page": cp,
            "comment_pages": comment_pages,
            "votes": votes,
            "vote_total": vote_total,
            "vote_page": vp,
            "vote_pages": vote_pages,
            "karma": karma,
            "tab": tab,
        },
    )
