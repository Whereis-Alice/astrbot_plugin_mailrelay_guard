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
        raise MailRelayValidationError("???????????????")

    recipients: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        address = validate_email_address(raw, field_name="???")
        key = address.casefold()
        if key not in seen:
            seen.add(key)
            recipients.append(address)
    return recipients


def validate_email_address(value: str, *, field_name: str = "????") -> str:
    """Accept only one bare RFC-like address, never a display-name header."""

    raw = str(value or "").strip()
    if not raw:
        raise MailRelayValidationError(f"{field_name}?????")
    _reject_header_injection(raw, field_name)
    parsed_name, parsed_address = parseaddr(raw)
    if parsed_name or parsed_address != raw or not _SIMPLE_EMAIL_RE.fullmatch(raw):
        raise MailRelayValidationError(
            f"{field_name}??????????,?????????"
        )
    return raw


def validate_dispatch_request(
    settings: MailRelaySettings,
    recipients: list[str],
    subject: str,
    body: str,
    *,
    enforce_recipient_policy: bool,
) -> None:
    """Validate content and, when requested, the admin-other allowlist."""

    if not recipients:
        raise MailRelayValidationError("???????????????")
    if len(recipients) > settings.max_recipients_per_message:
        raise MailRelayValidationError(
            f"???????? {settings.max_recipients_per_message} ?????"
        )
    if not subject.strip():
        raise MailRelayValidationError("?????????")
    _reject_header_injection(subject, "????")
    _reject_header_injection(settings.sender_name, "?????")
    if len(subject) > settings.max_subject_chars:
        raise MailRelayValidationError(
            f"???????? {settings.max_subject_chars} ????"
        )
    if not body.strip():
        raise MailRelayValidationError("?????????")
    if len(body) > settings.max_body_chars:
        raise MailRelayValidationError(
            f"???????? {settings.max_body_chars} ????"
        )

    validate_email_address(settings.sender_address, field_name="?????")
    for recipient in recipients:
        validate_email_address(recipient, field_name="???")
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
        "?????????????????????? "
        "admin_other_recipient_allowlist,?????? "
        "admin_other_allowed_domains?"
    )


def _reject_header_injection(value: str, field_name: str) -> None:
    if "\r" in value or "\n" in value:
        raise MailRelayValidationError(f"{field_name}????????")
