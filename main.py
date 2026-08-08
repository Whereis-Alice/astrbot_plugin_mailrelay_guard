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

PLUGIN_ID = "astrbot_plugin_mailrelay_guard"
PLUGIN_VERSION = "v1.1.0"
ONE_HOUR_SECONDS = 60 * 60
RecipientMode = Literal["owner", "self", "other", "binding"]


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
        "Send a plain-text email to the configured owner mailbox. The recipient is "
        "fixed by plugin configuration and cannot be changed. Use only when the "
        "current chat sender is the configured owner and an AstrBot administrator."
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Email subject."},
                "body": {"type": "string", "description": "Plain-text email body."},
            },
            "required": ["subject", "body"],
            "additionalProperties": False,
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> str:
        if self.plugin is None:
            return "MailRelay Guard is unavailable."
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
        "Send a plain-text email only to the current chat sender's verified bound "
        "mailbox or privacy-aware QQ/NapCat profile mailbox. This tool has no "
        "recipient parameter and must never be used to send to someone else."
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Email subject."},
                "body": {"type": "string", "description": "Plain-text email body."},
            },
            "required": ["subject", "body"],
            "additionalProperties": False,
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> str:
        if self.plugin is None:
            return "MailRelay Guard is unavailable."
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
        "Send a plain-text email to an explicit recipient. This is for configured "
        "AstrBot administrators only. Do not call it for ordinary users; use "
        "mailrelay_send_to_self for a user's own mailbox instead."
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "recipients": {
                    "type": "string",
                    "description": "One or more email addresses separated by commas.",
                },
                "subject": {"type": "string", "description": "Email subject."},
                "body": {"type": "string", "description": "Plain-text email body."},
            },
            "required": ["recipients", "subject", "body"],
            "additionalProperties": False,
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> str:
        if self.plugin is None:
            return "MailRelay Guard is unavailable."
        return await self.plugin.deliver_from_tool(
            event=_tool_event(context),
            mode="other",
            recipients_input=str(kwargs.get("recipients", "")),
            subject=str(kwargs.get("subject", "")),
            body=str(kwargs.get("body", "")),
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
        self._audit_writer: AuditWriter | None = None
        self._mailboxes: MailboxBindingStore | None = None
        self._mailbox_resolver = SelfMailboxResolver(None)
        self._llm_tools_registered = False

    async def initialize(self) -> None:
        """Prepare private storage and register the direct, mode-scoped LLM tools."""

        settings = self._settings()
        try:
            data_dir = Path(StarTools.get_data_dir(PLUGIN_ID))
            self._audit_writer = AuditWriter(
                data_dir,
                max_file_kb=settings.audit_max_file_kb,
            )
            self._mailboxes = MailboxBindingStore(data_dir)
            self._mailbox_resolver = SelfMailboxResolver(self._mailboxes)
        except (OSError, RuntimeError) as exc:
            logger.warning("[%s] local storage unavailable: %s", PLUGIN_ID, exc)

        if settings.enabled and settings.enable_llm_mail_tools:
            self.context.add_llm_tools(
                MailRelayNotifyOwnerTool(plugin=self),
                MailRelaySendToSelfTool(plugin=self),
                MailRelaySendToRecipientTool(plugin=self),
            )
            self._llm_tools_registered = True

        logger.info(
            "[%s] initialized | smtp=%s:%s security=%s direct_tools=%s",
            PLUGIN_ID,
            settings.smtp_host or "(unset)",
            settings.smtp_port,
            settings.smtp_security,
            self._llm_tools_registered,
        )

    async def terminate(self) -> None:
        """Discard in-memory profile caches and pending verification codes."""

        self._mailboxes = None
        self._mailbox_resolver = SelfMailboxResolver(None)
        logger.info("[%s] terminated", PLUGIN_ID)

    @filter.command("mailrelay_whoami", alias={"????"})
    async def mailrelay_whoami(self, event: AstrMessageEvent):
        """Show the platform-scoped identity required by owner/admin allowlists."""

        actor = get_actor_identity(event)
        if actor is None:
            yield event.plain_result("???????????? ID?")
            return
        yield event.plain_result(
            "MailRelay Guard ????\n"
            f"- platform_id: {actor.platform_id}\n"
            f"- platform_name: {actor.platform_name}\n"
            f"- sender_id: {actor.sender_id}\n"
            f"- ?? allowlist ??: {actor.key}\n"
            f"- AstrBot admin: {event_is_admin(event)}"
        )

    @filter.command("mailrelay_identity", alias={"??????"})
    async def mailrelay_identity(self, event: AstrMessageEvent):
        """Show whether the caller has a self-delivery mailbox, without revealing it."""

        settings = self._settings()
        actor = get_actor_identity(event)
        if actor is None:
            yield event.plain_result("???????????? ID?")
            return
        mailbox = await self._mailbox_resolver.resolve(event, settings, actor)
        if mailbox is None:
            yield event.plain_result(
                "??????'????'????????????? "
                "/mailrelay_bind ????,??????????? /mailrelay_verify?"
            )
            return
        yield event.plain_result(
            "????????????\n"
            f"- ??: {_mailbox_source_label(mailbox.source)}\n"
            "- ?????,???????????"
        )

    @filter.command("mailrelay_bind", alias={"??????"})
    async def mailrelay_bind(self, event: AstrMessageEvent, email: str = ""):
        """Email a one-time code to bind the sender's own fallback mailbox."""

        yield event.plain_result(await self.request_mailbox_binding(event, email))

    @filter.command("mailrelay_verify", alias={"??????"})
    async def mailrelay_verify(self, event: AstrMessageEvent, code: str = ""):
        """Confirm a pending self-mailbox binding using its emailed code."""

        yield event.plain_result(await self.verify_mailbox_binding(event, code))

    @filter.command("mailrelay_unbind", alias={"??????"})
    async def mailrelay_unbind(self, event: AstrMessageEvent):
        """Delete the caller's stored self-mailbox binding."""

        yield event.plain_result(await self.remove_mailbox_binding(event))

    @filter.command("mailrelay_self", alias={"?????"})
    async def mailrelay_self(self, event: AstrMessageEvent, payload: GreedyStr):
        """Directly send a plain-text email to the current sender only."""

        try:
            subject, body = _parse_subject_body(payload)
        except MailRelayValidationError as exc:
            yield event.plain_result(f"?????:{exc}")
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

    @filter.command("mailrelay_owner", alias={"?????"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_owner(self, event: AstrMessageEvent, payload: GreedyStr):
        """Directly notify the fixed owner mailbox for the configured owner only."""

        try:
            subject, body = _parse_subject_body(payload)
        except MailRelayValidationError as exc:
            yield event.plain_result(f"?????:{exc}")
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

    @filter.command("mailrelay_send", alias={"??????"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_send(self, event: AstrMessageEvent, payload: GreedyStr):
        """Administrator-only delivery to explicitly specified recipients."""

        try:
            recipients, subject, body = _parse_other_payload(payload)
        except MailRelayValidationError as exc:
            yield event.plain_result(f"?????:{exc}")
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

    @filter.command("mailrelay_status", alias={"??????"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_status(self, event: AstrMessageEvent):
        """Show administrator-only redacted configuration and readiness state."""

        settings = self._settings()
        actor = get_actor_identity(event)
        if not self._is_configured_admin(event, actor, settings):
            yield event.plain_result("???? MailRelay Guard ??:????? AstrBot ??????")
            return
        yield event.plain_result(self._format_status(settings))

    @filter.command("mailrelay_smtp_test", alias={"????????"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_smtp_test(self, event: AstrMessageEvent):
        """Test SMTP TLS/login without sending an email."""

        settings = self._settings()
        actor = get_actor_identity(event)
        if not self._is_configured_admin(event, actor, settings):
            yield event.plain_result("SMTP ?????:????? AstrBot ??????")
            return
        problems = configuration_problems(settings)
        if problems:
            yield event.plain_result(self._format_configuration_problem("SMTP ?????", problems))
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
            yield event.plain_result(f"SMTP ????:{exc}")
            return
        await self._audit(
            settings,
            action="smtp_test",
            outcome="succeeded",
            actor=actor,
        )
        yield event.plain_result("SMTP ?????????,???????")

    @filter.command("mailrelay_send_test", alias={"????????"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mailrelay_send_test(self, event: AstrMessageEvent):
        """Send a fixed test message only to the configured owner mailbox."""

        settings = self._settings()
        actor = get_actor_identity(event)
        if not self._is_configured_admin(event, actor, settings):
            yield event.plain_result("???????:????? AstrBot ??????")
            return
        denied = self._authorization_denial(event, actor, settings, "owner")
        if denied:
            yield event.plain_result(f"???????:{denied}")
            return
        now_text = time.strftime("%Y-%m-%d %H:%M:%S")
        yield event.plain_result(
            await self._deliver(
                event=event,
                actor=actor,
                settings=settings,
                mode="owner",
                recipients=[settings.owner_email],
                subject="MailRelay Guard SMTP ????",
                body=(
                    "????? MailRelay Guard ????????\n"
                    f"????:{now_text}\n"
                    "????????,?? SMTP?TLS ?????????"
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
    ) -> str:
        """The shared final boundary for LLM tools and interactive commands."""

        if event is None:
            return "?????:???????????"
        settings = self._settings()
        actor = get_actor_identity(event)
        if actor is None:
            return "?????:???????????? ID?"
        actual_action = action or f"llm_{mode}"
        denied = self._authorization_denial(event, actor, settings, mode)
        if denied:
            await self._audit(
                settings,
                action=actual_action,
                outcome="blocked",
                actor=actor,
                detail="authorization",
            )
            return f"?????:{denied}"

        problems = configuration_problems(settings)
        if problems:
            return self._format_configuration_problem("?????", problems)

        try:
            recipients = await self._recipients_for_mode(
                event=event,
                actor=actor,
                settings=settings,
                mode=mode,
                recipients_input=recipients_input,
            )
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
            return f"?????:{exc}"

        return await self._deliver(
            event=event,
            actor=actor,
            settings=settings,
            mode=mode,
            recipients=recipients,
            subject=subject.strip(),
            body=body.strip(),
            action=actual_action,
        )

    async def request_mailbox_binding(
        self, event: AstrMessageEvent, email: str
    ) -> str:
        """Send a verification code before storing a caller-provided fallback email."""

        settings = self._settings()
        actor = get_actor_identity(event)
        if actor is None:
            return "?????:???????????? ID?"
        if not settings.enable_self_delivery:
            return "?????:?????????????"
        if not settings.self_binding_enabled:
            return "?????:?????????????"
        if settings.require_private_chat_for_binding and not event_is_private_chat(event):
            return "?????:?????????,??????????????"
        problems = configuration_problems(settings)
        if problems:
            return self._format_configuration_problem("?????", problems)
        if self._mailboxes is None:
            return "?????:?????????,???????????"

        try:
            address = validate_email_address(email, field_name="??????")
        except MailRelayValidationError as exc:
            return f"?????:{exc}"

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
            return f"?????:{exc}"

        result = await self._deliver(
            event=event,
            actor=actor,
            settings=settings,
            mode="binding",
            recipients=[address],
            subject="MailRelay Guard ???????",
            body=(
                "???????????????,??'????'?\n"
                f"???:{code}\n"
                f"???:{settings.verification_code_ttl_seconds // 60} ???\n"
                "??????????,???????"
            ),
            action="binding_challenge",
            expose_recipient=False,
        )
        if not result.startswith("?????"):
            await self._mailboxes.discard_challenge(actor.key)
            return result.replace("??", "?????", 1)
        return (
            "??????????????????????????? "
            "/mailrelay_verify ??????"
        )

    async def verify_mailbox_binding(self, event: AstrMessageEvent, code: str) -> str:
        """Persist a mailbox only after the current sender proves control of it."""

        settings = self._settings()
        actor = get_actor_identity(event)
        if actor is None:
            return "?????:???????????? ID?"
        if not settings.self_binding_enabled:
            return "?????:?????????????"
        if settings.require_private_chat_for_binding and not event_is_private_chat(event):
            return "?????:???????????????"
        if self._mailboxes is None:
            return "?????:??????????"
        code = code.strip()
        if not code:
            return "?????:?????????????"
        if len(code) != 6 or not code.isdecimal():
            return "?????:???????????"
        try:
            binding = await self._mailboxes.verify(
                actor_key=actor.key,
                code=code,
                max_attempts=settings.verification_max_attempts,
            )
        except (MailboxBindingError, OSError) as exc:
            return f"?????:{exc}"
        await self._audit(
            settings,
            action="binding_verify",
            outcome="succeeded",
            actor=actor,
        )
        return f"????????:{_mask_email(binding.address)}?????????????????"

    async def remove_mailbox_binding(self, event: AstrMessageEvent) -> str:
        """Delete persisted self-mail data without requiring SMTP to be healthy."""

        settings = self._settings()
        actor = get_actor_identity(event)
        if actor is None:
            return "?????:???????????? ID?"
        if settings.require_private_chat_for_binding and not event_is_private_chat(event):
            return "?????:???????????????"
        if self._mailboxes is None:
            return "?????:??????????"
        try:
            removed = await self._mailboxes.remove(actor.key)
        except OSError as exc:
            return f"?????:{exc}"
        if not removed:
            return "?????????????"
        await self._audit(
            settings,
            action="binding_remove",
            outcome="succeeded",
            actor=actor,
        )
        return "????????????"

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
                raise MailRelayValidationError("??? owner_email ?????????????")
            return [validate_email_address(settings.owner_email, field_name="????")]
        if mode == "self":
            mailbox = await self._mailbox_resolver.resolve(event, settings, actor)
            if mailbox is None:
                raise MailRelayValidationError(
                    "????? QQ/NapCat ?????????????"
                    "???????? /mailrelay_bind ?????"
                )
            return [mailbox.address]
        if mode == "other":
            return parse_recipients(recipients_input)
        raise MailRelayValidationError("??????????")

    def _authorization_denial(
        self,
        event: AstrMessageEvent,
        actor: ActorIdentity,
        settings: MailRelaySettings,
        mode: RecipientMode,
    ) -> str:
        if mode == "owner":
            if not settings.enable_owner_delivery:
                return "?????????????"
            if not event_is_admin(event) or not actor_matches_configured_ids(
                actor, settings.owner_sender_ids
            ):
                return "????????? AstrBot ???????????"
            return ""
        if mode == "self":
            if not settings.enable_self_delivery:
                return "?????????????"
            if (
                settings.require_private_chat_for_self_delivery
                and not event_is_private_chat(event)
            ):
                return "???????,?????????????"
            return ""
        if mode == "other":
            if not settings.enable_admin_other_delivery:
                return "???????????????"
            if not self._is_configured_admin(event, actor, settings):
                return "?????? AstrBot ?????????????"
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
    ) -> str:
        problems = configuration_problems(settings)
        if problems:
            return self._format_configuration_problem("?????", problems)
        try:
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
            return f"?????:{exc}"

        if actor is None:
            return "?????:???????????? ID?"
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
                f"?????:?????? {settings.max_messages_per_hour} ????????,?? {remaining} ?????",
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
                    "?????:???????????????,"
                    f"?? {remaining} ?????"
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
                    "?????:?????????????????,"
                    f"?? {remaining} ?????"
                ),
            )

        # Attempts are charged before SMTP so failed logins cannot be hammered forever.
        self._actor_attempt_limiter.record(actor.key)
        try:
            result = await self._smtp_client.send(settings, recipients, subject, body)
        except MailRelayTransportError as exc:
            await self._audit(
                settings,
                action=action,
                outcome="failed",
                actor=actor,
                recipients=recipients,
                detail="transport_error",
            )
            logger.warning("[%s] %s failed: %s", PLUGIN_ID, action, exc)
            return False, f"?????:{exc}"

        accepted_count = len(result.accepted_recipients)
        if accepted_count:
            self._global_success_limiter.record_success()
            self._actor_success_limiter.record(actor.key)
        outcome = "succeeded" if result.is_complete else "partial"
        await self._audit(
            settings,
            action=action,
            outcome=outcome,
            actor=actor,
            recipients=recipients,
            detail=(
                f"mode={mode};accepted={accepted_count};"
                f"refused={len(result.refused_recipients)}"
            ),
        )
        if result.is_complete:
            return True, f"????? SMTP ???,? {accepted_count} ?????"
        if accepted_count:
            return (
                True,
                (
                    "??????? SMTP ???:"
                    f"?? {accepted_count} ?,?? {len(result.refused_recipients)} ??"
                ),
            )
        return False, "?????:SMTP ????????????"

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
        readiness = "??" if not problems else "????"
        lines = [
            "MailRelay Guard ??",
            f"- ????:{readiness}",
            f"- SMTP:{settings.smtp_host or '(???)'}:{settings.smtp_port} / {settings.smtp_security}",
            f"- ????:{_mask_email(settings.smtp_username)}",
            f"- ????:{_mask_email(settings.sender_address)}",
            f"- ??????:{_mask_email(settings.owner_email)}",
            f"- ???:{'???' if settings.smtp_password and not settings.smtp_password.startswith('YOUR_') else '???'}",
            f"- LLM ????:{'???' if self._llm_tools_registered else '???'}",
            f"- ??????:{'??' if settings.enable_self_delivery else '??'}",
            f"- ?????:{'??' if settings.enable_admin_other_delivery else '??'}",
            f"- ????????:{'??' if settings.restrict_admin_other_recipients else '??'}",
            f"- ????:{'??' if settings.self_binding_enabled else '??'}",
            f"- NapCat ????:{'??' if settings.napcat_email_lookup_enabled else '??'}",
            f"- ????????:??? {settings.max_messages_per_hour} ?",
            f"- ?????????:??? {settings.max_successful_messages_per_actor_per_hour} ?",
            f"- ???????:{settings.actor_min_send_interval_seconds} ?",
        ]
        if problems:
            lines.append("- ???:")
            lines.extend(f"  * {problem}" for problem in problems)
        return "\n".join(lines)

    @staticmethod
    def _format_configuration_problem(prefix: str, problems: list[str]) -> str:
        return prefix + ":\n" + "\n".join(f"- {problem}" for problem in problems)


def _parse_subject_body(payload: str) -> tuple[str, str]:
    parts = [part.strip() for part in str(payload or "").split("|", 1)]
    if len(parts) != 2 or not all(parts):
        raise MailRelayValidationError(
            "??????:?? | ????,?? /mailrelay_self ?? | ??"
        )
    return parts[0], parts[1]


def _parse_other_payload(payload: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in str(payload or "").split("|", 2)]
    if len(parts) != 3 or not all(parts):
        raise MailRelayValidationError(
            "??????:??? | ?? | ????,?? "
            "/mailrelay_send person@example.com | ?? | ??"
        )
    return parts[0], parts[1], parts[2]


def _mask_email(value: str) -> str:
    address = str(value or "").strip()
    if not address or "@" not in address:
        return "???"
    local, domain = address.rsplit("@", 1)
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"


def _mailbox_source_label(source: str) -> str:
    labels = {
        "configured_override": "??????????",
        "verified_binding": "?????????",
        "napcat_profile": "NapCat ?? QQ ??",
        "napcat_friend_profile": "NapCat QQ ????",
        "qq_mailbox_derivation": "QQ ?????(???)",
    }
    return labels.get(source, "????")
