import asyncio
import logging
import random
import time
from dataclasses import dataclass, field

import aiosqlite

from avatars.schema import DriftVector, Memory, build_system_prompt, post_affinity_score
from config import settings
from web.broadcaster import broadcaster
from avatars.threading import flatten_thread
from db.models import Archetype, Instance, Post
from db.queries import (
    count_instance_comments_on_post,
    get_active_board_post,
    get_archetype,
    get_avatar_commented_post_ids,
    get_comments_for_post,
    get_hot_posts,
    get_instance,
    get_pending_replies,
    get_unresolved_mention_posts_for_instance,
    insert_comment,
    insert_notification,
    insert_session,
    insert_vote,
    resolve_mentions_for_instance,
    update_instance_post_session,
    update_session,
)
from llm.client import OllamaClient
from llm.parsing import (
    parse_engagement_response,
    parse_react_response,
    parse_triage_response,
    parse_wind_down_response,
)
from llm.prompts import (
    build_engage_prompt,
    build_react_prompt,
    build_triage_prompt,
    build_wind_down_prompt,
)
from db.models import AuthorType

logger = logging.getLogger(__name__)

_AFFINITY_WEIGHT = 1.0  # additive boost per matched-favor tag in pool ranking
_MIN_ENGAGE = 2  # floor: force this many posts into engage_set if triage comes up short


@dataclass
class PostContext:
    post: Post
    must_engage: bool = False  # bypass triage selection, always enter engage phase
    must_comment: bool = False  # override comment_threshold, force a comment attempt


@dataclass
class SessionStats:
    session_id: int
    instance_id: str
    posts_triaged: int = 0
    posts_engaged: int = 0
    comments_made: int = 0
    votes_cast: int = 0
    llm_calls: int = 0
    comment_snippets: list[str] = field(default_factory=list)
    topics_engaged: list[str] = field(default_factory=list)


