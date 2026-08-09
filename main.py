"""MailRelay Guard: direct, policy-scoped SMTP delivery for AstrBot."""

from __future__ import annotations

import asyncio
import secrets
import time
from pathlib import Path
from typing import Any, Literal

from astrbot.api import AstrBotConfig, FunctionTool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.star.filter.command import GreedyStr
from pydantic import Field
from pydantic.dataclasses import dataclass as pydantic_dataclass

from .mailrelay_guard.audit import AuditWriter
from .mailrelay_guard.bindings import MailboxBindingError, MailboxBindingStore
from .mailrelay_guard.config import (
    MailRelaySettings,
    configuration_problems,
    is_placeholder_address,
    load_settings,
)
from .mailrelay_guard.history import HistoryMessage, MailHistoryError, MailHistoryStore
from .mailrelay_guard.html_sanitizer import prepare_html_mail, prepare_html_preview
from .mailrelay_guard.identity import (
    ActorIdentity,
    SelfMailboxResolver,
    actor_matches_configured_ids,
    event_is_admin,
    event_is_private_chat,
    get_actor_identity,
)
from .mailrelay_guard.policy import (
    MailRelayValidationError,
    parse_recipients,
    validate_dispatch_request,
    validate_email_address,
)
from .mailrelay_guard.rate_limit import (
    KeyedWindowRateLimiter,
    SuccessWindowRateLimiter,
)
from .mailrelay_guard.smtp_client import MailRelayTransportError, SMTPMailRelayClient
from .mailrelay_guard.web_api import (
    error_response,
    json_response,
    query_value,
    read_json_body,
)

PLUGIN_ID = "astrbot_plugin_mailrelay_guard"
PLUGIN_VERSION = "v1.3.4"
ONE_HOUR_SECONDS = 60 * 60
RecipientMode = Literal["owner", "self", "other", "binding"]
MailContentFormat = Literal["plain", "html"]

_WEBUI_BOOLEAN_SETTINGS = frozenset(
    {
        "enabled",
        "enable_owner_delivery",
        "enable_self_delivery",
        "enable_admin_other_delivery",
        "require_private_chat_for_self_delivery",
        "enable_html_mail",
        "sanitize_html_before_send",
        "html_allow_links",
        "html_allow_remote_images",
        "mail_history_enabled",
        "mail_history_store_content",
    }
)
_WEBUI_INTEGER_SETTINGS = {
    "max_html_body_chars",
    "mail_history_retention_days",
    "mail_history_max_records",
    "max_messages_per_hour",
    "max_successful_messages_per_actor_per_hour",
    "max_delivery_attempts_per_actor_per_hour",
    "actor_min_send_interval_seconds",
}
_WEBUI_LIST_SETTINGS = frozenset({"html_remote_image_allowed_domains"})


def _tool_event(context: ContextWrapper[AstrAgentContext]) -> AstrMessageEvent | None:
    """Read the original chat event from AstrBot's FunctionTool wrapper."""

    agent_context = getattr(context, "context", None)
    event = getattr(agent_context, "event", None)
    return event if event is not None else None


@pydantic_dataclass
class MailRelayNotifyOwnerTool(FunctionTool[AstrAgentContext]):
    """Directly notify the configured owner; no recipient can be supplied."""

    plugin: Any = Field(default=None, repr=False)
    name: str = "mailrelay_notify_owner"
    description: str = (
        "向配置中的主人固定邮箱发送纯文本邮件。收件人由插件配置固定，不能修改。"
        "仅当当前聊天发送者同时在 owner_sender_ids 中且为 AstrBot 管理员时调用。"
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "邮件主题。"},
                "body": {"type": "string", "description": "纯文本邮件正文。"},
            },
            "required": ["subject", "body"],
            "additionalProperties": False,
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> str:
        if self.plugin is None:
            return "MailRelay Guard 当前不可用。"
        return await self.plugin.deliver_from_tool(
            event=_tool_event(context),
            mode="owner",
            subject=str(kwargs.get("subject", "")),
            body=str(kwargs.get("body", "")),
        )


@pydantic_dataclass
class MailRelaySendToSelfTool(FunctionTool[AstrAgentContext]):
    """Send only to the current sender's resolved, self-owned mailbox."""

    plugin: Any = Field(default=None, repr=False)
    name: str = "mailrelay_send_to_self"
    description: str = (
        "仅向当前聊天发送者已验证绑定的邮箱，或从当前 QQ/NapCat 资料解析出的邮箱"
        "发送纯文本邮件。此工具没有收件人参数，绝不能用于向他人发送。"
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "邮件主题。"},
                "body": {"type": "string", "description": "纯文本邮件正文。"},
            },
            "required": ["subject", "body"],
            "additionalProperties": False,
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> str:
        if self.plugin is None:
            return "MailRelay Guard 当前不可用。"
        return await self.plugin.deliver_from_tool(
            event=_tool_event(context),
            mode="self",
            subject=str(kwargs.get("subject", "")),
            body=str(kwargs.get("body", "")),
        )


@pydantic_dataclass
class MailRelaySendToRecipientTool(FunctionTool[AstrAgentContext]):
    """Administrator-only direct delivery to a caller-provided recipient."""

    plugin: Any = Field(default=None, repr=False)
    name: str = "mailrelay_send_to_recipient"
    description: str = (
        "向明确指定的收件人发送纯文本邮件。仅供同时在 admin_sender_ids 中且为 "
        "AstrBot 管理员的当前发送者使用。普通用户应使用 mailrelay_send_to_self "
        "向自己的邮箱发送。"
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "recipients": {
                    "type": "string",
                    "description": "一个或多个收件邮箱地址，多个地址以逗号分隔。",
                },
                "subject": {"type": "string", "description": "邮件主题。"},
                "body": {"type": "string", "description": "纯文本邮件正文。"},
            },
            "required": ["recipients", "subject", "body"],
            "additionalProperties": False,
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> str:
        if self.plugin is None:
            return "MailRelay Guard 当前不可用。"
        return await self.plugin.deliver_from_tool(
            event=_tool_event(context),
            mode="other",
            recipients_input=str(kwargs.get("recipients", "")),
            subject=str(kwargs.get("subject", "")),
            body=str(kwargs.get("body", "")),
        )


@pydantic_dataclass
class MailRelayNotifyOwnerHtmlTool(FunctionTool[AstrAgentContext]):
    """Directly notify the configured owner with a sanitized HTML email."""

    plugin: Any = Field(default=None, repr=False)
    name: str = "mailrelay_notify_owner_html"
    description: str = (
        "向配置中的主人固定邮箱发送 HTML 模板邮件。请提供完整 HTML 片段并使用内联 "
        "CSS。插件会在 SMTP 投递前清洗 HTML，并自动生成纯文本备用内容。收件人由 "
        "配置固定，不能修改；仅配置的主人且为 AstrBot 管理员时可调用。"
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "邮件主题。"},
                "html_body": {
                    "type": "string",
                    "description": "完整 HTML 邮件正文。使用内联 CSS，不要使用脚本、表单或外链资源。",
                },
            },
            "required": ["subject", "html_body"],
            "additionalProperties": False,
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> str:
        if self.plugin is None:
            return "MailRelay Guard 当前不可用。"
        return await self.plugin.deliver_from_tool(
            event=_tool_event(context),
            mode="owner",
            subject=str(kwargs.get("subject", "")),
            body="",
            html_body=str(kwargs.get("html_body", "")),
            content_format="html",
        )


