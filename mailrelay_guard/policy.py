"""Validation and recipient safeguards for MailRelay Guard."""

from __future__ import annotations

import re
from collections.abc import Iterable
from email.utils import parseaddr

from .config import MailRelaySettings

_SIMPLE_EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")


class MailRelayValidationError(ValueError):
    """Raised when an outbound request is malformed or outside policy."""


def parse_recipients(value: str | Iterable[str]) -> list[str]:
    """Parse bare email addresses, rejecting headers and duplicate targets."""

    raw_values = _split_recipient_values(value)
    if not raw_values:
        raise MailRelayValidationError("请提供至少一个收件人邮箱地址。")

    recipients: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        address = validate_email_address(raw, field_name="收件人")
        key = address.casefold()
        if key not in seen:
            seen.add(key)
            recipients.append(address)
    return recipients


def validate_email_address(value: str, *, field_name: str = "邮箱地址") -> str:
    """Accept only one bare RFC-like address, never a display-name header."""

    raw = str(value or "").strip()
    if not raw:
        raise MailRelayValidationError(f"{field_name}不能为空。")
    _reject_header_injection(raw, field_name)
    parsed_name, parsed_address = parseaddr(raw)
    if parsed_name or parsed_address != raw or not _SIMPLE_EMAIL_RE.fullmatch(raw):
        raise MailRelayValidationError(
            f"{field_name}必须是单独的邮箱地址，不能包含显示名称。"
        )
    return raw


def validate_dispatch_request(
    settings: MailRelaySettings,
    recipients: list[str],
    subject: str,
    body: str,
    *,
    enforce_recipient_policy: bool,
    body_max_chars: int | None = None,
) -> None:
    """Validate content and, when requested, the admin-other allowlist."""

    if not recipients:
        raise MailRelayValidationError("请提供至少一个收件人邮箱地址。")
    if len(recipients) > settings.max_recipients_per_message:
        raise MailRelayValidationError(
            f"单封邮件最多允许 {settings.max_recipients_per_message} 位收件人。"
        )
    if not subject.strip():
        raise MailRelayValidationError("邮件主题不能为空。")
    _reject_header_injection(subject, "邮件主题")
    _reject_header_injection(settings.sender_name, "发件人名称")
    if len(subject) > settings.max_subject_chars:
        raise MailRelayValidationError(
            f"邮件主题不能超过 {settings.max_subject_chars} 个字符。"
        )
    if not body.strip():
        raise MailRelayValidationError("邮件正文不能为空。")
    maximum_body_chars = body_max_chars or settings.max_body_chars
    if len(body) > maximum_body_chars:
        raise MailRelayValidationError(
            f"邮件正文不能超过 {maximum_body_chars} 个字符。"
        )

    validate_email_address(settings.sender_address, field_name="发件人地址")
    for recipient in recipients:
        validate_email_address(recipient, field_name="收件人")
        if enforce_recipient_policy:
            _validate_admin_other_recipient_policy(settings, recipient)


def _split_recipient_values(value: str | Iterable[str]) -> list[str]:
    if isinstance(value, str):
        source_values = [value]
    else:
        source_values = [str(item) for item in value]

    values: list[str] = []
    for source in source_values:
        values.extend(
            part.strip() for part in re.split(r"[,;\n]", source) if part.strip()
        )
    return values


def _validate_admin_other_recipient_policy(
    settings: MailRelaySettings, recipient: str
) -> None:
    normalized = recipient.casefold()
    domain = normalized.rsplit("@", 1)[-1]
    if normalized in settings.admin_other_recipient_allowlist:
        return
    if domain in settings.admin_other_allowed_domains:
        return
    raise MailRelayValidationError(
        "该收件人不在管理员代发允许范围内。请将其加入 "
        "admin_other_recipient_allowlist，或将域名加入 "
        "admin_other_allowed_domains。"
    )


def _reject_header_injection(value: str, field_name: str) -> None:
    if "\r" in value or "\n" in value:
        raise MailRelayValidationError(f"{field_name}不能包含换行符。")
