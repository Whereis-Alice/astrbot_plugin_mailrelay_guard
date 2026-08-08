"""Configuration parsing for MailRelay Guard.

The AstrBot dashboard supplies loosely typed values.  This module turns them
into one immutable settings object so authorization and delivery code do not
need to make assumptions about dashboard input.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_SMTP_HOST = "smtp.163.com"
DEFAULT_SMTP_PORT = 465
DEFAULT_SMTP_SECURITY = "ssl"
DEFAULT_PLACEHOLDER_ADDRESS = "your_name@163.com"
DEFAULT_PLACEHOLDER_PASSWORD = "YOUR_163_SMTP_AUTHORIZATION_CODE"
DEFAULT_PLACEHOLDER_QQ_ID = "YOUR_QQ_ID"


@dataclass(frozen=True)
class MailRelaySettings:
    """Normalized plugin settings with bounded numeric values."""

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
    owner_email: str
    owner_sender_ids: frozenset[str]
    admin_sender_ids: frozenset[str]
    enable_llm_mail_tools: bool
    enable_owner_delivery: bool
    enable_self_delivery: bool
    enable_admin_other_delivery: bool
    require_private_chat_for_self_delivery: bool
    require_private_chat_for_binding: bool
    self_email_overrides: dict[str, str]
    napcat_email_lookup_enabled: bool
    napcat_friend_list_fallback_enabled: bool
    napcat_lookup_timeout_seconds: int
    napcat_lookup_cache_seconds: int
    qq_platform_names: frozenset[str]
    allow_qq_mailbox_derivation: bool
    qq_mail_domain: str
    self_binding_enabled: bool
    verification_code_ttl_seconds: int
    verification_resend_seconds: int
    verification_max_attempts: int
    restrict_admin_other_recipients: bool
    admin_other_recipient_allowlist: frozenset[str]
    admin_other_allowed_domains: frozenset[str]
    max_recipients_per_message: int
    max_subject_chars: int
    max_body_chars: int
    max_messages_per_hour: int
    max_successful_messages_per_actor_per_hour: int
    max_delivery_attempts_per_actor_per_hour: int
    actor_min_send_interval_seconds: int
    max_tracked_actors: int
    audit_log_enabled: bool
    audit_max_file_kb: int


def load_settings(config: Mapping[str, Any] | Any | None) -> MailRelaySettings:
    """Read config defensively and clamp operational limits."""

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
        smtp_username=_as_text(
            _get(config, "smtp_username", DEFAULT_PLACEHOLDER_ADDRESS),
            DEFAULT_PLACEHOLDER_ADDRESS,
        ),
        smtp_password=_as_text(
            _get(config, "smtp_password", DEFAULT_PLACEHOLDER_PASSWORD),
            DEFAULT_PLACEHOLDER_PASSWORD,
        ),
        sender_address=_as_text(
            _get(config, "sender_address", DEFAULT_PLACEHOLDER_ADDRESS),
            DEFAULT_PLACEHOLDER_ADDRESS,
        ),
        sender_name=_as_text(
            _get(config, "sender_name", "Alice MailRelay"), "Alice MailRelay"
        ),
        smtp_timeout_seconds=_as_int(
            _get(config, "smtp_timeout_seconds", 20), 20, minimum=5, maximum=120
        ),
        owner_email=_as_text(
            _get(config, "owner_email", DEFAULT_PLACEHOLDER_ADDRESS),
            DEFAULT_PLACEHOLDER_ADDRESS,
        ),
        owner_sender_ids=frozenset(
            _as_list(_get(config, "owner_sender_ids", [DEFAULT_PLACEHOLDER_QQ_ID]))
        ),
        admin_sender_ids=frozenset(
            _as_list(_get(config, "admin_sender_ids", [DEFAULT_PLACEHOLDER_QQ_ID]))
        ),
        enable_llm_mail_tools=_as_bool(
            _get(config, "enable_llm_mail_tools", True), True
        ),
        enable_owner_delivery=_as_bool(
            _get(config, "enable_owner_delivery", True), True
        ),
        enable_self_delivery=_as_bool(
            _get(config, "enable_self_delivery", True), True
        ),
        enable_admin_other_delivery=_as_bool(
            _get(config, "enable_admin_other_delivery", True), True
        ),
        require_private_chat_for_self_delivery=_as_bool(
            _get(config, "require_private_chat_for_self_delivery", True), True
        ),
        require_private_chat_for_binding=_as_bool(
            _get(config, "require_private_chat_for_binding", True), True
        ),
        self_email_overrides=_parse_identity_email_overrides(
            _as_list(_get(config, "self_email_overrides", []))
        ),
        napcat_email_lookup_enabled=_as_bool(
            _get(config, "napcat_email_lookup_enabled", True), True
        ),
        napcat_friend_list_fallback_enabled=_as_bool(
            _get(config, "napcat_friend_list_fallback_enabled", False), False
        ),
        napcat_lookup_timeout_seconds=_as_int(
            _get(config, "napcat_lookup_timeout_seconds", 6),
            6,
            minimum=1,
            maximum=30,
        ),
        napcat_lookup_cache_seconds=_as_int(
            _get(config, "napcat_lookup_cache_seconds", 300),
            300,
            minimum=60,
            maximum=3600,
        ),
        qq_platform_names=frozenset(
            value.casefold()
            for value in _as_list(_get(config, "qq_platform_names", ["aiocqhttp"]))
        ),
        allow_qq_mailbox_derivation=_as_bool(
            _get(config, "allow_qq_mailbox_derivation", False), False
        ),
        qq_mail_domain=_as_text(_get(config, "qq_mail_domain", "qq.com"), "qq.com")
        .casefold()
        .lstrip("@"),
        self_binding_enabled=_as_bool(
            _get(config, "self_binding_enabled", True), True
        ),
        verification_code_ttl_seconds=_as_int(
            _get(config, "verification_code_ttl_seconds", 900),
            900,
            minimum=60,
            maximum=3600,
        ),
        verification_resend_seconds=_as_int(
            _get(config, "verification_resend_seconds", 300),
            300,
            minimum=30,
            maximum=3600,
        ),
        verification_max_attempts=_as_int(
            _get(config, "verification_max_attempts", 5),
            5,
            minimum=1,
            maximum=20,
        ),
        restrict_admin_other_recipients=_as_bool(
            _get(config, "restrict_admin_other_recipients", False), False
        ),
        admin_other_recipient_allowlist=frozenset(
            _normalize_addresses(
                _as_list(_get(config, "admin_other_recipient_allowlist", []))
            )
        ),
        admin_other_allowed_domains=frozenset(
            _normalize_domains(
                _as_list(_get(config, "admin_other_allowed_domains", []))
            )
        ),
        max_recipients_per_message=_as_int(
            _get(config, "max_recipients_per_message", 3),
            3,
            minimum=1,
            maximum=20,
        ),
        max_subject_chars=_as_int(
            _get(config, "max_subject_chars", 160),
            160,
            minimum=1,
            maximum=998,
        ),
        max_body_chars=_as_int(
            _get(config, "max_body_chars", 8000),
            8000,
            minimum=1,
            maximum=50000,
        ),
        max_messages_per_hour=_as_int(
            _get(config, "max_messages_per_hour", 30),
            30,
            minimum=1,
            maximum=1000,
        ),
        max_successful_messages_per_actor_per_hour=_as_int(
            _get(config, "max_successful_messages_per_actor_per_hour", 5),
            5,
            minimum=1,
            maximum=100,
        ),
        max_delivery_attempts_per_actor_per_hour=_as_int(
            _get(config, "max_delivery_attempts_per_actor_per_hour", 8),
            8,
            minimum=1,
            maximum=200,
        ),
        actor_min_send_interval_seconds=_as_int(
            _get(config, "actor_min_send_interval_seconds", 60),
            60,
            minimum=0,
            maximum=3600,
        ),
        max_tracked_actors=_as_int(
            _get(config, "max_tracked_actors", 1000),
            1000,
            minimum=10,
            maximum=10000,
        ),
        audit_log_enabled=_as_bool(_get(config, "audit_log_enabled", True), True),
        audit_max_file_kb=_as_int(
            _get(config, "audit_max_file_kb", 512),
            512,
            minimum=64,
            maximum=10240,
        ),
    )


def configuration_problems(settings: MailRelaySettings) -> list[str]:
    """Return common SMTP readiness errors without exposing secrets."""

    problems: list[str] = []
    if not settings.enabled:
        problems.append("??????????")
    if not settings.smtp_host:
        problems.append("smtp_host ?????")
    if settings.smtp_security == "plain" and not settings.allow_plain_smtp:
        problems.append("?? SMTP ???????;??? SSL ? STARTTLS?")
    if is_placeholder_address(settings.smtp_username):
        problems.append("??? smtp_username(???????????)?")
    if _is_placeholder_secret(settings.smtp_password):
        problems.append("??? smtp_password(???? SMTP ??????)?")
    if is_placeholder_address(settings.sender_address):
        problems.append("??? sender_address(??? smtp_username ??)?")
    if not settings.sender_name:
        problems.append("sender_name ?????")
    return problems


def is_placeholder_address(value: str) -> bool:
    """Whether an address still carries the public example value."""

    return str(value or "").strip().casefold() in {
        "",
        DEFAULT_PLACEHOLDER_ADDRESS.casefold(),
        "your-email@example.com",
        "example@example.com",
    }


def is_placeholder_id(value: str) -> bool:
    """Whether an allowlist value is the documented placeholder."""

    return str(value or "").strip().casefold() in {
        "",
        DEFAULT_PLACEHOLDER_QQ_ID.casefold(),
    }


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


def _parse_identity_email_overrides(values: list[str]) -> dict[str, str]:
    """Read ``platform:sender=email`` entries without failing configuration."""

    overrides: dict[str, str] = {}
    for value in values:
        key, separator, address = value.partition("=")
        normalized_key = key.strip().casefold()
        normalized_address = address.strip()
        if separator and normalized_key and normalized_address:
            overrides[normalized_key] = normalized_address
    return overrides


def _is_placeholder_secret(value: str) -> bool:
    return str(value or "").strip() in {"", DEFAULT_PLACEHOLDER_PASSWORD}
