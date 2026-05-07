from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from db.connection import get_db
from db.queries import get_saved_posts, is_post_saved, toggle_saved_post
from web.templating import templates

router = APIRouter()

PAGE_SIZE = 25


@router.post("/save/{post_id}", response_class=HTMLResponse)
async def toggle_save(request: Request, post_id: int):
    db = await get_db()
    saved = await toggle_saved_post(db, post_id)
    return templates.TemplateResponse(
        request,
        "partials/star_btn.html",
        {"post_id": post_id, "saved": saved},
    )


@router.get("/saved", response_class=HTMLResponse)
async def saved_page(request: Request, page: int = Query(1, ge=1)):
    db = await get_db()
    offset = (page - 1) * PAGE_SIZE
    posts = await get_saved_posts(db, limit=PAGE_SIZE + 1, offset=offset)
    has_next = len(posts) > PAGE_SIZE
    posts = posts[:PAGE_SIZE]
    return templates.TemplateResponse(
        request,
        "saved.html",
        {
            "posts": posts,
            "page": page,
            "has_next": has_next,
        },
    )