@pydantic_dataclass
class MailRelaySendHtmlToSelfTool(FunctionTool[AstrAgentContext]):
    """Send a sanitized HTML email only to the current sender's mailbox."""

    plugin: Any = Field(default=None, repr=False)
    name: str = "mailrelay_send_html_to_self"
    description: str = (
        "仅向当前聊天发送者已解析或已验证绑定的邮箱发送 HTML 模板邮件。请提供完整 "
        "HTML 片段并使用内联 CSS。插件会在投递前清洗 HTML 并生成纯文本备用内容。"
        "此工具没有收件人参数，绝不能用于向他人发送。"
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "邮件主题。"},
                "html_body": {
                    "type": "string",
                    "description": "完整 HTML 邮件正文。使用内联 CSS，不要使用脚本、表单或外链资源。",
                },
            },
            "required": ["subject", "html_body"],
            "additionalProperties": False,
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> str:
        if self.plugin is None:
            return "MailRelay Guard 当前不可用。"
        return await self.plugin.deliver_from_tool(
            event=_tool_event(context),
            mode="self",
            subject=str(kwargs.get("subject", "")),
            body="",
            html_body=str(kwargs.get("html_body", "")),
            content_format="html",
        )


@pydantic_dataclass
class MailRelaySendHtmlToRecipientTool(FunctionTool[AstrAgentContext]):
    """Administrator-only sanitized HTML delivery to explicit recipients."""

    plugin: Any = Field(default=None, repr=False)
    name: str = "mailrelay_send_html_to_recipient"
    description: str = (
        "向明确指定的收件人发送 HTML 模板邮件。仅供同时在 admin_sender_ids 中且为 "
        "AstrBot 管理员的当前发送者使用。插件会在投递前清洗 HTML 并生成纯文本备用 "
        "内容；普通用户应使用 mailrelay_send_html_to_self。"
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "recipients": {
                    "type": "string",
                    "description": "一个或多个收件邮箱地址，多个地址以逗号分隔。",
                },
                "subject": {"type": "string", "description": "邮件主题。"},
                "html_body": {
                    "type": "string",
                    "description": "完整 HTML 邮件正文。使用内联 CSS，不要使用脚本、表单或外链资源。",
                },
            },
            "required": ["recipients", "subject", "html_body"],
            "additionalProperties": False,
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> str:
        if self.plugin is None:
            return "MailRelay Guard 当前不可用。"
        return await self.plugin.deliver_from_tool(
            event=_tool_event(context),
            mode="other",
            recipients_input=str(kwargs.get("recipients", "")),
            subject=str(kwargs.get("subject", "")),
            body="",
            html_body=str(kwargs.get("html_body", "")),
            content_format="html",
        )


