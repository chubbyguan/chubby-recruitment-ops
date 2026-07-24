#!/usr/bin/env python3
"""Validate a candidate assessment against recruitment safety rules."""

from __future__ import annotations

import argparse
import json
import re
import sys
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


def validate_assessment(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "candidate_id",
        "information_complete",
        "top_matches",
        "scores",
        "total_score",
        "recommendation",
        "evidence_summary",
        "red_flags",
        "missing_materials",
        "human_review_required",
    }
    missing = sorted(required - set(data))
    if missing:
        errors.append(f"missing fields: {', '.join(missing)}")

    candidate_id = data.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        errors.append("candidate_id must be a non-empty string")

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
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"score {key} must be numeric")
            elif not 0 <= value <= limit:
                errors.append(f"score {key} must be between 0 and {limit}")

        numeric_scores = [
            value
            for key, value in scores.items()
            if key in SCORE_LIMITS and isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if len(numeric_scores) == len(SCORE_LIMITS):
            expected_total = sum(numeric_scores)
            if data.get("total_score") != expected_total:
                errors.append(
                    f"total_score must equal dimension sum: expected {expected_total}, got {data.get('total_score')}"
                )

    total_score = data.get("total_score")
    if (
        not isinstance(total_score, (int, float))
        or isinstance(total_score, bool)
        or not 0 <= total_score <= 100
    ):
        errors.append("total_score must be numeric and between 0 and 100")

    recommendation = data.get("recommendation")
    if recommendation not in ALLOWED_RECOMMENDATIONS:
        errors.append(f"unsupported recommendation: {recommendation}")
    if data.get("human_review_required") is not True:
        errors.append("human_review_required must be true")

    matches = data.get("top_matches")
    if not isinstance(matches, list) or not 1 <= len(matches) <= 3:
        errors.append("top_matches must contain one to three entries")
    else:
        seen_job_ids: set[str] = set()
        for index, match in enumerate(matches):
            if not isinstance(match, dict):
                errors.append(f"top_matches[{index}] must be an object")
                continue
            job_id = match.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                errors.append(f"top_matches[{index}].job_id is required")
            elif job_id in seen_job_ids:
                errors.append(f"duplicate matched job_id: {job_id}")
            else:
                seen_job_ids.add(job_id)
            confidence = match.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                errors.append(f"top_matches[{index}].confidence must be numeric")
            elif not 0 <= confidence <= 1:
                errors.append(f"top_matches[{index}].confidence must be between 0 and 1")
            if match.get("mode") not in ALLOWED_MATCH_MODES:
                errors.append(f"top_matches[{index}].mode must be formal or talent_mapping")
            for evidence_key in (
                "matched_requirements",
                "missing_requirements",
                "evidence",
            ):
                if not isinstance(match.get(evidence_key), list):
                    errors.append(f"top_matches[{index}].{evidence_key} must be a list")

    for list_field in ("evidence_summary", "red_flags", "missing_materials"):
        if not isinstance(data.get(list_field), list):
            errors.append(f"{list_field} must be a list")
        elif any(not isinstance(item, str) or not item.strip() for item in data[list_field]):
            errors.append(f"{list_field} entries must be non-empty strings")

    red_flags = data.get("red_flags") if isinstance(data.get("red_flags"), list) else []
    missing_materials = (
        data.get("missing_materials")
        if isinstance(data.get("missing_materials"), list)
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
        if not isinstance(total_score, (int, float)) or total_score < 75:
            errors.append("hr_screen requires total_score >= 75")
        if red_flags:
            errors.append("hr_screen is not allowed when red_flags are present")
        if top_mode != "formal":
            errors.append("hr_screen requires a formal Top 1 match")
    elif recommendation == "request_more_info":
        if information_complete is True and not missing_materials:
            errors.append("request_more_info requires missing information or materials")
    elif recommendation == "talent_pool":
        if (
            isinstance(total_score, (int, float))
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

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assessment", type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.assessment.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("assessment root must be an object")
        errors = validate_assessment(data)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors = [str(exc)]

    result = {"ok": not errors, "errors": errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
