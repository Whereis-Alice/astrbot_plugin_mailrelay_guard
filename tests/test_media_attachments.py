from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from astrbot.api.message_components import File, Image, Reply

from astrbot_plugin_mailrelay_guard.mailrelay_guard.config import load_settings
from astrbot_plugin_mailrelay_guard.mailrelay_guard.media import (
    MailAttachment,
    collect_mail_attachments,
    contains_cid_placeholders,
    embed_cid_placeholders,
)
from astrbot_plugin_mailrelay_guard.mailrelay_guard.policy import (
    MailRelayValidationError,
)
from astrbot_plugin_mailrelay_guard.mailrelay_guard.smtp_client import (
    SMTPMailRelayClient,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"mailrelay-test-image"


class FakeEvent:
    def __init__(self, components) -> None:
        self.message_obj = type("Message", (), {"message": components})()


class FakeToolEvent:
    unified_msg_origin = "aiocqhttp:friend:10001"

    def __init__(self, temporary_paths=()) -> None:
        self._temporary_local_files = list(temporary_paths)


class FakeToolContext:
    def __init__(self, *, event=None) -> None:
        self.context = type(
            "AgentContext",
            (),
            {"event": event or FakeToolEvent(), "context": object()},
        )()


def settings(**overrides):
    config = {
        "smtp_username": "sender@163.com",
        "smtp_password": "authorization-code",
        "sender_address": "sender@163.com",
        "max_attachment_size_mb": 1,
        "max_total_attachment_size_mb": 2,
    }
    config.update(overrides)
    return load_settings(config)


class MediaAttachmentTests(unittest.TestCase):
    def test_detects_only_supported_cid_image_placeholders(self) -> None:
        self.assertTrue(contains_cid_placeholders("<img src='{{ image_1 }}'>"))
        self.assertTrue(contains_cid_placeholders("{{IMAGE_12}}"))
        self.assertFalse(contains_cid_placeholders("{{recipient}}"))
        self.assertFalse(contains_cid_placeholders(""))

    def test_collects_current_and_replied_media_and_deduplicates(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary_dir:
                root = Path(temporary_dir)
                image_path = root / "qq-image"
                document_path = root / "报告.txt"
                image_path.write_bytes(PNG)
                document_path.write_text("附件正文", encoding="utf-8")
                event = FakeEvent(
                    [
                        Image.fromFileSystem(str(image_path)),
                        Reply(
                            id="1",
                            chain=[
                                Image.fromFileSystem(str(image_path)),
                                File(name="报告.txt", file=str(document_path)),
                            ],
                        ),
                    ]
                )

                attachments = await collect_mail_attachments(
                    context=None,
                    event=event,
                    settings=settings(),
                    include_message_media=True,
                    workspace_file_paths=[],
                )

                self.assertEqual(len(attachments), 2)
                self.assertEqual(attachments[0].content_type, "image/png")
                self.assertEqual(attachments[0].filename, "image_1.png")
                self.assertEqual(attachments[1].filename, "报告.txt")

        asyncio.run(scenario())

    def test_allows_an_event_tracked_temp_file_when_runtime_is_disabled(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary_dir:
                report = Path(temporary_dir) / "generated.txt"
                report.write_text("generated", encoding="utf-8")
                tool_event = FakeToolEvent([str(report)])
                context = FakeToolContext(event=tool_event)
                with patch(
                    "astrbot.core.tools.computer_tools.util.is_local_runtime",
                    return_value=False,
                ):
                    attachments = await collect_mail_attachments(
                        context=context,
                        event=FakeEvent([]),
                        settings=settings(),
                        include_message_media=False,
                        workspace_file_paths=[str(report)],
                    )
                self.assertEqual(attachments[0].filename, "generated.txt")

        asyncio.run(scenario())

    def test_blocks_dangerous_extensions_and_size_overflow(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary_dir:
                root = Path(temporary_dir)
                script = root / "payload.exe"
                script.write_bytes(b"plain-dangerous-extension")
                with self.assertRaisesRegex(MailRelayValidationError, "禁止"):
                    await collect_mail_attachments(
                        context=None,
                        event=FakeEvent([File(name="payload.exe", file=str(script))]),
                        settings=settings(),
                        include_message_media=True,
                        workspace_file_paths=[],
                    )

                large = root / "large.bin"
                large.write_bytes(b"x" * (1024 * 1024 + 1))
                with self.assertRaisesRegex(MailRelayValidationError, "单文件大小上限"):
                    await collect_mail_attachments(
                        context=None,
                        event=FakeEvent([File(name="large.bin", file=str(large))]),
                        settings=settings(),
                        include_message_media=True,
                        workspace_file_paths=[],
                    )

        asyncio.run(scenario())

    def test_disabled_attachments_do_not_block_plain_mail_without_media(self) -> None:
        async def scenario() -> None:
            attachments = await collect_mail_attachments(
                context=None,
                event=FakeEvent([]),
                settings=settings(enable_attachments=False),
                include_message_media=True,
                workspace_file_paths=[],
            )
            self.assertEqual(attachments, [])

        asyncio.run(scenario())

    def test_disabled_attachments_reject_message_media(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary_dir:
                image_path = Path(temporary_dir) / "image.png"
                image_path.write_bytes(PNG)
                with self.assertRaisesRegex(MailRelayValidationError, "尚未开启邮件附件"):
                    await collect_mail_attachments(
                        context=None,
                        event=FakeEvent(
                            [Image.fromFileSystem(str(image_path))]
                        ),
                        settings=settings(enable_attachments=False),
                        include_message_media=True,
                        workspace_file_paths=[],
                    )

        asyncio.run(scenario())

    def test_cid_placeholder_builds_related_mime_part(self) -> None:
        source = MailAttachment("alice.png", "image/png", PNG)
        html_body, attachments = embed_cid_placeholders(
            '<div>图片如下</div><img src="{{image_1}}" alt="Alice">',
            [source],
            cid_domain="163.com",
        )
        self.assertIn("cid:mailrelay-image-1-", html_body)
        self.assertIsNotNone(attachments[0].content_id)

        message = SMTPMailRelayClient._build_message(
            settings(),
            ("receiver@example.com",),
            "CID 测试",
            "图片如下",
            html_body=html_body,
            attachments=tuple(attachments),
        )
        html_part = message.get_body(preferencelist=("html",))
        self.assertIn("cid:mailrelay-image-1-", html_part.get_content())
        related = next(
            part
            for part in message.walk()
            if part.get_content_maintype() == "image"
        )
        self.assertEqual(related.get_filename(), "alice.png")
        self.assertEqual(related["Content-ID"], f"<{attachments[0].content_id}>")
        self.assertEqual(related.get_content_disposition(), "inline")

    def test_rejects_renamed_executable_content(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary_dir:
                disguised = Path(temporary_dir) / "photo.jpg"
                disguised.write_bytes(b"MZ" + b"renamed executable")
                with self.assertRaisesRegex(MailRelayValidationError, "可执行内容"):
                    await collect_mail_attachments(
                        context=None,
                        event=FakeEvent(
                            [File(name="photo.jpg", file=str(disguised))]
                        ),
                        settings=settings(),
                        include_message_media=True,
                        workspace_file_paths=[],
                    )

        asyncio.run(scenario())

    def test_rejects_placeholder_without_a_matching_image(self) -> None:
        with self.assertRaisesRegex(MailRelayValidationError, "没有可嵌入图片"):
            embed_cid_placeholders(
                "<p>正文</p>{{image_1}}",
                [MailAttachment("note.txt", "text/plain", b"text")],
                cid_domain="163.com",
            )

    def test_resolves_legacy_astrbot_workspace_root_for_local_runtime(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary_dir:
                workspace = Path(temporary_dir)
                report = workspace / "report.txt"
                report.write_text("报告", encoding="utf-8")
                context = FakeToolContext()
                with patch(
                    "astrbot.core.tools.computer_tools.util.is_local_runtime",
                    return_value=True,
                ), patch(
                    "astrbot.core.tools.computer_tools.util.workspace_root",
                    return_value=workspace,
                ):
                    attachments = await collect_mail_attachments(
                        context=context,
                        event=FakeEvent([]),
                        settings=settings(),
                        include_message_media=False,
                        workspace_file_paths=["report.txt"],
                    )
                self.assertEqual([item.filename for item in attachments], ["report.txt"])
                self.assertEqual(attachments[0].data, "报告".encode())

        asyncio.run(scenario())
