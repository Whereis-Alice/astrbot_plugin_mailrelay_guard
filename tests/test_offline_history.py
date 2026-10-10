from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_mailrelay_guard.mailrelay_guard.config import load_settings
from astrbot_plugin_mailrelay_guard.mailrelay_guard.offline_history import (
    RETENTION_SECONDS,
    OfflineHistoryError,
    OfflineHistoryStore,
)
from astrbot_plugin_mailrelay_guard.mailrelay_guard.offline_monitor import (
    OfflinePlatformMonitor,
    _PlatformState,
    _ProbeResult,
)
from astrbot_plugin_mailrelay_guard.main import MailRelayGuardPlugin


class OfflineHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = OfflineHistoryStore(Path(self.temp.name))
        await self.store.initialize()
        self.base = 1800000000.0

    async def record(self, offset, state, *, key="qq:10001", since=None):
        await self.store.record(
            key=key,
            observed_at=self.base + offset,
            state=state,
            interval=60,
            offline_since=self.base + since if since is not None else None,
            platform="aiocqhttp",
            bot_id=key.split(":")[-1],
            nickname="测试机器人",
            reason="测试原因",
        )

    async def view(self, offset=600, **filters):
        return await self.store.overview(
            period=filters.pop("period", "24h"), now=self.base + offset, **filters
        )

    async def test_confirmed_suspects_are_backdated_and_coalesced(self):
        await self.record(0, "online")
        await self.record(60, "suspect")
        await self.record(120, "suspect")
        await self.record(180, "offline", since=60)
        await self.record(240, "offline", since=60)
        await self.record(300, "online")
        data = await self.view()
        self.assertEqual(data["summary"]["offline_seconds"], 240)
        self.assertEqual(data["summary"]["incident_count"], 1)
        self.assertEqual(data["summary"]["longest_seconds"], 240)
        self.assertAlmostEqual(data["summary"]["uptime_ratio"], 0.2)
        self.assertEqual(data["incidents"][0]["started_at"], self.base + 60)
        self.assertEqual(data["incidents"][0]["status"], "recovered")
        self.assertEqual(sum(b["offline_seconds"] or 0 for b in data["buckets"]), 240)

    async def test_brief_failure_is_not_an_outage(self):
        await self.record(0, "online")
        await self.record(60, "suspect")
        await self.record(120, "online")
        await self.record(180, "online")
        data = await self.view()
        self.assertEqual(data["summary"]["incident_count"], 0)
        self.assertEqual(data["summary"]["offline_seconds"], 0)
        self.assertEqual(data["summary"]["observed_seconds"], 120)
        self.assertIn("suspect", [s["state"] for s in data["series"][0]["segments"]])

    async def test_unknown_does_not_count_or_invent_recovery(self):
        await self.record(0, "offline")
        await self.record(60, "unknown")
        await self.record(120, "unknown")
        data = await self.view()
        self.assertEqual(data["summary"]["offline_seconds"], 60)
        self.assertEqual(data["summary"]["uptime_ratio"], 0)
        self.assertEqual(data["incidents"][0]["status"], "unresolved")
        await self.record(180, "online")
        data = await self.view()
        self.assertEqual(data["summary"]["offline_seconds"], 60)
        self.assertEqual(data["incidents"][0]["status"], "recovered")

    async def test_reload_gap_is_not_uptime_or_downtime(self):
        await self.record(0, "offline")
        await self.record(60, "offline")
        self.store = OfflineHistoryStore(Path(self.temp.name))
        await self.store.initialize()
        await self.record(120, "online")
        await self.record(180, "online")
        data = await self.view()
        self.assertEqual(data["summary"]["observed_seconds"], 120)
        self.assertEqual(data["summary"]["offline_seconds"], 60)
        self.assertEqual(data["incidents"][0]["status"], "gap")
        self.assertEqual(data["incidents"][0]["ended_at"], self.base + 60)

    async def test_monitor_pause_breaks_even_a_short_interval(self):
        await self.record(0, "online")
        await self.record(60, "online")
        self.store.restart_session()
        await self.record(90, "online")
        await self.record(120, "online")
        self.assertEqual((await self.view())["summary"]["observed_seconds"], 90)

    async def test_sleep_gap_cannot_be_backdated_as_suspect(self):
        await self.record(0, "suspect")
        await self.record(60, "suspect")
        await self.record(600, "suspect")
        await self.record(660, "offline", since=0)
        await self.record(720, "online")
        data = await self.view(900)
        self.assertEqual(data["summary"]["offline_seconds"], 120)
        self.assertEqual(data["incidents"][0]["started_at"], self.base + 600)

    async def test_unknown_breaks_backdating(self):
        await self.record(0, "suspect")
        await self.record(60, "unknown")
        await self.record(120, "suspect")
        await self.record(180, "offline", since=0)
        await self.record(240, "online")
        self.assertEqual((await self.view())["summary"]["offline_seconds"], 120)

    async def test_account_filter_and_multi_account_sum(self):
        for key in ("qq:10001", "qq:10002"):
            await self.record(0, "offline", key=key)
            await self.record(60, "online", key=key)
        self.assertEqual((await self.view())["summary"]["offline_seconds"], 120)
        one = await self.view(account="qq:10002")
        self.assertEqual(one["summary"]["offline_seconds"], 60)
        self.assertEqual(len(one["series"]), 1)
        self.assertEqual(len(one["accounts"]), 2)
        self.assertEqual((await self.view(account="missing"))["series"], [])

    async def test_empty_buckets_are_null_not_zero(self):
        data = await self.view()
        self.assertIsNone(data["summary"]["uptime_ratio"])
        self.assertTrue(all(b["offline_seconds"] is None for b in data["buckets"]))
        await self.record(0, "online")
        await self.record(60, "online")
        data = await self.view()
        self.assertEqual(data["buckets"][-1]["offline_seconds"], 0)
        self.assertIsNone(data["buckets"][0]["offline_seconds"])

    async def test_current_outage_stops_at_last_observation(self):
        await self.record(0, "offline")
        await self.record(60, "offline")
        data = await self.view(7200)
        self.assertEqual(data["summary"]["offline_seconds"], 60)
        self.assertEqual(data["last_observed_at"], self.base + 60)
        self.assertIsNone(data["buckets"][-1]["offline_seconds"])

    async def test_stale_unresolved_incident_is_not_assumed_to_continue(self):
        await self.record(0, "offline")
        await self.record(60, "offline")
        data = await self.view(10 * 86400, period="7d")
        self.assertEqual(data["summary"]["incident_count"], 0)
        self.assertEqual(data["incidents"], [])
        self.assertIsNone(data["summary"]["uptime_ratio"])

    async def test_bucket_and_range_clipping(self):
        await self.record(0, "offline")
        await self.record(120, "online")
        data = await self.view(86460)
        self.assertEqual(data["summary"]["offline_seconds"], 60)
        self.assertEqual(data["incidents"][0]["duration_seconds"], 60)
        self.assertEqual(len((await self.view(period="7d"))["buckets"]), 7)
        self.assertEqual(len((await self.view(period="30d"))["buckets"]), 30)
        with self.assertRaises(ValueError):
            await self.view(period="forever")

    async def test_backwards_clock_does_not_rewrite_data(self):
        await self.record(60, "online")
        await self.record(120, "online")
        await self.record(30, "offline")
        data = await self.view()
        self.assertEqual(data["summary"]["incident_count"], 0)
        self.assertEqual(data["last_observed_at"], self.base + 120)

    async def test_retention_and_row_limits(self):
        await self.record(0, "offline", key="old:1")
        await self.record(60, "online", key="old:1")
        await self.record(RETENTION_SECONDS + 120, "online", key="new:2")
        data = await self.view(RETENTION_SECONDS + 180)
        self.assertEqual([a["key"] for a in data["accounts"]], ["new:2"])
        with (
            patch(
                "astrbot_plugin_mailrelay_guard.mailrelay_guard.offline_history.MAX_SEGMENTS",
                3,
            ),
            patch(
                "astrbot_plugin_mailrelay_guard.mailrelay_guard.offline_history.MAX_INCIDENTS",
                2,
            ),
        ):
            for n in range(10):
                await self.record(
                    RETENTION_SECONDS + 200 + n * 60,
                    "online" if n % 2 else "offline",
                    key="new:2",
                )
        with closing(sqlite3.connect(self.store._path)) as db:
            self.assertLessEqual(
                db.execute("SELECT COUNT(*) FROM segments").fetchone()[0], 3
            )
            self.assertLessEqual(
                db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0], 2
            )

    async def test_identity_remapping_preserves_incident(self):
        await self.record(0, "offline", key="qq")
        await self.record(60, "offline", key="qq")
        await self.store.remap_account("qq", "qq:10001")
        await self.record(120, "online")
        data = await self.view()
        self.assertEqual([a["key"] for a in data["accounts"]], ["qq:10001"])
        self.assertEqual(data["incidents"][0]["status"], "recovered")
        self.assertEqual(data["summary"]["offline_seconds"], 120)

    async def test_removed_account_is_not_continuous_when_readded(self):
        await self.record(0, "online")
        await self.record(60, "online")
        await self.store.break_account("qq:10001")
        await self.record(120, "online")
        await self.record(180, "online")
        self.assertEqual((await self.view())["summary"]["observed_seconds"], 120)

    async def test_unavailable_storage_is_explicit(self):
        monitor = OfflinePlatformMonitor(
            SimpleNamespace(), lambda: load_settings({}), SimpleNamespace()
        )
        self.assertFalse((await monitor.history_overview())["available"])
        monitor._history = self.store
        with patch.object(
            self.store, "_overview_sync", side_effect=OfflineHistoryError("disk error")
        ):
            self.assertFalse((await monitor.history_overview())["available"])

    async def test_monitor_uses_one_timestamp_and_records_before_smtp(self):
        config = load_settings(
            {"enable_offline_notifications": True, "offline_failure_threshold": 2}
        )
        monitor = OfflinePlatformMonitor(
            SimpleNamespace(), lambda: config, SimpleNamespace()
        )
        monitor._history = self.store
        calls = []

        async def notify(*args, **kwargs):
            data = await self.view()
            calls.append(data["summary"]["incident_count"])

        monitor._notify_transition = notify
        state = _PlatformState(key="qq:10001")
        for offset, raw in [
            (0, "online"),
            (60, "offline"),
            (120, "offline"),
            (180, "online"),
        ]:
            with patch(
                "astrbot_plugin_mailrelay_guard.mailrelay_guard.offline_monitor.time.time",
                return_value=self.base + offset,
            ):
                await monitor._apply_probe(config, state, _ProbeResult(state=raw))
        data = await self.view()
        self.assertEqual(calls, [1, 1])
        self.assertEqual(data["incidents"][0]["started_at"], self.base + 60)
        self.assertEqual(data["summary"]["offline_seconds"], 120)

    async def test_history_write_error_does_not_block_notice(self):
        config = load_settings({"offline_failure_threshold": 1})
        monitor = OfflinePlatformMonitor(
            SimpleNamespace(), lambda: config, SimpleNamespace()
        )
        monitor._history = self.store
        calls = []

        async def notify(*args, **kwargs):
            calls.append(kwargs["kind"])

        monitor._notify_transition = notify
        with patch.object(
            self.store, "_record_sync", side_effect=OfflineHistoryError("disk error")
        ):
            await monitor._apply_probe(
                config, _PlatformState(key="qq:10001"), _ProbeResult(state="offline")
            )
        self.assertEqual(calls, ["offline"])

    async def test_dashboard_rejects_invalid_filters(self):
        routes = []
        context = SimpleNamespace(
            register_web_api=lambda path, handler, methods, description: routes.append(
                (path, methods)
            )
        )
        plugin = MailRelayGuardPlugin(context, {})
        self.assertIn(
            ("/astrbot_plugin_mailrelay_guard/webui/offline-history", ["GET"]), routes
        )
        module = "astrbot_plugin_mailrelay_guard.main"
        for period, account in [("all", ""), ("24h", "x" * 201)]:
            with (
                patch(module + ".query_value", side_effect=[period, account]),
                patch(module + ".error_response", side_effect=lambda message, **kw: kw),
            ):
                self.assertEqual(
                    (await plugin.webui_offline_history())["status_code"], 400
                )
        plugin._offline_monitor._history = self.store
        with (
            patch(module + ".query_value", side_effect=["7d", "qq:10001"]),
            patch(module + ".json_response", side_effect=lambda value: value),
        ):
            self.assertTrue((await plugin.webui_offline_history())["available"])
