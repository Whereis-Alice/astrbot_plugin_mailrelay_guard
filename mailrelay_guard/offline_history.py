"""Bounded local connection history with explicit gaps in observation."""

from __future__ import annotations

import asyncio
import secrets
import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any

RETENTION_SECONDS = 90 * 86400
MAX_SEGMENTS = 50000
MAX_INCIDENTS = 10000
RANGE_SECONDS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


class OfflineHistoryError(RuntimeError):
    """Local connection history is unavailable."""


class OfflineHistoryStore:
    """Coalesce observations; never count unobserved time as uptime or downtime."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "mailrelay_guard_offline.sqlite3"
        self._lock = asyncio.Lock()
        self._session = secrets.token_hex(12)

    def restart_session(self) -> None:
        """Do not bridge a pause or reload with the next observation."""
        self._session = secrets.token_hex(12)

    async def remap_account(self, old: str, new: str) -> None:
        """Attach startup observations to the subsequently discovered QQ identity."""
        async with self._lock:
            await asyncio.to_thread(self._remap_sync, old, new)

    def _remap_sync(self, old: str, new: str) -> None:
        try:
            with self._connect() as db:
                db.execute("UPDATE segments SET account=? WHERE account=?", (new, old))
                db.execute("UPDATE incidents SET account=? WHERE account=?", (new, old))
                db.execute("DELETE FROM accounts WHERE key=?", (old,))
        except sqlite3.Error as exc:
            raise OfflineHistoryError("无法更新掉线历史账号。") from exc

    async def break_account(self, key: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._break_sync, key)

    def _break_sync(self, key: str) -> None:
        try:
            with self._connect() as db:
                db.execute(
                    "UPDATE segments SET session='' WHERE id=(SELECT id FROM segments WHERE account=? ORDER BY id DESC LIMIT 1)",
                    (key,),
                )
        except sqlite3.Error as exc:
            raise OfflineHistoryError("无法结束掉线观测区间。") from exc

    async def initialize(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._initialize_sync)

    async def record(self, **observation: Any) -> None:
        async with self._lock:
            await asyncio.to_thread(self._record_sync, observation)

    async def overview(
        self, *, period: str = "7d", account: str = "", now: float | None = None
    ) -> dict[str, Any]:
        if period not in RANGE_SECONDS:
            raise ValueError("时间范围必须是 24h、7d 或 30d。")
        async with self._lock:
            return await asyncio.to_thread(
                self._overview_sync,
                period,
                account,
                time.time() if now is None else now,
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self._path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_sync(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS accounts (
                        key TEXT PRIMARY KEY, platform TEXT NOT NULL,
                        bot_id TEXT NOT NULL, nickname TEXT NOT NULL,
                        last_seen REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS incidents (
                        id INTEGER PRIMARY KEY, account TEXT NOT NULL,
                        started_at REAL NOT NULL, confirmed_at REAL NOT NULL,
                        ended_at REAL, end_kind TEXT NOT NULL DEFAULT '',
                        reason TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS incident_account ON incidents(account, started_at);
                    CREATE INDEX IF NOT EXISTS incident_active ON incidents(account, ended_at, id);
                    CREATE TABLE IF NOT EXISTS segments (
                        id INTEGER PRIMARY KEY, account TEXT NOT NULL,
                        session TEXT NOT NULL, started_at REAL NOT NULL,
                        ended_at REAL NOT NULL, state TEXT NOT NULL,
                        reason TEXT NOT NULL, incident_id INTEGER
                    );
                    CREATE INDEX IF NOT EXISTS segment_account ON segments(account, started_at);
                    CREATE INDEX IF NOT EXISTS segment_time ON segments(ended_at);
                    CREATE INDEX IF NOT EXISTS segment_latest ON segments(account, id);
                """)
            try:
                self._path.chmod(0o600)
            except OSError:
                pass  # Existing mail history also uses best-effort permissions.
        except (OSError, sqlite3.Error) as exc:
            raise OfflineHistoryError("无法初始化掉线历史。") from exc

    def _record_sync(self, value: dict[str, Any]) -> None:
        key = str(value["key"])
        observed_at = float(value["observed_at"])
        state = str(value["state"])
        if state not in {"online", "offline", "suspect", "unknown"}:
            raise ValueError("无效的连接历史状态。")
        reason = str(value.get("reason", ""))[:1000]
        gap_limit = max(60, float(value.get("interval", 180)) * 2 + 30)
        try:
            with self._connect() as db:
                previous = db.execute(
                    "SELECT * FROM segments WHERE account = ? ORDER BY id DESC LIMIT 1",
                    (key,),
                ).fetchone()
                if previous and observed_at < previous["ended_at"]:
                    return  # A backwards clock or stale result cannot rewrite history.
                db.execute(
                    """INSERT INTO accounts VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET platform=excluded.platform,
                       bot_id=excluded.bot_id, nickname=excluded.nickname,
                       last_seen=excluded.last_seen""",
                    (
                        key,
                        str(value.get("platform", ""))[:100],
                        str(value.get("bot_id", ""))[:100],
                        str(value.get("nickname", ""))[:200],
                        observed_at,
                    ),
                )
                active = db.execute(
                    "SELECT * FROM incidents WHERE account=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
                    (key,),
                ).fetchone()
                continuous = bool(
                    previous
                    and previous["session"] == self._session
                    and observed_at - previous["ended_at"] <= gap_limit
                )
                if active and not continuous:
                    db.execute(
                        "UPDATE incidents SET ended_at=?, end_kind='gap' WHERE id=?",
                        (
                            previous["ended_at"] if previous else observed_at,
                            active["id"],
                        ),
                    )
                    active = None
                if previous and continuous:
                    db.execute(
                        "UPDATE segments SET ended_at=? WHERE id=?",
                        (observed_at, previous["id"]),
                    )

                if state == "offline" and active is None:
                    since = float(value.get("offline_since") or observed_at)
                    # Only backdate into a continuous run of suspects. A reload or
                    # an unknown observation cannot turn a gap into known downtime.
                    candidates = (
                        db.execute(
                            "SELECT * FROM segments WHERE account=? AND started_at>=? ORDER BY id DESC",
                            (key, since),
                        ).fetchall()
                        if continuous
                        else []
                    )
                    backdated = []
                    boundary = observed_at
                    for row in candidates:
                        if (
                            row["session"] != self._session
                            or row["state"] != "suspect"
                            or row["ended_at"] < boundary
                        ):
                            break
                        backdated.append(row)
                        boundary = row["started_at"]
                    start = min(
                        [observed_at] + [row["started_at"] for row in backdated]
                    )
                    cursor = db.execute(
                        "INSERT INTO incidents(account,started_at,confirmed_at,reason) VALUES (?,?,?,?)",
                        (key, start, observed_at, reason),
                    )
                    incident_id = cursor.lastrowid
                    for row in backdated:
                        db.execute(
                            "UPDATE segments SET state='offline',incident_id=? WHERE id=?",
                            (incident_id, row["id"]),
                        )
                    active = {"id": incident_id}
                incident_id = active["id"] if active and state == "offline" else None
                if active and state == "online":
                    db.execute(
                        "UPDATE incidents SET ended_at=?,end_kind='recovered' WHERE id=?",
                        (observed_at, active["id"]),
                    )

                if (
                    previous
                    and continuous
                    and previous["state"] == state
                    and previous["incident_id"] == incident_id
                ):
                    db.execute(
                        "UPDATE segments SET reason=? WHERE id=?",
                        (reason, previous["id"]),
                    )
                else:
                    db.execute(
                        """INSERT INTO segments(account,session,started_at,ended_at,state,reason,incident_id)
                                  VALUES (?,?,?,?,?,?,?)""",
                        (
                            key,
                            self._session,
                            observed_at,
                            observed_at,
                            state,
                            reason,
                            incident_id,
                        ),
                    )
                cutoff = observed_at - RETENTION_SECONDS
                db.execute("DELETE FROM segments WHERE ended_at<?", (cutoff,))
                db.execute(
                    "DELETE FROM segments WHERE id IN (SELECT id FROM segments ORDER BY id DESC LIMIT -1 OFFSET ?)",
                    (MAX_SEGMENTS,),
                )
                db.execute(
                    "DELETE FROM incidents WHERE COALESCE(ended_at,confirmed_at)<? AND id NOT IN (SELECT incident_id FROM segments WHERE incident_id IS NOT NULL)",
                    (cutoff,),
                )
                db.execute(
                    "DELETE FROM incidents WHERE id IN (SELECT id FROM incidents ORDER BY id DESC LIMIT -1 OFFSET ?)",
                    (MAX_INCIDENTS,),
                )
                db.execute(
                    "DELETE FROM segments WHERE incident_id IS NOT NULL AND incident_id NOT IN (SELECT id FROM incidents)"
                )
                db.execute("DELETE FROM accounts WHERE last_seen<?", (cutoff,))
        except sqlite3.Error as exc:
            raise OfflineHistoryError("无法写入掉线历史。") from exc

    def _overview_sync(self, period: str, account: str, now: float) -> dict[str, Any]:
        start = now - RANGE_SECONDS[period]
        step = 3600 if period == "24h" else 86400
        buckets = [
            {
                "start": start + i * step,
                "end": min(now, start + (i + 1) * step),
                "offline_seconds": None,
                "online_seconds": 0,
                "observed_seconds": 0,
                "incident_count": 0,
            }
            for i in range(RANGE_SECONDS[period] // step)
        ]
        try:
            with self._connect() as db:
                accounts = [
                    dict(row)
                    for row in db.execute("SELECT * FROM accounts ORDER BY key")
                ]
                rows = db.execute(
                    "SELECT * FROM segments WHERE ended_at>=? AND started_at<=? AND (?='' OR account=?) ORDER BY account,started_at,id",
                    (start, now, account, account),
                ).fetchall()
                events = {
                    row["id"]: dict(row)
                    for row in db.execute(
                        "SELECT * FROM incidents WHERE COALESCE(ended_at,?)>=? AND started_at<=? AND (?='' OR account=?)",
                        (now, start, now, account, account),
                    )
                }
        except sqlite3.Error as exc:
            raise OfflineHistoryError("无法读取掉线历史。") from exc
        observed_incidents = {
            row["incident_id"] for row in rows if row["incident_id"] is not None
        }
        events = {
            key: event
            for key, event in events.items()
            if event["started_at"] >= start
            or event["ended_at"] is not None
            or key in observed_incidents
        }
        account_map = {value["key"]: value for value in accounts}
        durations: dict[int, float] = defaultdict(float)
        bucket_events = [set() for _ in buckets]
        series: dict[str, list[dict[str, Any]]] = defaultdict(list)
        latest = None
        for row in rows:
            left, right = max(start, row["started_at"]), min(now, row["ended_at"])
            latest = max(latest or 0, row["ended_at"])
            if right <= left:
                continue
            segments = series[row["account"]]
            if (
                segments
                and segments[-1]["end"] == left
                and segments[-1]["state"] == row["state"]
                and segments[-1]["reason"] == row["reason"]
            ):
                segments[-1]["end"] = right
            else:
                segments.append(
                    {
                        "start": left,
                        "end": right,
                        "state": row["state"],
                        "reason": row["reason"],
                    }
                )
            for i, bucket in enumerate(buckets):
                seconds = max(0, min(right, bucket["end"]) - max(left, bucket["start"]))
                if not seconds or row["state"] not in {"online", "offline"}:
                    continue
                bucket["observed_seconds"] += seconds
                if row["state"] == "online":
                    bucket["online_seconds"] += seconds
                else:
                    bucket["offline_seconds"] = (
                        bucket["offline_seconds"] or 0
                    ) + seconds
                    if row["incident_id"]:
                        bucket_events[i].add(row["incident_id"])
                        durations[row["incident_id"]] += seconds
        for bucket, ids in zip(buckets, bucket_events, strict=True):
            if bucket["observed_seconds"] and bucket["offline_seconds"] is None:
                bucket["offline_seconds"] = 0
            bucket["incident_count"] = len(ids)
        incidents = []
        for event in events.values():
            identity = account_map.get(event["account"], {})
            incidents.append(
                {
                    **event,
                    "nickname": identity.get("nickname", ""),
                    "bot_id": identity.get("bot_id", ""),
                    "duration_seconds": durations.get(event["id"], 0),
                    "status": event["end_kind"] or "unresolved",
                }
            )
        incidents.sort(key=lambda value: value["started_at"], reverse=True)
        observed = sum(value["observed_seconds"] for value in buckets)
        online = sum(value["online_seconds"] for value in buckets)
        present = {row["account"] for row in rows}
        selected = [
            value
            for value in accounts
            if (value["key"] == account if account else value["key"] in present)
        ]
        return {
            "available": True,
            "period": period,
            "account": account,
            "start": start,
            "end": now,
            "last_observed_at": latest,
            "retention_days": 90,
            "accounts": accounts,
            "summary": {
                "incident_count": len(events),
                "offline_seconds": sum(
                    value["offline_seconds"] or 0 for value in buckets
                ),
                "longest_seconds": max(durations.values(), default=0),
                "uptime_ratio": online / observed if observed else None,
                "observed_seconds": observed,
                "coverage_ratio": observed / (RANGE_SECONDS[period] * len(selected))
                if selected
                else 0,
            },
            "buckets": buckets,
            "series": [
                {**value, "segments": series.get(value["key"], [])}
                for value in selected
            ],
            "incidents": incidents[:100],
            "incident_total": len(incidents),
        }
