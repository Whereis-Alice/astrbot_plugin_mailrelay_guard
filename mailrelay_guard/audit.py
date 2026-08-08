"""Minimal local audit records that deliberately omit message content."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditWriter:
    """Append privacy-minimized JSONL records with single-file rotation."""

    def __init__(self, data_dir: Path, *, max_file_kb: int) -> None:
        self._data_dir = data_dir
        self._path = data_dir / "mailrelay_guard_audit.jsonl"
        self._rotated_path = data_dir / "mailrelay_guard_audit.previous.jsonl"
        self._max_bytes = max_file_kb * 1024

    async def append(
        self,
        *,
        action: str,
        outcome: str,
        actor_id: str,
        recipients: list[str] | tuple[str, ...] = (),
        detail: str = "",
    ) -> None:
        record = {
            "at": datetime.now(UTC).isoformat(),
            "action": action,
            "outcome": outcome,
            "actor_fingerprint": _fingerprint(actor_id),
            "recipient_count": len(recipients),
            "recipient_domains": sorted(
                {
                    recipient.rsplit("@", 1)[-1].casefold()
                    for recipient in recipients
                    if "@" in recipient
                }
            ),
            "detail": detail[:160],
        }
        await asyncio.to_thread(self._append_sync, record)

    def _append_sync(self, record: dict[str, Any]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        if self._path.exists() and self._path.stat().st_size >= self._max_bytes:
            try:
                if self._rotated_path.exists():
                    self._rotated_path.unlink()
                self._path.replace(self._rotated_path)
            except OSError:
                # Audit failure must never prevent an already-authorized delivery.
                pass

        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass


def _fingerprint(value: str) -> str:
    normalized = str(value or "anonymous").encode("utf-8", errors="replace")
    return hashlib.sha256(normalized).hexdigest()[:16]
