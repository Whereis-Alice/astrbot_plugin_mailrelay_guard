from __future__ import annotations

import asyncio
import unittest
from typing import ClassVar
from unittest.mock import patch

from astrbot_plugin_mailrelay_guard.mailrelay_guard.config import load_settings
from astrbot_plugin_mailrelay_guard.mailrelay_guard.smtp_client import (
    SMTPMailRelayClient,
)


class FakeSMTP:
    instances: ClassVar[list[FakeSMTP]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.logged_in_as = ""
        self.sent_message = None
        self.closed = False
        self.ehlo_count = 0
        self.starttls_called = False
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.closed = True

    def ehlo(self) -> None:
        self.ehlo_count += 1

    def starttls(self, **kwargs) -> None:
        self.starttls_called = True

    def login(self, username: str, password: str) -> None:
        self.logged_in_as = username

    def send_message(self, message, from_addr: str, to_addrs: list[str]):
        self.sent_message = (message, from_addr, to_addrs)
        return {}

    def close(self) -> None:
        self.closed = True


def smtp_settings(**overrides):
    config = {
        "smtp_username": "sender@163.com",
        "smtp_password": "authorization-code",
        "sender_address": "sender@163.com",
        "sender_name": "MailRelay Test",
    }
    config.update(overrides)
    return load_settings(config)


class SMTPClientTests(unittest.TestCase):
    def test_ssl_smtp_sends_utf8_plain_text_message(self) -> None:
        from astrbot_plugin_mailrelay_guard.mailrelay_guard import smtp_client

        FakeSMTP.instances.clear()
        with patch.object(smtp_client.smtplib, "SMTP_SSL", FakeSMTP):
            client = SMTPMailRelayClient()
            result = asyncio.run(
                client.send(
                    smtp_settings(),
                    ["receiver@example.com"],
                    "测试主题",
                    "测试正文",
                )
            )

        instance = FakeSMTP.instances[-1]
        message, from_addr, to_addrs = instance.sent_message
        self.assertEqual(instance.logged_in_as, "sender@163.com")
        self.assertEqual(from_addr, "sender@163.com")
        self.assertEqual(to_addrs, ["receiver@example.com"])
        self.assertEqual(message["X-AstrBot-Plugin"], "MailRelayGuard")
        self.assertIn("测试正文", message.get_content())
        self.assertTrue(result.is_complete)
        self.assertEqual(result.accepted_recipients, ("receiver@example.com",))

    def test_starttls_performs_ehlo_before_and_after_tls(self) -> None:
        from astrbot_plugin_mailrelay_guard.mailrelay_guard import smtp_client

        FakeSMTP.instances.clear()
        with patch.object(smtp_client.smtplib, "SMTP", FakeSMTP):
            client = SMTPMailRelayClient()
            asyncio.run(
                client.test_connection(
                    smtp_settings(smtp_security="starttls", smtp_port=587)
                )
            )

        instance = FakeSMTP.instances[-1]
        self.assertTrue(instance.starttls_called)
        self.assertEqual(instance.ehlo_count, 2)
        self.assertTrue(instance.closed)
