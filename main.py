"""MailRelay Guard: policy-constrained SMTP delivery for AstrBot.

This is an independent implementation. It intentionally keeps email delivery
small and explicit: plain-text mail only, no arbitrary attachments, a strict
recipient policy, administrator confirmation, and an optional LLM draft flow.
"""

import asyncio
import time
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, FunctionTool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.star.filter.command import GreedyStr
from pydantic import Field
from pydantic.dataclasses import dataclass as pydantic_dataclass

from .mailrelay_guard.audit import AuditWriter
from .mailrelay_guard.config import (
    MailRelaySettings,
    configuration_problems,
    load_settings,
)
from .mailrelay_guard.drafts import DraftAccessError, DraftStore
from .mailrelay_guard.policy import (
    MailRelayValidationError,
    parse_recipients,
    validate_dispatch_request,
)
from .mailrelay_guard.rate_limit import SuccessWindowRateLimiter
from .mailrelay_guard.smtp_client import MailRelayTransportError, SMTPMailRelayClient

PLUGIN_ID = "astrbot_plugin_mailrelay_guard"
PLUGIN_VERSION = "v1.0.0"
PLUGIN_DESC = "受策略保护的 SMTP 邮件投递：白名单、确认草稿、限流与最小审计。"
LLM_DRAFT_TOOL_NAME = "mailrelay_prepare_draft"
ONE_HOUR_SECONDS = 60 * 60


@pydantic_dataclass
class MailRelayDraftTool(FunctionTool[AstrAgentContext]):
    """An LLM tool that prepares a draft but cannot send email by itself."""

    plugin: Any = Field(default=None, repr=False)
    name: str = LLM_DRAFT_TOOL_NAME
    description: str = (
        "创建一封待管理员确认的纯文本邮件草稿，不会直接发送邮件。"
        "只在用户明确要求起草邮件且收件人符合 MailRelay Guard 白名单策略时调用。"
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "recipients": {
                    "type": "string",
                    "description": "一个或多个收件人邮箱，使用英文逗号分隔。",
                },
                "subject": {
                    "type": "string",
                    "description": "邮件主题。",
                },
                "body": {
                    "type": "string",
                    "description": "纯文本邮件正文。",
                },
            },
            "required": ["recipients", "subject", "body"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs: Any,
    ) -> str:
        if self.plugin is None:
            return "MailRelay Guard 草稿工具尚未绑定插件实例。"
        event = getattr(getattr(context, "context", None), "event", None)
        return await self.plugin.prepare_draft_from_llm(
            event=event,
            recipients=str(kwargs.get("recipients", "")),
            subject=str(kwargs.get("subject", "")),
            body=str(kwargs.get("body", "")),
        )


