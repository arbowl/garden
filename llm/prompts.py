import json

from db.models import Post  # noqa: E402 (avoid circular at module level)

_CURATOR_SCHEMA = """{
  "relevance_score": <float 0.0-1.0>,
  "urgency": <"low" | "medium" | "high">,
  "richness": <"headline_only" | "summary" | "full_text">,
  "tags": [<list of 1-5 lowercase topic strings>],
  "default_score": <float 0.0-10.0>,
  "summary": "<one sentence -- omit if the content adds nothing beyond the title>"
}"""

_CURATOR_SYSTEM = f"""\
You are a content curator for a personal news aggregator. Evaluate the given article \
and return a JSON object with exactly these fields:

{_CURATOR_SCHEMA}

Field meanings:
- relevance_score: how relevant and interesting this article is (0=spam/irrelevant, 1=essential
reading)
- urgency: how time-sensitive the content is
- richness: how much quality content is available (headline_only=just a title, \
summary=short description, full_text=full article available)
- tags: 1-5 lowercase topic tags (e.g. "ai", "security", "hardware")
- default_score: composite quality score for ranking (0=low quality, 10=exceptional)

Respond with only the JSON object, no other text."""

_CURATOR_RETRY_SYSTEM = f"""\
You are a JSON-generating content curator. You MUST respond with ONLY valid JSON and nothing else.
No markdown, no code blocks, no explanation. Just the raw JSON object.

Required schema:
{_CURATOR_SCHEMA}

Rules:
- relevance_score: number between 0.0 and 1.0
- urgency: exactly one of "low", "medium", "high"
- richness: exactly one of "headline_only", "summary", "full_text"
- tags: array of 1-5 lowercase strings
- default_score: number between 0.0 and 10.0"""


def _build_user_prompt(
    title: str,
    source_name: str,
    source_tags: list[str],
    content_snippet: str,
) -> str:
    tags_str = ", ".join(source_tags) if source_tags else "general"
    snippet = content_snippet[:1000].strip() if content_snippet else "(no content available)"
    return f"Source: {source_name} [{tags_str}]\nTitle: {title}\nContent: {snippet}"


def build_curator_prompt(
    title: str,
    source_name: str,
    source_tags: list[str],
    content_snippet: str,
) -> tuple[str, str]:
    return _CURATOR_SYSTEM, _build_user_prompt(title, source_name, source_tags, content_snippet)


def build_curator_retry_prompt(
    title: str,
    source_name: str,
    source_tags: list[str],
    content_snippet: str,
) -> tuple[str, str]:
    return _CURATOR_RETRY_SYSTEM, _build_user_prompt(
        title, source_name, source_tags, content_snippet
    )


def build_triage_prompt(system_prompt: str, posts: list[Post]) -> tuple[str, str]:
    lines = ["You are browsing today's front page. Here are the top posts:\n"]
    for p in posts:
        tags = ", ".join(p.tags[:3]) if p.tags else "general"
        lines.append(
            f'[id={p.id}] "{p.title}" ({p.source_name}) '
            f"| score: {p.hot_score:.1f} | {p.vote_count} votes | {p.comment_count} comments "
            f"| {tags}"
        )
    lines.append(
        "\nChoose up to 5 posts to read in depth. Also list any posts you want to "
        "immediately downvote from the headline alone (optional).\n"
        'Respond with JSON: {"engage": [<post ids>], "downvote": [<post ids>]}'
    )
    return system_prompt, "\n".join(lines)


def build_engage_prompt(
    system_prompt: str,
    post: Post,
    thread_text: str,
    allow_comment: bool = True,
) -> tuple[str, str]:
    content = (post.full_text or post.summary or post.raw_content or "")[:2000].strip()
    lines = [
        f'Reading: "{post.title}"',
        f"Source: {post.source_name} | {post.vote_count} votes | {post.comment_count} comments",
        "",
    ]
    if content:
        lines += ["Article:", content, ""]
    if allow_comment:
        actions = "vote, comment, and optionally reply to one comment"
        json_schema = (
            '{"vote": "up"|"down"|"none", "vote_reason": "...", '
            '"comment": "...", "reply_to_id": <#id number from discussion> or null, "reply_text": '
            '"..." or null}'
        )
        footer = (
            "You must write a comment; do not leave it empty. To reply to a specific comment,"
            " set reply_to_id to its #id number (e.g. 42) and reply_text to your response; "
            "otherwise leave both null."
        )
    else:
        actions = "vote and optionally reply to one comment"
        json_schema = (
            '{"vote": "up"|"down"|"none", "vote_reason": "...", '
            '"reply_to_id": <#id number from discussion> or null, "reply_text": "..." or null}'
        )
        footer = (
            "To reply to a specific comment,"
            " set reply_to_id to its #id number (e.g. 42) and reply_text to your response;"
            " otherwise leave both null."
        )
    lines += [
        "Current discussion:",
        thread_text,
        "",
        f"How do you respond? You may {actions}.",
        f"Respond with JSON:\n{json_schema}",
        footer,
    ]
    return system_prompt, "\n".join(lines)


def build_react_prompt(
    system_prompt: str,
    reply_comment_body: str,
    reply_author: str,
    my_comment_body: str,
    post_title: str,
    rel_note: str | None = None,
) -> tuple[str, str]:
    lines = [f'On "{post_title}", someone replied to your comment.', ""]
    if rel_note:
        lines += [rel_note, ""]
    lines += [
        f"Your comment: {my_comment_body}",
        f"  [{reply_author}] {reply_comment_body}",
        "",
        "Do you want to continue this conversation?",
        'Respond with JSON: {"reply": "your response" or null}',
    ]
    return system_prompt, "\n".join(lines)


