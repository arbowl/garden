from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db.connection import get_db
from db.queries import (
    count_unread_notifications,
    get_notifications,
    mark_all_notifications_read,
    mark_notification_read,
)
from web.templating import templates

router = APIRouter()


@router.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request):
    db = await get_db()
    notifications = await get_notifications(db)
    unread = [n for n in notifications if not n["read"]]
    read = [n for n in notifications if n["read"]]
    return templates.TemplateResponse(
        request, "inbox.html", {"unread": unread, "read": read, "unread_count": len(unread)}
    )


@router.post("/inbox/mark-read", response_class=HTMLResponse)
async def mark_read(request: Request):
    db = await get_db()
    await mark_all_notifications_read(db)
    return '<span id="inbox-badge"></span>'


@router.post("/inbox/mark-read/{notification_id}", response_class=HTMLResponse)
async def mark_one_read(request: Request, notification_id: int):
    db = await get_db()
    await mark_notification_read(db, notification_id)
    count = await count_unread_notifications(db)
    if count:
        badge = f'<span id="inbox-badge" class="inbox-badge" hx-swap-oob="true">{count}</span>'
    else:
        badge = '<span id="inbox-badge" hx-swap-oob="true"></span>'
    return badge


@router.get("/inbox/unread-count", response_class=HTMLResponse)
async def unread_count(request: Request):
    db = await get_db()
    count = await count_unread_notifications(db)
    if count:
        return f'<span id="inbox-badge" class="inbox-badge">{count}</span>'
    return '<span id="inbox-badge"></span>'
