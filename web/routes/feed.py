from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from config import settings
from db.connection import get_db
from db.models import Comment, PostStatus
from db.queries import (
    get_comments_for_post,
    get_hot_posts,
    get_mentions_for_post,
    get_new_posts,
    get_post,
    get_posts_by_status,
    get_top_posts,
)
from web.templating import templates

router = APIRouter()


@dataclass
class CommentNode:
    comment: Comment
    children: list["CommentNode"] = field(default_factory=list)


def build_comment_tree(comments: list[Comment]) -> list[CommentNode]:
    nodes = {c.id: CommentNode(comment=c) for c in comments}
    roots: list[CommentNode] = []
    for comment in comments:
        node = nodes[comment.id]
        if comment.parent_comment_id and comment.parent_comment_id in nodes:
            nodes[comment.parent_comment_id].children.append(node)
        else:
            roots.append(node)
    return roots


PAGE_SIZE = 25


@router.get("/", response_class=HTMLResponse)
async def feed_hot(request: Request, page: int = Query(1, ge=1)):
    db = await get_db()
    offset = (page - 1) * PAGE_SIZE
    posts = await get_hot_posts(
        db, limit=PAGE_SIZE + 1, max_per_source=settings.max_posts_per_source, offset=offset
    )
    if not posts and page == 1:
        posts = await get_posts_by_status(db, PostStatus.RAW)
        has_next = False
    else:
        has_next = len(posts) > PAGE_SIZE
        posts = posts[:PAGE_SIZE]
    return templates.TemplateResponse(
        request,
        "feed.html",
        {
            "posts": posts,
            "sort": "hot",
            "page": page,
            "has_next": has_next,
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def feed_new(request: Request, page: int = Query(1, ge=1)):
    db = await get_db()
    offset = (page - 1) * PAGE_SIZE
    posts = await get_new_posts(db, limit=PAGE_SIZE + 1, offset=offset)
    has_next = len(posts) > PAGE_SIZE
    posts = posts[:PAGE_SIZE]
    return templates.TemplateResponse(
        request,
        "feed.html",
        {
            "posts": posts,
            "sort": "new",
            "page": page,
            "has_next": has_next,
        },
    )


_TOP_INTERVALS = {
    "24h": "-1 day",
    "7d": "-7 days",
    "30d": "-30 days",
    "all": None,
}


@router.get("/top", response_class=HTMLResponse)
async def feed_top(request: Request, page: int = Query(1, ge=1), since: str = Query("24h")):
    if since not in _TOP_INTERVALS:
        since = "24h"
    db = await get_db()
    offset = (page - 1) * PAGE_SIZE
    posts = await get_top_posts(
        db, limit=PAGE_SIZE + 1, offset=offset, since_interval=_TOP_INTERVALS[since]
    )
    has_next = len(posts) > PAGE_SIZE
    posts = posts[:PAGE_SIZE]
    return templates.TemplateResponse(
        request,
        "feed.html",
        {
            "posts": posts,
            "sort": "top",
            "since": since,
            "page": page,
            "has_next": has_next,
        },
    )


@router.get("/post/{post_id}", response_class=HTMLResponse)
async def post_detail(request: Request, post_id: int):
    db = await get_db()
    post = await get_post(db, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    flat_comments = await get_comments_for_post(db, post_id)
    comment_tree = build_comment_tree(flat_comments)
    sort = request.query_params.get("sort", "hot")
    if sort == "top":
        comment_tree.sort(key=lambda n: n.comment.vote_count, reverse=True)
    elif sort == "hot":
        comment_tree.sort(key=lambda n: (n.comment.vote_count, n.comment.created_at), reverse=True)
    else:
        comment_tree.sort(key=lambda n: n.comment.created_at, reverse=True)
    mention_map = await get_mentions_for_post(db, post_id)
    return templates.TemplateResponse(
        request,
        "post.html",
        {
            "post": post,
            "comments": comment_tree,
            "sort": sort,
            "mention_map": mention_map,
        },
    )
