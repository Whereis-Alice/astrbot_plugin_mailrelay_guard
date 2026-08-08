from __future__ import annotations

import asyncio
import unittest

from astrbot_plugin_mailrelay_guard.mailrelay_guard.drafts import (
    DraftAccessError,
    DraftStore,
)
from astrbot_plugin_mailrelay_guard.mailrelay_guard.rate_limit import (
    SuccessWindowRateLimiter,
)


class DraftAndRateLimitTests(unittest.TestCase):
    def test_drafts_are_bound_to_their_creator_and_conversation(self) -> None:
        async def scenario() -> None:
            store = DraftStore()
            draft = await store.create(
                actor_id="owner",
                unified_msg_origin="session-a",
                recipients=["receiver@example.com"],
                subject="Subject",
                body="Body",
                ttl_seconds=600,
                max_pending_for_actor=3,
            )

            fetched = await store.get_for_actor(
                draft.token,
                actor_id="owner",
                unified_msg_origin="session-a",
            )
            self.assertEqual(fetched.token, draft.token)

            with self.assertRaisesRegex(DraftAccessError, "创建它的用户"):
                await store.get_for_actor(
                    draft.token,
                    actor_id="other",
                    unified_msg_origin="session-a",
                )

        asyncio.run(scenario())

    def test_success_window_only_blocks_after_success_is_recorded(self) -> None:
        limiter = SuccessWindowRateLimiter()

        self.assertTrue(limiter.can_send(max_messages=1, window_seconds=3600))
        limiter.record_success()
        self.assertFalse(limiter.can_send(max_messages=1, window_seconds=3600))
        self.assertGreater(limiter.remaining_seconds(window_seconds=3600), 0)
