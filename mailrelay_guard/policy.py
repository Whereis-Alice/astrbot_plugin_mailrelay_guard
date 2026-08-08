"""Input and outbound-recipient safeguards for MailRelay Guard."""

from __future__ import annotations

import re
from collections.abc import Iterable
from email.utils import parseaddr

from .config import MailRelaySettings

_SIMPLE_EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")


class MailRelayValidationError(ValueError):
    """Raised when a requested email conflicts with a delivery safeguard."""


def parse_recipients(value: str | Iterable[str]) -> list[str]:
    """Parse one or more bare RFC-like email addresses and remove duplicates."""

    raw_values = _split_recipient_values(value)
    if not raw_values:
        raise MailRelayValidationError("请提供至少一个收件人邮箱地址。")

    recipients: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        address = _validate_bare_address(raw, field_name="收件人")
        key = address.casefold()
        if key not in seen:
            seen.add(key)
            recipients.append(address)
    return recipients


def validate_dispatch_request(
    settings: MailRelaySettings,
    recipients: list[str],
    subject: str,
    body: str,
) -> None:
    """Validate a mail body and enforce recipient restrictions."""

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
    if len(body) > settings.max_body_chars:
        raise MailRelayValidationError(
            f"邮件正文不能超过 {settings.max_body_chars} 个字符。"
        )

    _validate_bare_address(settings.sender_address, field_name="发件人地址")
    for recipient in recipients:
        _validate_recipient_policy(settings, recipient)


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


def _validate_bare_address(value: str, *, field_name: str) -> str:
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


def _validate_recipient_policy(settings: MailRelaySettings, recipient: str) -> None:
    if not settings.require_recipient_allowlist:
        return

    normalized = recipient.casefold()
    domain = normalized.rsplit("@", 1)[-1]
    if normalized in settings.recipient_allowlist:
        return
    if domain in settings.allowed_recipient_domains:
        return
    raise MailRelayValidationError(
        "该收件人不在允许范围内。请将其加入 recipient_allowlist，"
        "或将其域名加入 allowed_recipient_domains。"
    )


def _reject_header_injection(value: str, field_name: str) -> None:
    if "\r" in value or "\n" in value:
        raise MailRelayValidationError(f"{field_name}不能包含换行符。")
