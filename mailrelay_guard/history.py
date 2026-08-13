"""Private, local mail history for the MailRelay Guard dashboard.

This store is deliberately separate from the minimal audit log. It supports a
local outbox and a recipient-scoped delivery mirror without retaining raw
mailbox addresses. Full message content is optional and is only written after
SMTP accepts at least one recipient.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

HistoryStatus = Literal["sending", "submitted", "partial", "failed", "unknown"]
RecipientStatus = Literal["pending", "accepted", "refused", "failed", "unknown"]

_SCHEMA_VERSION = "2"
_EXCLUDED_ACTIONS = frozenset({"binding_challenge"})


class MailHistoryError(RuntimeError):
    """Raised when the optional local mail history cannot be updated."""


@dataclass(frozen=True)
class HistoryMessage:
    """A final SMTP delivery state to persist without raw recipient addresses."""

    message_id: str | None
    status: HistoryStatus
    accepted_recipients: tuple[str, ...] = ()
    refused_recipients: tuple[str, ...] = ()
    error_code: str = ""
    store_content: bool = False
    subject: str = ""
    plain_body: str = ""
    html_body: str | None = None
    attachment_count: int = 0
    inline_image_count: int = 0
    attachment_total_bytes: int = 0
    attachment_names: tuple[str, ...] = ()


class MailHistoryStore:
    """SQLite-backed, privacy-scoped history for the protected plugin page."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "mailrelay_guard_history.sqlite3"
        self._data_dir = data_dir
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self, *, retention_days: int, max_records: int) -> None:
        """Prepare the schema, recover interrupted sends, and enforce retention."""

        async with self._lock:
            await asyncio.to_thread(
                self._initialize_sync,
                retention_days,
                max_records,
            )
            self._initialized = True

    async def begin_delivery(
        self,
        *,
        action: str,
        mode: str,
        content_format: str,
        actor_id: str,
        recipients: list[str] | tuple[str, ...],
        retention_days: int,
        max_records: int,
    ) -> str | None:
        """Create a pending outbox item before issuing an SMTP command.

        Binding-code mail is intentionally excluded: its body contains a secret
        and it is not a user-authored mailbox message.
        """

        if action in _EXCLUDED_ACTIONS:
            return None
        async with self._lock:
            await self._ensure_initialized_locked(retention_days, max_records)
            return await asyncio.to_thread(
                self._begin_delivery_sync,
                action,
                mode,
                content_format,
                actor_id,
                tuple(recipients),
                retention_days,
                max_records,
            )

    async def finalize_delivery(
        self,
        history_id: str | None,
        result: HistoryMessage,
        *,
        retention_days: int,
        max_records: int,
    ) -> None:
        """Persist the final SMTP state for an existing pending message."""

        if not history_id:
            return
        async with self._lock:
            await self._ensure_initialized_locked(retention_days, max_records)
            await asyncio.to_thread(
                self._finalize_delivery_sync,
                history_id,
                result,
                retention_days,
                max_records,
            )

    async def summary(self) -> dict[str, Any]:
        """Return small operational metrics without exposing message content."""

        async with self._lock:
            await self._ensure_initialized_locked(30, 500)
            return await asyncio.to_thread(self._summary_sync)

    async def list_messages(
        self,
        *,
        folder: Literal["sent", "inbox", "errors"],
        query: str = "",
        status: str = "",
        content_format: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List sanitized summary rows for an outbox or delivery mirror."""

        async with self._lock:
            await self._ensure_initialized_locked(30, 500)
            return await asyncio.to_thread(
                self._list_messages_sync,
                folder,
                query,
                status,
                content_format,
                limit,
                offset,
            )

    async def get_message(self, history_id: str) -> dict[str, Any] | None:
        """Return one archived message without altering its mailbox state."""

        async with self._lock:
            await self._ensure_initialized_locked(30, 500)
            return await asyncio.to_thread(self._get_message_sync, history_id)

    async def update_mailbox_state(
        self,
        *,
        history_id: str,
        recipient_token: str,
        is_read: bool | None = None,
        is_starred: bool | None = None,
        archived: bool | None = None,
    ) -> dict[str, bool] | None:
        """Update local-only state for one accepted delivery-mirror item."""

        async with self._lock:
            await self._ensure_initialized_locked(30, 500)
            return await asyncio.to_thread(
                self._update_mailbox_state_sync,
                history_id,
                recipient_token,
                is_read,
                is_starred,
                archived,
            )

    async def clear(self) -> int:
        """Remove all optional history records and return the deleted count."""

        async with self._lock:
            await self._ensure_initialized_locked(30, 500)
            return await asyncio.to_thread(self._clear_sync)

    async def _ensure_initialized_locked(
        self, retention_days: int, max_records: int
    ) -> None:
        if self._initialized:
            return
        await asyncio.to_thread(self._initialize_sync, retention_days, max_records)
        self._initialized = True

    def _initialize_sync(self, retention_days: int, max_records: int) -> None:
        connection = self._connect()
        try:
            self._create_schema_sync(connection)
            now = _now_iso()
            connection.execute(
                "UPDATE messages SET status = 'unknown', completed_at = ? "
                "WHERE status = 'sending'",
                (now,),
            )
            connection.execute(
                "UPDATE message_recipients SET status = 'unknown' "
                "WHERE message_id IN (SELECT id FROM messages WHERE status = 'unknown') "
                "AND status = 'pending'"
            )
            self._prune_sync(connection, retention_days, max_records)
            connection.commit()
            _restrict_file_permissions(self._path)
        except sqlite3.Error as exc:
            connection.rollback()
            raise MailHistoryError("unable to initialize mail history") from exc
        finally:
            connection.close()

    def _begin_delivery_sync(
        self,
        action: str,
        mode: str,
        content_format: str,
        actor_id: str,
        recipients: tuple[str, ...],
        retention_days: int,
        max_records: int,
    ) -> str:
        connection = self._connect()
        try:
            secret = self._history_secret_sync(connection)
            history_id = secrets.token_hex(16)
            now = _now_iso()
            connection.execute(
                """
                INSERT INTO messages (
                    id, created_at, action, mode, content_format, status,
                    actor_token, recipient_count, accepted_count, refused_count,
                    content_saved
                ) VALUES (?, ?, ?, ?, ?, 'sending', ?, ?, 0, 0, 0)
                """,
                (
                    history_id,
                    now,
                    _bounded_text(action, 80),
                    _bounded_text(mode, 32),
                    "html" if content_format == "html" else "plain",
                    _stable_token(secret, actor_id),
                    len(recipients),
                ),
            )
            for recipient in recipients:
                connection.execute(
                    """
                    INSERT INTO message_recipients (
                        message_id, recipient_token, masked_address, recipient_domain,
                        status
                    ) VALUES (?, ?, ?, ?, 'pending')
                    """,
                    (
                        history_id,
                        _stable_token(secret, recipient.casefold()),
                        _mask_email(recipient),
                        _recipient_domain(recipient),
                    ),
                )
            self._prune_sync(connection, retention_days, max_records)
            connection.commit()
            return history_id
        except sqlite3.Error as exc:
            connection.rollback()
            raise MailHistoryError("unable to create mail history item") from exc
        finally:
            connection.close()

    def _finalize_delivery_sync(
        self,
        history_id: str,
        result: HistoryMessage,
        retention_days: int,
        max_records: int,
    ) -> None:
        connection = self._connect()
        try:
            secret = self._history_secret_sync(connection)
            accepted_tokens = {
                _stable_token(secret, recipient.casefold())
                for recipient in result.accepted_recipients
            }
            refused_tokens = {
                _stable_token(secret, recipient.casefold())
                for recipient in result.refused_recipients
            }
            rows = connection.execute(
                "SELECT recipient_token FROM message_recipients WHERE message_id = ?",
                (history_id,),
            ).fetchall()
            for row in rows:
                token = str(row["recipient_token"])
                recipient_status = _recipient_status_for(
                    token,
                    accepted_tokens,
                    refused_tokens,
                    result.status,
                )
                connection.execute(
                    "UPDATE message_recipients SET status = ? "
                    "WHERE message_id = ? AND recipient_token = ?",
                    (recipient_status, history_id, token),
                )

            should_store_content = result.store_content and bool(accepted_tokens)
            connection.execute(
                """
                UPDATE messages
                SET completed_at = ?, status = ?, message_id = ?,
                    accepted_count = ?, refused_count = ?, error_code = ?,
                    content_saved = ?, subject = ?, plain_body = ?, html_body = ?,
                    attachment_count = ?, inline_image_count = ?,
                    attachment_total_bytes = ?, attachment_names = ?
                WHERE id = ?
                """,
                (
                    _now_iso(),
                    result.status,
                    _bounded_text(result.message_id or "", 255) or None,
                    len(accepted_tokens),
                    len(refused_tokens),
                    _bounded_text(result.error_code, 80) or None,
                    1 if should_store_content else 0,
                    _bounded_text(result.subject, 998) if should_store_content else None,
                    _bounded_text(result.plain_body, 50_000)
                    if should_store_content
                    else None,
                    _bounded_text(result.html_body or "", 200_000)
                    if should_store_content and result.html_body
                    else None,
                    max(0, int(result.attachment_count)),
                    max(0, int(result.inline_image_count)),
                    max(0, int(result.attachment_total_bytes)),
                    _serialize_attachment_names(result.attachment_names)
                    if should_store_content
                    else None,
                    history_id,
                ),
            )
            self._prune_sync(connection, retention_days, max_records)
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise MailHistoryError("unable to finalize mail history item") from exc
        finally:
            connection.close()

    def _summary_sync(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            today_start = datetime.now(UTC).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            totals = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'submitted' THEN 1 ELSE 0 END) AS submitted,
                    SUM(CASE WHEN status = 'partial' THEN 1 ELSE 0 END) AS partial,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN content_format = 'html' THEN 1 ELSE 0 END) AS html,
                    MAX(created_at) AS latest_at
                FROM messages
                """
            ).fetchone()
            today = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status IN ('submitted', 'partial') THEN 1 ELSE 0 END)
                        AS accepted
                FROM messages WHERE created_at >= ?
                """,
                (today_start,),
            ).fetchone()
            return {
                "total": int(totals["total"] or 0),
                "submitted": int(totals["submitted"] or 0),
                "partial": int(totals["partial"] or 0),
                "failed": int(totals["failed"] or 0),
                "html": int(totals["html"] or 0),
                "latest_at": totals["latest_at"],
                "today_total": int(today["total"] or 0),
                "today_accepted": int(today["accepted"] or 0),
            }
        except sqlite3.Error as exc:
            raise MailHistoryError("unable to read mail history summary") from exc
        finally:
            connection.close()

    def _list_messages_sync(
        self,
        folder: Literal["sent", "inbox", "errors"],
        query: str,
        status: str,
        content_format: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        connection = self._connect()
        try:
            where, parameters = _message_filters(
                folder=folder,
                query=query,
                status=status,
                content_format=content_format,
            )
            total_row = connection.execute(
                f"SELECT COUNT(*) AS count FROM messages {where}", parameters
            ).fetchone()
            rows = connection.execute(
                """
                SELECT id, created_at, completed_at, action, mode, content_format,
                       status, recipient_count, accepted_count, refused_count,
                       error_code, content_saved, subject, attachment_count,
                       inline_image_count, attachment_total_bytes
                FROM messages
                """
                + where
                + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*parameters, limit, offset),
            ).fetchall()
            recipient_map = self._recipient_map_sync(
                connection, [str(row["id"]) for row in rows]
            )
            if folder == "inbox":
                items = _as_inbox_items(rows, recipient_map)
            else:
                items = _as_message_items(rows, recipient_map)
            return {
                "items": items,
                "total": int(total_row["count"] or 0),
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(rows) < int(total_row["count"] or 0),
            }
        except sqlite3.Error as exc:
            raise MailHistoryError("unable to list mail history") from exc
        finally:
            connection.close()

    def _get_message_sync(self, history_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT id, created_at, completed_at, action, mode, content_format,
                       status, message_id, actor_token, recipient_count,
                       accepted_count, refused_count, error_code, content_saved,
                       subject, plain_body, html_body, attachment_count,
                       inline_image_count, attachment_total_bytes, attachment_names
                FROM messages WHERE id = ?
                """,
                (history_id,),
            ).fetchone()
            if row is None:
                return None
            recipients = self._recipient_map_sync(connection, [history_id]).get(
                history_id, []
            )
            return {
                "id": str(row["id"]),
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
                "action": row["action"],
                "mode": row["mode"],
                "content_format": row["content_format"],
                "status": row["status"],
                "message_id": row["message_id"],
                "actor_token": row["actor_token"],
                "recipient_count": int(row["recipient_count"] or 0),
                "accepted_count": int(row["accepted_count"] or 0),
                "refused_count": int(row["refused_count"] or 0),
                "error_code": row["error_code"],
                "content_saved": bool(row["content_saved"]),
                "subject": row["subject"],
                "plain_body": row["plain_body"],
                "html_body": row["html_body"],
                "attachment_count": int(row["attachment_count"] or 0),
                "inline_image_count": int(row["inline_image_count"] or 0),
                "attachment_total_bytes": int(
                    row["attachment_total_bytes"] or 0
                ),
                "attachment_names": _deserialize_attachment_names(
                    row["attachment_names"]
                ),
                "recipients": recipients,
            }
        except sqlite3.Error as exc:
            raise MailHistoryError("unable to read mail history item") from exc
        finally:
            connection.close()

    def _update_mailbox_state_sync(
        self,
        history_id: str,
        recipient_token: str,
        is_read: bool | None,
        is_starred: bool | None,
        archived: bool | None,
    ) -> dict[str, bool] | None:
        connection = self._connect()
        try:
            recipient = connection.execute(
                """
                SELECT 1 FROM message_recipients
                WHERE message_id = ? AND recipient_token = ? AND status = 'accepted'
                """,
                (history_id, recipient_token),
            ).fetchone()
            if recipient is None:
                return None
            existing = connection.execute(
                """
                SELECT is_read, is_starred, archived_at FROM mailbox_item_state
                WHERE message_id = ? AND recipient_token = ?
                """,
                (history_id, recipient_token),
            ).fetchone()
            current_read = bool(existing["is_read"]) if existing else False
            current_starred = bool(existing["is_starred"]) if existing else False
            current_archived = bool(existing and existing["archived_at"])
            next_read = current_read if is_read is None else is_read
            next_starred = current_starred if is_starred is None else is_starred
            next_archived = current_archived if archived is None else archived
            archived_at = _now_iso() if next_archived else None
            connection.execute(
                """
                INSERT INTO mailbox_item_state (
                    message_id, recipient_token, is_read, is_starred, archived_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(message_id, recipient_token) DO UPDATE SET
                    is_read = excluded.is_read,
                    is_starred = excluded.is_starred,
                    archived_at = excluded.archived_at
                """,
                (
                    history_id,
                    recipient_token,
                    1 if next_read else 0,
                    1 if next_starred else 0,
                    archived_at,
                ),
            )
            connection.commit()
            return {
                "is_read": next_read,
                "is_starred": next_starred,
                "archived": next_archived,
            }
        except sqlite3.Error as exc:
            connection.rollback()
            raise MailHistoryError("unable to update local mailbox state") from exc
        finally:
            connection.close()

    def _clear_sync(self) -> int:
        connection = self._connect()
        try:
            row = connection.execute("SELECT COUNT(*) AS count FROM messages").fetchone()
            connection.execute("DELETE FROM messages")
            connection.commit()
            return int(row["count"] or 0)
        except sqlite3.Error as exc:
            connection.rollback()
            raise MailHistoryError("unable to clear mail history") from exc
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 3000")
        return connection

    @staticmethod
    def _create_schema_sync(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS history_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                action TEXT NOT NULL,
                mode TEXT NOT NULL,
                content_format TEXT NOT NULL,
                status TEXT NOT NULL,
                message_id TEXT,
                actor_token TEXT NOT NULL,
                recipient_count INTEGER NOT NULL,
                accepted_count INTEGER NOT NULL DEFAULT 0,
                refused_count INTEGER NOT NULL DEFAULT 0,
                error_code TEXT,
                content_saved INTEGER NOT NULL DEFAULT 0,
                subject TEXT,
                plain_body TEXT,
                html_body TEXT
                , attachment_count INTEGER NOT NULL DEFAULT 0
                , inline_image_count INTEGER NOT NULL DEFAULT 0
                , attachment_total_bytes INTEGER NOT NULL DEFAULT 0
                , attachment_names TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_messages_created_at
                ON messages(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_status
                ON messages(status);
            CREATE TABLE IF NOT EXISTS message_recipients (
                message_id TEXT NOT NULL,
                recipient_token TEXT NOT NULL,
                masked_address TEXT NOT NULL,
                recipient_domain TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY(message_id, recipient_token),
                FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_message_recipients_status
                ON message_recipients(status);
            CREATE TABLE IF NOT EXISTS mailbox_item_state (
                message_id TEXT NOT NULL,
                recipient_token TEXT NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0,
                is_starred INTEGER NOT NULL DEFAULT 0,
                archived_at TEXT,
                PRIMARY KEY(message_id, recipient_token),
                FOREIGN KEY(message_id, recipient_token)
                    REFERENCES message_recipients(message_id, recipient_token)
                    ON DELETE CASCADE
            );
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO history_meta(key, value) VALUES ('schema_version', ?)",
            (_SCHEMA_VERSION,),
        )
        _ensure_column(
            connection,
            "messages",
            "attachment_count",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            connection,
            "messages",
            "inline_image_count",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            connection,
            "messages",
            "attachment_total_bytes",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(connection, "messages", "attachment_names", "TEXT")
        connection.execute(
            "UPDATE history_meta SET value = ? WHERE key = 'schema_version'",
            (_SCHEMA_VERSION,),
        )

    @staticmethod
    def _history_secret_sync(connection: sqlite3.Connection) -> bytes:
        row = connection.execute(
            "SELECT value FROM history_meta WHERE key = 'hmac_secret'"
        ).fetchone()
        if row is not None:
            try:
                return bytes.fromhex(str(row["value"]))
            except ValueError:
                pass
        secret = secrets.token_bytes(32)
        connection.execute(
            "INSERT INTO history_meta(key, value) VALUES ('hmac_secret', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (secret.hex(),),
        )
        return secret

    @staticmethod
    def _prune_sync(
        connection: sqlite3.Connection, retention_days: int, max_records: int
    ) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        connection.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
        connection.execute(
            """
            DELETE FROM messages WHERE id IN (
                SELECT id FROM messages
                ORDER BY created_at DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (max_records,),
        )

    @staticmethod
    def _recipient_map_sync(
        connection: sqlite3.Connection, history_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        if not history_ids:
            return {}
        placeholders = ",".join("?" for _ in history_ids)
        rows = connection.execute(
            """
            SELECT recipients.message_id, recipients.recipient_token,
                   recipients.masked_address, recipients.recipient_domain,
                   recipients.status, states.is_read, states.is_starred,
                   states.archived_at
            FROM message_recipients AS recipients
            LEFT JOIN mailbox_item_state AS states
                ON states.message_id = recipients.message_id
                AND states.recipient_token = recipients.recipient_token
            WHERE recipients.message_id IN ("""
            + placeholders
            + ") ORDER BY recipients.masked_address ASC",
            history_ids,
        ).fetchall()
        result: dict[str, list[dict[str, Any]]] = {history_id: [] for history_id in history_ids}
        for row in rows:
            result[str(row["message_id"])].append(
                {
                    "token": str(row["recipient_token"]),
                    "address": str(row["masked_address"]),
                    "domain": str(row["recipient_domain"]),
                    "status": str(row["status"]),
                    "is_read": bool(row["is_read"]),
                    "is_starred": bool(row["is_starred"]),
                    "archived": bool(row["archived_at"]),
                }
            )
        return result


def _message_filters(
    *, folder: str, query: str, status: str, content_format: str
) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    values: list[Any] = []
    if folder == "errors":
        clauses.append("status IN ('failed', 'partial', 'unknown')")
    if status in {"sending", "submitted", "partial", "failed", "unknown"}:
        clauses.append("status = ?")
        values.append(status)
    if content_format in {"plain", "html"}:
        clauses.append("content_format = ?")
        values.append(content_format)
    normalized_query = _bounded_text(query, 120)
    if normalized_query:
        clauses.append(
            "(subject LIKE ? OR action LIKE ? OR mode LIKE ? OR "
            "EXISTS (SELECT 1 FROM message_recipients AS query_recipients "
            "WHERE query_recipients.message_id = messages.id "
            "AND (query_recipients.masked_address LIKE ? "
            "OR query_recipients.recipient_domain LIKE ?)))"
        )
        search = f"%{normalized_query}%"
        values.extend((search, search, search, search, search))
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return where, tuple(values)


def _as_message_items(
    rows: list[sqlite3.Row], recipient_map: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    return [_summary_item(row, recipient_map.get(str(row["id"]), [])) for row in rows]


def _as_inbox_items(
    rows: list[sqlite3.Row], recipient_map: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        message_id = str(row["id"])
        for recipient in recipient_map.get(message_id, []):
            if recipient["status"] != "accepted" or recipient["archived"]:
                continue
            item = _summary_item(row, [recipient])
            item["recipient"] = recipient
            item["is_read"] = recipient["is_read"]
            item["is_starred"] = recipient["is_starred"]
            items.append(item)
    return items


def _summary_item(row: sqlite3.Row, recipients: list[dict[str, Any]]) -> dict[str, Any]:
    content_saved = bool(row["content_saved"])
    return {
        "id": str(row["id"]),
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "action": row["action"],
        "mode": row["mode"],
        "content_format": row["content_format"],
        "status": row["status"],
        "recipient_count": int(row["recipient_count"] or 0),
        "accepted_count": int(row["accepted_count"] or 0),
        "refused_count": int(row["refused_count"] or 0),
        "error_code": row["error_code"],
        "content_saved": content_saved,
        "subject": row["subject"] if content_saved else None,
        "attachment_count": int(row["attachment_count"] or 0),
        "inline_image_count": int(row["inline_image_count"] or 0),
        "attachment_total_bytes": int(row["attachment_total_bytes"] or 0),
        "recipients": recipients,
    }


def _recipient_status_for(
    token: str,
    accepted_tokens: set[str],
    refused_tokens: set[str],
    message_status: HistoryStatus,
) -> RecipientStatus:
    if token in accepted_tokens:
        return "accepted"
    if token in refused_tokens:
        return "refused"
    if message_status == "failed":
        return "failed"
    if message_status == "unknown":
        return "unknown"
    return "pending"


def _stable_token(secret: bytes, value: str) -> str:
    return hmac.new(
        secret,
        str(value or "anonymous").encode("utf-8", errors="replace"),
        hashlib.sha256,
    ).hexdigest()[:24]


def _recipient_domain(address: str) -> str:
    value = str(address or "").strip().casefold()
    return value.rsplit("@", 1)[-1] if "@" in value else "unknown"


def _mask_email(address: str) -> str:
    local, separator, domain = str(address or "").strip().partition("@")
    if not separator or not local or not domain:
        return "(unknown)"
    if len(local) == 1:
        local_mask = "*"
    elif len(local) == 2:
        local_mask = local[0] + "*"
    else:
        local_mask = local[0] + "***" + local[-1]
    return f"{local_mask}@{domain.casefold()}"


def _bounded_text(value: str, maximum: int) -> str:
    return str(value or "").replace("\x00", "").strip()[:maximum]


def _serialize_attachment_names(values: tuple[str, ...]) -> str | None:
    names = [_bounded_text(value, 180) for value in values if _bounded_text(value, 180)]
    return "\n".join(names[:20]) or None


def _deserialize_attachment_names(value: Any) -> list[str]:
    return [line for line in str(value or "").splitlines() if line][:20]


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    existing = {
        str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")
    }
    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _restrict_file_permissions(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
