from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aiocqhttp.exceptions import ActionFailed, ApiNotAvailable

from astrbot_plugin_mailrelay_guard.mailrelay_guard.config import load_settings
from astrbot_plugin_mailrelay_guard.mailrelay_guard.offline_monitor import (
    OfflinePlatformMonitor,
)
from astrbot_plugin_mailrelay_guard.mailrelay_guard.smtp_client import (
    DeliveryResult,
    MailRelayTransportError,
)
from astrbot_plugin_mailrelay_guard.main import MailRelayGuardPlugin


class Client:
    def __init__(self):
        self._wsr_api_clients = {"10001": object()}
        self.status = {"10001": True, "10002": True}
        self.error = None
        self.calls = []
        self.nickname = "测试机器人"

    async def call_action(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if action == "get_status":
            if self.error is not None:
                raise self.error
            return {"online": self.status.get(kwargs.get("self_id"), True)}
        return {"user_id": kwargs.get("self_id", "10001"), "nickname": self.nickname}


class SMTP:
    def __init__(self):
        self.calls = []
        self.failing = set()

    async def send(self, settings, recipients, subject, body, *, html_body=None):
        self.calls.append((tuple(recipients), subject, body, html_body))
        if recipients[0] in self.failing:
            raise MailRelayTransportError("模拟 SMTP 暂时不可用")
        return DeliveryResult("test-message", tuple(recipients), ())


class OfflineMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = {
            "enabled": True,
            "enable_offline_notifications": True,
            "smtp_username": "sender@163.com",
            "smtp_password": "test-authorization",
            "sender_address": "sender@163.com",
            "owner_email": "owner@example.com",
            "offline_notify_recipients": ["notice@example.com"],
            "offline_failure_threshold": 2,
        }
        self.client = Client()
        self.platform = SimpleNamespace(
            meta=lambda: SimpleNamespace(name="aiocqhttp", id="qq-main"),
            get_client=lambda: self.client,
        )
        self.context = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[self.platform])
        )
        self.smtp = SMTP()
        self.monitor = OfflinePlatformMonitor(
            self.context, lambda: load_settings(self.config), self.smtp
        )

    async def test_debounce_once_per_outage_and_recovery_duration(self):
        await self.monitor.check_once()
        self.assertEqual(self.smtp.calls, [])
        self.client._wsr_api_clients.clear()
        with patch(
            "astrbot_plugin_mailrelay_guard.mailrelay_guard.offline_monitor.time.time",
            return_value=100,
        ):
            await self.monitor.check_once()
        self.assertEqual(self.smtp.calls, [])
        await self.monitor.check_once()
        await self.monitor.check_once()
        self.assertEqual(len(self.smtp.calls), 1)
        self.assertEqual(self.smtp.calls[0][0], ("notice@example.com",))
        self.client._wsr_api_clients["10001"] = object()
        with patch(
            "astrbot_plugin_mailrelay_guard.mailrelay_guard.offline_monitor.time.time",
            return_value=300,
        ):
            await self.monitor.check_once()
        self.assertEqual(len(self.smtp.calls), 2)
        self.assertIn("已恢复", self.smtp.calls[1][1])
        self.assertIn("3分20秒", self.smtp.calls[1][3])

    async def test_only_failed_recipient_is_retried(self):
        self.config["offline_notify_recipients"] = ["a@example.com", "b@example.com"]
        self.smtp.failing.add("b@example.com")
        await self.monitor.check_once()
        self.client._wsr_api_clients.clear()
        await self.monitor.check_once()
        await self.monitor.check_once()
        self.assertEqual(len(self.smtp.calls), 2)
        self.smtp.failing.clear()
        await self.monitor.check_once()
        await self.monitor.check_once()
        self.assertEqual(
            [call[0] for call in self.smtp.calls],
            [("a@example.com",), ("b@example.com",), ("b@example.com",)],
        )

    async def test_notification_failure_has_bounded_retries(self):
        self.smtp.failing.add("notice@example.com")
        self.client._wsr_api_clients.clear()
        for _ in range(12):
            await self.monitor.check_once()
        self.assertEqual(len(self.smtp.calls), 4)
        self.assertEqual(self.monitor.status()["monitored"][0]["retry_count"], 3)

    async def test_multi_account_disconnect_is_not_hidden_by_other_online_account(self):
        self.client._wsr_api_clients["10002"] = object()
        await self.monitor.check_once()
        ids = {
            kwargs["self_id"]
            for action, kwargs in self.client.calls
            if action == "get_status"
        }
        self.assertEqual(ids, {"10001", "10002"})
        del self.client._wsr_api_clients["10002"]
        await self.monitor.check_once()
        await self.monitor.check_once()
        statuses = {
            item["bot_id"]: item["online"]
            for item in self.monitor.status()["monitored"]
        }
        self.assertEqual(statuses, {"10001": True, "10002": False})
        self.assertEqual(len(self.smtp.calls), 1)
        self.assertIn("10002", self.smtp.calls[0][3])

    async def test_initial_disconnected_adapter_can_report_recovery(self):
        self.client._wsr_api_clients.clear()
        await self.monitor.check_once()
        await self.monitor.check_once()
        self.client._wsr_api_clients["10001"] = object()
        await self.monitor.check_once()
        self.assertEqual(len(self.smtp.calls), 2)
        self.assertIn("已恢复", self.smtp.calls[1][1])

    async def test_account_offline_with_websocket_still_connected(self):
        await self.monitor.check_once()
        self.client.status["10001"] = False
        await self.monitor.check_once()
        await self.monitor.check_once()
        self.assertEqual(len(self.smtp.calls), 1)
        self.assertIn("账号已离线", self.smtp.calls[0][2])

    async def test_api_unavailable_is_offline_but_unsupported_action_can_fall_back(
        self,
    ):
        self.client.error = ActionFailed({"retcode": 1404, "status": "failed"})
        await self.monitor.check_once()
        self.assertTrue(self.monitor.status()["monitored"][0]["online"])
        self.assertEqual(self.smtp.calls, [])
        self.client.error = ApiNotAvailable()
        await self.monitor.check_once()
        await self.monitor.check_once()
        self.assertEqual(len(self.smtp.calls), 1)

    async def test_unknown_error_breaks_consecutive_failure_counter(self):
        self.client.error = ApiNotAvailable()
        await self.monitor.check_once()
        self.client.error = ValueError("unexpected status")
        await self.monitor.check_once()
        self.client.error = ApiNotAvailable()
        await self.monitor.check_once()
        self.assertEqual(self.smtp.calls, [])

    async def test_headers_and_html_variable_injection_are_neutralized(self):
        self.client.nickname = "<img src=x onerror=alert(1)>\r\nBcc: other@example.com"
        self.config["offline_subject_template"] = "提醒\r\n{{ nickname }}"
        await self.monitor.check_once()
        result = await self.monitor.send_test()
        self.assertTrue(result["ok"])
        _, subject, _, html = self.smtp.calls[0]
        self.assertNotIn("\r", subject)
        self.assertNotIn("\n", subject)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img", html)
        self.assertNotIn("{{nickname}}", html)

    async def test_test_notice_works_when_monitor_disabled_but_service_must_be_enabled(
        self,
    ):
        self.config["enable_offline_notifications"] = False
        await self.monitor.check_once()
        self.assertEqual(self.monitor.status()["monitored"], [])
        self.assertTrue((await self.monitor.send_test())["ok"])
        self.config["enabled"] = False
        self.assertFalse((await self.monitor.send_test())["ok"])
        self.assertEqual(len(self.smtp.calls), 1)

    async def test_fallback_owner_empty_platform_list_and_recovery_switch(self):
        self.config["offline_notify_recipients"] = []
        self.config["offline_monitored_platforms"] = []
        await self.monitor.check_once()
        self.assertEqual(self.monitor.status()["monitored"], [])
        result = await self.monitor.send_test()
        self.assertEqual(result["recipients"], ["owner@example.com"])
        self.config["offline_monitored_platforms"] = ["qq-main"]
        self.config["offline_notify_on_recovery"] = False
        await self.monitor.check_once()
        self.client._wsr_api_clients.clear()
        await self.monitor.check_once()
        await self.monitor.check_once()
        self.client._wsr_api_clients["10001"] = object()
        await self.monitor.check_once()
        self.assertEqual(len(self.smtp.calls), 2)  # test plus outage, no recovery

    async def test_run_wakes_for_settings_change_and_cancels_cleanly(self):
        self.config["enable_offline_notifications"] = False
        task = asyncio.create_task(self.monitor.run())
        await asyncio.sleep(0)
        self.config["enable_offline_notifications"] = True
        self.monitor.wake()
        for _ in range(30):
            if self.monitor.status()["last_check_at"]:
                break
            await asyncio.sleep(0)
        self.assertIsNotNone(self.monitor.status()["last_check_at"])
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_dashboard_validates_notification_addresses_and_preserves_secrets(
        self,
    ):
        plugin = MailRelayGuardPlugin(SimpleNamespace(), self.config)
        with self.assertRaises(ValueError):
            await plugin._update_webui_settings(
                {
                    "offline_notify_recipients": [
                        "bad@example.com\r\nBcc:other@example.com"
                    ]
                }
            )
        with self.assertRaises(ValueError):
            await plugin._update_webui_settings({"smtp_password": "should-not-change"})
        changed = await plugin._update_webui_settings(
            {
                "offline_check_interval_seconds": 5,
                "offline_subject_template": "自定义 {{nickname}}",
            }
        )
        self.assertIn("offline_check_interval_seconds", changed)
        self.assertEqual(self.config["offline_check_interval_seconds"], 30)
        self.assertNotIn(
            "smtp_password",
            plugin._webui_settings_payload(plugin._settings())["settings"],
        )

    async def test_failed_config_save_does_not_change_runtime_settings(self):
        class FailingConfig(dict):
            def save_config(self):
                raise OSError("disk full")

        config = FailingConfig(self.config)
        plugin = MailRelayGuardPlugin(SimpleNamespace(), config)
        with self.assertRaises(OSError):
            await plugin._update_webui_settings({"offline_check_interval_seconds": 60})
        self.assertNotIn("offline_check_interval_seconds", config)
        self.assertEqual(plugin._settings().offline_check_interval_seconds, 180)

    async def test_client_lookup_failure_preserves_confirmed_outage(self):
        await self.monitor.check_once()
        self.client._wsr_api_clients.clear()
        await self.monitor.check_once()
        await self.monitor.check_once()

        def unavailable():
            raise RuntimeError("adapter reload in progress")

        self.platform.get_client = unavailable
        await self.monitor.check_once()
        state = self.monitor.status()["monitored"][0]
        self.assertFalse(state["online"])
        self.assertEqual(state["probe_state"], "unknown")
        self.assertEqual(len(self.smtp.calls), 1)
