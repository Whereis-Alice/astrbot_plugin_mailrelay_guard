from __future__ import annotations

import asyncio
import re
import unittest

from astrbot_plugin_mailrelay_guard.mailrelay_guard.smtp_client import DeliveryResult
from astrbot_plugin_mailrelay_guard.main import MailRelayGuardPlugin


class FakeContext:
    def __init__(self) -> None:
        self.tools = []

    def add_llm_tools(self, *tools) -> None:
        self.tools.extend(tools)


class FakeEvent:
    unified_msg_origin = "platform:session-a"

    def is_admin(self) -> bool:
        return True

    def get_sender_id(self) -> str:
        return "admin-1"

    @staticmethod
    def plain_result(text: str) -> str:
        return text


class PluginFlowTests(unittest.TestCase):
    def test_llm_creates_draft_and_only_confirmation_sends(self) -> None:
        async def scenario() -> None:
            context = FakeContext()
            plugin = MailRelayGuardPlugin(
                context,
                {
                    "smtp_username": "sender@163.com",
                    "smtp_password": "authorization-code",
                    "sender_address": "sender@163.com",
                    "recipient_allowlist": ["receiver@example.com"],
                    "command_allowed_sender_ids": ["admin-1"],
                    "enable_llm_draft_tool": True,
                    "llm_tool_allowed_sender_ids": ["admin-1"],
                    "audit_log_enabled": False,
                },
            )
            event = FakeEvent()
            sent_calls = []

            async def fake_send(settings, recipients, subject, body):
                sent_calls.append((recipients, subject, body))
                return DeliveryResult(
                    message_id="<test@example.com>",
                    accepted_recipients=tuple(recipients),
                    refused_recipients=(),
                )

            plugin._smtp_client.send = fake_send
            draft_result = await plugin.prepare_draft_from_llm(
                event=event,
                recipients="receiver@example.com",
                subject="Draft subject",
                body="Draft body",
            )
            self.assertEqual(sent_calls, [])
            token_match = re.search(r"确认令牌：([a-f0-9]+)", draft_result)
            self.assertIsNotNone(token_match)

            replies = [
                reply
                async for reply in plugin.mailrelay_confirm(event, token_match.group(1))
            ]
            self.assertEqual(len(sent_calls), 1)
            self.assertIn("邮件已提交 SMTP 服务器", replies[0])

        asyncio.run(scenario())
