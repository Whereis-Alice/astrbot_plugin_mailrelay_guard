from __future__ import annotations

import json
import unittest
from pathlib import Path

from astrbot_plugin_mailrelay_guard.mailrelay_guard.config import (
    DEFAULT_PLACEHOLDER_PASSWORD,
    DEFAULT_SMTP_HOST,
    DEFAULT_SMTP_PORT,
    MailRelaySettings,
    configuration_problems,
    load_settings,
)
from astrbot_plugin_mailrelay_guard.mailrelay_guard.policy import (
    MailRelayValidationError,
    parse_recipients,
    validate_dispatch_request,
)


def configured_settings(**overrides):
    config = {
        "smtp_username": "sender@163.com",
        "smtp_password": "authorization-code",
        "sender_address": "sender@163.com",
        "admin_other_recipient_allowlist": ["allowed@example.com"],
    }
    config.update(overrides)
    return load_settings(config)


class ConfigAndPolicyTests(unittest.TestCase):
    def test_schema_exposes_a_default_for_every_runtime_setting(self) -> None:
        schema_path = Path(__file__).parents[1] / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertEqual(set(MailRelaySettings.__dataclass_fields__), set(schema))
        self.assertTrue(all("default" in item for item in schema.values()))

    def test_defaults_preconfigure_netease_and_leave_safe_placeholders(self) -> None:
        settings = load_settings({})

        self.assertEqual(settings.smtp_host, DEFAULT_SMTP_HOST)
        self.assertEqual(settings.smtp_port, DEFAULT_SMTP_PORT)
        self.assertEqual(settings.smtp_security, "ssl")
        self.assertTrue(settings.enable_llm_mail_tools)
        self.assertTrue(settings.enable_self_delivery)
        self.assertFalse(settings.enable_html_mail)
        self.assertTrue(settings.sanitize_html_before_send)
        self.assertFalse(settings.html_allow_links)
        self.assertFalse(settings.html_allow_remote_images)
        self.assertEqual(settings.html_remote_image_allowed_domains, frozenset())
        self.assertEqual(settings.max_html_body_chars, 30000)
        self.assertTrue(settings.enable_attachments)
        self.assertTrue(settings.allow_message_images)
        self.assertTrue(settings.allow_message_files)
        self.assertTrue(settings.allow_workspace_attachments)
        self.assertTrue(settings.enable_inline_images)
        self.assertEqual(settings.max_attachments_per_message, 6)
        self.assertEqual(settings.max_attachment_size_mb, 10)
        self.assertEqual(settings.max_total_attachment_size_mb, 20)
        self.assertIn(".exe", settings.blocked_attachment_extensions)
        self.assertEqual(settings.smtp_password, DEFAULT_PLACEHOLDER_PASSWORD)
        problems = " ".join(configuration_problems(settings))
        self.assertIn("smtp_username", problems)
        self.assertIn("smtp_password", problems)

    def test_blank_or_null_smtp_host_does_not_fall_back_to_163(self) -> None:
        for value in ("", None):
            with self.subTest(value=value):
                settings = load_settings({"smtp_host": value})

                self.assertEqual(settings.smtp_host, "")
                self.assertIn("smtp_host", " ".join(configuration_problems(settings)))

    def test_unknown_boolean_values_use_conservative_settings(self) -> None:
        settings = load_settings(
            {
                "enabled": "开启不了",
                "allow_plain_smtp": "falsee",
                "enable_admin_other_delivery": "关闭",
                "require_private_chat_for_self_delivery": "私聊",
                "restrict_admin_other_recipients": "不限制",
                "audit_log_enabled": "日志",
                "enable_html_mail": "HTML",
                "sanitize_html_before_send": "清洗",
                "html_allow_links": "链接",
                "html_allow_remote_images": "图片",
                "enable_attachments": "附件",
                "allow_message_images": "图片附件",
                "allow_message_files": "文件附件",
                "allow_workspace_attachments": "工作区",
                "enable_inline_images": "内嵌",
            }
        )

        self.assertFalse(settings.enabled)
        self.assertFalse(settings.allow_plain_smtp)
        self.assertFalse(settings.enable_admin_other_delivery)
        self.assertTrue(settings.require_private_chat_for_self_delivery)
        self.assertTrue(settings.restrict_admin_other_recipients)
        self.assertTrue(settings.audit_log_enabled)
        self.assertFalse(settings.enable_html_mail)
        self.assertTrue(settings.sanitize_html_before_send)
        self.assertFalse(settings.html_allow_links)
        self.assertFalse(settings.html_allow_remote_images)
        self.assertFalse(settings.enable_attachments)
        self.assertFalse(settings.allow_message_images)
        self.assertFalse(settings.allow_message_files)
        self.assertFalse(settings.allow_workspace_attachments)
        self.assertFalse(settings.enable_inline_images)

    def test_null_boolean_values_use_conservative_settings(self) -> None:
        settings = load_settings(
            {
                "enabled": None,
                "allow_plain_smtp": None,
                "enable_admin_other_delivery": None,
                "require_private_chat_for_self_delivery": None,
                "restrict_admin_other_recipients": None,
                "audit_log_enabled": None,
            }
        )

        self.assertFalse(settings.enabled)
        self.assertFalse(settings.allow_plain_smtp)
        self.assertFalse(settings.enable_admin_other_delivery)
        self.assertTrue(settings.require_private_chat_for_self_delivery)
        self.assertTrue(settings.restrict_admin_other_recipients)
        self.assertTrue(settings.audit_log_enabled)

    def test_schema_labels_are_utf8_and_do_not_use_placeholder_glyphs(self) -> None:
        schema_path = Path(__file__).parents[1] / "_conf_schema.json"
        raw = schema_path.read_bytes()
        schema = json.loads(raw.decode("utf-8"))
        visible_text = "\n".join(
            str(item.get(key, ""))
            for item in schema.values()
            for key in ("description", "hint")
        )

        self.assertNotIn("?", visible_text)
        self.assertNotIn("\ufffd", visible_text)
        self.assertEqual(schema["owner_sender_ids"]["default"], [])
        self.assertEqual(schema["admin_sender_ids"]["default"], [])

    def test_parse_recipients_deduplicates_common_separators(self) -> None:
        recipients = parse_recipients(
            "allowed@example.com; second@example.org, allowed@example.com"
        )

        self.assertEqual(recipients, ["allowed@example.com", "second@example.org"])

    def test_admin_other_allowlist_is_only_applied_when_enabled(self) -> None:
        settings = configured_settings(restrict_admin_other_recipients=True)

        validate_dispatch_request(
            settings,
            ["allowed@example.com"],
            "Status",
            "Done.",
            enforce_recipient_policy=True,
        )
        with self.assertRaisesRegex(MailRelayValidationError, "管理员代发允许范围"):
            validate_dispatch_request(
                settings,
                ["outside@example.org"],
                "Status",
                "Done.",
                enforce_recipient_policy=True,
            )

        validate_dispatch_request(
            settings,
            ["outside@example.org"],
            "Status",
            "Done.",
            enforce_recipient_policy=False,
        )

    def test_header_injection_is_rejected_before_smtp(self) -> None:
        settings = configured_settings()

        with self.assertRaisesRegex(MailRelayValidationError, "换行符"):
            validate_dispatch_request(
                settings,
                ["allowed@example.com"],
                "Normal subject\r\nBcc: attacker@example.org",
                "Body",
                enforce_recipient_policy=False,
            )
