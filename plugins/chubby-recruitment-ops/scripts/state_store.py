#!/usr/bin/env python3
"""Local idempotency and run-state store for recruitment operations."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    candidate_id TEXT,
    received_at TEXT,
    processed_at TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_until TEXT,
    lease_token TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS attachments (
    sha256 TEXT PRIMARY KEY,
    filename TEXT,
    message_id TEXT NOT NULL,
    candidate_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    counts_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE TABLE IF NOT EXISTS run_locks (
    lock_name TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    lease_until TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    action_id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    decision TEXT,
    reviewer_id TEXT,
    reviewed_at TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    recipient_ref TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_until TEXT,
    lease_token TEXT,
    external_message_id TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(action_id) REFERENCES actions(action_id)
);

CREATE TABLE IF NOT EXISTS source_watermarks (
    source_name TEXT NOT NULL,
    stream_name TEXT NOT NULL,
    watermark TEXT NOT NULL,
    snapshot_fingerprint TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(source_name, stream_name)
);
"""


ALLOWED_MESSAGE_STATUSES = {"processing", "success", "duplicate", "failed", "manual_review"}
ALLOWED_COMPLETION_STATUSES = ALLOWED_MESSAGE_STATUSES - {"processing"}
ALLOWED_RUN_STATUSES = {"running", "success", "partial", "failed"}
ALLOWED_ACTION_STATUSES = {
    "pending_approval",
    "approved",
    "request_info",
    "talent_pool",
    "declined",
}
ACTION_DECISIONS = {
    "approve": "approved",
    "request_info": "request_info",
    "talent_pool": "talent_pool",
    "decline": "declined",
}
ALLOWED_NOTIFICATION_STATUSES = {"pending", "processing", "sent", "failed"}


def get_source_watermark(
    connection: sqlite3.Connection,
    source_name: str,
    stream_name: str,
) -> dict[str, Any] | None:
    if not source_name.strip() or not stream_name.strip():
        raise ValueError("source_name and stream_name are required")
    row = connection.execute(
        """
        SELECT source_name, stream_name, watermark, snapshot_fingerprint, updated_at
        FROM source_watermarks
        WHERE source_name = ? AND stream_name = ?
        """,
        (source_name, stream_name),
    ).fetchone()
    return dict(row) if row else None


def set_source_watermark(
    connection: sqlite3.Connection,
    source_name: str,
    stream_name: str,
    watermark: str,
    snapshot_fingerprint: str,
) -> dict[str, Any]:
    if not source_name.strip() or not stream_name.strip():
        raise ValueError("source_name and stream_name are required")
    if not watermark.isdigit():
        raise ValueError("watermark must be a millisecond timestamp string")
    normalized_fingerprint = snapshot_fingerprint.lower()
    if (
        len(normalized_fingerprint) != 64
        or any(ch not in "0123456789abcdef" for ch in normalized_fingerprint)
    ):
        raise ValueError("snapshot_fingerprint must be a SHA-256 hexadecimal string")
    existing = get_source_watermark(connection, source_name, stream_name)
    if existing and int(watermark) < int(existing["watermark"]):
        raise ValueError("source watermark cannot move backwards")
    now = utc_now()
    connection.execute(
        """
        INSERT INTO source_watermarks
            (source_name, stream_name, watermark, snapshot_fingerprint, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_name, stream_name) DO UPDATE SET
            watermark = excluded.watermark,
            snapshot_fingerprint = excluded.snapshot_fingerprint,
            updated_at = excluded.updated_at
        """,
        (
            source_name,
            stream_name,
            watermark,
            normalized_fingerprint,
            now,
        ),
    )
    connection.commit()
    stored = get_source_watermark(connection, source_name, stream_name)
    if stored is None:
        raise sqlite3.IntegrityError("failed to store source watermark")
    return stored


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(processed_messages)")
    }
    if "attempt_count" not in columns:
        connection.execute(
            "ALTER TABLE processed_messages ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
        )
    if "lease_until" not in columns:
        connection.execute("ALTER TABLE processed_messages ADD COLUMN lease_until TEXT")
    if "lease_token" not in columns:
        connection.execute("ALTER TABLE processed_messages ADD COLUMN lease_token TEXT")
    connection.commit()
    os.chmod(db_path, 0o600)
    return connection