class MailRelayGuardPlugin(Star):
    """A deliberately constrained mail relay for AstrBot 4.16 through 4.x."""

    def __init__(
        self,
        context: Context,
        config: AstrBotConfig | dict[str, Any] | None = None,
    ) -> None:
        super().__init__(context, config)
        self.context = context
        self.config = config or {}
        self._smtp_client = SMTPMailRelayClient()
        self._drafts = DraftStore()
        self._rate_limiter = SuccessWindowRateLimiter()
        self._send_lock = asyncio.Lock()
        self._audit_writer: AuditWriter | None = None
        self._llm_draft_tool_registered = False

    async def initialize(self) -> None:
        """Initialize optional local audit storage and the draft-only LLM tool."""

        settings = self._settings()
        try:
            data_dir = Path(StarTools.get_data_dir(PLUGIN_ID))
            self._audit_writer = AuditWriter(
                data_dir,
                max_file_kb=settings.audit_max_file_kb,
            )
        except (OSError, RuntimeError) as exc:
            logger.warning("[%s] audit storage unavailable: %s", PLUGIN_ID, exc)

        if (
            settings.enabled
            and settings.enable_llm_draft_tool
            and settings.llm_tool_allowed_sender_ids
        ):
            self.context.add_llm_tools(MailRelayDraftTool(plugin=self))
            self._llm_draft_tool_registered = True
        elif settings.enable_llm_draft_tool:
            logger.warning(
                "[%s] LLM draft tool was not registered because llm_tool_allowed_sender_ids is empty",
                PLUGIN_ID,
            )

        logger.info(
            "[%s] initialized | smtp=%s:%s security=%s llm_drafts=%s",
            PLUGIN_ID,
            settings.smtp_host or "(unset)",
            settings.smtp_port,
            settings.smtp_security,
            self._llm_draft_tool_registered,
        )

    async def terminate(self) -> None:
        """Drop sensitive in-memory drafts when the plugin is unloaded."""

        self._drafts = DraftStore()
        logger.info("[%s] terminated", PLUGIN_ID)

    @filter.command("mailrelay_whoami", alias={"邮件中继身份"})
    async def mailrelay_whoami(self, event: AstrMessageEvent):
        """Show only the caller's identifiers used for MailRelay Guard allowlists."""

        yield event.plain_result(
            "MailRelay Guard 身份信息\n"
            f"- sender_id: {self._actor_id(event) or '(无法读取)'}\n"
            f"- is_admin: {self._is_admin(event)}\n"
            "将 sender_id 填入 command_allowed_sender_ids；需要 LLM 草稿时，也填入 "
            "llm_tool_allowed_sender_ids。"
        )

    @filter.command("mailrelay_status", alias={"邮件中继状态"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_status(self, event: AstrMessageEvent):
        """Show protected, redacted configuration and readiness status."""

        settings = self._settings()
        denied = self._control_denial_reason(event, settings)
        if denied:
            yield event.plain_result(f"无法查看 MailRelay Guard 状态：{denied}")
            return
        yield event.plain_result(self._format_status(settings))

    @filter.command("mailrelay_smtp_test", alias={"邮件中继连接测试"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_smtp_test(self, event: AstrMessageEvent):
        """Test SMTP TLS/login without delivering any email."""

        settings = self._settings()
        denied = self._control_denial_reason(event, settings)
        if denied:
            yield event.plain_result(f"SMTP 测试未执行：{denied}")
            return
        problems = configuration_problems(settings)
        if problems:
            yield event.plain_result(
                self._format_configuration_problem("SMTP 测试未执行", problems)
            )
            return

        try:
            await self._smtp_client.test_connection(settings)
        except MailRelayTransportError as exc:
            await self._audit(
                settings,
                action="smtp_test",
                outcome="failed",
                actor_id=self._actor_id(event),
                detail="transport_error",
            )
            logger.warning("[%s] SMTP test failed: %s", PLUGIN_ID, exc)
            yield event.plain_result(f"SMTP 测试失败：{exc}")
            return

        await self._audit(
            settings,
            action="smtp_test",
            outcome="succeeded",
            actor_id=self._actor_id(event),
        )
        yield event.plain_result("SMTP 连接和登录测试成功，尚未发送邮件。")

    @filter.command("mailrelay_send", alias={"邮件中继发送"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_send(self, event: AstrMessageEvent, payload: GreedyStr):
        """Send after explicit administrator input: recipient | subject | body."""

        settings = self._settings()
        denied = self._control_denial_reason(event, settings)
        if denied:
            yield event.plain_result(f"邮件未发送：{denied}")
            return
        try:
            recipients, subject, body = _parse_send_payload(payload)
        except MailRelayValidationError as exc:
            yield event.plain_result(f"邮件未发送：{exc}")
            return
        yield event.plain_result(
            await self._dispatch(
                event=event,
                settings=settings,
                recipients_input=recipients,
                subject=subject,
                body=body,
                action="manual_send",
            )
        )

    @filter.command("mailrelay_send_test", alias={"邮件中继发测试信"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_send_test(self, event: AstrMessageEvent, recipient: str = ""):
        """Send a fixed test message to the configured or supplied recipient."""

        settings = self._settings()
        denied = self._control_denial_reason(event, settings)
        if denied:
            yield event.plain_result(f"测试邮件未发送：{denied}")
            return
        target = recipient.strip() or settings.test_recipient
        if not target:
            yield event.plain_result(
                "测试邮件未发送：请填写 test_recipient，或在命令后提供收件人邮箱。"
            )
            return
        now_text = time.strftime("%Y-%m-%d %H:%M:%S")
        yield event.plain_result(
            await self._dispatch(
                event=event,
                settings=settings,
                recipients_input=target,
                subject="MailRelay Guard SMTP 测试邮件",
                body=(
                    "这是一封由 MailRelay Guard 发出的测试邮件。\n"
                    f"发送时间：{now_text}\n"
                    "如果你收到此邮件，说明 SMTP 配置、TLS 和授权码工作正常。"
                ),
                action="test_send",
            )
        )

    @filter.command("mailrelay_confirm", alias={"邮件中继确认"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_confirm(self, event: AstrMessageEvent, token: str):
        """Confirm a session-bound LLM draft and perform the one allowed delivery."""

        settings = self._settings()
        denied = self._control_denial_reason(event, settings)
        if denied:
            yield event.plain_result(f"草稿未确认：{denied}")
            return
        problems = configuration_problems(settings)
        if problems:
            yield event.plain_result(
                self._format_configuration_problem("草稿未确认", problems)
            )
            return

        actor_id = self._actor_id(event)
        origin = self._origin(event)
        async with self._send_lock:
            try:
                draft = await self._drafts.get_for_actor(
                    token,
                    actor_id=actor_id,
                    unified_msg_origin=origin,
                )
            except DraftAccessError as exc:
                response = f"草稿未确认：{exc}"
            else:
                try:
                    # Policy may change after a draft was created, so it is checked
                    # again at the final external-action boundary.
                    validate_dispatch_request(
                        settings,
                        list(draft.recipients),
                        draft.subject,
                        draft.body,
                    )
                except MailRelayValidationError as exc:
                    await self._audit(
                        settings,
                        action="draft_confirm",
                        outcome="blocked",
                        actor_id=actor_id,
                        recipients=list(draft.recipients),
                        detail=type(exc).__name__,
                    )
                    response = f"草稿未确认：{exc}"
                else:
                    succeeded, response = await self._deliver_validated_locked(
                        event=event,
                        settings=settings,
                        recipients=list(draft.recipients),
                        subject=draft.subject,
                        body=draft.body,
                        action="draft_confirm",
                    )
                    if succeeded:
                        await self._drafts.remove_for_actor(
                            draft.token,
                            actor_id=actor_id,
                            unified_msg_origin=origin,
                        )
        yield event.plain_result(response)

    @filter.command("mailrelay_cancel", alias={"邮件中继取消"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_cancel(self, event: AstrMessageEvent, token: str):
        """Discard a session-bound LLM draft without contacting SMTP."""

        settings = self._settings()
        denied = self._control_denial_reason(event, settings)
        if denied:
            yield event.plain_result(f"草稿未取消：{denied}")
            return
        try:
            async with self._send_lock:
                await self._drafts.remove_for_actor(
                    token,
                    actor_id=self._actor_id(event),
                    unified_msg_origin=self._origin(event),
                )
        except DraftAccessError as exc:
            yield event.plain_result(f"草稿未取消：{exc}")
            return
        await self._audit(
            settings,
            action="draft_cancel",
            outcome="succeeded",
            actor_id=self._actor_id(event),
        )
        yield event.plain_result("草稿已取消，未发送任何邮件。")

    async def prepare_draft_from_llm(
        self,
        *,
        event: AstrMessageEvent | None,
        recipients: str,
        subject: str,
        body: str,
    ) -> str:
        """Create a constrained draft for an allowlisted administrator only."""

        settings = self._settings()
        if event is None:
            return "无法创建草稿：当前没有会话事件。"
        if not settings.enable_llm_draft_tool:
            return "LLM 草稿工具已关闭。"
        if not self._is_admin(event):
            return "无法创建草稿：当前会话发送者不是 AstrBot 管理员。"
        actor_id = self._actor_id(event)
        if not actor_id or actor_id not in settings.llm_tool_allowed_sender_ids:
            return "无法创建草稿：当前发送者不在 llm_tool_allowed_sender_ids 中。"
        problems = configuration_problems(settings)
        if problems:
            return self._format_configuration_problem("无法创建草稿", problems)

        try:
            parsed_recipients = parse_recipients(recipients)
            validate_dispatch_request(settings, parsed_recipients, subject, body)
            draft = await self._drafts.create(
                actor_id=actor_id,
                unified_msg_origin=self._origin(event),
                recipients=parsed_recipients,
                subject=subject.strip(),
                body=body.strip(),
                ttl_seconds=settings.draft_ttl_seconds,
                max_pending_for_actor=settings.max_pending_drafts_per_actor,
            )
        except (MailRelayValidationError, DraftAccessError) as exc:
            await self._audit(
                settings,
                action="llm_draft",
                outcome="blocked",
                actor_id=actor_id,
                detail=type(exc).__name__,
            )
            return f"无法创建草稿：{exc}"

        await self._audit(
            settings,
            action="llm_draft",
            outcome="created",
            actor_id=actor_id,
            recipients=list(draft.recipients),
        )
        ttl_minutes = max(1, int((draft.expires_at - time.monotonic()) / 60))
        return (
            "已创建待确认邮件草稿，尚未发送。\n"
            f"- 收件人数量：{len(draft.recipients)}\n"
            f"- 确认令牌：{draft.token}\n"
            f"- 有效期：约 {ttl_minutes} 分钟\n"
            f"创建者需在当前会话执行 /mailrelay_confirm {draft.token} 才会发送；"
            f"可执行 /mailrelay_cancel {draft.token} 取消。"
        )

    async def _dispatch(
        self,
        *,
        event: AstrMessageEvent,
        settings: MailRelaySettings,
        recipients_input: str,
        subject: str,
        body: str,
        action: str,
    ) -> str:
        problems = configuration_problems(settings)
        if problems:
            return self._format_configuration_problem("邮件未发送", problems)
        try:
            recipients = parse_recipients(recipients_input)
            validate_dispatch_request(settings, recipients, subject, body)
        except MailRelayValidationError as exc:
            await self._audit(
                settings,
                action=action,
                outcome="blocked",
                actor_id=self._actor_id(event),
                detail=type(exc).__name__,
            )
            return f"邮件未发送：{exc}"

        async with self._send_lock:
            _succeeded, response = await self._deliver_validated_locked(
                event=event,
                settings=settings,
                recipients=recipients,
                subject=subject.strip(),
                body=body.strip(),
                action=action,
            )
        return response

    async def _deliver_validated_locked(
        self,
        *,
        event: AstrMessageEvent,
        settings: MailRelaySettings,
        recipients: list[str],
        subject: str,
        body: str,
        action: str,
    ) -> tuple[bool, str]:
        if not self._rate_limiter.can_send(
            max_messages=settings.max_messages_per_hour,
            window_seconds=ONE_HOUR_SECONDS,
        ):
            remaining = self._rate_limiter.remaining_seconds(
                window_seconds=ONE_HOUR_SECONDS
            )
            await self._audit(
                settings,
                action=action,
                outcome="rate_limited",
                actor_id=self._actor_id(event),
                recipients=recipients,
            )
            return (
                False,
                f"邮件未发送：已达到每小时 {settings.max_messages_per_hour} 封的投递上限，请约 {remaining} 秒后重试。",
            )

        try:
            result = await self._smtp_client.send(settings, recipients, subject, body)
        except MailRelayTransportError as exc:
            await self._audit(
                settings,
                action=action,
                outcome="failed",
                actor_id=self._actor_id(event),
                recipients=recipients,
                detail="transport_error",
            )
            logger.warning("[%s] %s failed: %s", PLUGIN_ID, action, exc)
            return False, f"邮件未发送：{exc}"

        # The send lock makes this check-send-record sequence atomic. Only a
        # server-accepted delivery consumes quota, so failed SMTP attempts stay retryable.
        self._rate_limiter.record_success()
        outcome = "succeeded" if result.is_complete else "partial"
        await self._audit(
            settings,
            action=action,
            outcome=outcome,
            actor_id=self._actor_id(event),
            recipients=recipients,
            detail=f"accepted={len(result.accepted_recipients)} refused={len(result.refused_recipients)}",
        )
        if result.is_complete:
            return (
                True,
                f"邮件已提交 SMTP 服务器，共 {len(result.accepted_recipients)} 位收件人。",
            )
        return (
            True,
            (
                "邮件已部分提交 SMTP 服务器："
                f"成功 {len(result.accepted_recipients)} 位，拒绝 {len(result.refused_recipients)} 位。"
            ),
        )

    def _settings(self) -> MailRelaySettings:
        return load_settings(self.config)

    def _control_denial_reason(
        self,
        event: AstrMessageEvent,
        settings: MailRelaySettings,
    ) -> str:
        if not self._is_admin(event):
            return "当前发送者不是 AstrBot 管理员。"
        actor_id = self._actor_id(event)
        if not settings.command_allowed_sender_ids:
            return "command_allowed_sender_ids 尚未配置。先执行 /mailrelay_whoami 获取自己的 ID。"
        if actor_id not in settings.command_allowed_sender_ids:
            return "当前发送者不在 command_allowed_sender_ids 中。"
        return ""

    @staticmethod
    def _is_admin(event: AstrMessageEvent) -> bool:
        checker = getattr(event, "is_admin", None)
        try:
            return bool(checker()) if callable(checker) else False
        except (AttributeError, TypeError):
            return False

    @staticmethod
    def _actor_id(event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_sender_id", None)
        try:
            return str(getter() or "").strip() if callable(getter) else ""
        except (AttributeError, TypeError):
            return ""

    @staticmethod
    def _origin(event: AstrMessageEvent) -> str:
        return str(getattr(event, "unified_msg_origin", "") or "").strip()

    async def _audit(
        self,
        settings: MailRelaySettings,
        *,
        action: str,
        outcome: str,
        actor_id: str,
        recipients: list[str] | tuple[str, ...] = (),
        detail: str = "",
    ) -> None:
        if not settings.audit_log_enabled or self._audit_writer is None:
            return
        try:
            await self._audit_writer.append(
                action=action,
                outcome=outcome,
                actor_id=actor_id,
                recipients=recipients,
                detail=detail,
            )
        except (OSError, RuntimeError) as exc:
            logger.warning("[%s] audit write failed: %s", PLUGIN_ID, exc)

    def _format_status(self, settings: MailRelaySettings) -> str:
        problems = configuration_problems(settings)
        readiness = "就绪" if not problems else "需配置"
        lines = [
            "MailRelay Guard 状态",
            f"- 就绪状态：{readiness}",
            f"- SMTP：{settings.smtp_host or '(未填写)'}:{settings.smtp_port} / {settings.smtp_security}",
            f"- 发件账号：{_mask_email(settings.smtp_username)}",
            f"- 发件地址：{_mask_email(settings.sender_address)}",
            f"- 授权码：{'已填写' if settings.smtp_password else '未填写'}",
            (
                "- 收件人策略："
                f"{'严格白名单' if settings.require_recipient_allowlist else '未启用白名单'} "
                f"(精确地址 {len(settings.recipient_allowlist)}，域名 {len(settings.allowed_recipient_domains)})"
            ),
            f"- 控制者 allowlist：{len(settings.command_allowed_sender_ids)} 人",
            f"- 成功投递限额：每小时 {settings.max_messages_per_hour} 封（重载后重置）",
            f"- 最小审计：{'开启' if settings.audit_log_enabled else '关闭'}",
            (
                "- LLM 草稿工具："
                f"{'已注册' if self._llm_draft_tool_registered else '未注册'} "
                f"(允许者 {len(settings.llm_tool_allowed_sender_ids)} 人，仅创建草稿)"
            ),
        ]
        if problems:
            lines.append("- 待处理：")
            lines.extend(f"  * {problem}" for problem in problems)
        return "\n".join(lines)

    @staticmethod
    def _format_configuration_problem(prefix: str, problems: list[str]) -> str:
        return prefix + "：\n" + "\n".join(f"- {problem}" for problem in problems)


def _parse_send_payload(payload: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in str(payload or "").split("|", 2)]
    if len(parts) != 3 or not all(parts):
        raise MailRelayValidationError(
            "命令格式应为：/mailrelay_send 收件人 | 邮件主题 | 邮件正文"
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
