import asyncio
import logging
import re
from dataclasses import dataclass

import trafilatura

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    text = _HTML_TAG_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


@dataclass
class ExtractedContent:
    full_text: str
    summary: str
    word_count: int
    extraction_ok: bool


def _run_trafilatura(url: str) -> tuple[str | None, bool]:
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None, False
        text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
        return text, bool(text)
    except Exception as e:
        logger.debug("trafilatura failed for %s: %s", url, e)
        return None, False


async def extract_content(url: str, fallback_summary: str | None = None) -> ExtractedContent:
    try:
        text, ok = await asyncio.wait_for(
            asyncio.to_thread(_run_trafilatura, url),
            timeout=15.0,
        )
    except TimeoutError:
        logger.debug("extraction timed out for %s", url)
        text, ok = None, False

    if ok and text:
        summary = (fallback_summary or text)[:200]
        return ExtractedContent(
            full_text=text,
            summary=summary,
            word_count=len(text.split()),
            extraction_ok=True,
        )

    fallback = _strip_html(fallback_summary or "")
    return ExtractedContent(
        full_text=fallback,
        summary=fallback[:200],
        word_count=len(fallback.split()) if fallback else 0,
        extraction_ok=False,
    )