def message_seen(connection: sqlite3.Connection, message_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM processed_messages WHERE message_id = ?", (message_id,)
    ).fetchone()
    return {"seen": row is not None, "record": dict(row) if row else None}


def attachment_seen(connection: sqlite3.Connection, sha256: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM attachments WHERE sha256 = ?", (sha256.lower(),)
    ).fetchone()
    return {"seen": row is not None, "record": dict(row) if row else None}


def claim_message(
    connection: sqlite3.Connection,
    message_id: str,
    received_at: str | None = None,
    detail: dict[str, Any] | None = None,
    lease_seconds: int = 900,
    max_attempts: int = 5,
) -> str | None:
    """Atomically claim a new, failed, or expired message for processing."""
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")

    now = datetime.now(timezone.utc)
    now_text = now.isoformat(timespec="seconds")
    lease_until = (now + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
    lease_token = secrets.token_urlsafe(24)
    detail_json = json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)

    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            """
            SELECT status, attempt_count, lease_until
            FROM processed_messages
            WHERE message_id = ?
            """,
            (message_id,),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO processed_messages
                    (
                        message_id, status, candidate_id, received_at, processed_at,
                        attempt_count, lease_until, lease_token, detail_json
                    )
                VALUES (?, 'processing', NULL, ?, ?, 1, ?, ?, ?)
                """,
                (
                    message_id,
                    received_at,
                    now_text,
                    lease_until,
                    lease_token,
                    detail_json,
                ),
            )
            claimed = True
        else:
            expired = bool(row["lease_until"] and row["lease_until"] <= now_text)
            retryable = row["status"] == "failed" or (
                row["status"] == "processing" and expired
            )
            claimed = retryable and row["attempt_count"] < max_attempts
            if claimed:
                connection.execute(
                    """
                    UPDATE processed_messages
                    SET status = 'processing',
                        received_at = COALESCE(?, received_at),
                        processed_at = ?,
                        attempt_count = attempt_count + 1,
                        lease_until = ?,
                        lease_token = ?,
                        detail_json = ?
                    WHERE message_id = ?
                    """,
                    (
                        received_at,
                        now_text,
                        lease_until,
                        lease_token,
                        detail_json,
                        message_id,
                    ),
                )
        connection.commit()
        return lease_token if claimed else None
    except Exception:
        connection.rollback()
        raise


def mark_message(
    connection: sqlite3.Connection,
    message_id: str,
    status: str,
    lease_token: str,
    candidate_id: str | None = None,
    received_at: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    if status not in ALLOWED_COMPLETION_STATUSES:
        raise ValueError(f"unsupported message status: {status}")
    existing = connection.execute(
        "SELECT status, lease_token FROM processed_messages WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    if existing is None:
        raise KeyError(f"message must be claimed before completion: {message_id}")
    if existing["status"] != "processing" or existing["lease_token"] != lease_token:
        raise ValueError(f"stale or invalid message lease: {message_id}")
    cursor = connection.execute(
        """
        UPDATE processed_messages
        SET status = ?,
            candidate_id = COALESCE(?, candidate_id),
            received_at = COALESCE(?, received_at),
            processed_at = ?,
            lease_until = NULL,
            lease_token = NULL,
            detail_json = ?
        WHERE message_id = ?
          AND status = 'processing'
          AND lease_token = ?
        """,
        (
            status,
            candidate_id,
            received_at,
            utc_now(),
            json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
            message_id,
            lease_token,
        ),
    )
    if cursor.rowcount != 1:
        connection.rollback()
        raise ValueError(f"message lease changed during completion: {message_id}")
    connection.commit()


def mark_attachment(
    connection: sqlite3.Connection,
    sha256: str,
    message_id: str,
    filename: str | None = None,
    candidate_id: str | None = None,
) -> None:
    normalized_hash = sha256.lower()
    if len(normalized_hash) != 64 or any(ch not in "0123456789abcdef" for ch in normalized_hash):
        raise ValueError("sha256 must be a 64-character hexadecimal string")
    connection.execute(
        """
        INSERT OR IGNORE INTO attachments
            (sha256, filename, message_id, candidate_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (normalized_hash, filename, message_id, candidate_id, utc_now()),
    )
    connection.commit()


