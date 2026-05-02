"""Utilities for flattening a comment thread into text for LLM input."""

from db.models import Comment


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _score_chain(chain: list[Comment], instance_name: str | None) -> float:
    score = 0.0
    if not instance_name:
        return score

    instance_ids = {c.id for c in chain if c.author_name == instance_name}

    for c in chain:
        if c.author_name == instance_name:
            score += 20.0
        if c.parent_comment_id in instance_ids and c.author_name != instance_name:
            score += 15.0
        score += abs(c.vote_count) * 0.1

    return score


def _collect_chain(
    root: Comment,
    by_parent: dict[int | None, list[Comment]],
    max_depth: int,
    max_per_chain: int,
) -> list[Comment]:
    chain: list[Comment] = []
    stack = [(root, 0)]
    while stack and len(chain) < max_per_chain:
        node, depth = stack.pop(0)
        if depth > max_depth:
            continue
        chain.append(node)
        for child in by_parent.get(node.id, []):
            stack.append((child, depth + 1))
    return chain


def _format_comment(comment: Comment) -> str:
    indent = "  " * comment.depth
    return f"{indent}[#{comment.id} {comment.author_name}] {comment.body}"


def _format_chain(chain: list[Comment]) -> str:
    return "\n".join(_format_comment(c) for c in chain)


def flatten_thread(
    comments: list[Comment],
    instance_name: str | None = None,
    max_depth: int = 3,
    max_chains: int = 6,
    max_per_chain: int = 4,
    token_budget: int = 800,
) -> str:
    if not comments:
        return "(no comments yet)"

    by_parent: dict[int | None, list[Comment]] = {}
    for c in comments:
        by_parent.setdefault(c.parent_comment_id, []).append(c)

    roots = sorted(by_parent.get(None, []), key=lambda c: c.created_at)

    scored: list[tuple[float, list[Comment]]] = []
    for root in roots:
        chain = _collect_chain(root, by_parent, max_depth, max_per_chain)
        score = _score_chain(chain, instance_name)
        scored.append((score, chain))

    scored.sort(key=lambda x: -x[0])
    top_chains = scored[:max_chains]

    budget_remaining = token_budget
    sections: list[str] = []

    for _, chain in top_chains:
        text = _format_chain(chain)
        tokens = _estimate_tokens(text)
        if tokens > budget_remaining:
            break
        sections.append(text)
        budget_remaining -= tokens

    return "\n\n".join(sections) if sections else "(no comments yet)"
