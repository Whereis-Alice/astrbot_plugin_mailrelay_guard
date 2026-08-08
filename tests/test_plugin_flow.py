from __future__ import annotations

import asyncio
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_mailrelay_guard.mailrelay_guard.bindings import MailboxBindingStore
from astrbot_plugin_mailrelay_guard.mailrelay_guard.identity import SelfMailboxResolver
from astrbot_plugin_mailrelay_guard.mailrelay_guard.smtp_client import DeliveryResult
from astrbot_plugin_mailrelay_guard.main import (
    MailRelayGuardPlugin,
    MailRelaySendToSelfTool,
)


class FakeContext:
    def __init__(self) -> None:
        self.tools = []

    def add_llm_tools(self, *tools) -> None:
        self.tools.extend(tools)


class FakeEvent:
    unified_msg_origin = "aiocqhttp:friend:10001"

    def __init__(
        self,
        *,
        sender_id: str,
        admin: bool = False,
        private: bool = True,
        platform_id: str = "aiocqhttp",
    ) -> None:
        self.sender_id = sender_id
        self.admin = admin
        self.private = private
        self.platform_id = platform_id

    def is_admin(self) -> bool:
        return self.admin

    def is_private_chat(self) -> bool:
        return self.private

    def get_sender_id(self) -> str:
        return self.sender_id

    def get_platform_id(self) -> str:
        return self.platform_id

    def get_platform_name(self) -> str:
        return self.platform_id

    @staticmethod
    def plain_result(text: str) -> str:
        return text


class WrappedToolContext:
    def __init__(self, event) -> None:
        self.context = type("AgentContext", (), {"event": event})()


def plugin_config(**overrides):
    config = {
        "smtp_username": "sender@163.com",
        "smtp_password": "authorization-code",
        "sender_address": "sender@163.com",
        "owner_email": "owner@example.com",
        "owner_sender_ids": ["aiocqhttp:owner"],
        "admin_sender_ids": ["aiocqhttp:admin"],
        "self_email_overrides": ["aiocqhttp:user=user@example.com"],
        "napcat_email_lookup_enabled": False,
        "actor_min_send_interval_seconds": 0,
        "max_successful_messages_per_actor_per_hour": 20,
        "max_delivery_attempts_per_actor_per_hour": 20,
        "audit_log_enabled": False,
    }
    config.update(overrides)
    return config


