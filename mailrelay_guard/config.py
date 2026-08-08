"""Configuration parsing for MailRelay Guard.

The AstrBot configuration UI provides untyped values at runtime. This module
normalizes those values once so the mail and policy layers stay predictable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_SMTP_HOST = "smtp.163.com"
DEFAULT_SMTP_PORT = 465
DEFAULT_SMTP_SECURITY = "ssl"
DEFAULT_PLACEHOLDER_ADDRESS = "your_name@163.com"


@dataclass(frozen=True)
class MailRelaySettings:
    """Validated, bounded runtime settings for the plugin."""

    enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_security: str
    allow_plain_smtp: bool
    smtp_username: str
    smtp_password: str
    sender_address: str
    sender_name: str
    smtp_timeout_seconds: int
    require_recipient_allowlist: bool
    recipient_allowlist: frozenset[str]
    allowed_recipient_domains: frozenset[str]
    max_recipients_per_message: int
    max_subject_chars: int
    max_body_chars: int
    max_messages_per_hour: int
    test_recipient: str
    command_allowed_sender_ids: frozenset[str]
    audit_log_enabled: bool
    audit_max_file_kb: int
    enable_llm_draft_tool: bool
    llm_tool_allowed_sender_ids: frozenset[str]
    draft_ttl_seconds: int
    max_pending_drafts_per_actor: int


def load_settings(config: Mapping[str, Any] | Any | None) -> MailRelaySettings:
    """Read plugin config defensively and clamp values to safe ranges."""

    return MailRelaySettings(
        enabled=_as_bool(_get(config, "enabled", True), True),
        smtp_host=_as_text(
            _get(config, "smtp_host", DEFAULT_SMTP_HOST), DEFAULT_SMTP_HOST
        ),
        smtp_port=_as_int(
            _get(config, "smtp_port", DEFAULT_SMTP_PORT),
            DEFAULT_SMTP_PORT,
            minimum=1,
            maximum=65535,
        ),
        smtp_security=_as_choice(
            _get(config, "smtp_security", DEFAULT_SMTP_SECURITY),
            {"ssl", "starttls", "plain"},
            DEFAULT_SMTP_SECURITY,
        ),
        allow_plain_smtp=_as_bool(_get(config, "allow_plain_smtp", False), False),
        smtp_username=_as_text(_get(config, "smtp_username", "")),
        smtp_password=_as_text(_get(config, "smtp_password", "")),
        sender_address=_as_text(_get(config, "sender_address", "")),
        sender_name=_as_text(
            _get(config, "sender_name", "AstrBot MailRelay Guard"),
            "AstrBot MailRelay Guard",
        ),
        smtp_timeout_seconds=_as_int(
            _get(config, "smtp_timeout_seconds", 20), 20, minimum=5, maximum=120
        ),
        require_recipient_allowlist=_as_bool(
            _get(config, "require_recipient_allowlist", True), True
        ),
        recipient_allowlist=frozenset(
            _normalize_addresses(_as_list(_get(config, "recipient_allowlist", [])))
        ),
        allowed_recipient_domains=frozenset(
            _normalize_domains(_as_list(_get(config, "allowed_recipient_domains", [])))
        ),
        max_recipients_per_message=_as_int(
            _get(config, "max_recipients_per_message", 3), 3, minimum=1, maximum=20
        ),
        max_subject_chars=_as_int(
            _get(config, "max_subject_chars", 120), 120, minimum=1, maximum=998
        ),
        max_body_chars=_as_int(
            _get(config, "max_body_chars", 5000), 5000, minimum=1, maximum=50000
        ),
        max_messages_per_hour=_as_int(
            _get(config, "max_messages_per_hour", 10), 10, minimum=1, maximum=1000
        ),
        test_recipient=_as_text(_get(config, "test_recipient", "")),
        command_allowed_sender_ids=frozenset(
            _as_list(_get(config, "command_allowed_sender_ids", []))
        ),
        audit_log_enabled=_as_bool(_get(config, "audit_log_enabled", True), True),
        audit_max_file_kb=_as_int(
            _get(config, "audit_max_file_kb", 512), 512, minimum=64, maximum=10240
        ),
        enable_llm_draft_tool=_as_bool(
            _get(config, "enable_llm_draft_tool", False), False
        ),
        llm_tool_allowed_sender_ids=frozenset(
            _as_list(_get(config, "llm_tool_allowed_sender_ids", []))
        ),
        draft_ttl_seconds=_as_int(
            _get(config, "draft_ttl_seconds", 600), 600, minimum=60, maximum=3600
        ),
        max_pending_drafts_per_actor=_as_int(
            _get(config, "max_pending_drafts_per_actor", 3), 3, minimum=1, maximum=20
        ),
    )


def configuration_problems(settings: MailRelaySettings) -> list[str]:
    """Return actionable readiness errors without exposing secret values."""

    problems: list[str] = []
    if not settings.enabled:
        problems.append("插件已在配置中关闭。")
    if not settings.smtp_host:
        problems.append("smtp_host 不能为空。")
    if settings.smtp_security == "plain" and not settings.allow_plain_smtp:
        problems.append("明文 SMTP 被安全策略阻止；请改用 SSL/STARTTLS。")
    if _is_placeholder_address(settings.smtp_username):
        problems.append("请填写 smtp_username。")
    if not settings.smtp_password:
        problems.append("请填写 smtp_password（网易邮箱应使用 SMTP 授权码）。")
    if _is_placeholder_address(settings.sender_address):
        problems.append("请填写 sender_address。")
    if not settings.sender_name:
        problems.append("sender_name 不能为空。")
    return problems


def _get(config: Mapping[str, Any] | Any | None, key: str, default: Any) -> Any:
    getter = getattr(config, "get", None)
    if callable(getter):
        return getter(key, default)
    return default


def _as_text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    if value is None:
        return default
    return bool(value)


def _as_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _as_choice(value: Any, allowed: set[str], default: str) -> str:
    candidate = _as_text(value, default).lower()
    return candidate if candidate in allowed else default


def _as_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [
            item.strip() for item in value.replace("\n", ",").split(",") if item.strip()
        ]
    return []


def _normalize_addresses(values: list[str]) -> list[str]:
    return [value.casefold() for value in values if value]


def _normalize_domains(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        domain = value.strip().casefold().lstrip("@")
        if domain:
            normalized.append(domain)
    return normalized


def _is_placeholder_address(value: str) -> bool:
    return value.casefold() in {
        "",
        DEFAULT_PLACEHOLDER_ADDRESS,
        "your-email@example.com",
        "example@example.com",
    }