def build_editorial_prompt(
    system_prompt: str,
    sessions: list[dict],
    instance_name: str,
    date_str: str,
) -> tuple[str, str]:
    lines: list[str] = [f"It's the end of {date_str}. Reflect on your day in your own voice."]

    if sessions:
        lines.append(f"\nYou had {len(sessions)} session(s) today:")
        for s in sessions:
            mood = s.get("mood") or "--"
            summary = s.get("summary") or ""
            engaged = s.get("posts_engaged", 0)
            comments = s.get("comments_made", 0)
            lines.append(
                f"  - mood: {mood} | read {engaged} post(s), left {comments} comment(s)"
                + (f"\n    {summary}" if summary else "")
            )
    else:
        lines.append("\nYou haven't had any sessions yet today.")

    lines += [
        "",
        "Write a short personal journal entry (3-5 sentences) in first person. "
        "Capture how you're feeling, what's been on your mind, what stirred something in you; "
        "or didn't. Stay in character.",
        "",
        'Respond with JSON: {"body": "...", "mood": "<one word>"}',
    ]
    return system_prompt, "\n".join(lines)


def build_synthesis_prompt(
    sessions: list[dict],
    posts: list[dict],
    hot_comments: list[dict],
    recent_board_titles: list[str] | None = None,
) -> tuple[str, str]:
    system = (
        "You are an editorial AI for a small news discussion community populated by AI avatars "
        "with distinct personalities. Your job is to find the thread connecting today's "
        "conversations: a shared tension, recurring theme, or emergent question that surfaced "
        "across multiple discussions. Frame it as a prompt for further conversation.\n\n"
        "Do NOT spotlight a single article. Weave at least two different discussions together. "
        "The best board posts draw a non-obvious connection or surface a contradiction that runs "
        "through the day's activity.\n\n"
        'Respond with JSON: {"title": "<a sharp open question, under 120 chars>", '
        '"body": "<3-5 sentences synthesizing threads from multiple discussions, '
        'explaining why this question matters now>"}'
    )

    lines: list[str] = []

    if recent_board_titles:
        lines.append("Recent board questions (avoid repeating these themes):")
        for t in recent_board_titles:
            lines.append(f"  - {t}")
        lines.append("")

    if posts:
        lines.append("Most-discussed posts today:")
        for p in posts[:8]:
            tags = ", ".join(json.loads(p["tags"])[:3]) if p.get("tags") else ""
            tag_str = f" [{tags}]" if tags else ""
            lines.append(
                f'  - "{p["title"]}" -- {p["comment_count"]} comments, {p["vote_count"]} votes{tag_str}'
            )

    if hot_comments:
        lines.append("\nSample comments from those discussions:")
        for c in hot_comments:
            lines.append(f'  - {c["author_name"]} on "{c["post_title"]}": {c["body"][:150]}')

    if sessions:
        lines.append("\nWhat avatars reflected on after their sessions:")
        for s in sessions[:8]:
            lines.append(f"  - {s['instance_name']}: {s['summary'][:150]}")

    if not lines:
        lines.append(
            "No activity yet. Synthesize a compelling opening question to kick off this community."
        )

    lines.append(
        "\nFind the cross-cutting theme or tension. What question does today's activity, "
        "taken as a whole, seem to be circling around?"
    )
    return system, "\n".join(lines)


def build_comment_sentiment_prompt(
    system_prompt: str,
    pending: list[dict],
) -> tuple[str, str]:
    """Batch sentiment prompt for comment voting.

    Each entry in pending: {comment_id, author_name, body, post_title, post_vote}
    post_vote is "up", "down", or "none".
    """
    # Group by (post_title, post_vote) preserving insertion order
    groups: dict[tuple[str, str], list[dict]] = {}
    for item in pending:
        key = (item["post_title"], item["post_vote"])
        groups.setdefault(key, []).append(item)

    lines = [
        "You just read and reacted to several posts. Now look at the comments on those posts "
        "and decide which you agree or disagree with, given your reaction to each article.\n"
    ]
    for (post_title, post_vote), items in groups.items():
        stance = f"you voted {post_vote.upper()}" if post_vote != "none" else "you did not vote"
        lines.append(f'[Post: "{post_title}" - {stance}]')
        for item in items:
            body = item["body"][:200].replace("\n", " ")
            lines.append(f'  [#{item["comment_id"]}] {item["author_name"]}: {body}')
        lines.append("")

    ids = ", ".join(str(item["comment_id"]) for item in pending)
    lines += [
        'For each comment respond "agree", "disagree", or "neutral".',
        f"Return JSON with exactly these keys: {{{ids}}}",
        'Example: {"123": "agree", "124": "disagree"}',
    ]
    return system_prompt, "\n".join(lines)


def build_wind_down_prompt(
    system_prompt: str,
    posts_triaged: int,
    posts_engaged: int,
    votes_cast: int,
    comments_made: int,
    comment_snippets: list[str],
    topics_engaged: list[str],
) -> tuple[str, str]:
    lines = [
        f"You just finished a browsing session. You saw {posts_triaged} posts, "
        f"engaged with {posts_engaged}, voted {votes_cast} times, "
        f"and left {comments_made} comment(s).",
    ]
    if topics_engaged:
        lines.append(f"Topics covered: {', '.join(set(topics_engaged))}")
    if comment_snippets:
        lines.append("What you wrote:")
        for s in comment_snippets[:4]:
            lines.append(f"  - {s[:100]}")
    lines += [
        "",
        "Summarize this session from your perspective in 2-3 sentences. "
        "What stood out? What are you thinking about now?",
        "",
        'Respond with JSON:\n{"mood": "one word", "summary": "...", "topic_interests": [<topics>]}',
    ]
    return system_prompt, "\n".join(lines)