class AvatarSession:
    def __init__(
        self,
        db: aiosqlite.Connection,
        disciplined: OllamaClient,
        creative: OllamaClient,
        instance_id: str,
        max_seconds: int = 90,
    ):
        self.db = db
        self.disciplined = disciplined
        self.creative = creative
        self.instance_id = instance_id
        self.max_seconds = max_seconds
        self._start_time: float = 0.0

    def _over_budget(self) -> bool:
        return (time.monotonic() - self._start_time) > self.max_seconds

    async def _llm(
        self, client: OllamaClient, system: str, user: str, temperature: float = 0.7
    ) -> str | None:
        try:
            result = await client.chat(system, user, temperature=temperature)
            return result
        except Exception as e:
            logger.error("LLM error in session %s: %s", self.instance_id, e)
            return None

    async def run(self) -> bool:
        self._start_time = time.monotonic()

        instance = await get_instance(self.db, self.instance_id)
        if not instance:
            logger.error("Instance %s not found", self.instance_id)
            return False

        archetype = await get_archetype(self.db, instance.archetype_id)
        if not archetype:
            logger.error(
                "Archetype %d not found for instance %s", instance.archetype_id, self.instance_id
            )
            return False

        drift = DriftVector.from_dict(instance.drift_vector)
        memory = Memory.from_dict(instance.memory)
        system_prompt = build_system_prompt(archetype, drift, memory)

        session_id = await insert_session(self.db, self.instance_id)
        stats = SessionStats(session_id=session_id, instance_id=self.instance_id)
        logger.info("[%s] session %d started", instance.name, session_id)

        try:
            await self._run_phases(instance, archetype, drift, memory, system_prompt, stats)
        except Exception as e:
            logger.exception("[%s] session error: %s", instance.name, e)
            await update_session(self.db, session_id, error=str(e), ended=True)
            return False

        wind_down_result = await self._wind_down(
            system_prompt, stats, temperature=archetype.temperature
        )
        mood = wind_down_result.mood if wind_down_result else None
        summary = wind_down_result.summary if wind_down_result else None
        new_topics = wind_down_result.topic_interests if wind_down_result else []

        drift.update(stats.topics_engaged + new_topics)
        if wind_down_result:
            memory.add_notable(wind_down_result.summary[:150])

        await update_instance_post_session(
            self.db,
            self.instance_id,
            memory=memory.to_dict(),
            drift_vector=drift.to_dict(),
            mood=mood,
        )
        await update_session(
            self.db,
            session_id,
            phase="done",
            posts_triaged=stats.posts_triaged,
            posts_engaged=stats.posts_engaged,
            comments_made=stats.comments_made,
            votes_cast=stats.votes_cast,
            llm_calls=stats.llm_calls,
            summary=summary,
            ended=True,
        )
        logger.info(
            "[%s] session %d done — triaged=%d engaged=%d comments=%d votes=%d mood=%s",
            instance.name,
            session_id,
            stats.posts_triaged,
            stats.posts_engaged,
            stats.comments_made,
            stats.votes_cast,
            mood,
        )
        return True

    async def _run_phases(
        self,
        instance: Instance,
        archetype: Archetype,
        drift: DriftVector,
        memory: Memory,
        system_prompt: str,
        stats: SessionStats,
    ) -> None:
        all_posts = await get_hot_posts(
            self.db, limit=30, max_per_source=settings.max_posts_per_source
        )
        board_post = await get_active_board_post(self.db)
        if board_post and board_post.id not in {p.id for p in all_posts}:
            all_posts.insert(0, board_post)

        mentioned_posts = await get_unresolved_mention_posts_for_instance(self.db, self.instance_id)
        mention_post_ids = {mp.id for mp in mentioned_posts}
        existing_ids = {p.id for p in all_posts}
        for mp in mentioned_posts:
            if mp.id not in existing_ids:
                all_posts.insert(0, mp)
                existing_ids.add(mp.id)

        commented_ids = await get_avatar_commented_post_ids(self.db, self.instance_id)
        posts = sorted(
            [p for p in all_posts if p.comment_count < settings.max_post_comments],
            key=lambda p: (
                p.hot_score * (0.5 if p.id in commented_ids else 1.0)
                + post_affinity_score(p.tags, archetype, drift) * _AFFINITY_WEIGHT
            ),
            reverse=True,
        )[:15]

        if board_post and board_post.id not in {p.id for p in posts}:
            if board_post.comment_count < settings.max_post_comments:
                posts = [board_post] + posts[:14]

        for mp in mentioned_posts:
            if mp.id not in {p.id for p in posts} and mp.comment_count < settings.max_post_comments:
                posts = [mp] + posts[:14]

        board_id = board_post.id if board_post else None
        post_contexts = [
            PostContext(
                post=p,
                must_engage=p.id in mention_post_ids or p.id == board_id,
                must_comment=p.id in mention_post_ids,
            )
            for p in posts
        ]
        stats.posts_triaged = len(post_contexts)

        await update_session(
            self.db, stats.session_id, phase="triage", posts_triaged=len(post_contexts)
        )

        triage = await self._triage(
            system_prompt,
            [ctx.post for ctx in post_contexts],
            stats,
            temperature=archetype.temperature,
        )
        if not triage:
            return

        engage_set = set(triage.engage[:5])
        downvote_set = set(triage.downvote)

        for ctx in post_contexts:
            if ctx.must_engage:
                engage_set.add(ctx.post.id)

        if len(engage_set) < _MIN_ENGAGE:
            by_affinity = sorted(
                post_contexts,
                key=lambda ctx: post_affinity_score(ctx.post.tags, archetype, drift),
                reverse=True,
            )
            for ctx in by_affinity:
                if len(engage_set) >= _MIN_ENGAGE:
                    break
                engage_set.add(ctx.post.id)
            logger.debug(
                "[%s] engagement floor applied — %d posts forced", instance.name, len(engage_set)
            )

        for ctx in post_contexts:
            if ctx.post.id in downvote_set and random.random() < archetype.vote_probability:
                await insert_vote(
                    self.db,
                    voter_type=AuthorType.AVATAR,
                    direction=-1,
                    post_id=ctx.post.id,
                    voter_id=self.instance_id,
                    reason="drive-by downvote from headline",
                )
                stats.votes_cast += 1
                memory.add_vote(ctx.post.title, -1, "skimmed headline")

        await update_session(self.db, stats.session_id, phase="engage")

        for ctx in post_contexts:
            if ctx.post.id not in engage_set:
                continue
            if self._over_budget():
                logger.info("[%s] time budget reached, skipping remaining engage", instance.name)
                break
            await self._engage(
                instance,
                archetype,
                memory,
                system_prompt,
                ctx,
                stats,
                mention_post_ids=mention_post_ids,
            )
            stats.posts_engaged += 1
            await update_session(self.db, stats.session_id, posts_engaged=stats.posts_engaged)

        await update_session(self.db, stats.session_id, phase="react")

        if not self._over_budget():
            await self._react(instance, archetype, memory, system_prompt, stats)

    async def _triage(
        self,
        system_prompt: str,
        posts: list[Post],
        stats: SessionStats,
        temperature: float = 0.7,
    ):
        system, user = build_triage_prompt(system_prompt, posts)
        raw = await self._llm(self.disciplined, system, user, temperature=temperature)
        stats.llm_calls += 1
        if not raw:
            return None
        result = parse_triage_response(raw)
        if not result:
            logger.warning("[%s] triage parse failed", self.instance_id)
        return result

    async def _engage(
        self,
        instance: Instance,
        archetype: Archetype,
        memory: Memory,
        system_prompt: str,
        post_ctx: PostContext,
        stats: SessionStats,
        mention_post_ids: set[int] | None = None,
    ) -> None:
        post = post_ctx.post
        comments = await get_comments_for_post(self.db, post.id)
        thread_text = flatten_thread(comments, instance_name=instance.name)

        allow_comment = post_ctx.must_comment or random.random() < archetype.comment_threshold
        system, user = build_engage_prompt(
            system_prompt, post, thread_text, allow_comment=allow_comment
        )
        raw = await self._llm(self.creative, system, user, temperature=archetype.temperature)
        stats.llm_calls += 1
        if not raw:
            return

        result = parse_engagement_response(raw)
        if not result:
            logger.warning("[%s] engage parse failed for post %d", self.instance_id, post.id)
            return

        if result.vote != "none" and random.random() < archetype.vote_probability:
            direction = 1 if result.vote == "up" else -1
            await insert_vote(
                self.db,
                voter_type=AuthorType.AVATAR,
                direction=direction,
                post_id=post.id,
                voter_id=self.instance_id,
                reason=result.vote_reason[:200] if result.vote_reason else None,
            )
            stats.votes_cast += 1
            memory.add_vote(post.title, direction, result.vote_reason)

        for c in comments[:10]:
            if c.author_id == self.instance_id:
                continue
            if random.random() >= archetype.vote_probability:
                continue
            direction = -1 if random.random() < archetype.contrarian_factor else 1
            await insert_vote(
                self.db,
                voter_type=AuthorType.AVATAR,
                direction=direction,
                comment_id=c.id,
                voter_id=self.instance_id,
            )
            stats.votes_cast += 1

        if post.tags:
            stats.topics_engaged.extend(post.tags[:2])

        parent_id: int | None = None
        if (
            result.reply_to_id
            and result.reply_text
            and random.random() < archetype.reply_probability
        ):
            valid_ids = {c.id for c in comments if c.author_id != self.instance_id}
            if result.reply_to_id in valid_ids:
                parent_id = result.reply_to_id

        body: str | None = None
        if parent_id and result.reply_text:
            body = result.reply_text
        elif result.comment:
            body = result.comment

        if (
            body
            and parent_id is None
            and any(
                c.author_id == self.instance_id and c.parent_comment_id is None for c in comments
            )
        ):
            logger.debug(
                "[%s] skipping top-level on post %d — already has one", instance.name, post.id
            )
            return

        if body:
            comment_id = await insert_comment(
                self.db,
                post_id=post.id,
                author_type=AuthorType.AVATAR,
                author_name=instance.name,
                body=body,
                parent_comment_id=parent_id,
                author_id=self.instance_id,
            )
            if comment_id is None:
                logger.debug("[%s] post %d is locked, discarding comment", instance.name, post.id)
                return
            stats.comments_made += 1
            stats.comment_snippets.append(body[:100])
            memory.add_comment(post.title, body[:120])
            logger.info(
                "[%s] commented on post %d (comment %d)", instance.name, post.id, comment_id
            )
            if mention_post_ids and post.id in mention_post_ids:
                await resolve_mentions_for_instance(self.db, self.instance_id, [post.id])

            if parent_id:
                parent = next((c for c in comments if c.id == parent_id), None)
                if parent and parent.author_type == AuthorType.HUMAN:
                    await insert_notification(
                        self.db,
                        avatar_name=instance.name,
                        post_id=post.id,
                        post_title=post.title,
                        comment_id=comment_id,
                        body=body,
                    )
                    await broadcaster.publish(
                        {
                            "type": "reply_to_you",
                            "instance_name": instance.name,
                            "post_title": post.title,
                            "body": body[:200],
                        }
                    )

    async def _react(
        self,
        instance: Instance,
        archetype: Archetype,
        memory: Memory,
        system_prompt: str,
        stats: SessionStats,
    ) -> None:
        pending = await get_pending_replies(
            self.db, self.instance_id, max_depth=settings.max_reply_depth
        )
        if not pending:
            return

        for reply in pending[:3]:
            if self._over_budget():
                break
            if (
                reply.author_type != AuthorType.HUMAN
                and random.random() > archetype.reply_probability
            ):
                continue
            existing = await count_instance_comments_on_post(
                self.db, self.instance_id, reply.post_id
            )
            if existing >= settings.max_replies_per_post:
                logger.debug(
                    "[%s] skipping reply on post %d — at reply cap (%d)",
                    instance.name,
                    reply.post_id,
                    existing,
                )
                continue

            async with self.db.execute(
                "SELECT body FROM comments WHERE id = ?", (reply.parent_comment_id,)
            ) as cur:
                row = await cur.fetchone()
            my_body = row["body"] if row else "(unknown)"

            async with self.db.execute(
                "SELECT title FROM posts WHERE id = ?", (reply.post_id,)
            ) as cur:
                row = await cur.fetchone()
            post_title = row["title"] if row else "(unknown)"

            system, user = build_react_prompt(
                system_prompt,
                reply_comment_body=reply.body,
                reply_author=reply.author_name,
                my_comment_body=my_body,
                post_title=post_title,
            )
            raw = await self._llm(self.creative, system, user, temperature=archetype.temperature)
            stats.llm_calls += 1
            if not raw:
                continue

            result = parse_react_response(raw)
            if not result or not result.reply:
                continue

            new_comment_id = await insert_comment(
                self.db,
                post_id=reply.post_id,
                author_type=AuthorType.AVATAR,
                author_name=instance.name,
                body=result.reply,
                parent_comment_id=reply.id,
                author_id=self.instance_id,
            )
            if new_comment_id is None:
                logger.debug(
                    "[%s] post %d is locked, discarding reply", instance.name, reply.post_id
                )
                continue
            stats.comments_made += 1
            stats.comment_snippets.append(result.reply[:100])
            memory.add_comment(post_title, result.reply[:120])
            logger.info(
                "[%s] replied to %s on post %d", instance.name, reply.author_name, reply.post_id
            )
            if reply.author_type == AuthorType.HUMAN:
                await insert_notification(
                    self.db,
                    avatar_name=instance.name,
                    post_id=reply.post_id,
                    post_title=post_title,
                    comment_id=new_comment_id,
                    body=result.reply,
                )
                await broadcaster.publish(
                    {
                        "type": "reply_to_you",
                        "instance_name": instance.name,
                        "post_title": post_title,
                        "body": result.reply[:200],
                    }
                )

    async def _wind_down(self, system_prompt: str, stats: SessionStats, temperature: float = 0.7):
        system, user = build_wind_down_prompt(
            system_prompt,
            posts_triaged=stats.posts_triaged,
            posts_engaged=stats.posts_engaged,
            votes_cast=stats.votes_cast,
            comments_made=stats.comments_made,
            comment_snippets=stats.comment_snippets,
            topics_engaged=stats.topics_engaged,
        )
        raw = await self._llm(self.creative, system, user, temperature=temperature)
        stats.llm_calls += 1
        if not raw:
            return None
        result = parse_wind_down_response(raw)
        if not result:
            logger.warning("[%s] wind-down parse failed", self.instance_id)
        return result
