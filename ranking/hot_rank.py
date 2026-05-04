from datetime import UTC, datetime


def compute_score(score: float, last_activity: str, gravity: float = 1.8) -> float:
    """Time-decay hot-rank: score / (hours_since_last_activity + 2) ^ gravity"""
    dt = datetime.fromisoformat(last_activity)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    hours = max(0.0, (datetime.now(UTC) - dt).total_seconds() / 3600)
    return score / (hours + 2) ** gravity


def rank_hot_comments(comments: list[dict], limit: int = 10) -> list[dict]:
    scored = [(compute_score(c["vote_count"] + 1, c["created_at"]), c) for c in comments]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:limit]]
