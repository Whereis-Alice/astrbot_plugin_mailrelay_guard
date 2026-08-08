from __future__ import annotations

import json
import unittest
from pathlib import Path

from astrbot_plugin_mailrelay_guard.mailrelay_guard.config import (
    DEFAULT_SMTP_HOST,
    DEFAULT_SMTP_PORT,
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
        "recipient_allowlist": ["allowed@example.com"],
    }
    config.update(overrides)
    return load_settings(config)


class ConfigAndPolicyTests(unittest.TestCase):
    def test_schema_exposes_defaults_for_every_runtime_setting(self) -> None:
        schema_path = Path(__file__).parents[1] / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        runtime_keys = {
            "enabled",
            "smtp_host",
            "smtp_port",
            "smtp_security",
            "allow_plain_smtp",
            "smtp_username",
            "smtp_password",
            "sender_address",
            "sender_name",
            "smtp_timeout_seconds",
            "require_recipient_allowlist",
            "recipient_allowlist",
            "allowed_recipient_domains",
            "max_recipients_per_message",
            "max_subject_chars",
            "max_body_chars",
            "max_messages_per_hour",
            "test_recipient",
            "command_allowed_sender_ids",
            "audit_log_enabled",
            "audit_max_file_kb",
            "enable_llm_draft_tool",
            "llm_tool_allowed_sender_ids",
            "draft_ttl_seconds",
            "max_pending_drafts_per_actor",
        }

        self.assertEqual(runtime_keys, set(schema))
        self.assertTrue(all("default" in item for item in schema.values()))

    def test_defaults_preconfigure_netease_transport_but_not_credentials(self) -> None:
        settings = load_settings({})

        self.assertEqual(settings.smtp_host, DEFAULT_SMTP_HOST)
        self.assertEqual(settings.smtp_port, DEFAULT_SMTP_PORT)
        self.assertEqual(settings.smtp_security, "ssl")
        self.assertTrue(settings.require_recipient_allowlist)
        self.assertFalse(settings.recipient_allowlist)
        problems = " ".join(configuration_problems(settings))
        self.assertIn("smtp_username", problems)
        self.assertIn("smtp_password", problems)

    def test_parse_recipients_deduplicates_and_accepts_common_separators(self) -> None:
        recipients = parse_recipients(
            "allowed@example.com; second@example.org, allowed@example.com"
        )

        self.assertEqual(recipients, ["allowed@example.com", "second@example.org"])

    def test_recipient_policy_accepts_exact_address_or_allowed_domain(self) -> None:
        settings = configured_settings(allowed_recipient_domains=["example.org"])

        validate_dispatch_request(
            settings,
            ["allowed@example.com", "team@example.org"],
            "Status",
            "Done.",
        )

    def test_recipient_policy_rejects_unlisted_address(self) -> None:
        settings = configured_settings()

        with self.assertRaisesRegex(MailRelayValidationError, "允许范围"):
            validate_dispatch_request(
                settings, ["outside@example.org"], "Status", "Done."
            )

    def test_header_injection_is_rejected_before_smtp(self) -> None:
        settings = configured_settings()

        with self.assertRaisesRegex(MailRelayValidationError, "换行符"):
            validate_dispatch_request(
                settings,
                ["allowed@example.com"],
                "Normal subject\r\nBcc: attacker@example.org",
                "Body",
            )

    def test_empty_allowlist_stays_closed(self) -> None:
        settings = configured_settings(
            recipient_allowlist=[], allowed_recipient_domains=[]
        )

        with self.assertRaisesRegex(MailRelayValidationError, "允许范围"):
            validate_dispatch_request(
                settings, ["allowed@example.com"], "Status", "Done."
            )