def claim_run_lock(
    connection: sqlite3.Connection,
    lock_name: str,
    run_id: str,
    lease_seconds: int = 1800,
) -> str | None:
    """Claim an expiring singleton lock for a scheduled workflow."""
    if not lock_name.strip() or not run_id.strip():
        raise ValueError("lock_name and run_id are required")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    now = datetime.now(timezone.utc)
    now_text = now.isoformat(timespec="seconds")
    lease_until = (now + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
    lease_token = secrets.token_urlsafe(24)
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT lease_until FROM run_locks WHERE lock_name = ?",
            (lock_name,),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO run_locks
                    (lock_name, run_id, lease_until, lease_token, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (lock_name, run_id, lease_until, lease_token, now_text),
            )
            claimed = True
        else:
            claimed = row["lease_until"] <= now_text
            if claimed:
                connection.execute(
                    """
                    UPDATE run_locks
                    SET run_id = ?, lease_until = ?, lease_token = ?, updated_at = ?
                    WHERE lock_name = ?
                    """,
                    (run_id, lease_until, lease_token, now_text, lock_name),
                )
        connection.commit()
        return lease_token if claimed else None
    except Exception:
        connection.rollback()
        raise


def release_run_lock(
    connection: sqlite3.Connection,
    lock_name: str,
    lease_token: str,
) -> None:
    cursor = connection.execute(
        "DELETE FROM run_locks WHERE lock_name = ? AND lease_token = ?",
        (lock_name, lease_token),
    )
    if cursor.rowcount != 1:
        connection.rollback()
        raise ValueError(f"stale or invalid run lock: {lock_name}")
    connection.commit()


def enqueue_action(
    connection: sqlite3.Connection,
    action_id: str,
    action_type: str,
    target_id: str,
    fingerprint: str,
    payload: dict[str, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Insert an approval action once, using its fingerprint as the stable key."""
    if not all(
        isinstance(value, str) and value.strip()
        for value in (action_id, action_type, target_id, fingerprint)
    ):
        raise ValueError("action identifiers and fingerprint are required")
    now = utc_now()
    connection.execute(
        """
        INSERT OR IGNORE INTO actions
            (
                action_id, action_type, target_id, fingerprint, status,
                payload_json, created_at, updated_at
            )
        VALUES (?, ?, ?, ?, 'pending_approval', ?, ?, ?)
        """,
        (
            action_id,
            action_type,
            target_id,
            fingerprint,
            json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
            now,
            now,
        ),
    )
    row = connection.execute(
        "SELECT * FROM actions WHERE fingerprint = ?",
        (fingerprint,),
    ).fetchone()
    if commit:
        connection.commit()
    if row is None:
        raise sqlite3.IntegrityError("failed to enqueue action")
    return dict(row)


def record_action_decision(
    connection: sqlite3.Connection,
    action_id: str,
    decision: str,
    reviewer_id: str,
    expected_fingerprint: str,
) -> dict[str, Any]:
    """Record an authorized human decision without executing protected actions."""
    if decision not in ACTION_DECISIONS:
        raise ValueError(f"unsupported action decision: {decision}")
    if not reviewer_id.strip():
        raise ValueError("reviewer_id is required")
    row = connection.execute(
        "SELECT * FROM actions WHERE action_id = ?",
        (action_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"action not found: {action_id}")
    if row["fingerprint"] != expected_fingerprint:
        raise ValueError(f"action fingerprint changed: {action_id}")
    if row["status"] != "pending_approval":
        raise ValueError(f"action is no longer pending approval: {action_id}")
    now = utc_now()
    cursor = connection.execute(
        """
        UPDATE actions
        SET status = ?, decision = ?, reviewer_id = ?, reviewed_at = ?, updated_at = ?
        WHERE action_id = ? AND status = 'pending_approval' AND fingerprint = ?
        """,
        (
            ACTION_DECISIONS[decision],
            decision,
            reviewer_id,
            now,
            now,
            action_id,
            expected_fingerprint,
        ),
    )
    if cursor.rowcount != 1:
        connection.rollback()
        raise ValueError(f"action changed during approval: {action_id}")
    connection.commit()
    updated = connection.execute(
        "SELECT * FROM actions WHERE action_id = ?",
        (action_id,),
    ).fetchone()
    return dict(updated)


def enqueue_notification(
    connection: sqlite3.Connection,
    notification_id: str,
    action_id: str,
    recipient_ref: str,
    fingerprint: str,
    payload: dict[str, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Append one redacted notification to the transactional outbox."""
    action = connection.execute(
        "SELECT action_id FROM actions WHERE action_id = ?",
        (action_id,),
    ).fetchone()
    if action is None:
        raise KeyError(f"action not found: {action_id}")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (notification_id, recipient_ref, fingerprint)
    ):
        raise ValueError("notification identifiers and fingerprint are required")
    now = utc_now()
    connection.execute(
        """
        INSERT OR IGNORE INTO notifications
            (
                notification_id, action_id, recipient_ref, fingerprint, status,
                payload_json, created_at, updated_at
            )
        VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            notification_id,
            action_id,
            recipient_ref,
            fingerprint,
            json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
            now,
            now,
        ),
    )
    row = connection.execute(
        "SELECT * FROM notifications WHERE fingerprint = ?",
        (fingerprint,),
    ).fetchone()
    if commit:
        connection.commit()
    if row is None:
        raise sqlite3.IntegrityError("failed to enqueue notification")
    return dict(row)


def notification_seen(
    connection: sqlite3.Connection,
    notification_id: str,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM notifications WHERE notification_id = ?",
        (notification_id,),
    ).fetchone()
    return {"seen": row is not None, "record": dict(row) if row else None}


def list_notifications(
    connection: sqlite3.Connection,
    status: str = "pending",
    limit: int = 50,
) -> list[dict[str, Any]]:
    if status not in ALLOWED_NOTIFICATION_STATUSES:
        raise ValueError(f"unsupported notification status: {status}")
    if limit <= 0 or limit > 500:
        raise ValueError("notification limit must be between 1 and 500")
    rows = connection.execute(
        """
        SELECT *
        FROM notifications
        WHERE status = ?
        ORDER BY created_at, notification_id
        LIMIT ?
        """,
        (status, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def claim_notification(
    connection: sqlite3.Connection,
    notification_id: str,
    lease_seconds: int = 900,
    max_attempts: int = 5,
) -> str | None:
    """Claim one pending, failed, or expired notification for external delivery."""
    if lease_seconds <= 0 or max_attempts <= 0:
        raise ValueError("lease_seconds and max_attempts must be positive")
    now = datetime.now(timezone.utc)
    now_text = now.isoformat(timespec="seconds")
    lease_until = (now + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
    lease_token = secrets.token_urlsafe(24)
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            """
            SELECT status, attempt_count, lease_until
            FROM notifications
            WHERE notification_id = ?
            """,
            (notification_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"notification not found: {notification_id}")
        expired = bool(row["lease_until"] and row["lease_until"] <= now_text)
        claimable = row["status"] in {"pending", "failed"} or (
            row["status"] == "processing" and expired
        )
        claimed = claimable and row["attempt_count"] < max_attempts
        if claimed:
            connection.execute(
                """
                UPDATE notifications
                SET status = 'processing',
                    attempt_count = attempt_count + 1,
                    lease_until = ?,
                    lease_token = ?,
                    updated_at = ?
                WHERE notification_id = ?
                """,
                (lease_until, lease_token, now_text, notification_id),
            )
        connection.commit()
        return lease_token if claimed else None
    except Exception:
        connection.rollback()
        raise


def complete_notification(
    connection: sqlite3.Connection,
    notification_id: str,
    lease_token: str,
    status: str,
    external_message_id: str | None = None,
    last_error: str | None = None,
) -> None:
    if status not in {"sent", "failed"}:
        raise ValueError("notification completion status must be sent or failed")
    if status == "sent" and not external_message_id:
        raise ValueError("sent notification requires external_message_id")
    cursor = connection.execute(
        """
        UPDATE notifications
        SET status = ?,
            external_message_id = ?,
            last_error = ?,
            lease_until = NULL,
            lease_token = NULL,
            updated_at = ?
        WHERE notification_id = ?
          AND status = 'processing'
          AND lease_token = ?
        """,
        (
            status,
            external_message_id,
            last_error,
            utc_now(),
            notification_id,
            lease_token,
        ),
    )
    if cursor.rowcount != 1:
        connection.rollback()
        raise ValueError(f"stale or invalid notification lease: {notification_id}")
    connection.commit()


def start_run(connection: sqlite3.Connection, run_id: str, run_type: str) -> None:
    connection.execute(
        """
        INSERT INTO runs (run_id, run_type, status, started_at)
        VALUES (?, ?, 'running', ?)
        """,
        (run_id, run_type, utc_now()),
    )
    connection.commit()


def finish_run(
    connection: sqlite3.Connection,
    run_id: str,
    status: str,
    counts: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    if status not in ALLOWED_RUN_STATUSES - {"running"}:
        raise ValueError(f"unsupported finished run status: {status}")
    cursor = connection.execute(
        """
        UPDATE runs
        SET status = ?, finished_at = ?, counts_json = ?, error = ?
        WHERE run_id = ?
        """,
        (
            status,
            utc_now(),
            json.dumps(counts or {}, ensure_ascii=False, sort_keys=True),
            error,
            run_id,
        ),
    )
    if cursor.rowcount != 1:
        raise KeyError(f"run not found: {run_id}")
    connection.commit()


def parse_json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("value must be a JSON object")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Path to the SQLite state database")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init")

    message_seen_parser = subparsers.add_parser("message-seen")
    message_seen_parser.add_argument("message_id")

    claim_message_parser = subparsers.add_parser("claim-message")
    claim_message_parser.add_argument("message_id")
    claim_message_parser.add_argument("--received-at")
    claim_message_parser.add_argument("--detail", type=parse_json_object, default={})
    claim_message_parser.add_argument("--lease-seconds", type=int, default=900)
    claim_message_parser.add_argument("--max-attempts", type=int, default=5)

    attachment_seen_parser = subparsers.add_parser("attachment-seen")
    attachment_seen_parser.add_argument("sha256")

    mark_message_parser = subparsers.add_parser("mark-message")
    mark_message_parser.add_argument("message_id")
    mark_message_parser.add_argument(
        "--status", required=True, choices=sorted(ALLOWED_COMPLETION_STATUSES)
    )
    mark_message_parser.add_argument("--lease-token", required=True)
    mark_message_parser.add_argument("--candidate-id")
    mark_message_parser.add_argument("--received-at")
    mark_message_parser.add_argument("--detail", type=parse_json_object, default={})

    mark_attachment_parser = subparsers.add_parser("mark-attachment")
    mark_attachment_parser.add_argument("sha256")
    mark_attachment_parser.add_argument("--message-id", required=True)
    mark_attachment_parser.add_argument("--filename")
    mark_attachment_parser.add_argument("--candidate-id")

    start_run_parser = subparsers.add_parser("start-run")
    start_run_parser.add_argument("run_id")
    start_run_parser.add_argument("--type", required=True)

    finish_run_parser = subparsers.add_parser("finish-run")
    finish_run_parser.add_argument("run_id")
    finish_run_parser.add_argument(
        "--status", required=True, choices=sorted(ALLOWED_RUN_STATUSES - {"running"})
    )
    finish_run_parser.add_argument("--counts", type=parse_json_object, default={})
    finish_run_parser.add_argument("--error")

    record_decision_parser = subparsers.add_parser("record-action-decision")
    record_decision_parser.add_argument("action_id")
    record_decision_parser.add_argument(
        "--decision",
        required=True,
        choices=sorted(ACTION_DECISIONS),
    )
    record_decision_parser.add_argument("--reviewer-id", required=True)
    record_decision_parser.add_argument("--fingerprint", required=True)

    list_notifications_parser = subparsers.add_parser("list-notifications")
    list_notifications_parser.add_argument(
        "--status",
        choices=sorted(ALLOWED_NOTIFICATION_STATUSES),
        default="pending",
    )
    list_notifications_parser.add_argument("--limit", type=int, default=50)

    claim_notification_parser = subparsers.add_parser("claim-notification")
    claim_notification_parser.add_argument("notification_id")
    claim_notification_parser.add_argument("--lease-seconds", type=int, default=900)
    claim_notification_parser.add_argument("--max-attempts", type=int, default=5)

    complete_notification_parser = subparsers.add_parser("complete-notification")
    complete_notification_parser.add_argument("notification_id")
    complete_notification_parser.add_argument("--lease-token", required=True)
    complete_notification_parser.add_argument(
        "--status",
        required=True,
        choices=["sent", "failed"],
    )
    complete_notification_parser.add_argument("--external-message-id")
    complete_notification_parser.add_argument("--error")

    get_watermark_parser = subparsers.add_parser("get-source-watermark")
    get_watermark_parser.add_argument("--source", required=True)
    get_watermark_parser.add_argument("--stream", required=True)

    set_watermark_parser = subparsers.add_parser("set-source-watermark")
    set_watermark_parser.add_argument("--source", required=True)
    set_watermark_parser.add_argument("--stream", required=True)
    set_watermark_parser.add_argument("--watermark", required=True)
    set_watermark_parser.add_argument("--fingerprint", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    connection = connect(args.db)
    try:
        if args.command == "init":
            result: dict[str, Any] = {"ok": True, "db": str(args.db)}
        elif args.command == "message-seen":
            result = message_seen(connection, args.message_id)
        elif args.command == "claim-message":
            lease_token = claim_message(
                connection,
                args.message_id,
                args.received_at,
                args.detail,
                args.lease_seconds,
                args.max_attempts,
            )
            result = {
                "ok": True,
                "claimed": lease_token is not None,
                "lease_token": lease_token,
                **message_seen(connection, args.message_id),
            }
        elif args.command == "attachment-seen":
            result = attachment_seen(connection, args.sha256)
        elif args.command == "mark-message":
            mark_message(
                connection,
                args.message_id,
                args.status,
                args.lease_token,
                args.candidate_id,
                args.received_at,
                args.detail,
            )
            result = {"ok": True, **message_seen(connection, args.message_id)}
        elif args.command == "mark-attachment":
            mark_attachment(
                connection,
                args.sha256,
                args.message_id,
                args.filename,
                args.candidate_id,
            )
            result = {"ok": True, **attachment_seen(connection, args.sha256)}
        elif args.command == "start-run":
            start_run(connection, args.run_id, args.type)
            result = {"ok": True, "run_id": args.run_id, "status": "running"}
        elif args.command == "finish-run":
            finish_run(connection, args.run_id, args.status, args.counts, args.error)
            result = {"ok": True, "run_id": args.run_id, "status": args.status}
        elif args.command == "record-action-decision":
            result = {
                "ok": True,
                "action": record_action_decision(
                    connection,
                    args.action_id,
                    args.decision,
                    args.reviewer_id,
                    args.fingerprint,
                ),
            }
        elif args.command == "list-notifications":
            items = list_notifications(connection, args.status, args.limit)
            result = {
                "ok": True,
                "status": args.status,
                "count": len(items),
                "notifications": items,
            }
        elif args.command == "claim-notification":
            notification_lease = claim_notification(
                connection,
                args.notification_id,
                args.lease_seconds,
                args.max_attempts,
            )
            result = {
                "ok": True,
                "claimed": notification_lease is not None,
                "lease_token": notification_lease,
                **notification_seen(connection, args.notification_id),
            }
        elif args.command == "complete-notification":
            complete_notification(
                connection,
                args.notification_id,
                args.lease_token,
                args.status,
                args.external_message_id,
                args.error,
            )
            result = {
                "ok": True,
                **notification_seen(connection, args.notification_id),
            }
        elif args.command == "get-source-watermark":
            result = {
                "ok": True,
                "watermark": get_source_watermark(
                    connection,
                    args.source,
                    args.stream,
                ),
            }
        elif args.command == "set-source-watermark":
            result = {
                "ok": True,
                "watermark": set_source_watermark(
                    connection,
                    args.source,
                    args.stream,
                    args.watermark,
                    args.fingerprint,
                ),
            }
        else:
            raise AssertionError(f"unhandled command: {args.command}")
    except (ValueError, KeyError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        connection.close()

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
