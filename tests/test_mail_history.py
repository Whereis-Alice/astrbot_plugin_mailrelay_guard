from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_mailrelay_guard.mailrelay_guard.config import load_settings
from astrbot_plugin_mailrelay_guard.mailrelay_guard.history import (
    HistoryMessage,
    MailHistoryStore,
)
from astrbot_plugin_mailrelay_guard.mailrelay_guard.html_sanitizer import (
    prepare_html_preview,
)


class MailHistoryTests(unittest.TestCase):
    def test_history_keeps_masked_delivery_state_and_optional_content(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary_dir:
                store = MailHistoryStore(Path(temporary_dir))
                await store.initialize(retention_days=30, max_records=20)
                history_id = await store.begin_delivery(
                    action="llm_self",
                    mode="self",
                    content_format="html",
                    actor_id="aiocqhttp:123456",
                    recipients=["alice@example.com", "refused@example.net"],
                    retention_days=30,
                    max_records=20,
                )
                self.assertIsNotNone(history_id)
                await store.finalize_delivery(
                    history_id,
                    HistoryMessage(
                        message_id="<message@example.com>",
                        status="partial",
                        accepted_recipients=("alice@example.com",),
                        refused_recipients=("refused@example.net",),
                        store_content=True,
                        subject="安全主题",
                        plain_body="纯文本备用内容",
                        html_body='<p style="color:#ff00aa">HTML 内容</p>',
                    ),
                    retention_days=30,
                    max_records=20,
                )

                outbox = await store.list_messages(folder="sent")
                self.assertEqual(outbox["total"], 1)
                item = outbox["items"][0]
                self.assertEqual(item["status"], "partial")
                self.assertEqual(item["subject"], "安全主题")
                self.assertEqual(item["recipients"][0]["address"], "a***e@example.com")
                self.assertNotIn("alice@example.com", str(item))

                inbox = await store.list_messages(folder="inbox")
                self.assertEqual(len(inbox["items"]), 1)
                recipient = inbox["items"][0]["recipient"]
                state = await store.update_mailbox_state(
                    history_id=history_id,
                    recipient_token=recipient["token"],
                    is_read=True,
                    is_starred=True,
                )
                self.assertEqual(state, {"is_read": True, "is_starred": True, "archived": False})

                detail = await store.get_message(history_id)
                self.assertTrue(detail["content_saved"])
                self.assertEqual(detail["plain_body"], "纯文本备用内容")
                self.assertIn("HTML 内容", detail["html_body"])
                self.assertNotIn("alice@example.com", str(detail))

                hidden_id = await store.begin_delivery(
                    action="llm_self",
                    mode="self",
                    content_format="plain",
                    actor_id="aiocqhttp:123456",
                    recipients=["private@example.com"],
                    retention_days=30,
                    max_records=20,
                )
                await store.finalize_delivery(
                    hidden_id,
                    HistoryMessage(
                        message_id="<hidden@example.com>",
                        status="submitted",
                        accepted_recipients=("private@example.com",),
                        store_content=False,
                        subject="不应保存的主题",
                        plain_body="不应保存的正文",
                    ),
                    retention_days=30,
                    max_records=20,
                )
                hidden_detail = await store.get_message(hidden_id)
                self.assertFalse(hidden_detail["content_saved"])
                self.assertIsNone(hidden_detail["subject"])
                self.assertIsNone(hidden_detail["plain_body"])

                binding = await store.begin_delivery(
                    action="binding_challenge",
                    mode="binding",
                    content_format="plain",
                    actor_id="aiocqhttp:123456",
                    recipients=["private@example.com"],
                    retention_days=30,
                    max_records=20,
                )
                self.assertIsNone(binding)

        asyncio.run(scenario())

    def test_dashboard_preview_recleans_stored_html_without_network_resources(self) -> None:
        preview = prepare_html_preview(
            load_settings(
                {
                    "enable_html_mail": True,
                    "html_allow_links": True,
                    "html_allow_remote_images": True,
                    "html_remote_image_allowed_domains": ["images.example.com"],
                }
            ),
            (
                '<a href="https://safe.example">保留文字</a>'
                '<img src="https://images.example.com/card.png" alt="不加载">'
                '<div style="color:#ff00aa">安全预览</div>'
            ),
        )

        self.assertIn("保留文字", preview)
        self.assertIn("安全预览", preview)
        self.assertNotIn("href=", preview)
        self.assertNotIn("images.example.com", preview)
