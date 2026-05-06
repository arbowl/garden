"""Parsing LLM responses into structured data models."""

import json
import logging
import re
from typing import Literal

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


class CurateResponse(BaseModel):
    """Structured response for content triage and curation."""

    relevance_score: float
    urgency: Literal["low", "medium", "high"]
    richness: Literal["headline_only", "summary", "full_text"]
    tags: list[str]
    default_score: float

    @field_validator("relevance_score")
    @classmethod
    def clamp_relevance(cls, v: float) -> float:
        """Clamp relevance score to [0.0, 1.0]."""
        return max(0.0, min(1.0, v))

    @field_validator("default_score")
    @classmethod
    def clamp_default(cls, v: float) -> float:
        """Clamp default score to [0.0, 10.0]."""
        return max(0.0, min(10.0, v))

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, v: list[str]) -> list[str]:
        """Clean tags by stripping whitespace, converting to lowercase, and limiting to 5 tags."""
        return [t.lower().strip() for t in v[:5] if t.strip()]


def _clean_json(raw: str) -> str:
    raw = _THINK_RE.sub("", raw).strip()
    match = _FENCE_RE.match(raw)
    if match:
        raw = match.group(1).strip()
    raw = _TRAILING_COMMA_RE.sub(r"\1", raw)
    return raw


def parse_curate_response(raw: str) -> CurateResponse | None:
    """Parse raw LLM response into a CurateResponse model, with error handling."""
    try:
        cleaned = _clean_json(raw)
        data = json.loads(cleaned)
        return CurateResponse.model_validate(data)
    except Exception as e:
        logger.debug("Failed to parse curate response: %s | raw=%r", e, raw[:200])
        return None


class TriageResponse(BaseModel):
    """Structured response for triage decisions."""

    engage: list[int] = []
    downvote: list[int] = []

    @field_validator("engage", "downvote")
    @classmethod
    def cap_list(cls, v: list[int]) -> list[int]:
        """Cap lists to a maximum of 8 items."""
        return v[:8]


class EngagementResponse(BaseModel):
    vote: Literal["up", "down", "none"] = "none"
    vote_reason: str = ""
    comment: str | None = None
    reply_to_id: int | None = None
    reply_text: str | None = None

    @field_validator("comment", "reply_text")
    @classmethod
    def empty_to_none(cls, v: str | None) -> str | None:
        """Convert empty strings to None."""
        if v is not None and not v.strip():
            return None
        return v


class ReactResponse(BaseModel):
    reply: str | None = None

    @field_validator("reply")
    @classmethod
    def empty_to_none(cls, v: str | None) -> str | None:
        """Convert empty strings to None."""
        if v is not None and not v.strip():
            return None
        return v


class WindDownResponse(BaseModel):
    mood: str = "neutral"
    summary: str = ""
    topic_interests: list[str] = []

    @field_validator("mood")
    @classmethod
    def clean_mood(cls, v: str) -> str:
        """Clean mood by taking the first word, stripping whitespace, and converting to
        lowercase.
        """
        return v.strip().split()[0].lower() if v.strip() else "neutral"

    @field_validator("topic_interests")
    @classmethod
    def clean_topics(cls, v: list[str]) -> list[str]:
        """Clean topic interests by stripping whitespace, converting to lowercase, and limiting to
        8 topics.
        """
        return [t.lower().strip() for t in v[:8] if t.strip()]


def _parse(raw: str, model: type[BaseModel]) -> object | None:
    """Generic parser for LLM responses into specified Pydantic models, with error handling."""
    try:
        data = json.loads(_clean_json(raw))
        return model.model_validate(data)
    except Exception as e:
        logger.debug("Failed to parse %s: %s | raw=%r", model.__name__, e, raw[:200])
        return None


def parse_triage_response(raw: str) -> TriageResponse | None:
    return _parse(raw, TriageResponse)  # type: ignore[return-value]


def parse_engagement_response(raw: str) -> EngagementResponse | None:
    return _parse(raw, EngagementResponse)  # type: ignore[return-value]


def parse_react_response(raw: str) -> ReactResponse | None:
    return _parse(raw, ReactResponse)  # type: ignore[return-value]


def parse_wind_down_response(raw: str) -> WindDownResponse | None:
    return _parse(raw, WindDownResponse)  # type: ignore[return-value]


class EditorialResponse(BaseModel):
    body: str
    mood: str = "neutral"

    @field_validator("body")
    @classmethod
    def clean_body(cls, v: str) -> str:
        """Clean body by stripping whitespace and limiting to 2000 characters."""
        return v.strip()[:2000]

    @field_validator("mood")
    @classmethod
    def clean_mood(cls, v: str) -> str:
        """Clean mood by taking the first word, stripping whitespace, and converting to
        lowercase.
        """
        return v.strip().split()[0].lower() if v.strip() else "neutral"


def parse_editorial_response(raw: str) -> EditorialResponse | None:
    return _parse(raw, EditorialResponse)  # type: ignore[return-value]


class SynthesisResponse(BaseModel):
    title: str
    body: str

    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str) -> str:
        """Clean title by stripping whitespace and limiting to 200 characters."""
        return v.strip()[:200]

    @field_validator("body")
    @classmethod
    def clean_body(cls, v: str) -> str:
        """Clean body by stripping whitespace and limiting to 2000 characters."""
        return v.strip()[:2000]


def parse_synthesis_response(raw: str) -> SynthesisResponse | None:
    return _parse(raw, SynthesisResponse)  # type: ignore[return-value]


class CommentSentimentResponse(BaseModel):
    votes: dict[str, Literal["agree", "disagree", "neutral"]] = {}

    @field_validator("votes", mode="before")
    @classmethod
    def clean_votes(cls, v: object) -> dict[str, str]:
        if not isinstance(v, dict):
            return {}
        valid = {"agree", "disagree", "neutral"}
        return {str(k): (val if val in valid else "neutral") for k, val in list(v.items())[:50]}


def parse_comment_sentiment_response(raw: str) -> CommentSentimentResponse | None:
    try:
        data = json.loads(_clean_json(raw))
        # LLM may return the dict directly rather than wrapped in {"votes": ...}
        if isinstance(data, dict) and "votes" not in data:
            data = {"votes": data}
        return CommentSentimentResponse.model_validate(data)
    except Exception as e:
        logger.debug("Failed to parse CommentSentimentResponse: %s | raw=%r", e, raw[:200])
        return None
