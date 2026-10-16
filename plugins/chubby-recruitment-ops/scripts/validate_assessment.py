#!/usr/bin/env python3
"""Validate a candidate assessment against recruitment safety rules."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SCORE_LIMITS = {
    "outcome_relevance": 30,
    "hard_skills_and_samples": 25,
    "business_judgment_and_ownership": 15,
    "ai_fact_checking_and_correction": 15,
    "collaboration_writing_learning": 10,
    "credibility_and_completeness": 5,
}
ALLOWED_RECOMMENDATIONS = {
    "hr_screen",
    "request_more_info",
    "manual_review",
    "talent_pool",
}
ALLOWED_MATCH_MODES = {"formal", "talent_mapping"}
ALLOWED_EVIDENCE_CODES = {
    "outcome_evidence_verified",
    "hard_skill_evidence_verified",
    "work_sample_verified",
    "ownership_evidence",
    "ai_fact_check_evidence",
    "information_incomplete",
}
ALLOWED_RED_FLAG_CODES = {
    "conflicting_facts",
    "unverified_claim",
    "possible_fabrication",
    "red_line_requires_review",
}
ALLOWED_MISSING_MATERIAL_CODES = {
    "work_sample",
    "metrics_screenshot",
    "contribution_statement",
    "employment_evidence",
    "other_required_evidence",
}
SAFE_REFERENCE = re.compile(r"^[A-Z][A-Z0-9_-]{2,64}$")
FORBIDDEN_CHINESE = {
    "年龄",
    "出生日期",
    "性别",
    "照片",
    "婚姻",
    "婚育",
    "生育",
    "民族",
    "国籍",
    "籍贯",
    "户籍",
    "宗教",
    "残疾",
    "家庭情况",
    "政治面貌",
}
FORBIDDEN_ENGLISH = {
    "age",
    "date of birth",
    "gender",
    "sex",
    "photo",
    "marital status",
    "fertility",
    "ethnicity",
    "religion",
    "disability",
    "nationality",
}
PROXY_ATTRIBUTE_PATTERNS = (
    re.compile(r"(?:19|20)\d{2}\s*年\s*生"),
    re.compile(r"(?<!\d)\d{1,2}\s*岁(?!\d)"),
    re.compile(r"未婚|已婚|无小孩|没有小孩|有小孩|宝妈|宝爸"),
    re.compile(r"本地人|外地人|形象好|形象佳|身体健康|健康状况"),
    re.compile(r"男性|女性|男生|女生"),
)


def flatten_text(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            values.append(str(key))
            values.extend(flatten_text(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(flatten_text(child))
    elif isinstance(value, (str, int, float, bool)):
        values.append(str(value))
    return values


def is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def is_timezone_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_assessment(
    data: dict[str, Any],
    eligible_jobs: dict[str, dict[str, Any]] | None = None,
    job_source_revision: str | None = None,
    job_source_readback_at: str | None = None,
) -> list[str]:
    errors: list[str] = []
    required = {
        "candidate_id",
        "information_complete",
        "top_matches",
        "scores",
        "total_score",
        "recommendation",
        "evidence_codes",
        "red_flag_codes",
        "missing_material_codes",
        "human_review_required",
    }
    missing = sorted(required - set(data))
    if missing:
        errors.append(f"missing fields: {', '.join(missing)}")
    extra = sorted(set(data) - required)
    if extra:
        errors.append(f"unknown assessment fields: {', '.join(extra)}")

    candidate_id = data.get("candidate_id")
    if not isinstance(candidate_id, str) or not SAFE_REFERENCE.fullmatch(candidate_id):
        errors.append("candidate_id must be a safe reference ID")

    information_complete = data.get("information_complete")
    if not isinstance(information_complete, bool):
        errors.append("information_complete must be a boolean")

    scores = data.get("scores")
    if not isinstance(scores, dict):
        errors.append("scores must be an object")
    else:
        missing_scores = sorted(set(SCORE_LIMITS) - set(scores))
        extra_scores = sorted(set(scores) - set(SCORE_LIMITS))
        if missing_scores:
            errors.append(f"missing score dimensions: {', '.join(missing_scores)}")
        if extra_scores:
            errors.append(f"unknown score dimensions: {', '.join(extra_scores)}")
        for key, limit in SCORE_LIMITS.items():
            value = scores.get(key)
            if not is_finite_number(value):
                errors.append(f"score {key} must be numeric")
            elif not 0 <= value <= limit:
                errors.append(f"score {key} must be between 0 and {limit}")

        numeric_scores = [
            value
            for key, value in scores.items()
            if key in SCORE_LIMITS and is_finite_number(value)
        ]
        if len(numeric_scores) == len(SCORE_LIMITS):
            expected_total = sum(numeric_scores)
            if data.get("total_score") != expected_total:
                errors.append(
                    f"total_score must equal dimension sum: expected {expected_total}, got {data.get('total_score')}"
                )

    total_score = data.get("total_score")
    if (
        not is_finite_number(total_score)
        or not 0 <= total_score <= 100
    ):
        errors.append("total_score must be numeric and between 0 and 100")

    recommendation = data.get("recommendation")
    if recommendation not in ALLOWED_RECOMMENDATIONS:
        errors.append(f"unsupported recommendation: {recommendation}")
    if data.get("human_review_required") is not True:
        errors.append("human_review_required must be true")

    matches = data.get("top_matches")
    if eligible_jobs is None:
        errors.append("current eligible job source is required")
    if not isinstance(job_source_revision, str) or not job_source_revision.strip():
        errors.append("current eligible job source revision is required")
    if not is_timezone_timestamp(job_source_readback_at):
        errors.append("current eligible job source readback time is required")
    if not isinstance(matches, list) or not 1 <= len(matches) <= 3:
        errors.append("top_matches must contain one to three entries")
    else:
        seen_job_ids: set[str] = set()
        match_fields = {
            "job_id",
            "job_version",
            "confidence",
            "mode",
            "matched_requirement_ids",
            "missing_requirement_ids",
            "evidence_refs",
        }
        for index, match in enumerate(matches):
            if not isinstance(match, dict):
                errors.append(f"top_matches[{index}] must be an object")
                continue
            missing_match_fields = sorted(match_fields - set(match))
            extra_match_fields = sorted(set(match) - match_fields)
            if missing_match_fields:
                errors.append(
                    f"top_matches[{index}] missing fields: {', '.join(missing_match_fields)}"
                )
            if extra_match_fields:
                errors.append(
                    f"top_matches[{index}] unknown fields: {', '.join(extra_match_fields)}"
                )
            job_id = match.get("job_id")
            if not isinstance(job_id, str) or not SAFE_REFERENCE.fullmatch(job_id):
                errors.append(f"top_matches[{index}].job_id must be a safe reference ID")
            elif job_id in seen_job_ids:
                errors.append(f"duplicate matched job_id: {job_id}")
            else:
                seen_job_ids.add(job_id)
            matched_job = (
                eligible_jobs.get(job_id)
                if eligible_jobs is not None and isinstance(job_id, str)
                else None
            )
            if matched_job is None:
                errors.append(f"top_matches[{index}] job is absent from the current job source")
            else:
                job_version = match.get("job_version")
                if job_version != matched_job.get("job_version"):
                    errors.append(f"top_matches[{index}] job_version is stale")
                gate = matched_job.get("gate")
                expected_mode = (
                    "formal"
                    if gate == "open"
                    else "talent_mapping"
                    if gate == "conditional"
                    else None
                )
                if expected_mode is None:
                    errors.append(f"top_matches[{index}] job gate is not eligible")
                elif match.get("mode") != expected_mode:
                    errors.append(
                        f"top_matches[{index}] mode differs from the current job gate"
                    )
            confidence = match.get("confidence")
            if not is_finite_number(confidence):
                errors.append(f"top_matches[{index}].confidence must be numeric")
            elif not 0 <= confidence <= 1:
                errors.append(f"top_matches[{index}].confidence must be between 0 and 1")
            if match.get("mode") not in ALLOWED_MATCH_MODES:
                errors.append(f"top_matches[{index}].mode must be formal or talent_mapping")
            for evidence_key in (
                "matched_requirement_ids",
                "missing_requirement_ids",
                "evidence_refs",
            ):
                values = match.get(evidence_key)
                if not isinstance(values, list):
                    errors.append(f"top_matches[{index}].{evidence_key} must be a list")
                elif any(
                    not isinstance(item, str) or not SAFE_REFERENCE.fullmatch(item)
                    for item in values
                ):
                    errors.append(
                        f"top_matches[{index}].{evidence_key} must contain safe reference IDs"
                    )

    code_fields = {
        "evidence_codes": ALLOWED_EVIDENCE_CODES,
        "red_flag_codes": ALLOWED_RED_FLAG_CODES,
        "missing_material_codes": ALLOWED_MISSING_MATERIAL_CODES,
    }
    for list_field, allowed_values in code_fields.items():
        values = data.get(list_field)
        if not isinstance(values, list):
            errors.append(f"{list_field} must be a list")
        elif any(
            not isinstance(item, str) or item not in allowed_values
            for item in values
        ):
            errors.append(f"{list_field} contains an unsupported code")

    red_flags = (
        data.get("red_flag_codes")
        if isinstance(data.get("red_flag_codes"), list)
        else []
    )
    missing_materials = (
        data.get("missing_material_codes")
        if isinstance(data.get("missing_material_codes"), list)
        else []
    )
    top_mode = (
        matches[0].get("mode")
        if isinstance(matches, list) and matches and isinstance(matches[0], dict)
        else None
    )
    if recommendation == "hr_screen":
        if information_complete is not True:
            errors.append("hr_screen requires complete information")
        if not is_finite_number(total_score) or total_score < 75:
            errors.append("hr_screen requires total_score >= 75")
        if red_flags:
            errors.append("hr_screen is not allowed when red_flag_codes are present")
        if top_mode != "formal":
            errors.append("hr_screen requires a formal Top 1 match")
    elif recommendation == "request_more_info":
        if information_complete is True and not missing_materials:
            errors.append("request_more_info requires missing information or materials")
    elif recommendation == "talent_pool":
        if (
            is_finite_number(total_score)
            and total_score >= 75
            and top_mode == "formal"
            and not red_flags
        ):
            errors.append(
                "a high-scoring formal match cannot be sent to talent_pool without a documented risk"
            )

    combined_text = "\n".join(flatten_text(data)).lower()
    for token in sorted(FORBIDDEN_CHINESE):
        if token in combined_text:
            errors.append(f"assessment contains protected attribute: {token}")
    for token in sorted(FORBIDDEN_ENGLISH):
        if re.search(rf"\b{re.escape(token)}\b", combined_text):
            errors.append(f"assessment contains protected attribute: {token}")
    for pattern in PROXY_ATTRIBUTE_PATTERNS:
        if pattern.search(combined_text):
            errors.append(
                f"assessment contains protected-attribute proxy: {pattern.pattern}"
            )

    return errors


def load_eligible_jobs(
    value: Any,
) -> tuple[dict[str, dict[str, Any]], str, str]:
    rows = value.get("jobs") if isinstance(value, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("eligible jobs source must contain a non-empty jobs list")
    jobs: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"jobs[{index}] must be an object")
        job_id = row.get("job_id")
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError(f"jobs[{index}].job_id must be a non-empty string")
        if job_id in jobs:
            raise ValueError(f"duplicate eligible job_id: {job_id}")
        jobs[job_id] = row
    source_revision = value.get("source_revision")
    source_readback_at = value.get("source_readback_at")
    if not isinstance(source_revision, str) or not source_revision.strip():
        raise ValueError("eligible jobs source_revision is required")
    if not is_timezone_timestamp(source_readback_at):
        raise ValueError("eligible jobs source_readback_at must be timezone-aware ISO 8601")
    return jobs, source_revision, source_readback_at


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assessment", type=Path)
    parser.add_argument("--jobs", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.assessment.read_text(encoding="utf-8"))
        jobs_data = json.loads(args.jobs.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("assessment root must be an object")
        eligible_jobs, source_revision, source_readback_at = load_eligible_jobs(
            jobs_data
        )
        errors = validate_assessment(
            data,
            eligible_jobs,
            source_revision,
            source_readback_at,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors = [str(exc)]

    result = {"ok": not errors, "errors": errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
