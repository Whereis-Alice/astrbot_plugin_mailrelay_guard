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
        self.assertEqual(settings.smtp_password, DEFAULT_PLACEHOLDER_PASSWORD)
        problems = " ".join(configuration_problems(settings))
        self.assertIn("smtp_username", problems)
        self.assertIn("smtp_password", problems)

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
        with self.assertRaisesRegex(MailRelayValidationError, "?????????"):
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

        with self.assertRaisesRegex(MailRelayValidationError, "???"):
            validate_dispatch_request(
                settings,
                ["allowed@example.com"],
                "Normal subject\r\nBcc: attacker@example.org",
                "Body",
                enforce_recipient_policy=False,
            )
