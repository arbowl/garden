"""Data models for posts, comments, votes, archetypes, and instances."""

from dataclasses import dataclass, field
from enum import StrEnum


class PostStatus(StrEnum):
    """The lifecycle status of a post, which can guide how avatars interact with it."""

    RAW = "raw"
    CURATED = "curated"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class ContentType(StrEnum):
    """The type of content in a post, which can guide how avatars engage with it."""

    FETCHED = "fetched"
    EDITORIAL = "editorial"
    BOARD = "board"


class Urgency(StrEnum):
    """The level of time-sensitivity for engaging with the content, which can guide avatar
    behavior.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Richness(StrEnum):
    """The level of depth and detail in the content, which can guide how avatars engage with it."""

    HEADLINE_ONLY = "headline_only"
    SUMMARY = "summary"
    FULL_TEXT = "full_text"


class AuthorType(StrEnum):
    """Distinguishes between human users and avatar instances as authors of comments and votes."""

    HUMAN = "human"
    AVATAR = "avatar"


@dataclass
class Post:
    """Represents a news post or article that avatars can interact with."""

    id: int
    url: str
    title: str
    source_name: str
    status: PostStatus
    hot_score: float
    vote_count: int
    comment_count: int
    content_type: ContentType
    created_at: str
    last_activity: str
    raw_content: str | None = None
    full_text: str | None = None
    summary: str | None = None
    word_count: int = 0
    extraction_ok: bool = False
    relevance_score: float | None = None
    urgency: Urgency | None = None
    richness: Richness | None = None
    tags: list[str] = field(default_factory=list)
    default_score: float = 0.0
    engagement_score: float = 0.0


@dataclass
class Comment:
    """A comment made by an avatar or human on a post, with potential nesting for threads."""

    id: int
    post_id: int
    author_type: AuthorType
    author_name: str
    body: str
    depth: int
    vote_count: int
    created_at: str
    parent_comment_id: int | None = None
    author_id: str | None = None
    edited_at: str | None = None


@dataclass
class Vote:
    """A record of a vote cast by an avatar or human on a post or comment."""

    id: int
    voter_type: AuthorType
    direction: int
    created_at: str
    post_id: int | None = None
    comment_id: int | None = None
    voter_id: str | None = None
    reason: str | None = None


@dataclass
class Archetype:
    """A blueprint for an avatar, defining its general personality and behavior patterns."""

    id: int
    name: str
    version: int
    bio: str
    role: str
    vote_probability: float
    comment_threshold: float
    reply_probability: float
    verbosity: str
    contrarian_factor: float
    temperature: float
    max_instances: int
    is_active: bool
    created_at: str
    new_post_bias: float = 0.0
    tone: str | None = None
    sentence_style: str | None = None
    vocabulary_level: str | None = None
    quirks: str | None = None
    example_comment: str | None = None
    favors: list[str] = field(default_factory=list)
    dislikes: list[str] = field(default_factory=list)
    indifferent: list[str] = field(default_factory=list)


@dataclass
class Instance:
    """A specific avatar in action, with its own drift vector and memory based on its unique
    experiences.
    """

    id: str
    archetype_id: int
    archetype_version: int
    name: str
    drift_vector: dict
    memory: dict
    session_count: int
    is_active: bool
    created_at: str
    last_session: str | None = None
    mood: str | None = None
    new_post_bias: float = 0.0


@dataclass
class Session:
    """A single session of avatar activity, capturing its engagements and interactions during that
    session."""

    id: int
    instance_id: str
    started_at: str
    posts_triaged: int
    posts_engaged: int
    comments_made: int
    votes_cast: int
    llm_calls: int
    ended_at: str | None = None
    phase: str | None = None
    summary: str | None = None
    error: str | None = None
