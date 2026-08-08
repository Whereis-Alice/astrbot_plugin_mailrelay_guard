"""Ephemeral, session-bound mail drafts used by the LLM confirmation flow."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass


class DraftAccessError(ValueError):
    """Raised when a draft is expired, absent, or belongs to another session."""


@dataclass(frozen=True)
class PendingDraft:
    """A sensitive draft retained only in memory until confirmed or expired."""

    token: str
    actor_id: str
    unified_msg_origin: str
    recipients: tuple[str, ...]
    subject: str
    body: str
    expires_at: float


class DraftStore:
    """Store a small number of unpersisted drafts per actor and conversation."""

    def __init__(self) -> None:
        self._drafts: dict[str, PendingDraft] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        actor_id: str,
        unified_msg_origin: str,
        recipients: list[str],
        subject: str,
        body: str,
        ttl_seconds: int,
        max_pending_for_actor: int,
    ) -> PendingDraft:
        async with self._lock:
            now = time.monotonic()
            self._purge_expired(now)
            scoped = [
                draft
                for draft in self._drafts.values()
                if draft.actor_id == actor_id
                and draft.unified_msg_origin == unified_msg_origin
            ]
            if len(scoped) >= max_pending_for_actor:
                raise DraftAccessError(
                    f"当前会话最多保留 {max_pending_for_actor} 封待确认草稿，请先确认或取消已有草稿。"
                )

            token = self._new_token()
            draft = PendingDraft(
                token=token,
                actor_id=actor_id,
                unified_msg_origin=unified_msg_origin,
                recipients=tuple(recipients),
                subject=subject,
                body=body,
                expires_at=now + ttl_seconds,
            )
            self._drafts[token] = draft
            return draft

    async def get_for_actor(
        self,
        token: str,
        *,
        actor_id: str,
        unified_msg_origin: str,
    ) -> PendingDraft:
        normalized = token.strip().lower()
        async with self._lock:
            self._purge_expired(time.monotonic())
            draft = self._drafts.get(normalized)
            if draft is None:
                raise DraftAccessError("草稿不存在或已过期。")
            if (
                draft.actor_id != actor_id
                or draft.unified_msg_origin != unified_msg_origin
            ):
                raise DraftAccessError("该草稿只能由创建它的用户在原会话中确认。")
            return draft

    async def remove_for_actor(
        self,
        token: str,
        *,
        actor_id: str,
        unified_msg_origin: str,
    ) -> None:
        draft = await self.get_for_actor(
            token,
            actor_id=actor_id,
            unified_msg_origin=unified_msg_origin,
        )
        async with self._lock:
            self._drafts.pop(draft.token, None)

    @staticmethod
    def _new_token() -> str:
        return secrets.token_hex(4)

    def _purge_expired(self, now: float) -> None:
        expired = [
            token for token, draft in self._drafts.items() if draft.expires_at <= now
        ]
        for token in expired:
            self._drafts.pop(token, None)
