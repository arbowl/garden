from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db.connection import get_db
from db.queries import get_board_posts
from web.templating import templates

router = APIRouter()


@router.get("/board", response_class=HTMLResponse)
async def board(request: Request):
    db = await get_db()
    posts = await get_board_posts(db)
    active = next((p for p in posts if p.status == "curated"), None)
    archive = [p for p in posts if p.status != "curated"]
    return templates.TemplateResponse(request, "board.html", {"active": active, "archive": archive})