class PluginFlowTests(unittest.TestCase):
    def test_initialize_registers_the_three_direct_tools(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary_dir:
                context = FakeContext()
                plugin = MailRelayGuardPlugin(context, plugin_config())
                with patch(
                    "astrbot_plugin_mailrelay_guard.main.StarTools.get_data_dir",
                    return_value=temporary_dir,
                ):
                    await plugin.initialize()
                self.assertEqual(
                    {tool.name for tool in context.tools},
                    {
                        "mailrelay_notify_owner",
                        "mailrelay_send_to_self",
                        "mailrelay_send_to_recipient",
                    },
                )
                await plugin.terminate()

        asyncio.run(scenario())

    def test_normal_user_can_only_send_to_their_resolved_mailbox(self) -> None:
        async def scenario() -> None:
            plugin = MailRelayGuardPlugin(FakeContext(), plugin_config())
            sent_calls = []

            async def fake_send(settings, recipients, subject, body):
                sent_calls.append((recipients, subject, body))
                return DeliveryResult("<test@example.com>", tuple(recipients), ())

            plugin._smtp_client.send = fake_send
            user = FakeEvent(sender_id="user")
            response = await plugin.deliver_from_tool(
                event=user,
                mode="self",
                recipients_input="attacker@example.com",
                subject="Self mail",
                body="Body",
            )
            self.assertIn("SMTP", response)
            self.assertEqual(sent_calls[0][0], ["user@example.com"])

            denied = await plugin.deliver_from_tool(
                event=user,
                mode="other",
                recipients_input="attacker@example.com",
                subject="Attack",
                body="Body",
            )
            self.assertIn("???", denied)
            self.assertEqual(len(sent_calls), 1)

            group_denied = await plugin.deliver_from_tool(
                event=FakeEvent(sender_id="user", private=False),
                mode="self",
                subject="Group mail",
                body="Body",
            )
            self.assertIn("??", group_denied)
            self.assertEqual(len(sent_calls), 1)

        asyncio.run(scenario())

    def test_owner_and_admin_other_modes_require_their_own_identities(self) -> None:
        async def scenario() -> None:
            plugin = MailRelayGuardPlugin(FakeContext(), plugin_config())
            sent_calls = []

            async def fake_send(settings, recipients, subject, body):
                sent_calls.append((recipients, subject, body))
                return DeliveryResult("<test@example.com>", tuple(recipients), ())

            plugin._smtp_client.send = fake_send
            owner = FakeEvent(sender_id="owner", admin=True)
            response = await plugin.deliver_from_tool(
                event=owner,
                mode="owner",
                subject="Owner notice",
                body="Body",
            )
            self.assertIn("SMTP", response)
            self.assertEqual(sent_calls[0][0], ["owner@example.com"])

            admin = FakeEvent(sender_id="admin", admin=True)
            response = await plugin.deliver_from_tool(
                event=admin,
                mode="other",
                recipients_input="outside@example.net",
                subject="Admin send",
                body="Body",
            )
            self.assertIn("SMTP", response)
            self.assertEqual(sent_calls[1][0], ["outside@example.net"])

            wrong_owner = FakeEvent(sender_id="user", admin=True)
            denied = await plugin.deliver_from_tool(
                event=wrong_owner,
                mode="owner",
                subject="Nope",
                body="Body",
            )
            self.assertIn("???", denied)
            self.assertEqual(len(sent_calls), 2)

            cross_platform = await plugin.deliver_from_tool(
                event=FakeEvent(sender_id="admin", admin=True, platform_id="telegram"),
                mode="other",
                recipients_input="outside@example.net",
                subject="Cross-platform attempt",
                body="Body",
            )
            self.assertIn("???", cross_platform)
            self.assertEqual(len(sent_calls), 2)

        asyncio.run(scenario())

    def test_llm_self_tool_uses_wrapped_event_and_ignores_recipient_control(self) -> None:
        async def scenario() -> None:
            plugin = MailRelayGuardPlugin(FakeContext(), plugin_config())
            sent_calls = []

            async def fake_send(settings, recipients, subject, body):
                sent_calls.append((recipients, subject, body))
                return DeliveryResult("<test@example.com>", tuple(recipients), ())

            plugin._smtp_client.send = fake_send
            result = await MailRelaySendToSelfTool(plugin=plugin).call(
                WrappedToolContext(FakeEvent(sender_id="user")),
                subject="Tool mail",
                body="Body",
                recipients="attacker@example.com",
            )

            self.assertIn("SMTP", result)
            self.assertEqual(sent_calls[0][0], ["user@example.com"])

        asyncio.run(scenario())

    def test_admin_test_mail_cannot_bypass_owner_identity(self) -> None:
        async def scenario() -> None:
            plugin = MailRelayGuardPlugin(FakeContext(), plugin_config())
            sent_calls = []

            async def fake_send(settings, recipients, subject, body):
                sent_calls.append((recipients, subject, body))
                return DeliveryResult("<test@example.com>", tuple(recipients), ())

            plugin._smtp_client.send = fake_send
            replies = [
                reply
                async for reply in plugin.mailrelay_send_test(
                    FakeEvent(sender_id="admin", admin=True)
                )
            ]

            self.assertIn("???", replies[0])
            self.assertEqual(sent_calls, [])

        asyncio.run(scenario())

    def test_binding_requires_email_code_before_self_delivery_uses_it(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary_dir:
                plugin = MailRelayGuardPlugin(FakeContext(), plugin_config())
                plugin._mailboxes = MailboxBindingStore(Path(temporary_dir))
                plugin._mailbox_resolver = SelfMailboxResolver(plugin._mailboxes)
                sent_calls = []

                async def fake_send(settings, recipients, subject, body):
                    sent_calls.append((recipients, subject, body))
                    return DeliveryResult("<test@example.com>", tuple(recipients), ())

                plugin._smtp_client.send = fake_send
                user = FakeEvent(sender_id="new-user", private=True)
                response = await plugin.request_mailbox_binding(user, "new@example.com")
                self.assertIn("??????", response)
                code = re.search(r"???:(\d{6})", sent_calls[0][2]).group(1)
                verified = await plugin.verify_mailbox_binding(user, code)
                self.assertIn("???", verified)

                response = await plugin.deliver_from_tool(
                    event=user,
                    mode="self",
                    subject="Bound self mail",
                    body="Body",
                )
                self.assertIn("SMTP", response)
                self.assertEqual(sent_calls[-1][0], ["new@example.com"])

        asyncio.run(scenario())
