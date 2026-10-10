"""QQ 机器人掉线检测与邮件告警。

通过周期性探测平台适配器（主要是 aiocqhttp / NapCat / LLOneBot）的连接状态，
在机器人掉线或恢复时，用可自定义的 HTML 模板向配置的收件人发送邮件提醒。

设计要点：
- 反向 WebSocket 的 OneBot 客户端断开后 bot._wsr_api_clients 会清空，这是最可靠
  的一手信号；仍连接时再调用 get_status 捕捉“连接在、但账号被踢下线”的情况。
- 多账号场景下调用 call_action 必须显式传 self_id，否则会误判离线。
- 发信主题会折叠所有空白/换行，避免邮件头注入。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

try:  # pragma: no cover - astrbot 运行环境
    from astrbot.api import logger
except ImportError:  # pragma: no cover - 本地测试回退
    import logging

    logger = logging.getLogger("mailrelay_guard.offline_monitor")

from .config import (
    DEFAULT_OFFLINE_BODY_TEMPLATE,
    DEFAULT_OFFLINE_SUBJECT_TEMPLATE,
    DEFAULT_RECOVERY_BODY_TEMPLATE,
    DEFAULT_RECOVERY_SUBJECT_TEMPLATE,
    MailRelaySettings,
    configuration_problems,
    is_placeholder_address,
)
from .offline_history import OfflineHistoryError, OfflineHistoryStore
from .policy import (
    MailRelayValidationError,
    validate_dispatch_request,
    validate_email_address,
)
from .smtp_client import MailRelayTransportError, SMTPMailRelayClient

PROBE_TIMEOUT_SECONDS = 10.0
IDLE_SLEEP_SECONDS = 30.0
MIN_LOOP_SLEEP_SECONDS = 30.0

# 代表“连接已断开 / 不可达”的异常类名（按名字匹配，避免强依赖 aiocqhttp）。
_OFFLINE_EXC_NAMES = frozenset(
    {
        "ApiNotAvailable",
        "NetworkError",
        "HttpFailed",
        "WebSocketError",
    }
)
# OneBot 已收到请求但执行失败：说明连接是通的，只是该动作不被支持。
_ACTION_FAILED_NAMES = frozenset({"ActionFailed"})

_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _render(template: str, variables: dict[str, str]) -> str:
    """用 {{name}} 占位符渲染模板；未知占位符原样保留。"""

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables:
            return str(variables[key])
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, str(template or ""))


def _clean_subject(value: str) -> str:
    """折叠所有空白与换行，防止邮件主题头注入。"""

    return " ".join(str(value or "").split())


def _format_downtime(seconds: float) -> str:
    """把秒数格式化成“X天Y小时Z分W秒”，省略为零的高位单位。"""

    total = int(max(0, round(seconds)))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if hours or parts:
        parts.append(f"{hours}小时")
    if minutes or parts:
        parts.append(f"{minutes}分")
    parts.append(f"{secs}秒")
    return "".join(parts)


def _connected_self_ids(client: Any) -> list[str] | None:
    """反向 WS 客户端的在线账号列表；非反向 WS 客户端返回 None。"""

    api_clients = getattr(client, "_wsr_api_clients", None)
    if isinstance(api_clients, dict):
        return [str(self_id) for self_id in api_clients]
    return None


@dataclass
class _PlatformState:
    """单个平台适配器的掉线判定状态。"""

    key: str
    online: bool = True  # 乐观假设：启动时认为在线，避免首次探测即误报。
    fail_count: int = 0
    first_fail_at: float | None = None
    offline_since: float | None = None
    bot_id: str = ""
    nickname: str = ""
    platform_name: str = ""
    last_reason: str = ""
    pending_kind: str = ""
    pending_recipients: tuple[str, ...] = ()
    retry_count: int = 0
    pending_downtime: str = ""
    probe_state: str = "unknown"


@dataclass
class _ProbeResult:
    """一次探测结果。"""

    state: str  # "online" / "offline" / "unknown"
    reason: str = ""
    bot_id: str = ""
    nickname: str = ""
    platform_name: str = ""


class OfflinePlatformMonitor:
    """周期性探测平台连接状态并在掉线/恢复时发信。"""

    def __init__(
        self,
        context: Any,
        settings_provider: Callable[[], MailRelaySettings],
        smtp_client: SMTPMailRelayClient,
        audit_hook: Callable[..., Any] | None = None,
    ) -> None:
        self._context = context
        self._settings_provider = settings_provider
        self._smtp_client = smtp_client
        self._audit_hook = audit_hook
        self._states: dict[str, _PlatformState] = {}
        self._last_check_at: float | None = None
        self._last_error: str = ""
        self._wake_event = asyncio.Event()
        self._check_lock = asyncio.Lock()
        self._notification_lock = asyncio.Lock()
        self._history: OfflineHistoryStore | None = None

    async def initialize_history(self, data_dir: Path) -> None:
        history = OfflineHistoryStore(data_dir)
        try:
            await history.initialize()
        except OfflineHistoryError as exc:
            logger.warning("掉线历史不可用：%s", exc)
            return
        self._history = history

    async def history_overview(
        self, *, period: str = "7d", account: str = ""
    ) -> dict[str, Any]:
        if self._history is None:
            return {
                "available": False,
                "message": "掉线历史存储暂时不可用，请检查 AstrBot 日志和数据目录权限。",
            }
        try:
            return await self._history.overview(period=period, account=account)
        except OfflineHistoryError as exc:
            logger.warning("读取掉线历史失败：%s", exc)
            return {"available": False, "message": "暂时无法读取掉线历史，请稍后重试。"}

    async def _record_history(
        self, settings: MailRelaySettings, state: _PlatformState, observed_at: float
    ) -> None:
        if self._history is None:
            return
        status = (
            "unknown"
            if state.probe_state == "unknown"
            else "offline"
            if not state.online
            else "suspect"
            if state.fail_count
            else "online"
        )
        try:
            await self._history.record(
                key=state.key,
                observed_at=observed_at,
                state=status,
                reason=state.last_reason,
                interval=settings.offline_check_interval_seconds,
                offline_since=state.offline_since,
                platform=state.platform_name,
                bot_id=state.bot_id,
                nickname=state.nickname,
            )
        except OfflineHistoryError as exc:
            logger.warning("记录掉线历史失败（不影响通知）：%s", exc)

    def wake(self) -> None:
        """配置保存后立即重读，避免等待旧的检测间隔。"""
        self._wake_event.set()

    # ------------------------------------------------------------------ 生命周期

    async def run(self) -> None:
        """常驻后台任务：按配置间隔探测，关闭时空转。"""

        while True:
            interval = IDLE_SLEEP_SECONDS
            try:
                settings = self._settings_provider()
            except Exception as exc:  # noqa: BLE001 - isolate configuration provider
                logger.warning("掉线监控读取配置失败：%s", exc)
                settings = None

            try:
                if (
                    settings is None
                    or not settings.enabled
                    or not settings.enable_offline_notifications
                ):
                    self.reset()
                else:
                    await self.check_once(settings)
                    interval = max(
                        MIN_LOOP_SLEEP_SECONDS,
                        float(settings.offline_check_interval_seconds),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - background task boundary
                self._last_error = str(exc)
                logger.warning("掉线监控检测出错：%s", exc)

            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=interval)
            except TimeoutError:
                pass
            self._wake_event.clear()

    async def check_once(self, settings: MailRelaySettings | None = None) -> None:
        """执行一轮探测。可独立调用（命令 / WebUI 手动触发）。"""

        if settings is None:
            settings = self._settings_provider()
        if not settings.enabled or not settings.enable_offline_notifications:
            self.reset()
            return
        async with self._check_lock:
            await self._check_platforms(settings)

    async def _check_platforms(self, settings: MailRelaySettings) -> None:
        self._last_check_at = time.time()
        self._last_error = ""

        active_keys: set[str] = set()
        for key, platform, meta in self._collect_platforms(settings):
            try:
                client = platform.get_client()
            except Exception as exc:  # noqa: BLE001 - isolate adapter client creation
                logger.warning("读取平台 %s 客户端失败：%s", key, exc)
                self._last_error = "平台客户端暂时不可用，请查看 AstrBot 日志。"
                for value in self._states.values():
                    if value.key == key or value.key.startswith(key + ":"):
                        active_keys.add(value.key)
                        value.probe_state = "unknown"
                        value.last_reason = "平台客户端暂时不可用"
                        value.fail_count = 0
                        value.first_fail_at = None
                        await self._record_history(settings, value, time.time())
                continue
            ids = _connected_self_ids(client)
            # Keep disconnected accounts until the adapter is removed. Otherwise one
            # connected QQ account would hide a second account's disconnection.
            known = {
                value.bot_id
                for value in self._states.values()
                if value.key.startswith(key + ":") and value.bot_id
            }
            targets = sorted(set(ids or []) | known) or [""]
            if key in self._states and targets != [""]:
                previous = self._states.pop(key)
                previous.bot_id = targets[0]
                previous.key = key + ":" + targets[0]
                self._states[previous.key] = previous
                if self._history is not None:
                    try:
                        await self._history.remap_account(key, previous.key)
                    except OfflineHistoryError as exc:
                        logger.warning("更新掉线历史账号失败：%s", exc)
            for bot_id in targets:
                account_key = key + ":" + bot_id if bot_id else key
                active_keys.add(account_key)
                state = self._states.setdefault(
                    account_key, _PlatformState(key=account_key, bot_id=bot_id)
                )
                await self._check_account(settings, state, platform, meta, bot_id)

        # 清理已不再被监控的平台状态。
        for stale in [key for key in self._states if key not in active_keys]:
            self._states.pop(stale, None)
            if self._history is not None:
                try:
                    await self._history.break_account(stale)
                except OfflineHistoryError as exc:
                    logger.warning("结束掉线历史观测失败：%s", exc)

    async def _check_account(self, settings, state, platform, meta, bot_id):
        try:
            probe = await self._evaluate(
                platform, meta, need_identity=not state.nickname, self_id=bot_id or None
            )
            await self._apply_probe(settings, state, probe)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - isolate platform adapter
            self._last_error = "平台状态探测失败，请查看 AstrBot 日志。"
            logger.warning("探测平台 %s 时出错：%s", state.key, exc)

    def reset(self) -> None:
        """清空运行态（关闭监控或插件卸载时调用）。"""

        self._states.clear()
        if self._history is not None:
            self._history.restart_session()
        self._last_check_at = None
        self._last_error = ""

    # ------------------------------------------------------------------ 平台收集

    def _collect_platforms(
        self, settings: MailRelaySettings
    ) -> list[tuple[str, Any, Any]]:
        monitored = settings.offline_monitored_platforms
        if not monitored:
            return []
        manager = getattr(self._context, "platform_manager", None)
        platforms = getattr(manager, "platform_insts", None) or []
        collected: list[tuple[str, Any, Any]] = []
        for platform in platforms:
            try:
                meta = platform.meta()
            except Exception as exc:  # noqa: BLE001 - third-party metadata boundary
                logger.debug("读取监控平台元数据失败：%s", exc)
                continue
            name = str(getattr(meta, "name", "") or "").casefold()
            pid = str(getattr(meta, "id", "") or "").casefold()
            if name in monitored or pid in monitored:
                key = str(getattr(meta, "id", "") or "") or name or pid
                if key:
                    collected.append((key, platform, meta))
        return collected

    # ------------------------------------------------------------------ 探测逻辑

    async def _evaluate(
        self,
        platform: Any,
        meta: Any,
        *,
        need_identity: bool,
        self_id: str | None = None,
    ) -> _ProbeResult:
        platform_name = str(getattr(meta, "name", "") or "")
        result = _ProbeResult(state="unknown", platform_name=platform_name)

        try:
            client = platform.get_client()
        except Exception as exc:  # noqa: BLE001 - third-party client boundary
            result.reason = f"无法获取平台客户端：{exc}"
            return result
        if client is None:
            result.reason = "平台客户端尚未就绪"
            return result

        self_ids = _connected_self_ids(client)
        if self_ids is not None and (
            len(self_ids) == 0 or (self_id and self_id not in self_ids)
        ):
            result.state = "offline"
            result.reason = "OneBot 客户端连接已断开"
            return result

        self_id = self_id or (self_ids[0] if self_ids else None)
        if self_id:
            result.bot_id = str(self_id)

        call_action = getattr(client, "call_action", None)
        if not callable(call_action):
            # 没有可用的探测接口：有连接证据则视为在线，否则无法判定。
            result.state = "online" if self_ids else "unknown"
            if result.state == "unknown":
                result.reason = "平台客户端不支持状态探测"
            return result

        kwargs: dict[str, Any] = {"self_id": self_id} if self_id else {}
        try:
            data = await asyncio.wait_for(
                call_action("get_status", **kwargs), PROBE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            result.state = "offline"
            result.reason = "状态探测超时"
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - classify adapter-specific exceptions
            classified = self._classify_exception(exc)
            if classified == "action_failed":
                # 连接是通的，只是 get_status 不被支持；用 get_login_info 复核。
                return await self._confirm_via_login(
                    call_action, kwargs, result, need_identity=need_identity
                )
            if classified == "offline":
                result.state = "offline"
                result.reason = self._offline_reason(exc)
                return result
            result.reason = f"状态探测异常：{exc}"
            logger.warning("探测 %s 状态时遇到未分类异常：%r", platform_name, exc)
            return result

        if isinstance(data, dict) and data.get("online") in (False, 0):
            result.state = "offline"
            result.reason = "平台报告账号已离线"
            return result

        if not isinstance(data, dict) or data.get("online") not in (True, 1):
            result.reason = "平台未返回可判定的在线状态"
            return result
        result.state = "online"
        if need_identity:
            await self._fill_identity(call_action, kwargs, result)
        return result

    async def _confirm_via_login(
        self,
        call_action: Callable[..., Any],
        kwargs: dict[str, Any],
        result: _ProbeResult,
        *,
        need_identity: bool,
    ) -> _ProbeResult:
        try:
            info = await asyncio.wait_for(
                call_action("get_login_info", **kwargs), PROBE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            result.state = "offline"
            result.reason = "状态探测超时"
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - classify adapter-specific exceptions
            if self._classify_exception(exc) == "offline":
                result.state = "offline"
                result.reason = self._offline_reason(exc)
            else:
                result.reason = f"状态探测异常：{exc}"
            return result

        if isinstance(info, dict) and info.get("user_id"):
            result.state = "online"
            self._apply_login_info(info, result)
        else:
            result.reason = "平台未返回账号身份，暂时无法判定"
        return result

    async def _fill_identity(
        self,
        call_action: Callable[..., Any],
        kwargs: dict[str, Any],
        result: _ProbeResult,
    ) -> None:
        try:
            info = await asyncio.wait_for(
                call_action("get_login_info", **kwargs), PROBE_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - identity lookup is optional
            logger.debug("读取机器人昵称失败：%s", exc)
            return
        if isinstance(info, dict):
            self._apply_login_info(info, result)

    @staticmethod
    def _apply_login_info(info: dict[str, Any], result: _ProbeResult) -> None:
        user_id = info.get("user_id")
        nickname = info.get("nickname")
        if user_id not in (None, ""):
            result.bot_id = str(user_id)
        if nickname:
            result.nickname = str(nickname)

    @staticmethod
    def _classify_exception(exc: Exception) -> str:
        name = type(exc).__name__
        if name in _ACTION_FAILED_NAMES:
            return "action_failed"
        if name in _OFFLINE_EXC_NAMES:
            return "offline"
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return "offline"
        return "unknown"

    @staticmethod
    def _offline_reason(exc: Exception) -> str:
        name = type(exc).__name__
        if name == "ApiNotAvailable":
            return "OneBot 客户端连接已断开"
        if isinstance(exc, (ConnectionError, OSError)):
            return "与平台的网络连接中断"
        return "与平台的连接不可用"

    # ------------------------------------------------------------------ 状态迁移

    async def _apply_probe(
        self, settings: MailRelaySettings, state: _PlatformState, probe: _ProbeResult
    ) -> None:
        observed_at = time.time()
        state.probe_state = probe.state
        if probe.platform_name:
            state.platform_name = probe.platform_name
        if probe.bot_id:
            state.bot_id = probe.bot_id
        if probe.nickname:
            state.nickname = probe.nickname

        if probe.state == "unknown":
            state.fail_count = 0
            state.first_fail_at = None
            if probe.reason:
                state.last_reason = probe.reason
            await self._record_history(settings, state, observed_at)
            return

        if probe.state == "online":
            state.last_reason = ""
            state.fail_count = 0
            state.first_fail_at = None
            was_offline = not state.online
            offline_since = state.offline_since
            state.online = True
            state.offline_since = None
            await self._record_history(settings, state, observed_at)
            if was_offline:
                downtime = ""
                if offline_since is not None:
                    downtime = _format_downtime(observed_at - offline_since)
                state.last_reason = ""
                if settings.offline_notify_on_recovery:
                    state.pending_kind = ""
                    await self._notify_transition(
                        settings,
                        kind="recovery",
                        state=state,
                        reason="连接已恢复",
                        downtime=downtime,
                    )
            elif (
                state.pending_kind == "recovery" and settings.offline_notify_on_recovery
            ):
                await self._retry_notification(settings, state)
            if not settings.offline_notify_on_recovery:
                state.pending_kind = ""
            return

        # offline
        state.last_reason = probe.reason or "连接中断"
        state.fail_count += 1
        if state.first_fail_at is None:
            state.first_fail_at = observed_at
        threshold = max(1, settings.offline_failure_threshold)
        if state.online and state.fail_count >= threshold:
            state.online = False
            state.offline_since = state.first_fail_at
            await self._record_history(settings, state, observed_at)
            await self._notify_transition(
                settings,
                kind="offline",
                state=state,
                reason=state.last_reason,
                downtime="",
            )
        else:
            await self._record_history(settings, state, observed_at)
            if not state.online and state.pending_kind == "offline":
                await self._retry_notification(settings, state)

    async def _notify_transition(self, settings, *, kind, state, reason, downtime):
        result = await self._send_notification(
            settings, kind=kind, state=state, reason=reason, downtime=downtime
        )
        state.pending_kind = kind if result["failed"] else ""
        state.pending_recipients = tuple(result["failed"])
        state.retry_count = 0
        state.pending_downtime = downtime

    async def _retry_notification(self, settings, state):
        if state.retry_count >= 3:
            return
        state.retry_count += 1
        result = await self._send_notification(
            settings,
            kind=state.pending_kind,
            state=state,
            reason=state.last_reason,
            downtime=state.pending_downtime,
            only_recipients=state.pending_recipients,
        )
        state.pending_recipients = tuple(result["failed"])
        if not result["failed"]:
            state.pending_kind = ""

    # ------------------------------------------------------------------ 发信

    def _recipients(self, settings: MailRelaySettings) -> list[str]:
        if settings.offline_notify_recipients:
            unique = {
                value.casefold(): value
                for value in sorted(settings.offline_notify_recipients)
            }
            return list(unique.values())
        owner = str(settings.owner_email or "").strip()
        if owner and not is_placeholder_address(owner):
            return [owner]
        return []

    def _variables(
        self, state: _PlatformState, *, reason: str, downtime: str
    ) -> dict[str, str]:
        bot_id = state.bot_id or ""
        nickname = state.nickname or bot_id or "QQ 机器人"
        platform = state.platform_name or "aiocqhttp"
        return {
            "bot_id": bot_id or "未知",
            "nickname": nickname,
            "platform": platform,
            "time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
            "reason": reason or "连接中断",
            "downtime": downtime or "未知",
        }

    @staticmethod
    def _plain_body(kind: str, variables: dict[str, str]) -> str:
        if kind == "recovery":
            lines = [
                "【我的邮件】QQ 机器人已恢复",
                "",
                f"机器人：{variables['nickname']}",
                f"账号：{variables['bot_id']}",
                f"平台：{variables['platform']}",
                f"恢复时间：{variables['time']}",
                f"本次掉线：{variables['downtime']}",
                "",
                "机器人已重新上线，服务恢复正常。",
                "此邮件由「我的邮件」自动发送，请勿直接回复。",
            ]
            return "\n".join(lines)
        lines = [
            "【我的邮件】QQ 机器人掉线提醒",
            "",
            f"机器人：{variables['nickname']}",
            f"账号：{variables['bot_id']}",
            f"平台：{variables['platform']}",
            f"掉线时间：{variables['time']}",
            f"原因：{variables['reason']}",
            "",
            "请尽快检查 NapCat / LLOneBot 的运行状态与网络连接。",
            "此邮件由「我的邮件」自动发送，请勿直接回复。",
        ]
        return "\n".join(lines)

    def _templates(
        self, settings: MailRelaySettings, kind: str
    ) -> tuple[str, str, str]:
        if kind == "recovery":
            return (
                settings.recovery_subject_template or DEFAULT_RECOVERY_SUBJECT_TEMPLATE,
                settings.recovery_body_template or DEFAULT_RECOVERY_BODY_TEMPLATE,
                DEFAULT_RECOVERY_SUBJECT_TEMPLATE,
            )
        return (
            settings.offline_subject_template or DEFAULT_OFFLINE_SUBJECT_TEMPLATE,
            settings.offline_body_template or DEFAULT_OFFLINE_BODY_TEMPLATE,
            DEFAULT_OFFLINE_SUBJECT_TEMPLATE,
        )

    async def _send_notification(
        self,
        settings: MailRelaySettings,
        *,
        kind: str,
        state: _PlatformState,
        reason: str,
        downtime: str,
        only_recipients: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        recipients = self._recipients(settings)
        if only_recipients is not None:
            recipients = [value for value in recipients if value in only_recipients]
        problems = configuration_problems(settings)
        if problems:
            return {
                "ok": False,
                "recipients": [],
                "failed": recipients,
                "message": "通知未发送：" + " ".join(problems),
            }
        if not recipients:
            logger.warning("检测到%s事件，但未配置掉线通知收件人，已跳过发信。", kind)
            await self._audit(
                settings,
                action=f"offline_{kind}",
                outcome="blocked",
                detail="no_recipient",
            )
            return {
                "ok": False,
                "recipients": [],
                "failed": [],
                "message": "未配置收件人",
            }

        subject_tpl, body_tpl, subject_default = self._templates(settings, kind)
        variables = self._variables(state, reason=reason, downtime=downtime)
        subject = _clean_subject(_render(subject_tpl, variables))
        if not subject:
            subject = _clean_subject(_render(subject_default, variables))
        html_body = _render(
            body_tpl,
            {key: escape(value, quote=True) for key, value in variables.items()},
        )
        plain_body = self._plain_body(kind, variables)
        try:
            validate_dispatch_request(
                settings,
                [settings.sender_address],
                subject,
                plain_body,
                enforce_recipient_policy=False,
            )
        except MailRelayValidationError as exc:
            return {
                "ok": False,
                "recipients": [],
                "failed": recipients,
                "message": str(exc),
            }

        sent: list[str] = []
        failed: list[str] = []
        for recipient in recipients:
            try:
                address = validate_email_address(recipient, field_name="掉线通知收件人")
            except MailRelayValidationError as exc:
                logger.warning("掉线通知收件人无效（%s）：%s", recipient, exc)
                failed.append(recipient)
                continue
            try:
                async with self._notification_lock:
                    result = await self._smtp_client.send(
                        settings,
                        [address],
                        subject,
                        plain_body,
                        html_body=html_body,
                    )
            except MailRelayTransportError as exc:
                logger.warning("向 %s 发送掉线通知失败：%s", address, exc)
                failed.append(address)
                continue
            except Exception as exc:  # noqa: BLE001 - SMTP notification boundary
                logger.warning("向 %s 发送掉线通知异常：%s", address, exc)
                failed.append(address)
                continue
            if result.is_complete:
                sent.append(address)
            else:
                failed.append(address)

        if sent and not failed:
            outcome = "success"
        elif sent:
            outcome = "partial"
        else:
            outcome = "failure"
        await self._audit(
            settings,
            action=f"offline_{kind}",
            outcome=outcome,
            recipients=tuple(sent),
            detail=f"failed={len(failed)}" if failed else "",
        )
        return {
            "ok": bool(sent) and not failed,
            "recipients": sent,
            "failed": failed,
            "message": f"通知邮件已提交（{len(sent)} 位收件人）。"
            if sent and not failed
            else f"通知发送失败或部分失败：成功 {len(sent)}，失败 {len(failed)}。请检查 SMTP 配置与日志。",
        }

    async def _audit(
        self,
        settings: MailRelaySettings,
        *,
        action: str,
        outcome: str,
        recipients: tuple[str, ...] = (),
        detail: str = "",
    ) -> None:
        if self._audit_hook is None:
            return
        try:
            await self._audit_hook(
                settings,
                action=action,
                outcome=outcome,
                recipients=recipients,
                detail=detail,
            )
        except Exception as exc:  # noqa: BLE001 - audit must not interrupt monitoring
            logger.debug("掉线通知审计写入失败：%s", exc)

    # ------------------------------------------------------------------ 对外接口

    async def send_test(
        self, reason: str = "这是一封掉线通知测试邮件"
    ) -> dict[str, Any]:
        """用离线样式发送一封测试邮件，便于验证收件人与模板。"""

        settings = self._settings_provider()
        state: _PlatformState | None = None
        for value in self._states.values():
            state = value
            break
        if state is None:
            state = _PlatformState(
                key="test",
                platform_name="aiocqhttp",
                bot_id="10001",
                nickname="测试机器人",
            )
        return await self._send_notification(
            settings,
            kind="offline",
            state=state,
            reason=reason,
            downtime="3分20秒",
        )

    def status(self) -> dict[str, Any]:
        """返回监控运行态，供 WebUI / 命令展示。"""

        monitored = []
        now = time.time()
        for state in self._states.values():
            downtime = ""
            if state.offline_since is not None:
                downtime = _format_downtime(now - state.offline_since)
            monitored.append(
                {
                    "key": state.key,
                    "platform": state.platform_name,
                    "online": state.online,
                    "fail_count": state.fail_count,
                    "bot_id": state.bot_id,
                    "nickname": state.nickname,
                    "offline_since": state.offline_since,
                    "downtime": downtime,
                    "last_reason": state.last_reason,
                    "notification_pending": bool(state.pending_kind),
                    "retry_count": state.retry_count,
                    "probe_state": state.probe_state,
                }
            )
        monitored.sort(key=lambda item: item["key"])
        return {
            "last_check_at": self._last_check_at,
            "last_error": self._last_error,
            "monitored": monitored,
        }

    def recipient_summary(self, settings: MailRelaySettings) -> dict[str, Any]:
        recipients = self._recipients(settings)
        return {
            "count": len(recipients),
            "source": "custom" if settings.offline_notify_recipients else "owner",
        }
