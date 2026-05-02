import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from markupsafe import Markup, escape
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.removeprefix("www.")
    except Exception:
        return url


def _timeago(dt_str: str) -> str:
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        s = int((datetime.now(timezone.utc) - dt).total_seconds())
        if s < 60:
            return "just now"
        if s < 3600:
            m = s // 60
            return f"{m}m ago"
        if s < 86400:
            h = s // 3600
            return f"{h}h ago"
        d = s // 86400
        return f"{d}d ago"
    except Exception:
        return dt_str


def _timeshort(dt_str: str) -> str:
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        s = int((datetime.now(timezone.utc) - dt).total_seconds())
        if s < 60:
            return "now"
        if s < 3600:
            return f"{s // 60}m"
        if s < 86400:
            return f"{s // 3600}h"
        return f"{s // 86400}d"
    except Exception:
        return dt_str


class _TagStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(p.strip() for p in self._parts if p.strip())


class _ImageExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.src: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img" and self.src is None:
            d = dict(attrs)
            src = d.get("src") or ""
            if src.startswith("http"):
                self.src = src


def _first_image(raw: str | None) -> str | None:
    if not raw:
        return None
    ex = _ImageExtractor()
    ex.feed(raw)
    return ex.src


def _preview_text(raw: str | None) -> str | None:
    """Return stripped plain text if raw_content has meaningful prose, else None."""
    if not raw:
        return None
    stripper = _TagStripper()
    stripper.feed(raw)
    text = stripper.get_text().strip()
    if not text or text.lower() in {"comments", "read more", "more", "continue reading"}:
        return None
    return text


_MENTION_RE = re.compile(r"@(\w+)")


def _render_mentions(body: str, mentions: dict) -> Markup:
    """Replace confirmed @name tokens with profile links; leave others as plain text."""
    escaped = str(escape(body))
    if not mentions:
        return Markup(escaped)

    def _replace(m: re.Match) -> str:
        name = m.group(1)
        iid = mentions.get(name.lower())
        if iid:
            return f'<a href="/profile/avatar/{iid}" class="mention">@{name}</a>'
        return m.group(0)

    return Markup(_MENTION_RE.sub(_replace, escaped))


templates.env.filters["domain"] = _domain
templates.env.filters["timeago"] = _timeago
templates.env.filters["timeshort"] = _timeshort
templates.env.filters["preview_text"] = _preview_text
templates.env.filters["first_image"] = _first_image
templates.env.filters["render_mentions"] = _render_mentions
