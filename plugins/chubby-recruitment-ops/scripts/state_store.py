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
"""


ALLOWED_MESSAGE_STATUSES = {"processing", "success", "duplicate", "failed", "manual_review"}
ALLOWED_COMPLETION_STATUSES = ALLOWED_MESSAGE_STATUSES - {"processing"}
ALLOWED_RUN_STATUSES = {"running", "success", "partial", "failed"}


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
