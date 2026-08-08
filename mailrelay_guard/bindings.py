"""Private, local storage for user-verified self-delivery mailboxes."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class MailboxBindingError(ValueError):
    """Raised when a mailbox binding cannot be created or verified."""


@dataclass(frozen=True)
class BoundMailbox:
    """A verified mailbox linked to one platform-scoped sender identity."""

    address: str
    verified_at: str


@dataclass(frozen=True)
class PendingVerification:
    """An in-memory verification challenge; the plaintext code is never kept."""

    address: str
    code_hash: str
    issued_at: float
    expires_at: float
    failed_attempts: int = 0


class MailboxBindingStore:
    """Persist verified bindings and retain verification challenges in memory only."""

    def __init__(
        self,
        data_dir: Path,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = data_dir / "mailrelay_guard_mailboxes.json"
        self._now = now
        self._bindings: dict[str, BoundMailbox] = {}
        self._challenges: dict[str, PendingVerification] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    async def get(self, actor_key: str) -> BoundMailbox | None:
        """Return the verified binding for one identity, if any."""

        async with self._lock:
            await self._ensure_loaded_locked()
            return self._bindings.get(actor_key)

    async def issue_challenge(
        self,
        *,
        actor_key: str,
        address: str,
        code: str,
        ttl_seconds: int,
        resend_seconds: int,
    ) -> None:
        """Store a new code hash while enforcing one sender's resend interval."""

        now = self._now()
        async with self._lock:
            await self._ensure_loaded_locked()
            previous = self._challenges.get(actor_key)
            if previous and now < previous.expires_at:
                wait = int(resend_seconds - (now - previous.issued_at))
                if wait > 0:
                    raise MailboxBindingError(
                        f"??????,?? {wait} ?????"
                    )
            self._challenges[actor_key] = PendingVerification(
                address=address,
                code_hash=_hash_code(code),
                issued_at=now,
                expires_at=now + ttl_seconds,
            )

    async def discard_challenge(self, actor_key: str) -> None:
        """Drop an unsent challenge after SMTP rejects the verification message."""

        async with self._lock:
            self._challenges.pop(actor_key, None)

    async def verify(
        self, *, actor_key: str, code: str, max_attempts: int
    ) -> BoundMailbox:
        """Validate a code, then atomically persist the verified mailbox."""

        now = self._now()
        async with self._lock:
            await self._ensure_loaded_locked()
            challenge = self._challenges.get(actor_key)
            if challenge is None:
                raise MailboxBindingError("????????,?????????")
            if now >= challenge.expires_at:
                self._challenges.pop(actor_key, None)
                raise MailboxBindingError("??????,??????????")
            if not hmac.compare_digest(challenge.code_hash, _hash_code(code.strip())):
                failed_attempts = challenge.failed_attempts + 1
                if failed_attempts >= max_attempts:
                    self._challenges.pop(actor_key, None)
                    raise MailboxBindingError(
                        "?????????,???????,??????????"
                    )
                self._challenges[actor_key] = PendingVerification(
                    address=challenge.address,
                    code_hash=challenge.code_hash,
                    issued_at=challenge.issued_at,
                    expires_at=challenge.expires_at,
                    failed_attempts=failed_attempts,
                )
                remaining = max_attempts - failed_attempts
                raise MailboxBindingError(f"??????,???? {remaining} ??")

            binding = BoundMailbox(
                address=challenge.address,
                verified_at=datetime.now(UTC).isoformat(),
            )
            previous = self._bindings.get(actor_key)
            self._bindings[actor_key] = binding
            try:
                await self._save_locked()
            except OSError:
                if previous is None:
                    self._bindings.pop(actor_key, None)
                else:
                    self._bindings[actor_key] = previous
                raise
            self._challenges.pop(actor_key, None)
            return binding

    async def remove(self, actor_key: str) -> bool:
        """Remove one stored binding and any pending verification challenge."""

        async with self._lock:
            await self._ensure_loaded_locked()
            previous = self._bindings.pop(actor_key, None)
            if previous is not None:
                try:
                    await self._save_locked()
                except OSError:
                    self._bindings[actor_key] = previous
                    raise
            self._challenges.pop(actor_key, None)
            return previous is not None

    async def _ensure_loaded_locked(self) -> None:
        if self._loaded:
            return
        raw_bindings = await asyncio.to_thread(_read_bindings_sync, self._path)
        self._bindings = {
            key: BoundMailbox(address=value["address"], verified_at=value["verified_at"])
            for key, value in raw_bindings.items()
        }
        self._loaded = True

    async def _save_locked(self) -> None:
        data = {
            "version": 1,
            "bindings": {
                key: {"address": value.address, "verified_at": value.verified_at}
                for key, value in self._bindings.items()
            },
        }
        await asyncio.to_thread(_write_bindings_sync, self._path, data)


def _read_bindings_sync(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    source = decoded.get("bindings", {}) if isinstance(decoded, dict) else {}
    if not isinstance(source, dict):
        return {}

    bindings: dict[str, dict[str, str]] = {}
    for key, value in source.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        address = value.get("address")
        verified_at = value.get("verified_at")
        if isinstance(address, str) and isinstance(verified_at, str):
            bindings[key] = {"address": address, "verified_at": verified_at}
    return bindings


def _write_bindings_sync(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary_path, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8", errors="replace")).hexdigest()