class MailRelayGuardPlugin(Star):
    """Direct SMTP delivery with recipient modes enforced at the final boundary."""

    def __init__(
        self,
        context: Context,
        config: AstrBotConfig | dict[str, Any] | None = None,
    ) -> None:
        super().__init__(context, config)
        self.context = context
        self.config = config or {}
        settings = self._settings()
        self._smtp_client = SMTPMailRelayClient()
        self._global_success_limiter = SuccessWindowRateLimiter()
        self._actor_success_limiter = KeyedWindowRateLimiter(
            max_keys=settings.max_tracked_actors
        )
        self._actor_attempt_limiter = KeyedWindowRateLimiter(
            max_keys=settings.max_tracked_actors
        )
        self._send_lock = asyncio.Lock()
        self._history_lock = asyncio.Lock()
        self._audit_writer: AuditWriter | None = None
        self._mailboxes: MailboxBindingStore | None = None
        self._data_dir: Path | None = None
        self._mail_history: MailHistoryStore | None = None
        self._mailbox_resolver = SelfMailboxResolver(None)
        self._llm_tools_registered = False
        self._html_tools_registered = False
        self._register_dashboard_apis()

    async def initialize(self) -> None:
        """Prepare private storage and register the direct, mode-scoped LLM tools."""

        settings = self._settings()
        try:
            data_dir = Path(StarTools.get_data_dir(PLUGIN_ID))
            self._data_dir = data_dir
            self._audit_writer = AuditWriter(
                data_dir,
                max_file_kb=settings.audit_max_file_kb,
            )
            self._mailboxes = MailboxBindingStore(data_dir)
            self._mailbox_resolver = SelfMailboxResolver(self._mailboxes)
        except (OSError, RuntimeError) as exc:
            logger.warning("[%s] local storage unavailable: %s", PLUGIN_ID, exc)

        await self._ensure_mail_history(settings)

        if settings.enabled and settings.enable_llm_mail_tools:
            self.context.add_llm_tools(
                MailRelayNotifyOwnerTool(plugin=self),
                MailRelaySendToSelfTool(plugin=self),
                MailRelaySendToRecipientTool(plugin=self),
            )
            self._llm_tools_registered = True
            if settings.enable_html_mail:
                self.context.add_llm_tools(
                    MailRelayNotifyOwnerHtmlTool(plugin=self),
                    MailRelaySendHtmlToSelfTool(plugin=self),
                    MailRelaySendHtmlToRecipientTool(plugin=self),
                )
                self._html_tools_registered = True

        logger.info(
            "[%s] initialized | smtp=%s:%s security=%s direct_tools=%s html_tools=%s",
            PLUGIN_ID,
            settings.smtp_host or "(unset)",
            settings.smtp_port,
            settings.smtp_security,
            self._llm_tools_registered,
            self._html_tools_registered,
        )

    async def terminate(self) -> None:
        """Discard in-memory profile caches and pending verification codes."""

        self._mailboxes = None
        self._mail_history = None
        self._data_dir = None
        self._mailbox_resolver = SelfMailboxResolver(None)
        logger.info("[%s] terminated", PLUGIN_ID)

    def _register_dashboard_apis(self) -> None:
        """Expose the dashboard-only API used by the plugin Page bridge."""

        register = getattr(self.context, "register_web_api", None)
        if not callable(register):
            return
        routes = (
            ("/webui/summary", self.webui_summary, ["GET"], "MailRelay summary"),
            ("/webui/messages", self.webui_messages, ["GET"], "MailRelay messages"),
            (
                "/webui/message/<message_id>",
                self.webui_message,
                ["GET"],
                "MailRelay message detail",
            ),
            ("/webui/settings", self.webui_settings_get, ["GET"], "MailRelay settings"),
            (
                "/webui/settings",
                self.webui_settings_update,
                ["POST"],
                "MailRelay settings update",
            ),
            (
                "/webui/smtp-probe",
                self.webui_smtp_probe,
                ["POST"],
                "MailRelay SMTP probe",
            ),
            (
                "/webui/mailbox-state",
                self.webui_mailbox_state,
                ["POST"],
                "MailRelay local mailbox state",
            ),
            (
                "/webui/history-clear",
                self.webui_history_clear,
                ["POST"],
                "MailRelay history clear",
            ),
        )
        for suffix, handler, methods, description in routes:
            register(f"/{PLUGIN_ID}{suffix}", handler, methods, description)

    async def webui_summary(self):
        """Return redacted operational data for the protected Mail Center page."""

        settings = self._settings()
        history = await self._ensure_mail_history(settings)
        try:
            history_summary = await history.summary() if history is not None else None
        except MailHistoryError as exc:
            logger.warning("[%s] history summary unavailable: %s", PLUGIN_ID, exc)
            history_summary = None
        return json_response(
            {
                "version": PLUGIN_VERSION,
                "readiness": "ready" if not configuration_problems(settings) else "needs_config",
                "configuration_problems": configuration_problems(settings),
                "smtp": {
                    "host": settings.smtp_host or None,
                    "port": settings.smtp_port,
                    "security": settings.smtp_security,
                    "account_configured": not is_placeholder_address(
                        settings.smtp_username
                    ),
                    "sender_configured": not is_placeholder_address(
                        settings.sender_address
                    ),
                },
                "features": {
                    "llm_tools_registered": self._llm_tools_registered,
                    "html_tools_registered": self._html_tools_registered,
                    "html_mail_enabled": settings.enable_html_mail,
                    "html_strict_cleaning": settings.sanitize_html_before_send,
                    "history_enabled": settings.mail_history_enabled,
                    "history_store_content": settings.mail_history_store_content,
                    "history_available": history is not None,
                },
                "history": history_summary,
            }
        )

    async def webui_messages(self):
        """List privacy-scoped outbox, delivery mirror, or failure rows."""

        settings = self._settings()
        history = await self._ensure_mail_history(settings)
        if history is None:
            return json_response(
                {
                    "enabled": False,
                    "items": [],
                    "total": 0,
                    "limit": 0,
                    "offset": 0,
                    "has_more": False,
                }
            )
        folder = str(query_value("folder", "sent") or "sent").casefold()
        if folder not in {"sent", "inbox", "errors"}:
            return error_response("不支持的邮件视图。", status_code=400)
        limit = max(1, min(100, int(query_value("limit", 50, int))))
        offset = max(0, int(query_value("offset", 0, int)))
        try:
            payload = await history.list_messages(
                folder=folder,  # type: ignore[arg-type]
                query=str(query_value("query", "") or ""),
                status=str(query_value("status", "") or "").casefold(),
                content_format=str(query_value("format", "") or "").casefold(),
                limit=limit,
                offset=offset,
            )
        except MailHistoryError as exc:
            logger.warning("[%s] history list unavailable: %s", PLUGIN_ID, exc)
            return error_response("本地邮件历史暂时不可用。", status_code=503)
        payload["enabled"] = True
        payload["folder"] = folder
        payload["content_recording"] = settings.mail_history_store_content
        return json_response(payload)

    async def webui_message(self, message_id: str):
        """Return one message detail and a second-pass-safe HTML preview."""

        if not _is_hex_token(message_id, 32):
            return error_response("邮件记录标识无效。", status_code=400)
        settings = self._settings()
        history = await self._ensure_mail_history(settings)
        if history is None:
            return error_response("邮件历史尚未启用。", status_code=404)
        try:
            record = await history.get_message(message_id)
        except MailHistoryError as exc:
            logger.warning("[%s] history detail unavailable: %s", PLUGIN_ID, exc)
            return error_response("本地邮件历史暂时不可用。", status_code=503)
        if record is None:
            return error_response("未找到该邮件记录。", status_code=404)
        archived_html = record.pop("html_body", None)
        if archived_html:
            try:
                record["html_preview"] = prepare_html_preview(settings, archived_html)
            except MailRelayValidationError:
                record["html_preview"] = ""
        else:
            record["html_preview"] = ""
        return json_response(record)

    async def webui_settings_get(self):
        """Return only non-secret settings that the Mail Center can edit."""

        return json_response(self._webui_settings_payload(self._settings()))

    async def webui_settings_update(self):
        """Update a strict allowlist of non-secret runtime controls."""

        payload = await read_json_body({})
        updates = payload.get("settings") if isinstance(payload, dict) else None
        if not isinstance(updates, dict):
            return error_response("请求必须包含 settings 对象。", status_code=400)
        try:
            changed = await self._update_webui_settings(updates)
        except (TypeError, ValueError) as exc:
            return error_response(str(exc), status_code=400)
        except OSError as exc:
            logger.warning("[%s] dashboard config save failed: %s", PLUGIN_ID, exc)
            return error_response("配置保存失败。", status_code=500)
        settings = self._settings()
        await self._ensure_mail_history(settings)
        return json_response(
            {
                "changed": sorted(changed),
                "restart_required": "enable_html_mail" in changed,
                "settings": self._webui_settings_payload(settings),
            }
        )

    async def webui_smtp_probe(self):
        """Test TLS/login from the authenticated Dashboard without sending mail."""

        settings = self._settings()
        problems = configuration_problems(settings)
        if problems:
            return error_response("SMTP 配置尚未就绪。", data=problems, status_code=400)
        try:
            await self._smtp_client.test_connection(settings)
        except MailRelayTransportError as exc:
            await self._audit(
                settings,
                action="webui_smtp_probe",
                outcome="failed",
                actor=None,
                detail="transport_error",
            )
            return error_response(str(exc), status_code=400)
        await self._audit(
            settings,
            action="webui_smtp_probe",
            outcome="succeeded",
            actor=None,
        )
        return json_response({"message": "SMTP 连接与登录测试成功，未发送邮件。"})

    async def webui_mailbox_state(self):
        """Persist read/star/archive state for one accepted local delivery copy."""

        payload = await read_json_body({})
        if not isinstance(payload, dict):
            return error_response("请求正文无效。", status_code=400)
        message_id = str(payload.get("message_id", ""))
        recipient_token = str(payload.get("recipient_token", ""))
        if not _is_hex_token(message_id, 32) or not _is_hex_token(recipient_token, 24):
            return error_response("投递副本标识无效。", status_code=400)
        values = {
            key: payload[key]
            for key in ("is_read", "is_starred", "archived")
            if key in payload
        }
        if not values or any(not isinstance(value, bool) for value in values.values()):
            return error_response("邮箱状态必须是布尔值。", status_code=400)
        history = await self._ensure_mail_history(self._settings())
        if history is None:
            return error_response("邮件历史尚未启用。", status_code=404)
        try:
            state = await history.update_mailbox_state(
                history_id=message_id,
                recipient_token=recipient_token,
                is_read=values.get("is_read"),
                is_starred=values.get("is_starred"),
                archived=values.get("archived"),
            )
        except MailHistoryError as exc:
            logger.warning("[%s] mailbox state update failed: %s", PLUGIN_ID, exc)
            return error_response("本地投递副本更新失败。", status_code=503)
        if state is None:
            return error_response("未找到可更新的 SMTP 已接受副本。", status_code=404)
        return json_response(state)

    async def webui_history_clear(self):
        """Clear optional local history after an explicit browser confirmation."""

        payload = await read_json_body({})
        if not isinstance(payload, dict) or payload.get("confirm") != "clear-mail-history":
            return error_response("需要明确确认后才能清空本地邮件历史。", status_code=400)
        history = await self._ensure_mail_history(self._settings())
        if history is None:
            return error_response("邮件历史尚未启用。", status_code=404)
        try:
            removed = await history.clear()
        except MailHistoryError as exc:
            logger.warning("[%s] history clear failed: %s", PLUGIN_ID, exc)
            return error_response("清空本地邮件历史失败。", status_code=503)
        return json_response({"removed": removed})

    async def _ensure_mail_history(
        self, settings: MailRelaySettings
    ) -> MailHistoryStore | None:
        if not settings.mail_history_enabled:
            return None
        if self._mail_history is not None:
            return self._mail_history
        if self._data_dir is None:
            return None
        async with self._history_lock:
            if self._mail_history is not None:
                return self._mail_history
            try:
                history = MailHistoryStore(self._data_dir)
                await history.initialize(
                    retention_days=settings.mail_history_retention_days,
                    max_records=settings.mail_history_max_records,
                )
            except (MailHistoryError, OSError, RuntimeError) as exc:
                logger.warning("[%s] mail history unavailable: %s", PLUGIN_ID, exc)
                return None
            self._mail_history = history
            return history

    async def _begin_history_delivery(
        self,
        *,
        settings: MailRelaySettings,
        action: str,
        mode: RecipientMode,
        actor: ActorIdentity,
        recipients: list[str],
        content_format: MailContentFormat,
    ) -> str | None:
        history = await self._ensure_mail_history(settings)
        if history is None:
            return None
        try:
            return await history.begin_delivery(
                action=action,
                mode=mode,
                content_format=content_format,
                actor_id=actor.key,
                recipients=recipients,
                retention_days=settings.mail_history_retention_days,
                max_records=settings.mail_history_max_records,
            )
        except MailHistoryError as exc:
            logger.warning("[%s] history create failed: %s", PLUGIN_ID, exc)
            return None

    async def _finalize_history_delivery(
        self,
        history_id: str | None,
        result: HistoryMessage,
        settings: MailRelaySettings,
    ) -> None:
        if not history_id or self._mail_history is None:
            return
        try:
            await self._mail_history.finalize_delivery(
                history_id,
                result,
                retention_days=settings.mail_history_retention_days,
                max_records=settings.mail_history_max_records,
            )
        except MailHistoryError as exc:
            logger.warning("[%s] history finalize failed: %s", PLUGIN_ID, exc)

    async def _update_webui_settings(self, updates: dict[str, Any]) -> set[str]:
        if not updates:
            raise ValueError("没有可保存的设置。")
        unsupported = set(updates) - (
            _WEBUI_BOOLEAN_SETTINGS | _WEBUI_INTEGER_SETTINGS | _WEBUI_LIST_SETTINGS
        )
        if unsupported:
            raise ValueError("包含不允许从 WebUI 修改的配置项。")
        for key in _WEBUI_BOOLEAN_SETTINGS & set(updates):
            if not isinstance(updates[key], bool):
                raise TypeError(f"{key} 必须是布尔值。")
        for key in _WEBUI_INTEGER_SETTINGS & set(updates):
            if isinstance(updates[key], bool) or not isinstance(updates[key], int):
                raise TypeError(f"{key} 必须是整数。")
        for key in _WEBUI_LIST_SETTINGS & set(updates):
            if not isinstance(updates[key], list) or any(
                not isinstance(value, str) for value in updates[key]
            ):
                raise ValueError(f"{key} 必须是字符串列表。")

        candidate = dict(self.config)
        candidate.update(updates)
        normalized = load_settings(candidate)
        changed: set[str] = set()
        for key in updates:
            value = getattr(normalized, key)
            if isinstance(value, frozenset):
                value = sorted(value)
            if self.config.get(key) != value:
                self.config[key] = value
                changed.add(key)
        saver = getattr(self.config, "save_config", None)
        if changed and callable(saver):
            saver()
        return changed

    def _webui_settings_payload(self, settings: MailRelaySettings) -> dict[str, Any]:
        return {
            "settings": {
                "enabled": settings.enabled,
                "enable_owner_delivery": settings.enable_owner_delivery,
                "enable_self_delivery": settings.enable_self_delivery,
                "enable_admin_other_delivery": settings.enable_admin_other_delivery,
                "require_private_chat_for_self_delivery": (
                    settings.require_private_chat_for_self_delivery
                ),
                "enable_html_mail": settings.enable_html_mail,
                "sanitize_html_before_send": settings.sanitize_html_before_send,
                "html_allow_links": settings.html_allow_links,
                "html_allow_remote_images": settings.html_allow_remote_images,
                "html_remote_image_allowed_domains": sorted(
                    settings.html_remote_image_allowed_domains
                ),
                "max_html_body_chars": settings.max_html_body_chars,
                "mail_history_enabled": settings.mail_history_enabled,
                "mail_history_store_content": settings.mail_history_store_content,
                "mail_history_retention_days": settings.mail_history_retention_days,
                "mail_history_max_records": settings.mail_history_max_records,
                "max_messages_per_hour": settings.max_messages_per_hour,
                "max_successful_messages_per_actor_per_hour": (
                    settings.max_successful_messages_per_actor_per_hour
                ),
                "max_delivery_attempts_per_actor_per_hour": (
                    settings.max_delivery_attempts_per_actor_per_hour
                ),
                "actor_min_send_interval_seconds": (
                    settings.actor_min_send_interval_seconds
                ),
            },
            "restart_required_fields": ["enable_html_mail"],
            "secret_fields": ["smtp_username", "smtp_password", "sender_address"],
        }

    @filter.command("mailrelay_whoami", alias={"邮件身份"})
    async def mailrelay_whoami(self, event: AstrMessageEvent):
        """Show the platform-scoped identity required by owner/admin allowlists."""

        actor = get_actor_identity(event)
        if actor is None:
            yield event.plain_result("无法读取当前平台和发送者 ID。")
            return
        yield event.plain_result(
            "MailRelay Guard 身份信息\n"
            f"- platform_id: {actor.platform_id}\n"
            f"- platform_name: {actor.platform_name}\n"
            f"- sender_id: {actor.sender_id}\n"
            f"- 推荐 allowlist 写法: {actor.key}\n"
            f"- AstrBot admin: {event_is_admin(event)}"
        )

    @filter.command("mailrelay_identity", alias={"邮件地址状态"})
    async def mailrelay_identity(self, event: AstrMessageEvent):
        """Show whether the caller has a self-delivery mailbox, without revealing it."""

        settings = self._settings()
        actor = get_actor_identity(event)
        if actor is None:
            yield event.plain_result("无法读取当前平台和发送者 ID。")
            return
        mailbox = await self._mailbox_resolver.resolve(event, settings, actor)
        if mailbox is None:
            yield event.plain_result(
                "未找到可用于‘发给自己’的邮箱。请私聊爱丽丝后执行 "
                "/mailrelay_bind 你的邮箱，再按邮件中的验证码执行 /mailrelay_verify。"
            )
            return
        yield event.plain_result(
            "已找到你的自助收件邮箱。\n"
            f"- 来源: {_mailbox_source_label(mailbox.source)}\n"
            "- 为保护隐私，聊天中不显示邮箱地址。"
        )

    @filter.command("mailrelay_bind", alias={"绑定邮件邮箱"})
    async def mailrelay_bind(self, event: AstrMessageEvent, email: str = ""):
        """Email a one-time code to bind the sender's own fallback mailbox."""

        yield event.plain_result(await self.request_mailbox_binding(event, email))

    @filter.command("mailrelay_verify", alias={"验证邮件邮箱"})
    async def mailrelay_verify(self, event: AstrMessageEvent, code: str = ""):
        """Confirm a pending self-mailbox binding using its emailed code."""

        yield event.plain_result(await self.verify_mailbox_binding(event, code))

    @filter.command("mailrelay_unbind", alias={"解绑邮件邮箱"})
    async def mailrelay_unbind(self, event: AstrMessageEvent):
        """Delete the caller's stored self-mailbox binding."""

        yield event.plain_result(await self.remove_mailbox_binding(event))

    @filter.command("mailrelay_self", alias={"邮件给自己"})
    async def mailrelay_self(self, event: AstrMessageEvent, payload: GreedyStr):
        """Directly send a plain-text email to the current sender only."""

        try:
            subject, body = _parse_subject_body(payload)
        except MailRelayValidationError as exc:
            yield event.plain_result(f"邮件未发送：{exc}")
            return
        yield event.plain_result(
            await self.deliver_from_tool(
                event=event,
                mode="self",
                subject=subject,
                body=body,
                action="command_self",
            )
        )

    @filter.command("mailrelay_owner", alias={"邮件给主人"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_owner(self, event: AstrMessageEvent, payload: GreedyStr):
        """Directly notify the fixed owner mailbox for the configured owner only."""

        try:
            subject, body = _parse_subject_body(payload)
        except MailRelayValidationError as exc:
            yield event.plain_result(f"邮件未发送：{exc}")
            return
        yield event.plain_result(
            await self.deliver_from_tool(
                event=event,
                mode="owner",
                subject=subject,
                body=body,
                action="command_owner",
            )
        )

    @filter.command("mailrelay_send", alias={"邮件发给别人"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_send(self, event: AstrMessageEvent, payload: GreedyStr):
        """Administrator-only delivery to explicitly specified recipients."""

        try:
            recipients, subject, body = _parse_other_payload(payload)
        except MailRelayValidationError as exc:
            yield event.plain_result(f"邮件未发送：{exc}")
            return
        yield event.plain_result(
            await self.deliver_from_tool(
                event=event,
                mode="other",
                recipients_input=recipients,
                subject=subject,
                body=body,
                action="command_other",
            )
        )

    @filter.command("mailrelay_status", alias={"邮件中继状态"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_status(self, event: AstrMessageEvent):
        """Show administrator-only redacted configuration and readiness state."""

        settings = self._settings()
        actor = get_actor_identity(event)
        if not self._is_configured_admin(event, actor, settings):
            yield event.plain_result("无法查看 MailRelay Guard 状态：需要配置的 AstrBot 管理员身份。")
            return
        yield event.plain_result(self._format_status(settings))

    @filter.command("mailrelay_smtp_test", alias={"邮件中继连接测试"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_smtp_test(self, event: AstrMessageEvent):
        """Test SMTP TLS/login without sending an email."""

        settings = self._settings()
        actor = get_actor_identity(event)
        if not self._is_configured_admin(event, actor, settings):
            yield event.plain_result("SMTP 测试未执行：需要配置的 AstrBot 管理员身份。")
            return
        problems = configuration_problems(settings)
        if problems:
            yield event.plain_result(self._format_configuration_problem("SMTP 测试未执行", problems))
            return
        try:
            await self._smtp_client.test_connection(settings)
        except MailRelayTransportError as exc:
            await self._audit(
                settings,
                action="smtp_test",
                outcome="failed",
                actor=actor,
                detail="transport_error",
            )
            logger.warning("[%s] SMTP test failed: %s", PLUGIN_ID, exc)
            yield event.plain_result(f"SMTP 测试失败：{exc}")
            return
        await self._audit(
            settings,
            action="smtp_test",
            outcome="succeeded",
            actor=actor,
        )
        yield event.plain_result("SMTP 连接和登录测试成功，尚未发送邮件。")

    @filter.command("mailrelay_send_test", alias={"邮件中继发测试信"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_send_test(self, event: AstrMessageEvent):
        """Send a fixed test message only to the configured owner mailbox."""

        settings = self._settings()
        actor = get_actor_identity(event)
        if not self._is_configured_admin(event, actor, settings):
            yield event.plain_result("测试邮件未发送：需要配置的 AstrBot 管理员身份。")
            return
        denied = self._authorization_denial(event, actor, settings, "owner")
        if denied:
            yield event.plain_result(f"测试邮件未发送：{denied}")
            return
        try:
            recipients = await self._recipients_for_mode(
                event=event,
                actor=actor,
                settings=settings,
                mode="owner",
                recipients_input="",
            )
        except MailRelayValidationError as exc:
            yield event.plain_result(f"测试邮件未发送：{exc}")
            return
        now_text = time.strftime("%Y-%m-%d %H:%M:%S")
        yield event.plain_result(
            await self._deliver(
                event=event,
                actor=actor,
                settings=settings,
                mode="owner",
                recipients=recipients,
                subject="MailRelay Guard SMTP 测试邮件",
                body=(
                    "这是一封由 MailRelay Guard 发出的测试邮件。\n"
                    f"发送时间：{now_text}\n"
                    "如果你收到此邮件，说明 SMTP、TLS 和授权码工作正常。"
                ),
                action="test_owner",
            )
        )

    async def deliver_from_tool(
        self,
        *,
        event: AstrMessageEvent | None,
        mode: RecipientMode,
        subject: str,
        body: str,
        recipients_input: str = "",
        action: str | None = None,
        html_body: str | None = None,
        content_format: MailContentFormat = "plain",
    ) -> str:
        """The shared final boundary for LLM tools and interactive commands."""

        if event is None:
            return "邮件未发送：无法读取当前会话事件。"
        settings = self._settings()
        actor = get_actor_identity(event)
        if actor is None:
            return "邮件未发送：无法读取当前平台和发送者 ID。"
        actual_action = action or f"llm_{mode}"
        if content_format not in {"plain", "html"}:
            return "邮件未发送：不支持的邮件内容格式。"
        if content_format == "html" and not settings.enable_html_mail:
            await self._audit(
                settings,
                action=actual_action,
                outcome="blocked",
                actor=actor,
                detail="html_disabled",
            )
            return "邮件未发送：管理员尚未开启 HTML 邮件功能。"
        denied = self._authorization_denial(event, actor, settings, mode)
        if denied:
            await self._audit(
                settings,
                action=actual_action,
                outcome="blocked",
                actor=actor,
                detail="authorization",
            )
            return f"邮件未发送：{denied}"

        problems = configuration_problems(settings)
        if problems:
            return self._format_configuration_problem("邮件未发送", problems)

        try:
            recipients = await self._recipients_for_mode(
                event=event,
                actor=actor,
                settings=settings,
                mode=mode,
                recipients_input=recipients_input,
            )
            if content_format == "plain":
                validate_dispatch_request(
                    settings,
                    recipients,
                    subject,
                    body,
                    enforce_recipient_policy=(
                        mode == "other" and settings.restrict_admin_other_recipients
                    ),
                )
        except MailRelayValidationError as exc:
            await self._audit(
                settings,
                action=actual_action,
                outcome="blocked",
                actor=actor,
                detail=type(exc).__name__,
            )
            return f"邮件未发送：{exc}"

        return await self._deliver(
            event=event,
            actor=actor,
            settings=settings,
            mode=mode,
            recipients=recipients,
            subject=subject.strip(),
            body=body.strip(),
            action=actual_action,
            html_body=html_body if content_format == "html" else None,
        )

    async def request_mailbox_binding(
        self, event: AstrMessageEvent, email: str
    ) -> str:
        """Send a verification code before storing a caller-provided fallback email."""

        settings = self._settings()
        actor = get_actor_identity(event)
        if actor is None:
            return "邮箱未绑定：无法读取当前平台和发送者 ID。"
        if not settings.enable_self_delivery:
            return "邮箱未绑定：管理员已关闭给自己发邮件。"
        if not settings.self_binding_enabled:
            return "邮箱未绑定：管理员已关闭自助邮箱绑定。"
        if settings.require_private_chat_for_binding and not event_is_private_chat(event):
            return "邮箱未绑定：为保护你的邮箱地址，请私聊爱丽丝后再执行此命令。"
        problems = configuration_problems(settings)
        if problems:
            return self._format_configuration_problem("邮箱未绑定", problems)
        if self._mailboxes is None:
            return "邮箱未绑定：插件本地存储不可用，无法安全保存验证状态。"

        try:
            address = validate_email_address(email, field_name="要绑定的邮箱")
        except MailRelayValidationError as exc:
            return f"邮箱未绑定：{exc}"

        code = f"{secrets.randbelow(1_000_000):06d}"
        try:
            await self._mailboxes.issue_challenge(
                actor_key=actor.key,
                address=address,
                code=code,
                ttl_seconds=settings.verification_code_ttl_seconds,
                resend_seconds=settings.verification_resend_seconds,
            )
        except MailboxBindingError as exc:
            return f"邮箱未绑定：{exc}"

        result = await self._deliver(
            event=event,
            actor=actor,
            settings=settings,
            mode="binding",
            recipients=[address],
            subject="MailRelay Guard 邮箱绑定验证码",
            body=(
                "你正在为当前聊天账号绑定此邮箱，用于‘发给自己’。\n"
                f"验证码：{code}\n"
                f"有效期：{settings.verification_code_ttl_seconds // 60} 分钟。\n"
                "若这不是你发起的操作，请忽略此邮件。"
            ),
            action="binding_challenge",
            expose_recipient=False,
        )
        if not result.startswith("邮件已提交"):
            await self._mailboxes.discard_challenge(actor.key)
            return result.replace("邮件", "验证码邮件", 1)
        return (
            "验证码已发送到你提交的邮箱。请在有效期内私聊爱丽丝执行 "
            "/mailrelay_verify 六位验证码。"
        )

    async def verify_mailbox_binding(self, event: AstrMessageEvent, code: str) -> str:
        """Persist a mailbox only after the current sender proves control of it."""

        settings = self._settings()
        actor = get_actor_identity(event)
        if actor is None:
            return "邮箱未验证：无法读取当前平台和发送者 ID。"
        if not settings.self_binding_enabled:
            return "邮箱未验证：管理员已关闭自助邮箱绑定。"
        if settings.require_private_chat_for_binding and not event_is_private_chat(event):
            return "邮箱未验证：请私聊爱丽丝后再执行验证命令。"
        if self._mailboxes is None:
            return "邮箱未验证：插件本地存储不可用。"
        code = code.strip()
        if not code:
            return "邮箱未验证：请提供邮件中的六位验证码。"
        if len(code) != 6 or not code.isdecimal():
            return "邮箱未验证：验证码必须是六位数字。"
        try:
            binding = await self._mailboxes.verify(
                actor_key=actor.key,
                code=code,
                max_attempts=settings.verification_max_attempts,
            )
        except (MailboxBindingError, OSError) as exc:
            return f"邮箱未验证：{exc}"
        await self._audit(
            settings,
            action="binding_verify",
            outcome="succeeded",
            actor=actor,
        )
        return f"邮箱已验证并绑定：{_mask_email(binding.address)}。现在可以让爱丽丝发邮件给你自己。"

    async def remove_mailbox_binding(self, event: AstrMessageEvent) -> str:
        """Delete persisted self-mail data without requiring SMTP to be healthy."""

        settings = self._settings()
        actor = get_actor_identity(event)
        if actor is None:
            return "邮箱未解绑：无法读取当前平台和发送者 ID。"
        if settings.require_private_chat_for_binding and not event_is_private_chat(event):
            return "邮箱未解绑：请私聊爱丽丝后再执行解绑命令。"
        if self._mailboxes is None:
            return "邮箱未解绑：插件本地存储不可用。"
        try:
            removed = await self._mailboxes.remove(actor.key)
        except OSError as exc:
            return f"邮箱未解绑：{exc}"
        if not removed:
            return "没有找到已保存的邮箱绑定。"
        await self._audit(
            settings,
            action="binding_remove",
            outcome="succeeded",
            actor=actor,
        )
        return "已删除你保存的邮箱绑定。"

    async def _recipients_for_mode(
        self,
        *,
        event: AstrMessageEvent,
        actor: ActorIdentity,
        settings: MailRelaySettings,
        mode: RecipientMode,
        recipients_input: str,
    ) -> list[str]:
        if mode == "owner":
            if is_placeholder_address(settings.owner_email):
                raise MailRelayValidationError("请先在 owner_email 中填写主人的真实收件邮箱。")
            return [validate_email_address(settings.owner_email, field_name="主人邮箱")]
        if mode == "self":
            mailbox = await self._mailbox_resolver.resolve(event, settings, actor)
            if mailbox is None:
                raise MailRelayValidationError(
                    "未找到你的 QQ/NapCat 资料邮箱或已验证绑定邮箱。"
                    "请私聊爱丽丝执行 /mailrelay_bind 你的邮箱。"
                )
            return [mailbox.address]
        if mode == "other":
            return parse_recipients(recipients_input)
        raise MailRelayValidationError("不支持的收件人模式。")

    def _authorization_denial(
        self,
        event: AstrMessageEvent,
        actor: ActorIdentity,
        settings: MailRelaySettings,
        mode: RecipientMode,
    ) -> str:
        if mode == "owner":
            if not settings.enable_owner_delivery:
                return "管理员已关闭给主人发邮件。"
            if not event_is_admin(event) or not actor_matches_configured_ids(
                actor, settings.owner_sender_ids
            ):
                return "仅允许配置的主人以 AstrBot 管理员身份发送给主人。"
            return ""
        if mode == "self":
            if not settings.enable_self_delivery:
                return "管理员已关闭给自己发邮件。"
            if (
                settings.require_private_chat_for_self_delivery
                and not event_is_private_chat(event)
            ):
                return "为保护你的邮箱，请私聊爱丽丝后再请求发送。"
            return ""
        if mode == "other":
            if not settings.enable_admin_other_delivery:
                return "管理员已关闭代发给其他收件人。"
            if not self._is_configured_admin(event, actor, settings):
                return "仅允许配置的 AstrBot 管理员给其他收件人发邮件。"
            return ""
        return ""

    def _is_configured_admin(
        self,
        event: AstrMessageEvent,
        actor: ActorIdentity | None,
        settings: MailRelaySettings,
    ) -> bool:
        return event_is_admin(event) and actor_matches_configured_ids(
            actor, settings.admin_sender_ids
        )

    async def _deliver(
        self,
        *,
        event: AstrMessageEvent,
        actor: ActorIdentity | None,
        settings: MailRelaySettings,
        mode: RecipientMode,
        recipients: list[str],
        subject: str,
        body: str,
        action: str,
        expose_recipient: bool = False,
        html_body: str | None = None,
    ) -> str:
        problems = configuration_problems(settings)
        if problems:
            return self._format_configuration_problem("邮件未发送", problems)
        if html_body is not None:
            try:
                prepared_html = prepare_html_mail(settings, html_body)
            except MailRelayValidationError as exc:
                await self._audit(
                    settings,
                    action=action,
                    outcome="blocked",
                    actor=actor,
                    recipients=recipients,
                    detail=type(exc).__name__,
                )
                return f"邮件未发送：{exc}"
            body = prepared_html.plain_body
            html_body = prepared_html.html_body
        try:
            validate_dispatch_request(
                settings,
                recipients,
                subject,
                body,
                enforce_recipient_policy=(
                    mode == "other" and settings.restrict_admin_other_recipients
                ),
                body_max_chars=(
                    settings.max_html_body_chars if html_body is not None else None
                ),
            )
        except MailRelayValidationError as exc:
            await self._audit(
                settings,
                action=action,
                outcome="blocked",
                actor=actor,
                recipients=recipients,
                detail=type(exc).__name__,
            )
            return f"邮件未发送：{exc}"

        if actor is None:
            return "邮件未发送：无法读取当前平台和发送者 ID。"
        async with self._send_lock:
            succeeded, response = await self._deliver_validated_locked(
                event=event,
                actor=actor,
                settings=settings,
                mode=mode,
                recipients=recipients,
                subject=subject,
                body=body,
                action=action,
                html_body=html_body,
            )
        if succeeded and expose_recipient:
            return response
        return response

    async def _deliver_validated_locked(
        self,
        *,
        event: AstrMessageEvent,
        actor: ActorIdentity,
        settings: MailRelaySettings,
        mode: RecipientMode,
        recipients: list[str],
        subject: str,
        body: str,
        action: str,
        html_body: str | None = None,
    ) -> tuple[bool, str]:
        if not self._global_success_limiter.can_send(
            max_messages=settings.max_messages_per_hour,
            window_seconds=ONE_HOUR_SECONDS,
        ):
            remaining = self._global_success_limiter.remaining_seconds(
                window_seconds=ONE_HOUR_SECONDS
            )
            await self._audit(
                settings,
                action=action,
                outcome="rate_limited",
                actor=actor,
                recipients=recipients,
                detail="global_success_limit",
            )
            return (
                False,
                f"邮件未发送：已达到每小时 {settings.max_messages_per_hour} 封的全局投递上限，请约 {remaining} 秒后重试。",
            )

        if not self._actor_success_limiter.can_record(
            actor.key,
            max_events=settings.max_successful_messages_per_actor_per_hour,
            window_seconds=ONE_HOUR_SECONDS,
        ):
            remaining = self._actor_success_limiter.remaining_seconds(
                actor.key,
                max_events=settings.max_successful_messages_per_actor_per_hour,
                window_seconds=ONE_HOUR_SECONDS,
            )
            await self._audit(
                settings,
                action=action,
                outcome="rate_limited",
                actor=actor,
                recipients=recipients,
                detail="actor_success_limit",
            )
            return (
                False,
                (
                    "邮件未发送：你本小时的成功投递次数已达上限，"
                    f"请约 {remaining} 秒后重试。"
                ),
            )

        if not self._actor_attempt_limiter.can_record(
            actor.key,
            max_events=settings.max_delivery_attempts_per_actor_per_hour,
            window_seconds=ONE_HOUR_SECONDS,
            minimum_interval_seconds=settings.actor_min_send_interval_seconds,
        ):
            remaining = self._actor_attempt_limiter.remaining_seconds(
                actor.key,
                max_events=settings.max_delivery_attempts_per_actor_per_hour,
                window_seconds=ONE_HOUR_SECONDS,
                minimum_interval_seconds=settings.actor_min_send_interval_seconds,
            )
            await self._audit(
                settings,
                action=action,
                outcome="rate_limited",
                actor=actor,
                recipients=recipients,
                detail="actor_attempt_limit",
            )
            return (
                False,
                (
                    "邮件未发送：你的发送冷却或尝试次数限制正在生效，"
                    f"请约 {remaining} 秒后重试。"
                ),
            )

        # Attempts are charged before SMTP so failed logins cannot be hammered forever.
        self._actor_attempt_limiter.record(actor.key)
        history_id = await self._begin_history_delivery(
            settings=settings,
            action=action,
            mode=mode,
            actor=actor,
            recipients=recipients,
            content_format="html" if html_body is not None else "plain",
        )
        try:
            if html_body is None:
                result = await self._smtp_client.send(
                    settings,
                    recipients,
                    subject,
                    body,
                )
            else:
                result = await self._smtp_client.send(
                    settings,
                    recipients,
                    subject,
                    body,
                    html_body=html_body,
                )
        except MailRelayTransportError as exc:
            await self._finalize_history_delivery(
                history_id,
                HistoryMessage(
                    message_id=None,
                    status="failed",
                    error_code="transport_error",
                ),
                settings,
            )
            await self._audit(
                settings,
                action=action,
                outcome="failed",
                actor=actor,
                recipients=recipients,
                detail="transport_error",
            )
            logger.warning("[%s] %s failed: %s", PLUGIN_ID, action, exc)
            return False, f"邮件未发送：{exc}"

        accepted_count = len(result.accepted_recipients)
        if accepted_count:
            self._global_success_limiter.record_success()
            self._actor_success_limiter.record(actor.key)
        history_status: Literal["submitted", "partial", "failed"]
        audit_outcome: Literal["succeeded", "partial", "failed"]
        if result.is_complete:
            history_status = "submitted"
            audit_outcome = "succeeded"
        elif accepted_count:
            history_status = "partial"
            audit_outcome = "partial"
        else:
            history_status = "failed"
            audit_outcome = "failed"
        await self._finalize_history_delivery(
            history_id,
            HistoryMessage(
                message_id=result.message_id,
                status=history_status,
                accepted_recipients=result.accepted_recipients,
                refused_recipients=result.refused_recipients,
                store_content=(
                    settings.mail_history_store_content and accepted_count > 0
                ),
                subject=subject,
                plain_body=body,
                html_body=html_body,
            ),
            settings,
        )
        await self._audit(
            settings,
            action=action,
            outcome=audit_outcome,
            actor=actor,
            recipients=recipients,
            detail=(
                f"mode={mode};format={'html' if html_body is not None else 'plain'};"
                f"accepted={accepted_count};"
                f"refused={len(result.refused_recipients)}"
            ),
        )
        if result.is_complete:
            return True, f"邮件已提交 SMTP 服务器，共 {accepted_count} 位收件人。"
        if accepted_count:
            return (
                True,
                (
                    "邮件已部分提交 SMTP 服务器："
                    f"成功 {accepted_count} 位，拒绝 {len(result.refused_recipients)} 位。"
                ),
            )
        return False, "邮件未发送：SMTP 服务器拒绝了所有收件人。"

    def _settings(self) -> MailRelaySettings:
        return load_settings(self.config)

    async def _audit(
        self,
        settings: MailRelaySettings,
        *,
        action: str,
        outcome: str,
        actor: ActorIdentity | None,
        recipients: list[str] | tuple[str, ...] = (),
        detail: str = "",
    ) -> None:
        if not settings.audit_log_enabled or self._audit_writer is None:
            return
        try:
            await self._audit_writer.append(
                action=action,
                outcome=outcome,
                actor_id=actor.key if actor is not None else "unknown",
                recipients=recipients,
                detail=detail,
            )
        except (OSError, RuntimeError) as exc:
            logger.warning("[%s] audit write failed: %s", PLUGIN_ID, exc)

    def _format_status(self, settings: MailRelaySettings) -> str:
        problems = configuration_problems(settings)
        readiness = "就绪" if not problems else "需要配置"
        lines = [
            "MailRelay Guard 状态",
            f"- 就绪状态：{readiness}",
            f"- SMTP：{settings.smtp_host or '(未填写)'}:{settings.smtp_port} / {settings.smtp_security}",
            f"- 发件账号：{_mask_email(settings.smtp_username)}",
            f"- 发件地址：{_mask_email(settings.sender_address)}",
            f"- 主人收件地址：{_mask_email(settings.owner_email)}",
            f"- 授权码：{'已填写' if settings.smtp_password and not settings.smtp_password.startswith('YOUR_') else '未填写'}",
            f"- LLM 直发工具：{'已注册' if self._llm_tools_registered else '未注册'}",
            f"- HTML 模板邮件：{'开启' if settings.enable_html_mail else '关闭'}",
            f"- HTML 邮件工具：{'已注册' if self._html_tools_registered else '未注册'}",
            f"- HTML 严格清洗：{'开启' if settings.sanitize_html_before_send else '关闭'}",
            f"- WebUI 邮件历史：{'开启' if settings.mail_history_enabled else '关闭'}",
            f"- WebUI 保存邮件内容：{'开启' if settings.mail_history_store_content else '关闭'}",
            f"- 自助发给自己：{'开启' if settings.enable_self_delivery else '关闭'}",
            f"- 管理员代发：{'开启' if settings.enable_admin_other_delivery else '关闭'}",
            f"- 管理员代发白名单：{'开启' if settings.restrict_admin_other_recipients else '关闭'}",
            f"- 邮箱绑定：{'开启' if settings.self_binding_enabled else '关闭'}",
            f"- NapCat 资料查询：{'开启' if settings.napcat_email_lookup_enabled else '关闭'}",
            f"- 全局成功投递上限：每小时 {settings.max_messages_per_hour} 封",
            f"- 单用户成功投递上限：每小时 {settings.max_successful_messages_per_actor_per_hour} 封",
            f"- 单用户发送冷却：{settings.actor_min_send_interval_seconds} 秒",
        ]
        if problems:
            lines.append("- 待处理：")
            lines.extend(f"  * {problem}" for problem in problems)
        return "\n".join(lines)

    @staticmethod
    def _format_configuration_problem(prefix: str, problems: list[str]) -> str:
        return prefix + "：\n" + "\n".join(f"- {problem}" for problem in problems)


def _parse_subject_body(payload: str) -> tuple[str, str]:
    parts = [part.strip() for part in str(payload or "").split("|", 1)]
    if len(parts) != 2 or not all(parts):
        raise MailRelayValidationError(
            "命令格式应为：主题 | 邮件正文，例如 /mailrelay_self 提醒 | 正文"
        )
    return parts[0], parts[1]


def _parse_other_payload(payload: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in str(payload or "").split("|", 2)]
    if len(parts) != 3 or not all(parts):
        raise MailRelayValidationError(
            "命令格式应为：收件人 | 主题 | 邮件正文，例如 "
            "/mailrelay_send person@example.com | 提醒 | 正文"
        )
    return parts[0], parts[1], parts[2]


def _mask_email(value: str) -> str:
    address = str(value or "").strip()
    if not address or "@" not in address:
        return "未填写"
    local, domain = address.rsplit("@", 1)
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"


def _is_hex_token(value: str, length: int) -> bool:
    candidate = str(value or "")
    return len(candidate) == length and all(char in "0123456789abcdef" for char in candidate)


def _mailbox_source_label(source: str) -> str:
    labels = {
        "configured_override": "管理员配置的身份映射",
        "verified_binding": "你已验证的绑定邮箱",
        "napcat_profile": "NapCat 当前 QQ 资料",
        "napcat_friend_profile": "NapCat QQ 好友资料",
        "qq_mailbox_derivation": "QQ 号邮箱推导（未验证）",
    }
    return labels.get(source, "未知来源")
