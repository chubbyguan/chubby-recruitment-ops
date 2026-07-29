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


SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
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
    application_id: str | None = None,
) -> str:
    payload = {
        "candidate_id": candidate_id,
        "application_id": application_id,
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
    application_id: str | None = None,
    job_id: str | None = None,
) -> bool:
    def matches_application_or_same_role(record: dict[str, Any]) -> bool:
        record_application_id = record.get("application_id")
        if application_id and record_application_id == application_id:
            return True
        if application_id:
            return (
                record.get("candidate_id") == candidate_id
                and record.get("job_id") == job_id
            )
        return record.get("candidate_id") == candidate_id

    for interview in interviews:
        if not matches_application_or_same_role(interview):
            continue
        if interview.get("record_type") not in FORMAL_INTERVIEW_TYPES:
            continue
        status = str(interview.get("status", "")).strip().lower()
        if status not in CANCELED_INTERVIEW_STATUSES:
            return True
    for event in calendar_events:
        if not matches_application_or_same_role(event):
            continue
        status = str(event.get("status", "")).strip().lower()
        if status not in CANCELED_CALENDAR_STATUSES:
            return True
    return False


def recommend_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Rank application records without collapsing them into candidate identity."""
    policy = as_object(data.get("recommendation_policy"))
    default_threshold = score_value(
        policy.get("default_minimum_score", 80),
        "recommendation_policy.default_minimum_score",
    )
    default_top_k = policy.get("default_top_k_per_job", 3)
    if type(default_top_k) is not int or default_top_k <= 0:
        raise ValueError(
            "recommendation_policy.default_top_k_per_job must be a positive integer"
        )
    max_per_candidate = policy.get("max_recommendations_per_candidate", 1)
    if type(max_per_candidate) is not int or max_per_candidate <= 0:
        raise ValueError(
            "recommendation_policy.max_recommendations_per_candidate must be positive"
        )
    require_hard_gate_default = (
        policy.get("require_hard_gate_pass", True) is True
    )

    jobs: dict[str, dict[str, Any]] = {}
    for index, raw_job in enumerate(as_list(data.get("jobs"))):
        job = as_object(raw_job)
        job_id = safe_id(job.get("job_id"), f"jobs[{index}].job_id")
        if job_id in jobs:
            raise ValueError(f"duplicate job_id: {job_id}")
        jobs[job_id] = job

    candidates: dict[str, dict[str, Any]] = {}
    for index, raw_candidate in enumerate(as_list(data.get("candidates"))):
        candidate = as_object(raw_candidate)
        candidate_id = safe_id(
            candidate.get("candidate_id"),
            f"candidates[{index}].candidate_id",
        )
        if candidate_id in candidates:
            raise ValueError(f"duplicate candidate_id: {candidate_id}")
        candidates[candidate_id] = candidate

    interviews = [as_object(item) for item in as_list(data.get("interviews"))]
    calendar_events = [
        as_object(item) for item in as_list(data.get("calendar_events"))
    ]
    anomalies: list[dict[str, Any]] = []
    for index, event in enumerate(calendar_events):
        if not event.get("application_id") and not (
            event.get("candidate_id") and event.get("job_id")
        ):
            event_id = event.get("event_id")
            anomalies.append(
                {
                    "event_id": (
                        safe_id(event_id, f"calendar_events[{index}].event_id")
                        if event_id
                        else f"calendar-index-{index}"
                    ),
                    "reason_code": "calendar_event_missing_application_identity",
                    "requires_human_review": True,
                }
            )

    sent_keys = {
        safe_id(item, f"previous_sent_keys[{index}]")
        for index, item in enumerate(as_list(data.get("previous_sent_keys")))
    }
    needs_information: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    eligible_by_job: dict[str, list[dict[str, Any]]] = {}
    seen_applications: set[str] = set()

    for index, raw_application in enumerate(as_list(data.get("applications"))):
        application = as_object(raw_application)
        application_id = safe_id(
            application.get("application_id"),
            f"applications[{index}].application_id",
        )
        if application_id in seen_applications:
            raise ValueError(f"duplicate application_id: {application_id}")
        seen_applications.add(application_id)
        candidate_id = safe_id(
            application.get("candidate_id"),
            f"applications[{index}].candidate_id",
        )
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise ValueError(
                f"applications[{index}] references unknown candidate_id: {candidate_id}"
            )
        job_id = safe_id(
            application.get("job_id"),
            f"applications[{index}].job_id",
        )
        job_version = safe_id(
            application.get("job_version"),
            f"applications[{index}].job_version",
        )
        score = score_value(
            application.get("score"),
            f"applications[{index}].score",
        )
        evidence_refs = string_codes(
            application.get("evidence_refs"),
            f"applications[{index}].evidence_refs",
        )
        risk_codes = string_codes(
            application.get("risk_codes"),
            f"applications[{index}].risk_codes",
        )
        missing_material_codes = string_codes(
            application.get("missing_material_codes"),
            f"applications[{index}].missing_material_codes",
        )
        base_result = {
            "application_id": application_id,
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
        if has_interview_fact(
            candidate_id,
            interviews,
            calendar_events,
            application_id=application_id,
            job_id=job_id,
        ):
            excluded.append(
                {**base_result, "reason_code": "already_in_interview_flow"}
            )
            continue

        gate_policy = as_object(job.get("recommendation_gate"))
        threshold = score_value(
            gate_policy.get("minimum_score", default_threshold),
            f"jobs[{job_id}].recommendation_gate.minimum_score",
        )
        require_hard_gate = gate_policy.get(
            "require_hard_gate_pass",
            require_hard_gate_default,
        )
        if not isinstance(require_hard_gate, bool):
            raise ValueError(
                f"jobs[{job_id}].recommendation_gate.require_hard_gate_pass "
                "must be boolean"
            )
        incomplete_reasons: list[str] = []
        if candidate.get("has_resume") is not True:
            incomplete_reasons.append("resume_missing")
        if candidate.get("information_complete") is not True:
            incomplete_reasons.append("information_incomplete")
        if not evidence_refs:
            incomplete_reasons.append("evidence_missing")
        if missing_material_codes:
            incomplete_reasons.append("materials_missing")
        if application.get("duplicate_review_pending") is True:
            incomplete_reasons.append("duplicate_review_pending")
        if (
            application.get("screening_recommendation")
            not in ALLOWED_SCREENING_RECOMMENDATIONS
        ):
            incomplete_reasons.append("screening_recommendation_not_eligible")
        if require_hard_gate and application.get("hard_gate_pass") is not True:
            incomplete_reasons.append("hard_gate_not_verified")
        if score < threshold:
            incomplete_reasons.append("below_recommendation_threshold")
        if incomplete_reasons:
            needs_information.append(
                {
                    **base_result,
                    "effective_minimum_score": threshold,
                    "reason_codes": sorted(set(incomplete_reasons)),
                    "suggested_action": "request_more_info",
                }
            )
            continue

        requires_fact_confirmation = (
            str(application.get("application_stage", "")).strip()
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
            application_id=application_id,
        )
        eligible_by_job.setdefault(job_id, []).append(
            {
                **base_result,
                "effective_minimum_score": threshold,
                "suggested_action": suggested_action,
                "requires_fact_confirmation": requires_fact_confirmation,
                "requires_human_approval": True,
                "idempotency_key": idempotency_key,
                "action_id": f"ACTION-{idempotency_key}",
                "notification_status": (
                    "already_sent" if idempotency_key in sent_keys else "pending"
                ),
            }
        )

    recommendations: list[dict[str, Any]] = []
    candidate_counts: dict[str, int] = {}
    for job_id in sorted(eligible_by_job):
        gate_policy = as_object(jobs[job_id].get("recommendation_gate"))
        top_k = gate_policy.get("top_k", default_top_k)
        if type(top_k) is not int or top_k <= 0:
            raise ValueError(
                f"jobs[{job_id}].recommendation_gate.top_k must be positive"
            )
        ranked = sorted(
            eligible_by_job[job_id],
            key=lambda item: (-item["score"], item["application_id"]),
        )
        role_count = 0
        for item in ranked:
            candidate_id = item["candidate_id"]
            if candidate_counts.get(candidate_id, 0) >= max_per_candidate:
                excluded.append(
                    {**item, "reason_code": "candidate_already_recommended"}
                )
                continue
            if role_count >= top_k:
                excluded.append({**item, "reason_code": "outside_role_top_k"})
                continue
            recommendations.append(item)
            candidate_counts[candidate_id] = candidate_counts.get(candidate_id, 0) + 1
            role_count += 1

    notifications = [
        item
        for item in recommendations
        if item["notification_status"] == "pending"
    ]
    return {
        "schema_version": "2.0",
        "counts": {
            "candidates_scanned": len(candidates),
            "applications_scanned": len(seen_applications),
            "recommended": len(recommendations),
            "needs_information": len(needs_information),
            "excluded": len(excluded),
            "new_notifications": len(notifications),
            "anomalies": len(anomalies),
        },
        "recommendations": recommendations,
        "needs_information": needs_information,
        "excluded": excluded,
        "notifications": notifications,
        "anomalies": anomalies,
    }


def recommend(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema_version") == "2.0":
        return recommend_v2(data)
    if data.get("schema_version") != "1.0":
        raise ValueError("schema_version must be 1.0 or 2.0")
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
