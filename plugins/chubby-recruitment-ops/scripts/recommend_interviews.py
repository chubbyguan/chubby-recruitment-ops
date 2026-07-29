#!/usr/bin/env python3
"""Recommend high-scoring candidates who have not entered an interview flow."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


SAFE_ID = re.compile(r"^[A-Za-z0-9_:-]{1,128}$")
OPEN_JOB_GATES = {"open", "招聘中"}
CONDITIONAL_JOB_GATES = {"conditional", "条件岗", "人才寻访", "条件未满足"}
BLOCKED_JOB_GATES = {"frozen", "closed", "冻结", "关闭", "已到岗", "已在位"}
FORMAL_INTERVIEW_TYPES = {"formal_interview", "正式面试"}
CANCELED_INTERVIEW_STATUSES = {"canceled", "cancelled", "已取消", "取消"}
CANCELED_CALENDAR_STATUSES = {"canceled", "cancelled", "已取消", "取消"}
INTERVIEW_STAGE_VALUES = {"business_interview", "interview", "业务初面", "面试中"}
ALLOWED_SCREENING_RECOMMENDATIONS = {"hr_screen", "建议HR初筛"}


def as_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def safe_id(value: Any, field: str) -> str:
    text = str(value if value is not None else "").strip()
    if not SAFE_ID.fullmatch(text):
        raise ValueError(f"{field} must be a stable identifier")
    return text


def score_value(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    score = float(value)
    if not 0 <= score <= 100:
        raise ValueError(f"{field} must be between 0 and 100")
    return score


def string_codes(value: Any, field: str) -> list[str]:
    raw_values = as_list(value)
    result: list[str] = []
    for index, raw_value in enumerate(raw_values):
        result.append(safe_id(raw_value, f"{field}[{index}]"))
    return sorted(set(result))


def recommendation_key(
    candidate_id: str,
    job_id: str,
    job_version: str,
    score: float,
    action: str,
    evidence_refs: list[str],
    risk_codes: list[str],
    missing_material_codes: list[str],
) -> str:
    payload = {
        "candidate_id": candidate_id,
        "job_id": job_id,
        "job_version": job_version,
        "score": score,
        "action": action,
        "evidence_refs": evidence_refs,
        "risk_codes": risk_codes,
        "missing_material_codes": missing_material_codes,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"INTREC-{digest[:24]}"


def has_interview_fact(
    candidate_id: str,
    interviews: list[dict[str, Any]],
    calendar_events: list[dict[str, Any]],
) -> bool:
    for interview in interviews:
        if interview.get("candidate_id") != candidate_id:
            continue
        if interview.get("record_type") not in FORMAL_INTERVIEW_TYPES:
            continue
        status = str(interview.get("status", "")).strip().lower()
        if status not in CANCELED_INTERVIEW_STATUSES:
            return True
    for event in calendar_events:
        if event.get("candidate_id") != candidate_id:
            continue
        status = str(event.get("status", "")).strip().lower()
        if status not in CANCELED_CALENDAR_STATUSES:
            return True
    return False


def recommend(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema_version") != "1.0":
        raise ValueError("schema_version must be 1.0")
    threshold = data.get("minimum_score", 80)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("minimum_score must be a number")
    threshold = float(threshold)
    if not 0 <= threshold <= 100:
        raise ValueError("minimum_score must be between 0 and 100")

    jobs: dict[str, dict[str, Any]] = {}
    for index, raw_job in enumerate(as_list(data.get("jobs"))):
        job = as_object(raw_job)
        job_id = safe_id(job.get("job_id"), f"jobs[{index}].job_id")
        if job_id in jobs:
            raise ValueError(f"duplicate job_id: {job_id}")
        jobs[job_id] = job

    interviews = [as_object(item) for item in as_list(data.get("interviews"))]
    calendar_events = [
        as_object(item) for item in as_list(data.get("calendar_events"))
    ]
    sent_keys = {
        safe_id(item, f"previous_sent_keys[{index}]")
        for index, item in enumerate(as_list(data.get("previous_sent_keys")))
    }

    recommendations: list[dict[str, Any]] = []
    needs_information: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    notifications: list[dict[str, Any]] = []
    seen_candidates: set[str] = set()

    for index, raw_candidate in enumerate(as_list(data.get("candidates"))):
        candidate = as_object(raw_candidate)
        candidate_id = safe_id(
            candidate.get("candidate_id"),
            f"candidates[{index}].candidate_id",
        )
        if candidate_id in seen_candidates:
            raise ValueError(f"duplicate candidate_id: {candidate_id}")
        seen_candidates.add(candidate_id)
        job_id = safe_id(candidate.get("job_id"), f"candidates[{index}].job_id")
        job_version = safe_id(
            candidate.get("job_version"),
            f"candidates[{index}].job_version",
        )
        score = score_value(candidate.get("score"), f"candidates[{index}].score")
        evidence_refs = string_codes(
            candidate.get("evidence_refs"),
            f"candidates[{index}].evidence_refs",
        )
        risk_codes = string_codes(
            candidate.get("risk_codes"),
            f"candidates[{index}].risk_codes",
        )
        missing_material_codes = string_codes(
            candidate.get("missing_material_codes"),
            f"candidates[{index}].missing_material_codes",
        )
        base_result = {
            "candidate_id": candidate_id,
            "job_id": job_id,
            "job_version": job_version,
            "score": score,
            "evidence_refs": evidence_refs,
            "risk_codes": risk_codes,
            "missing_material_codes": missing_material_codes,
        }

        job = jobs.get(job_id)
        if job is None:
            excluded.append({**base_result, "reason_code": "job_not_found"})
            continue
        current_job_version = safe_id(
            job.get("job_version"),
            f"jobs[{job_id}].job_version",
        )
        if current_job_version != job_version:
            excluded.append({**base_result, "reason_code": "job_version_stale"})
            continue
        gate = str(job.get("gate", "")).strip()
        if gate in CONDITIONAL_JOB_GATES:
            excluded.append({**base_result, "reason_code": "job_conditional"})
            continue
        if gate in BLOCKED_JOB_GATES or gate not in OPEN_JOB_GATES:
            excluded.append({**base_result, "reason_code": "job_not_open"})
            continue
        if has_interview_fact(candidate_id, interviews, calendar_events):
            excluded.append(
                {**base_result, "reason_code": "already_in_interview_flow"}
            )
            continue

        information_complete = candidate.get("information_complete") is True
        has_resume = candidate.get("has_resume") is True
        screening_recommendation = candidate.get("screening_recommendation")
        duplicate_review_pending = candidate.get("duplicate_review_pending") is True
        incomplete_reasons: list[str] = []
        if not has_resume:
            incomplete_reasons.append("resume_missing")
        if not information_complete:
            incomplete_reasons.append("information_incomplete")
        if not evidence_refs:
            incomplete_reasons.append("evidence_missing")
        if missing_material_codes:
            incomplete_reasons.append("materials_missing")
        if duplicate_review_pending:
            incomplete_reasons.append("duplicate_review_pending")
        if screening_recommendation not in ALLOWED_SCREENING_RECOMMENDATIONS:
            incomplete_reasons.append("screening_recommendation_not_eligible")
        if score < threshold:
            incomplete_reasons.append("below_recommendation_threshold")
        if incomplete_reasons:
            needs_information.append(
                {
                    **base_result,
                    "reason_codes": sorted(set(incomplete_reasons)),
                    "suggested_action": "request_more_info",
                }
            )
            continue

        requires_fact_confirmation = (
            str(candidate.get("candidate_stage", "")).strip()
            in INTERVIEW_STAGE_VALUES
        )
        suggested_action = (
            "confirm_interview_fact_then_review"
            if requires_fact_confirmation
            else "recommend_interview_review"
        )
        idempotency_key = recommendation_key(
            candidate_id,
            job_id,
            job_version,
            score,
            suggested_action,
            evidence_refs,
            risk_codes,
            missing_material_codes,
        )
        item = {
            **base_result,
            "suggested_action": suggested_action,
            "requires_fact_confirmation": requires_fact_confirmation,
            "requires_human_approval": True,
            "idempotency_key": idempotency_key,
            "action_id": f"ACTION-{idempotency_key}",
            "notification_status": (
                "already_sent" if idempotency_key in sent_keys else "pending"
            ),
        }
        recommendations.append(item)
        if idempotency_key not in sent_keys:
            notifications.append(item)

    return {
        "schema_version": "1.0",
        "minimum_score": threshold,
        "counts": {
            "candidates_scanned": len(seen_candidates),
            "recommended": len(recommendations),
            "needs_information": len(needs_information),
            "excluded": len(excluded),
            "new_notifications": len(notifications),
        },
        "recommendations": recommendations,
        "needs_information": needs_information,
        "excluded": excluded,
        "notifications": notifications,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("input root must be an object")
        result = recommend(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
