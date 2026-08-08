"""Platform-scoped actor identity and privacy-aware QQ mailbox resolution."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .bindings import MailboxBindingStore
from .config import MailRelaySettings, is_placeholder_id
from .policy import MailRelayValidationError, validate_email_address


@dataclass(frozen=True)
class ActorIdentity:
    """One sender bound to a platform instance, rather than a bare QQ number."""

    platform_id: str
    platform_name: str
    sender_id: str

    @property
    def key(self) -> str:
        return f"{self.platform_id.casefold()}:{self.sender_id.casefold()}"

    @property
    def allowlist_keys(self) -> frozenset[str]:
        return frozenset({self.key})


@dataclass(frozen=True)
class ResolvedMailbox:
    """A mailbox resolved for only the current event sender."""

    address: str
    source: str


class SelfMailboxResolver:
    """Resolve a sender's self-mailbox without exposing other QQ profiles."""

    def __init__(self, bindings: MailboxBindingStore | None) -> None:
        self._bindings = bindings
        self._profile_cache: OrderedDict[
            str, tuple[float, ResolvedMailbox | None]
        ] = OrderedDict()

    async def resolve(
        self, event: Any, settings: MailRelaySettings, actor: ActorIdentity
    ) -> ResolvedMailbox | None:
        """Resolve config override, verified binding, then optional NapCat data."""

        configured = settings.self_email_overrides.get(actor.key)
        if configured:
            address = _valid_or_none(configured)
            if address:
                return ResolvedMailbox(address=address, source="configured_override")

        if self._bindings is not None:
            binding = await self._bindings.get(actor.key)
            if binding is not None:
                address = _valid_or_none(binding.address)
                if address:
                    return ResolvedMailbox(address=address, source="verified_binding")

        return await self._resolve_napcat_profile(event, settings, actor)

    async def _resolve_napcat_profile(
        self, event: Any, settings: MailRelaySettings, actor: ActorIdentity
    ) -> ResolvedMailbox | None:
        if not settings.napcat_email_lookup_enabled:
            return self._derived_qq_mailbox(settings, actor)
        if not _is_configured_qq_platform(settings, actor):
            return None

        now = time.monotonic()
        cached = self._profile_cache.get(actor.key)
        if cached is not None and now < cached[0]:
            self._profile_cache.move_to_end(actor.key)
            return cached[1]

        self._profile_cache.pop(actor.key, None)

        resolved = await self._lookup_napcat_profile(event, settings, actor)
        if resolved is None:
            resolved = self._derived_qq_mailbox(settings, actor)
        if settings.napcat_lookup_cache_seconds:
            if len(self._profile_cache) >= settings.max_tracked_actors:
                self._profile_cache.popitem(last=False)
            self._profile_cache[actor.key] = (
                now + settings.napcat_lookup_cache_seconds,
                resolved,
            )
        return resolved

    async def _lookup_napcat_profile(
        self, event: Any, settings: MailRelaySettings, actor: ActorIdentity
    ) -> ResolvedMailbox | None:
        bot = getattr(event, "bot", None)
        call_action = getattr(bot, "call_action", None)
        if not callable(call_action):
            return None

        stranger = await _napcat_action(
            call_action,
            settings.napcat_lookup_timeout_seconds,
            action="get_stranger_info",
            user_id=actor.sender_id,
            no_cache=False,
        )
        address = _mailbox_from_mapping(stranger)
        if address:
            return ResolvedMailbox(address=address, source="napcat_profile")

        if not settings.napcat_friend_list_fallback_enabled:
            return None
        friends = await _napcat_action(
            call_action,
            settings.napcat_lookup_timeout_seconds,
            action="get_friend_list",
            no_cache=False,
        )
        for friend in _as_list(friends):
            if str(friend.get("user_id", "")).strip() != actor.sender_id:
                continue
            address = _mailbox_from_mapping(friend)
            if address:
                return ResolvedMailbox(address=address, source="napcat_friend_profile")
            break
        return None

    @staticmethod
    def _derived_qq_mailbox(
        settings: MailRelaySettings, actor: ActorIdentity
    ) -> ResolvedMailbox | None:
        if not settings.allow_qq_mailbox_derivation:
            return None
        if not _is_configured_qq_platform(settings, actor):
            return None
        if not actor.sender_id.isdecimal() or not settings.qq_mail_domain:
            return None
        address = _valid_or_none(f"{actor.sender_id}@{settings.qq_mail_domain}")
        if not address:
            return None
        return ResolvedMailbox(address=address, source="qq_mailbox_derivation")


def get_actor_identity(event: Any) -> ActorIdentity | None:
    """Extract a non-empty platform-scoped sender identity from one event."""

    sender_id = _event_text(event, "get_sender_id")
    platform_id = _event_text(event, "get_platform_id")
    platform_name = _event_text(event, "get_platform_name")
    if not platform_id:
        platform_id = platform_name
    if not platform_name:
        platform_name = platform_id
    if not sender_id or not platform_id:
        return None
    return ActorIdentity(
        platform_id=platform_id,
        platform_name=platform_name,
        sender_id=sender_id,
    )


def actor_matches_configured_ids(
    actor: ActorIdentity | None, configured_ids: frozenset[str]
) -> bool:
    """Match only an exact platform-scoped sender identity."""

    if actor is None:
        return False
    configured = {
        value.strip().casefold()
        for value in configured_ids
        if not is_placeholder_id(value)
    }
    return bool(actor.allowlist_keys & configured)


def event_is_admin(event: Any) -> bool:
    """Safely read AstrBot's runtime administrator role."""

    checker = getattr(event, "is_admin", None)
    try:
        return bool(checker()) if callable(checker) else False
    except (AttributeError, TypeError):
        return False


def event_is_private_chat(event: Any) -> bool:
    """Safely determine whether the command/tool call came from a private chat."""

    checker = getattr(event, "is_private_chat", None)
    try:
        return bool(checker()) if callable(checker) else False
    except (AttributeError, TypeError):
        return False


async def _napcat_action(
    call_action: Any, timeout_seconds: int, **kwargs: Any
) -> Any:
    """Feature-detect NapCat actions; lookup failures are intentionally silent."""

    try:
        result = call_action(**kwargs)
        if inspect.isawaitable(result):
            return await asyncio.wait_for(result, timeout=timeout_seconds)
        return result
    except Exception:  # noqa: BLE001 - a platform adapter is an optional external boundary.
        return None


def _event_text(event: Any, method_name: str) -> str:
    getter = getattr(event, method_name, None)
    try:
        return str(getter() or "").strip() if callable(getter) else ""
    except (AttributeError, TypeError):
        return ""


def _is_configured_qq_platform(
    settings: MailRelaySettings, actor: ActorIdentity
) -> bool:
    allowed = settings.qq_platform_names
    return (
        actor.platform_name.casefold() in allowed
        or actor.platform_id.casefold() in allowed
    )


def _mailbox_from_mapping(value: Any) -> str | None:
    mapping = _as_mapping(value)
    if mapping is None:
        return None
    for key in ("email", "eMail", "email_address"):
        address = _valid_or_none(mapping.get(key))
        if address:
            return address
    return None


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    nested = value.get("data")
    return nested if isinstance(nested, Mapping) else value


def _as_list(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        value = value.get("data", value.get("friends", []))
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _valid_or_none(value: Any) -> str | None:
    try:
        return validate_email_address(str(value or ""), field_name="QQ 资料邮箱")
    except MailRelayValidationError:
        return None
