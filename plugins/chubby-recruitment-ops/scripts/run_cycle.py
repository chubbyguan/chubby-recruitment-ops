#!/usr/bin/env python3
"""Run the deterministic recruitment recommendation core with an atomic outbox."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from recommend_interviews import recommend
from state_store import (
    claim_run_lock,
    connect,
    enqueue_action,
    enqueue_notification,
    finish_run,
    release_run_lock,
    start_run,
)


REQUIRED_READBACKS = {
    "jobs",
    "candidates",
    "applications",
    "interviews",
    "calendar_events",
}


def as_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def validate_readbacks(payload: dict[str, Any]) -> None:
    readbacks = as_object(payload.get("source_readbacks"))
    missing = sorted(REQUIRED_READBACKS - set(readbacks))
    if missing:
        raise ValueError(f"missing source readbacks: {', '.join(missing)}")
    for name in sorted(REQUIRED_READBACKS):
        record = as_object(readbacks.get(name))
        revision = record.get("revision")
        readback_at = record.get("readback_at")
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError(f"source_readbacks.{name}.revision is required")
        if not isinstance(readback_at, str) or not readback_at.strip():
            raise ValueError(f"source_readbacks.{name}.readback_at is required")
        try:
            datetime.fromisoformat(readback_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"source_readbacks.{name}.readback_at must be ISO-8601"
            ) from exc


def redacted_action_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Allow only stable IDs, scores, and approved codes into the outbox."""
    payload = {
        "application_id": item["application_id"],
        "candidate_id": item["candidate_id"],
        "job_id": item["job_id"],
        "job_version": item["job_version"],
        "score": item["score"],
        "effective_minimum_score": item["effective_minimum_score"],
        "suggested_action": item["suggested_action"],
        "requires_fact_confirmation": item.get(
            "requires_fact_confirmation",
            False,
        ),
        "evidence_refs": item["evidence_refs"],
        "risk_codes": item["risk_codes"],
        "missing_material_codes": item["missing_material_codes"],
        "requires_human_approval": True,
        "invite_requires_separate_human_confirmation": True,
    }
    if item.get("review_reason_codes"):
        payload["review_reason_codes"] = item["review_reason_codes"]
    if item.get("source_job_version_status"):
        payload["source_job_version_status"] = item[
            "source_job_version_status"
        ]
    if item.get("current_job_version"):
        payload["current_job_version"] = item["current_job_version"]
    return payload


def action_type(item: dict[str, Any]) -> str:
    if item["suggested_action"] == "review_job_version_migration":
        return "review_job_version_migration"
    if item["suggested_action"] == "verify_hard_gate_and_missing_information":
        return "verify_hard_gate_human_review"
    return "recommend_interview_human_review"


def execute_cycle(
    payload: dict[str, Any],
    state_db: Path,
    commit: bool = False,
) -> dict[str, Any]:
    if payload.get("schema_version") != "1.0":
        raise ValueError("cycle schema_version must be 1.0")
    if payload.get("mode") not in {"recommendation", "full"}:
        raise ValueError("cycle mode must be recommendation or full")
    validate_readbacks(payload)
    recommendation_input = as_object(payload.get("recommendation_input"))
    if recommendation_input.get("schema_version") != "2.0":
        raise ValueError("recommendation_input must use schema_version 2.0")
    result = recommend(recommendation_input)
    action_items = [
        *result["recommendations"],
        *result.get("review_actions", []),
    ]
    recipient_ref = payload.get("notification_recipient_ref")
    if result["notifications"] and (
        not isinstance(recipient_ref, str) or not recipient_ref.strip()
    ):
        raise ValueError("notification_recipient_ref is required")

    if not commit:
        return {
            "ok": True,
            "committed": False,
            "recommendation": result,
            "counts": {
                "actions_created": 0,
                "notifications_created": 0,
                "planned_actions": len(action_items),
                "planned_notifications": len(result["notifications"]),
            },
        }

    connection = connect(state_db)
    run_id = f"RUN-{uuid.uuid4().hex}"
    workspace_key = (
        payload.get("workspace_id") or payload.get("department_id") or "default"
    )
    lock_name = f"recruitment-cycle:{workspace_key}"
    lease_token = claim_run_lock(
        connection,
        lock_name=lock_name,
        run_id=run_id,
        lease_seconds=int(payload.get("run_lease_seconds", 1800)),
    )
    if lease_token is None:
        connection.close()
        return {
            "ok": False,
            "committed": False,
            "busy": True,
            "error": "another recruitment cycle holds the active lease",
        }

    actions_created = 0
    notifications_created = 0
    run_started = False
    try:
        start_run(connection, run_id, str(payload["mode"]))
        run_started = True
        for item in action_items:
            action_payload = redacted_action_payload(item)
            before = connection.total_changes
            action = enqueue_action(
                connection,
                action_id=item["action_id"],
                action_type=action_type(item),
                target_id=item["application_id"],
                fingerprint=item["idempotency_key"],
                payload=action_payload,
                commit=False,
            )
            actions_created += int(connection.total_changes > before)
            if item["notification_status"] == "pending":
                before = connection.total_changes
                enqueue_notification(
                    connection,
                    notification_id=f"NOTIFY-{item['idempotency_key']}",
                    action_id=action["action_id"],
                    recipient_ref=str(recipient_ref),
                    fingerprint=item["idempotency_key"],
                    payload=action_payload,
                    commit=False,
                )
                notifications_created += int(connection.total_changes > before)
            connection.commit()
        counts = {
            "actions_created": actions_created,
            "notifications_created": notifications_created,
            "planned_actions": len(action_items),
            "planned_notifications": len(result["notifications"]),
        }
        finish_run(connection, run_id, "success", counts=counts)
        return {
            "ok": True,
            "committed": True,
            "run_id": run_id,
            "recommendation": result,
            "counts": counts,
            "next_step": (
                "Deliver pending outbox items through the authorized collaboration "
                "adapter, then record each returned message ID."
            ),
        }
    except Exception as exc:
        connection.rollback()
        if run_started:
            finish_run(
                connection,
                run_id,
                "failed",
                counts={
                    "actions_created": actions_created,
                    "notifications_created": notifications_created,
                },
                error=str(exc),
            )
        raise
    finally:
        original_error_active = sys.exc_info()[0] is not None
        try:
            release_run_lock(connection, lock_name, str(lease_token))
        except ValueError:
            if not original_error_active:
                raise
        finally:
            connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("input root must be an object")
        result = execute_cycle(payload, args.state_db, commit=args.commit)
    except (OSError, json.JSONDecodeError, ValueError, KeyError, sqlite3.Error) as exc:
        result = {"ok": False, "error": str(exc)}
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
