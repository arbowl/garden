import logging
import re
from dataclasses import dataclass, field

import aiosqlite
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from db.connection import get_db
from db.models import AuthorType, Comment, PostStatus
from db.queries import (
    delete_comment_cascade,
    get_comment,
    get_mentions_for_post,
    get_my_comment_votes,
    get_my_post_votes,
    get_post,
    insert_comment,
    insert_mention,
    insert_vote,
    retract_vote,
    update_comment_body,
)
from web.templating import templates

_MENTION_RE = re.compile(r"@(\w+)")


logger = logging.getLogger(__name__)

router = APIRouter()


async def _handle_mentions(
    db: aiosqlite.Connection, body: str, comment_id: int, post_id: int
) -> None:
    names = {str(m).lower() for m in _MENTION_RE.findall(body)}
    if not names:
        return
    for name in names:
        async with db.execute(
            "SELECT id FROM instances WHERE lower(name) = ? AND is_active = 1 LIMIT 1",
            (name,),
        ) as cur:
            row = await cur.fetchone()
        if row:
            await insert_mention(db, comment_id, row["id"], post_id)


@dataclass
class CommentNode:
    comment: Comment
    children: list["CommentNode"] = field(default_factory=list)


@router.post("/vote", response_class=HTMLResponse)
async def vote(
    request: Request,
    post_id: int = Form(...),
    direction: int = Form(...),
):
    db = await get_db()
    post = await get_post(db, post_id)
    if post is None:
        raise HTTPException(status_code=404)
    if direction not in (1, -1):
        raise HTTPException(status_code=422, detail="direction must be 1 or -1")
    current = await get_my_post_votes(db, [post_id])
    if current.get(post_id) == direction:
        await retract_vote(db, "you", post_id=post_id)
    else:
        await insert_vote(
            db, voter_type=AuthorType.HUMAN, direction=direction, post_id=post_id, voter_id="you"
        )
    post = await get_post(db, post_id)
    assert post is not None
    my_votes = await get_my_post_votes(db, [post_id])
    return templates.TemplateResponse(
        request, "partials/vote_col.html", {"post": post, "my_vote": my_votes.get(post_id, 0)}
    )


@router.post("/vote-comment", response_class=HTMLResponse)
async def vote_comment(
    request: Request,
    comment_id: int = Form(...),
    direction: int = Form(...),
):
    db = await get_db()
    comment = await get_comment(db, comment_id)
    if comment is None:
        raise HTTPException(status_code=404)
    if direction not in (1, -1):
        raise HTTPException(status_code=422, detail="direction must be 1 or -1")
    current = await get_my_comment_votes(db, [comment_id])
    if current.get(comment_id) == direction:
        await retract_vote(db, "you", comment_id=comment_id)
    else:
        await insert_vote(
            db, voter_type=AuthorType.HUMAN, direction=direction, comment_id=comment_id, voter_id="you"
        )
    comment = await get_comment(db, comment_id)
    assert comment is not None
    my_votes = await get_my_comment_votes(db, [comment_id])
    return templates.TemplateResponse(
        request,
        "partials/comment_vote.html",
        {"comment": comment, "my_vote": my_votes.get(comment_id, 0)},
    )


@router.post("/comment", response_class=HTMLResponse)
async def comment(
    request: Request,
    post_id: int = Form(...),
    body: str = Form(...),
):
    body = body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="body cannot be empty")
    db = await get_db()
    post = await get_post(db, post_id)
    if post is None:
        raise HTTPException(status_code=404)
    if post.status == PostStatus.ARCHIVED:
        raise HTTPException(status_code=423, detail="This discussion is locked")
    comment_id = await insert_comment(
        db,
        post_id=post_id,
        author_type=AuthorType.HUMAN,
        author_name="you",
        body=body,
    )
    assert comment_id is not None
    await _handle_mentions(db, body, comment_id, post_id)
    new_comment = await get_comment(db, comment_id)
    assert new_comment is not None
    node = CommentNode(comment=new_comment)
    mention_map = await get_mentions_for_post(db, post_id)
    return templates.TemplateResponse(
        request, "partials/comment_fragment.html", {"node": node, "mention_map": mention_map}
    )


@router.patch("/comment/{comment_id}", response_class=HTMLResponse)
async def edit_comment(
    request: Request,
    comment_id: int,
    body: str = Form(...),
):
    body = body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="body cannot be empty")
    db = await get_db()
    comment = await get_comment(db, comment_id)
    if comment is None:
        raise HTTPException(status_code=404)
    if comment.author_type != AuthorType.HUMAN:
        raise HTTPException(status_code=403)
    updated = await update_comment_body(db, comment_id, body)
    if not updated:
        raise HTTPException(status_code=404)
    comment = await get_comment(db, comment_id)
    assert comment is not None
    mention_map = await get_mentions_for_post(db, comment.post_id)
    return templates.TemplateResponse(
        request,
        "partials/comment_body.html",
        {"comment": comment, "mention_map": mention_map},
    )


@router.delete("/comment/{comment_id}")
async def delete_comment(
    request: Request,
    comment_id: int,
):
    db = await get_db()
    comment = await get_comment(db, comment_id)
    if comment is None:
        raise HTTPException(status_code=404)
    if comment.author_type != AuthorType.HUMAN:
        raise HTTPException(status_code=403)
    deleted = await delete_comment_cascade(db, comment_id)
    if not deleted:
        raise HTTPException(status_code=404)
    return HTMLResponse("", status_code=200)


@router.post("/reply", response_class=HTMLResponse)
async def reply(
    request: Request,
    post_id: int = Form(...),
    parent_comment_id: int = Form(...),
    body: str = Form(...),
):
    body = body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="body cannot be empty")
    db = await get_db()
    post = await get_post(db, post_id)
    if post is None:
        raise HTTPException(status_code=404)
    if post.status == PostStatus.ARCHIVED:
        raise HTTPException(status_code=423, detail="This discussion is locked")
    comment_id = await insert_comment(
        db,
        post_id=post_id,
        author_type=AuthorType.HUMAN,
        author_name="you",
        body=body,
        parent_comment_id=parent_comment_id,
    )
    assert comment_id is not None
    await _handle_mentions(db, body, comment_id, post_id)
    new_comment = await get_comment(db, comment_id)
    assert new_comment is not None
    node = CommentNode(comment=new_comment)
    mention_map = await get_mentions_for_post(db, post_id)
    return templates.TemplateResponse(
        request, "partials/comment_fragment.html", {"node": node, "mention_map": mention_map}
    )
