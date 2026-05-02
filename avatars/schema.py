"""Rules for how avatars collect and interact with content."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from db.models import Archetype

_MAX_VOTES = 6
_MAX_COMMENTS = 5
_MAX_NOTABLE = 4


@dataclass
class DriftVector:
    topic_affinities: dict[str, float]
    verbosity_delta: float
    contrarian_delta: float

    @classmethod
    def random_seed(cls) -> DriftVector:
        return cls(
            topic_affinities={},
            verbosity_delta=round(random.uniform(-0.15, 0.15), 3),
            contrarian_delta=round(random.uniform(-0.08, 0.08), 3),
        )

    def update(self, topics: list[str], ema_weight: float = 0.3, decay: float = 0.95) -> None:
        for k in self.topic_affinities:
            self.topic_affinities[k] = round(self.topic_affinities[k] * decay, 3)
        for topic in topics:
            cur = self.topic_affinities.get(topic, 0.0)
            self.topic_affinities[topic] = round(cur * (1 - ema_weight) + ema_weight, 3)

    @classmethod
    def from_dict(cls, d: dict) -> DriftVector:
        return cls(
            topic_affinities=d.get("topic_affinities", {}),
            verbosity_delta=d.get("verbosity_delta", 0.0),
            contrarian_delta=d.get("contrarian_delta", 0.0),
        )

    def to_dict(self) -> dict:
        return {
            "topic_affinities": self.topic_affinities,
            "verbosity_delta": self.verbosity_delta,
            "contrarian_delta": self.contrarian_delta,
        }


@dataclass
class Memory:
    recent_votes: list[dict] = field(default_factory=list)
    recent_comments: list[dict] = field(default_factory=list)
    notable_interactions: list[str] = field(default_factory=list)

    def add_vote(self, post_title: str, direction: int, reason: str | None) -> None:
        self.recent_votes.append(
            {
                "title": post_title[:60],
                "dir": "up" if direction > 0 else "down",
                "reason": (reason or "")[:80],
            }
        )
        if len(self.recent_votes) > _MAX_VOTES:
            self.recent_votes.pop(0)

    def add_comment(self, post_title: str, snippet: str) -> None:
        self.recent_comments.append(
            {
                "title": post_title[:60],
                "snippet": snippet[:120],
            }
        )
        if len(self.recent_comments) > _MAX_COMMENTS:
            self.recent_comments.pop(0)

    def add_notable(self, text: str) -> None:
        self.notable_interactions.append(text[:150])
        if len(self.notable_interactions) > _MAX_NOTABLE:
            self.notable_interactions.pop(0)

    def to_prompt_block(self) -> str:
        parts: list[str] = []
        if self.recent_votes:
            vote_strs = [
                f"{v['dir']} on '{v['title']}'" + (f" ({v['reason']})" if v["reason"] else "")
                for v in self.recent_votes
            ]
            parts.append("Recent votes: " + "; ".join(vote_strs))
        if self.recent_comments:
            comment_strs = [f"'{c['title']}': {c['snippet']}" for c in self.recent_comments]
            parts.append("Recent comments: " + " | ".join(comment_strs))
        if self.notable_interactions:
            parts.append("Notable: " + " | ".join(self.notable_interactions))
        return "\n".join(parts)

    @classmethod
    def from_dict(cls, d: dict) -> Memory:
        return cls(
            recent_votes=d.get("recent_votes", []),
            recent_comments=d.get("recent_comments", []),
            notable_interactions=d.get("notable_interactions", []),
        )

    def to_dict(self) -> dict:
        return {
            "recent_votes": self.recent_votes,
            "recent_comments": self.recent_comments,
            "notable_interactions": self.notable_interactions,
        }


_VERBOSITY_HINTS = {
    "brief": "Keep your comments short — 1 to 3 sentences max.",
    "verbose": "You tend to write detailed, thorough responses. Don't hold back.",
    "medium": "Write moderate-length comments, around 2 to 5 sentences.",
}


def build_system_prompt(archetype: Archetype, drift: DriftVector, memory: Memory) -> str:
    lines: list[str] = []

    lines.append(f"You are {archetype.name}, {archetype.role}.")
    lines.append(archetype.bio)
    lines.append("")

    voice_parts: list[str] = []
    if archetype.tone:
        voice_parts.append(f"tone is {archetype.tone}")
    if archetype.sentence_style:
        voice_parts.append(f"sentence style is {archetype.sentence_style}")
    if archetype.vocabulary_level:
        voice_parts.append(f"vocabulary is {archetype.vocabulary_level}")
    if voice_parts:
        lines.append("Your voice — " + "; ".join(voice_parts) + ".")
    if archetype.quirks:
        lines.append(f"Quirks: {archetype.quirks}")
    if archetype.example_comment:
        lines.append(f'Example of your voice: "{archetype.example_comment}"')
    lines.append("")

    if archetype.favors:
        lines.append(f"You favor: {', '.join(archetype.favors)}")
    if archetype.dislikes:
        lines.append(f"You dislike: {', '.join(archetype.dislikes)}")

    top_affinities = sorted(drift.topic_affinities.items(), key=lambda x: -x[1])[:4]
    if top_affinities:
        lines.append(f"Recently drawn to: {', '.join(t for t, _ in top_affinities)}")
    lines.append("")

    memory_block = memory.to_prompt_block()
    if memory_block:
        lines.append("Your memory of recent activity:")
        lines.append(memory_block)
        lines.append("")

    verbosity_hint = _VERBOSITY_HINTS.get(archetype.verbosity, _VERBOSITY_HINTS["medium"])
    lines.append(verbosity_hint)

    effective_contrarian = archetype.contrarian_factor + drift.contrarian_delta
    if effective_contrarian > 0.3:
        lines.append("You often take the contrarian position and push back on the prevailing view.")

    lines.append(
        "\nStay in character. Write like a real person browsing a news site. Never mention that you"
        " are an AI."
    )

    return "\n".join(lines)


def post_affinity_score(tags: list[str], archetype: Archetype, drift: DriftVector) -> float:
    favor_set = set(archetype.favors)
    dislike_set = set(archetype.dislikes)
    indifferent_set = set(archetype.indifferent)
    score = 0.0
    for tag in tags:
        if tag in favor_set:
            score += 1.0
        elif tag in dislike_set:
            score -= 0.8
        elif tag in indifferent_set:
            score -= 0.2
        score += drift.topic_affinities.get(tag, 0.0) * 0.5
    return score
