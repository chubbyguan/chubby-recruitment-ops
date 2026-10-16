#!/usr/bin/env python3
"""Reconcile calendar, video-meeting, and transcript facts without making hiring decisions."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
MEETING_COMPLETED = {"ended", "completed"}
MEETING_CANCELED = {"canceled", "cancelled"}
USABLE_ARTIFACT_TYPES = ("intelligent_transcript", "minutes_transcript")
ARTIFACT_STATUSES = {
    "available",
    "missing",
    "permission_denied",
    "processing",
}
HUMAN_DECISIONS = {
    "pending",
    "recommend_next_round",
    "request_more_info",
    "talent_pool",
    "close",
}
EVALUATION_STATUSES = {"not_started", "generated", "reviewed", "blocked"}


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def safe_id(value: Any) -> str | None:
    text = str(value if value is not None else "").strip()
    return text if SAFE_ID.fullmatch(text) else None


def as_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def artifact_state(
    artifacts: list[Any],
    minimum_seconds: int,
) -> tuple[str, list[str], list[str]]:
    available: dict[str, list[str]] = {
        "intelligent_transcript": [],
        "minutes_transcript": [],
    }
    short: list[str] = []
    permission_denied = False
    processing = False

    for raw_artifact in artifacts:
        artifact = as_object(raw_artifact)
        artifact_id = safe_id(artifact.get("artifact_id"))
        artifact_type = artifact.get("type")
        status = artifact.get("status")
        duration = artifact.get("duration_seconds")
        if artifact_type not in USABLE_ARTIFACT_TYPES:
            continue
        if status not in ARTIFACT_STATUSES:
            continue
        if status == "permission_denied":
            permission_denied = True
            continue
        if status == "processing":
            processing = True
            continue
        if status != "available" or not artifact_id:
            continue
        if type(duration) is int and duration >= minimum_seconds:
            available[artifact_type].append(artifact_id)
        else:
            short.append(artifact_id)

    for artifact_type in USABLE_ARTIFACT_TYPES:
        if available[artifact_type]:
            return artifact_type, available[artifact_type], short
    if short:
        return "insufficient_transcript", [], short
    if permission_denied:
        return "permission_denied", [], []
    if processing:
        return "processing", [], []
    return "no_artifact", [], []


def interview_status(
    calendar: dict[str, Any],
    meeting: dict[str, Any],
    run_at: datetime,
) -> str:
    meeting_status = str(meeting.get("status", "")).lower()
    if meeting_status in MEETING_COMPLETED:
        return "completed"
    if meeting_status in MEETING_CANCELED:
        return "canceled"

    calendar_status = str(calendar.get("status", "")).lower()
    if calendar_status in MEETING_CANCELED:
        return "canceled"
    planned_end = parse_time(calendar.get("planned_end"))
    if planned_end and planned_end < run_at:
        return "attendance_unverified"
    return "scheduled"


def reconcile(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema_version") != "1.0":
        raise ValueError("schema_version must be 1.0")
    run_at = parse_time(data.get("run_at"))
    if run_at is None or run_at.tzinfo is None:
        raise ValueError("run_at must be a timezone-aware ISO timestamp")
    minimum_seconds = data.get("artifact_min_seconds")
    if type(minimum_seconds) is not int or minimum_seconds <= 0:
        raise ValueError("artifact_min_seconds must be a positive integer")
    evaluation_sla_hours = data.get("evaluation_sla_hours", 24)
    decision_sla_hours = data.get("decision_sla_hours", 48)
    for field, value in (
        ("evaluation_sla_hours", evaluation_sla_hours),
        ("decision_sla_hours", decision_sla_hours),
    ):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{field} must be a positive integer")

    updates: list[dict[str, Any]] = []
    decision_queue: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    seen_interviews: set[str] = set()

    raw_interviews = data.get("interviews")
    if not isinstance(raw_interviews, list):
        raise ValueError("interviews must be an array")

    for index, raw_interview in enumerate(raw_interviews):
        interview = as_object(raw_interview)
        interview_id = safe_id(interview.get("interview_id"))
        candidate_id = safe_id(interview.get("candidate_id"))
        application_id = safe_id(interview.get("application_id"))
        interviewed_job_id = safe_id(interview.get("interviewed_job_id"))
        current_job_id = safe_id(interview.get("current_matched_job_id"))
        required = {
            "interview_id": interview_id,
            "candidate_id": candidate_id,
            "application_id": application_id,
            "interviewed_job_id": interviewed_job_id,
            "current_matched_job_id": current_job_id,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ValueError(
                f"interviews[{index}] has invalid or missing IDs: {', '.join(missing)}"
            )
        if interview_id in seen_interviews:
            raise ValueError(f"duplicate interview_id: {interview_id}")
        seen_interviews.add(interview_id)

        human_decision = interview.get("human_decision_status", "pending")
        if human_decision not in HUMAN_DECISIONS:
            raise ValueError(
                f"interviews[{index}].human_decision_status is invalid"
            )
        evaluation_status = interview.get("evaluation_status", "not_started")
        if evaluation_status not in EVALUATION_STATUSES:
            raise ValueError(f"interviews[{index}].evaluation_status is invalid")

        calendar = as_object(interview.get("calendar"))
        meeting = as_object(interview.get("meeting"))
        status = interview_status(calendar, meeting, run_at)
        artifact_status, artifact_ids, short_artifact_ids = artifact_state(
            as_list(interview.get("artifacts")),
            minimum_seconds,
        )
        meeting_id = safe_id(meeting.get("meeting_id"))
        event_id = safe_id(calendar.get("event_id"))
        actual_end = parse_time(meeting.get("actual_end"))
        evaluation_due_at = (
            actual_end + timedelta(hours=evaluation_sla_hours)
            if status == "completed" and actual_end
            else None
        )
        decision_due_at = (
            actual_end + timedelta(hours=decision_sla_hours)
            if status == "completed" and actual_end
            else None
        )
        evaluation_overdue = bool(
            evaluation_due_at
            and evaluation_due_at < run_at
            and artifact_status in USABLE_ARTIFACT_TYPES
            and evaluation_status == "not_started"
        )
        decision_overdue = bool(
            decision_due_at
            and decision_due_at < run_at
            and human_decision == "pending"
        )

        update = {
            "interview_id": interview_id,
            "candidate_id": candidate_id,
            "application_id": application_id,
            "interviewed_job_id": interviewed_job_id,
            "current_matched_job_id": current_job_id,
            "calendar_event_id": event_id,
            "meeting_id": meeting_id,
            "planned_start": calendar.get("planned_start"),
            "actual_start": meeting.get("actual_start"),
            "actual_end": meeting.get("actual_end"),
            "interview_status": status,
            "artifact_status": artifact_status,
            "source_artifact_ids": artifact_ids,
            "evaluation_status": evaluation_status,
            "evaluation_due_at": (
                evaluation_due_at.isoformat() if evaluation_due_at else None
            ),
            "evaluation_overdue": evaluation_overdue,
            "human_decision_status": human_decision,
            "decision_due_at": decision_due_at.isoformat() if decision_due_at else None,
            "decision_overdue": decision_overdue,
        }
        updates.append(update)

        if status == "completed" and (
            parse_time(meeting.get("actual_start")) is None or actual_end is None
        ):
            anomalies.append(
                {
                    "code": "interview_meeting_fact_incomplete",
                    "interview_id": interview_id,
                    "artifact_status": artifact_status,
                    "artifact_ids": [],
                }
            )

        if status == "completed" and artifact_status in USABLE_ARTIFACT_TYPES:
            if evaluation_status == "not_started":
                decision_queue.append(
                    {
                        "action_id": f"INTERVIEW-EVALUATE-{interview_id}",
                        "action_type": "generate_interview_evaluation",
                        "target_id": interview_id,
                        "risk": "low",
                        "requires_human_approval": False,
                        "due_at": update["evaluation_due_at"],
                    }
                )
        elif status == "completed":
            code = (
                "interview_transcript_insufficient"
                if artifact_status == "insufficient_transcript"
                else "interview_artifact_missing"
            )
            anomalies.append(
                {
                    "code": code,
                    "interview_id": interview_id,
                    "artifact_status": artifact_status,
                    "artifact_ids": short_artifact_ids,
                }
            )
            decision_queue.append(
                {
                    "action_id": f"INTERVIEW-MANUAL-NOTE-{interview_id}",
                    "action_type": "request_manual_interview_note",
                    "target_id": interview_id,
                    "risk": "medium",
                    "requires_human_approval": False,
                    "due_at": update["evaluation_due_at"],
                }
            )
        elif status == "attendance_unverified":
            anomalies.append(
                {
                    "code": "interview_attendance_unverified",
                    "interview_id": interview_id,
                    "artifact_status": artifact_status,
                    "artifact_ids": [],
                }
            )

        if status == "completed" and human_decision == "pending":
            decision_queue.append(
                {
                    "action_id": f"INTERVIEW-HUMAN-DECISION-{interview_id}",
                    "action_type": "human_interview_decision",
                    "target_id": interview_id,
                    "risk": "high",
                    "requires_human_approval": True,
                    "due_at": update["decision_due_at"],
                    "overdue": decision_overdue,
                }
            )
            if decision_overdue:
                anomalies.append(
                    {
                        "code": "interview_decision_overdue",
                        "interview_id": interview_id,
                        "artifact_status": artifact_status,
                        "artifact_ids": [],
                    }
                )

        duplicate_candidates = [
            safe_id(value)
            for value in as_list(interview.get("possible_duplicate_candidate_ids"))
        ]
        duplicate_candidates = [
            value
            for value in duplicate_candidates
            if value and value != candidate_id
        ]
        if duplicate_candidates:
            decision_queue.append(
                {
                    "action_id": f"CANDIDATE-MERGE-REVIEW-{candidate_id}",
                    "action_type": "review_candidate_merge",
                    "target_id": candidate_id,
                    "related_candidate_ids": sorted(set(duplicate_candidates)),
                    "risk": "high",
                    "requires_human_approval": True,
                }
            )

    counts = {
        "scheduled": sum(
            item["interview_status"] == "scheduled" for item in updates
        ),
        "completed": sum(
            item["interview_status"] == "completed" for item in updates
        ),
        "attendance_unverified": sum(
            item["interview_status"] == "attendance_unverified"
            for item in updates
        ),
        "usable_transcript": sum(
            item["artifact_status"] in USABLE_ARTIFACT_TYPES for item in updates
        ),
        "artifact_missing_or_unusable": sum(
            item["artifact_status"]
            in {
                "no_artifact",
                "insufficient_transcript",
                "permission_denied",
                "processing",
            }
            for item in updates
        ),
        "awaiting_human_decision": sum(
            item["interview_status"] == "completed"
            and item["human_decision_status"] == "pending"
            for item in updates
        ),
        "evaluation_overdue": sum(
            item["evaluation_overdue"] for item in updates
        ),
        "decision_overdue": sum(item["decision_overdue"] for item in updates),
    }
    return {
        "schema_version": "1.0",
        "run_at": data["run_at"],
        "record_updates": updates,
        "decision_queue": decision_queue,
        "anomalies": anomalies,
        "counts": counts,
        "note": "completed means the meeting happened; it never means passed.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("input root must be an object")
        result = reconcile(data)
        output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
