from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db.connection import get_db
from db.queries import get_hot_comments, get_new_comments
from ranking.hot_rank import rank_hot_comments
from web.templating import templates

router = APIRouter()


@router.get("/sidebar/hot-comments", response_class=HTMLResponse)
async def sidebar_hot_comments(request: Request):
    db = await get_db()
    sort = request.query_params.get("sort", "hot")
    if sort == "new":
        comments = await get_new_comments(db, limit=10)
    elif sort == "top":
        pool = await get_hot_comments(db, since_interval="-1 day")
        comments = sorted(pool, key=lambda c: c["vote_count"], reverse=True)[:10]
    else:
        pool = await get_hot_comments(db)
        comments = rank_hot_comments(pool, limit=10)
    return templates.TemplateResponse(
        request, "partials/hot_comments.html", {"comments": comments, "sort": sort}
    )
