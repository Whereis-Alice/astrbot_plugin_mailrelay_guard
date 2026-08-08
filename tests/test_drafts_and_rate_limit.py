from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_mailrelay_guard.mailrelay_guard.bindings import (
    MailboxBindingError,
    MailboxBindingStore,
)
from astrbot_plugin_mailrelay_guard.mailrelay_guard.config import load_settings
from astrbot_plugin_mailrelay_guard.mailrelay_guard.identity import (
    SelfMailboxResolver,
    get_actor_identity,
)
from astrbot_plugin_mailrelay_guard.mailrelay_guard.rate_limit import (
    KeyedWindowRateLimiter,
    SuccessWindowRateLimiter,
)


class FakeNapCat:
    def __init__(self, stranger_result, friend_result=None) -> None:
        self.stranger_result = stranger_result
        self.friend_result = friend_result
        self.calls = []

    async def call_action(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["action"] == "get_stranger_info":
            return self.stranger_result
        if kwargs["action"] == "get_friend_list":
            return self.friend_result
        raise AssertionError(f"Unexpected action: {kwargs}")


class FakeEvent:
    def __init__(self, *, sender_id="10001", bot=None) -> None:
        self._sender_id = sender_id
        self.bot = bot

    def get_sender_id(self) -> str:
        return self._sender_id

    @staticmethod
    def get_platform_id() -> str:
        return "aiocqhttp"

    @staticmethod
    def get_platform_name() -> str:
        return "aiocqhttp"


class BindingIdentityAndRateLimitTests(unittest.TestCase):
    def test_binding_is_verified_before_persistence(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary_dir:
                store = MailboxBindingStore(Path(temporary_dir))
                await store.issue_challenge(
                    actor_key="aiocqhttp:10001",
                    address="user@example.com",
                    code="123456",
                    ttl_seconds=600,
                    resend_seconds=60,
                )
                with self.assertRaisesRegex(MailboxBindingError, "??????"):
                    await store.verify(
                        actor_key="aiocqhttp:10001",
                        code="000000",
                        max_attempts=5,
                    )
                binding = await store.verify(
                    actor_key="aiocqhttp:10001",
                    code="123456",
                    max_attempts=5,
                )
                self.assertEqual(binding.address, "user@example.com")

                reloaded = MailboxBindingStore(Path(temporary_dir))
                persisted = await reloaded.get("aiocqhttp:10001")
                self.assertIsNotNone(persisted)
                self.assertEqual(persisted.address, "user@example.com")

        asyncio.run(scenario())

    def test_binding_code_expires_after_its_attempt_budget_is_used(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary_dir:
                store = MailboxBindingStore(Path(temporary_dir))
                await store.issue_challenge(
                    actor_key="aiocqhttp:10001",
                    address="user@example.com",
                    code="123456",
                    ttl_seconds=600,
                    resend_seconds=60,
                )
                with self.assertRaisesRegex(MailboxBindingError, "???? 1 ?"):
                    await store.verify(
                        actor_key="aiocqhttp:10001",
                        code="000000",
                        max_attempts=2,
                    )
                with self.assertRaisesRegex(MailboxBindingError, "??????"):
                    await store.verify(
                        actor_key="aiocqhttp:10001",
                        code="111111",
                        max_attempts=2,
                    )
                with self.assertRaisesRegex(MailboxBindingError, "?????"):
                    await store.verify(
                        actor_key="aiocqhttp:10001",
                        code="123456",
                        max_attempts=2,
                    )

        asyncio.run(scenario())

    def test_napcat_profile_lookup_reads_only_current_sender(self) -> None:
        async def scenario() -> None:
            bot = FakeNapCat({"user_id": "10001", "eMail": "friend@qq.com"})
            event = FakeEvent(bot=bot)
            actor = get_actor_identity(event)
            self.assertIsNotNone(actor)
            resolver = SelfMailboxResolver(None)
            mailbox = await resolver.resolve(event, load_settings({}), actor)

            self.assertIsNotNone(mailbox)
            self.assertEqual(mailbox.address, "friend@qq.com")
            self.assertEqual(mailbox.source, "napcat_profile")
            self.assertEqual(len(bot.calls), 1)
            self.assertEqual(bot.calls[0]["user_id"], "10001")

        asyncio.run(scenario())

    def test_friend_list_is_only_a_fallback_and_matches_current_sender(self) -> None:
        async def scenario() -> None:
            bot = FakeNapCat(
                {"user_id": "10001"},
                [
                    {"user_id": "99999", "email": "other@example.com"},
                    {"user_id": "10001", "email": "self@example.com"},
                ],
            )
            event = FakeEvent(bot=bot)
            actor = get_actor_identity(event)
            resolver = SelfMailboxResolver(None)
            mailbox = await resolver.resolve(
                event,
                load_settings({"napcat_friend_list_fallback_enabled": True}),
                actor,
            )

            self.assertIsNotNone(mailbox)
            self.assertEqual(mailbox.address, "self@example.com")
            self.assertEqual(mailbox.source, "napcat_friend_profile")
            self.assertEqual(
                [call["action"] for call in bot.calls],
                ["get_stranger_info", "get_friend_list"],
            )

        asyncio.run(scenario())

    def test_qq_address_is_only_derived_when_explicitly_enabled(self) -> None:
        async def scenario() -> None:
            event = FakeEvent(bot=FakeNapCat({}))
            actor = get_actor_identity(event)
            settings = load_settings(
                {
                    "allow_qq_mailbox_derivation": True,
                    "napcat_email_lookup_enabled": False,
                }
            )
            mailbox = await SelfMailboxResolver(None).resolve(event, settings, actor)

            self.assertIsNotNone(mailbox)
            self.assertEqual(mailbox.address, "10001@qq.com")
            self.assertEqual(mailbox.source, "qq_mailbox_derivation")

        asyncio.run(scenario())

    def test_keyed_attempt_limiter_charges_failures_and_cooldown(self) -> None:
        now = [0.0]
        limiter = KeyedWindowRateLimiter(max_keys=10, now=lambda: now[0])

        self.assertTrue(
            limiter.can_record(
                "aiocqhttp:10001",
                max_events=2,
                window_seconds=3600,
                minimum_interval_seconds=30,
            )
        )
        limiter.record("aiocqhttp:10001")
        self.assertFalse(
            limiter.can_record(
                "aiocqhttp:10001",
                max_events=2,
                window_seconds=3600,
                minimum_interval_seconds=30,
            )
        )
        now[0] = 31.0
        self.assertTrue(
            limiter.can_record(
                "aiocqhttp:10001",
                max_events=2,
                window_seconds=3600,
                minimum_interval_seconds=30,
            )
        )
        limiter.record("aiocqhttp:10001")
        self.assertFalse(
            limiter.can_record(
                "aiocqhttp:10001",
                max_events=2,
                window_seconds=3600,
            )
        )

    def test_global_success_window_only_charges_successes(self) -> None:
        limiter = SuccessWindowRateLimiter()

        self.assertTrue(limiter.can_send(max_messages=1, window_seconds=3600))
        limiter.record_success()
        self.assertFalse(limiter.can_send(max_messages=1, window_seconds=3600))
