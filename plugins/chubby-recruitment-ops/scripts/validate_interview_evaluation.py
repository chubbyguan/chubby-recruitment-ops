#!/usr/bin/env python3
"""Validate an evidence-linked interview evaluation without making a final decision."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
TIMESTAMP = re.compile(r"^(?:\d{2}:)?\d{2}:\d{2}$")
DIMENSIONS = {
    "outcome_ownership": 25,
    "business_judgment": 20,
    "role_capability": 20,
    "ai_execution": 15,
    "collaboration": 10,
    "credibility_risk": 10,
}
USABLE_ARTIFACTS = {"intelligent_transcript", "minutes_transcript"}
ARTIFACT_STATUSES = USABLE_ARTIFACTS | {
    "insufficient_transcript",
    "no_artifact",
    "permission_denied",
    "processing",
}
RECOMMENDATIONS = {
    "recommend_next_round",
    "request_more_info",
    "manual_review",
    "talent_pool",
    "close_review",
    "unable_to_assess",
}
STRENGTH_CODES = {
    "outcome_evidence",
    "ownership_evidence",
    "business_model_evidence",
    "role_case_evidence",
    "ai_workflow_evidence",
    "cross_function_evidence",
    "learning_evidence",
}
RISK_CODES = {
    "claim_not_verified",
    "attribution_unclear",
    "answer_too_general",
    "business_depth_gap",
    "role_skill_gap",
    "ai_safety_gap",
    "inconsistent_evidence",
}
MISSING_CODES = {
    "work_sample_missing",
    "quantified_result_missing",
    "role_case_missing",
    "ai_demo_missing",
    "reference_missing",
    "manual_interviewer_note_missing",
}
PROTECTED_TERMS = (
    "年龄",
    "出生",
    "性别",
    "男",
    "女",
    "婚",
    "生育",
    "民族",
    "国籍",
    "籍贯",
    "户口",
    "宗教",
    "残障",
    "疾病",
    "怀孕",
    "照片",
    "颜值",
    "形象",
)


def safe_id(value: Any) -> bool:
    return isinstance(value, str) and bool(SAFE_ID.fullmatch(value))


def enum_list(
    value: Any,
    allowed: set[str],
    field: str,
    errors: list[str],
) -> None:
    if not isinstance(value, list):
        errors.append(f"{field} must be an array")
        return
    if any(not isinstance(item, str) or item not in allowed for item in value):
        errors.append(f"{field} contains an unsupported code")
    if len(value) != len(set(value)):
        errors.append(f"{field} contains duplicates")


def all_strings(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            values.append(str(key))
            values.extend(all_strings(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(all_strings(child))
    elif isinstance(value, str):
        values.append(value)
    return values


def validate_evaluation(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    allowed_fields = {
        "schema_version",
        "evaluation_id",
        "interview_id",
        "candidate_id",
        "job_id",
        "job_version",
        "artifact_status",
        "source_artifact_ids",
        "evidence_refs",
        "scores",
        "total_score",
        "recommendation",
        "strength_codes",
        "risk_codes",
        "missing_material_codes",
        "human_review_status",
    }
    unknown = sorted(set(data) - allowed_fields)
    if unknown:
        errors.append(f"unknown evaluation fields: {', '.join(unknown)}")
    if data.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    for field in (
        "evaluation_id",
        "interview_id",
        "candidate_id",
        "job_id",
        "job_version",
    ):
        if not safe_id(data.get(field)):
            errors.append(f"{field} must be a safe stable ID")

    artifact_status = data.get("artifact_status")
    if artifact_status not in ARTIFACT_STATUSES:
        errors.append("artifact_status is invalid")
    artifact_ids = data.get("source_artifact_ids")
    if not isinstance(artifact_ids, list) or any(
        not safe_id(value) for value in artifact_ids
    ):
        errors.append("source_artifact_ids must contain safe stable IDs")

    evidence_refs = data.get("evidence_refs")
    evidence_dimensions: set[str] = set()
    if not isinstance(evidence_refs, list):
        errors.append("evidence_refs must be an array")
        evidence_refs = []
    for index, raw_reference in enumerate(evidence_refs):
        if not isinstance(raw_reference, dict):
            errors.append(f"evidence_refs[{index}] must be an object")
            continue
        if set(raw_reference) != {
            "evidence_id",
            "artifact_id",
            "dimension",
            "locator",
        }:
            errors.append(f"evidence_refs[{index}] has unknown or missing fields")
            continue
        if not safe_id(raw_reference.get("evidence_id")):
            errors.append(f"evidence_refs[{index}].evidence_id is invalid")
        if raw_reference.get("artifact_id") not in artifact_ids:
            errors.append(f"evidence_refs[{index}].artifact_id is not a source artifact")
        dimension = raw_reference.get("dimension")
        if dimension not in DIMENSIONS:
            errors.append(f"evidence_refs[{index}].dimension is invalid")
        else:
            evidence_dimensions.add(dimension)
        locator = raw_reference.get("locator")
        if not isinstance(locator, str) or not TIMESTAMP.fullmatch(locator):
            errors.append(f"evidence_refs[{index}].locator must be a transcript timestamp")

    recommendation = data.get("recommendation")
    if recommendation not in RECOMMENDATIONS:
        errors.append("recommendation is invalid")
    if data.get("human_review_status") not in {"pending", "reviewed"}:
        errors.append("human_review_status must be pending or reviewed")

    enum_list(data.get("strength_codes"), STRENGTH_CODES, "strength_codes", errors)
    enum_list(data.get("risk_codes"), RISK_CODES, "risk_codes", errors)
    enum_list(
        data.get("missing_material_codes"),
        MISSING_CODES,
        "missing_material_codes",
        errors,
    )

    scores = data.get("scores")
    usable = artifact_status in USABLE_ARTIFACTS
    if usable:
        if not isinstance(scores, dict) or set(scores) != set(DIMENSIONS):
            errors.append("scores must contain exactly the six interview dimensions")
        else:
            total = 0
            for dimension, maximum in DIMENSIONS.items():
                score = scores[dimension]
                if type(score) is not int or not 0 <= score <= maximum:
                    errors.append(f"scores.{dimension} must be an integer from 0 to {maximum}")
                else:
                    total += score
                if score and dimension not in evidence_dimensions:
                    errors.append(f"scores.{dimension} lacks transcript evidence")
            if data.get("total_score") != total:
                errors.append("total_score must equal the dimension sum")
        if not artifact_ids:
            errors.append("usable transcript evaluation requires source_artifact_ids")
        if recommendation == "unable_to_assess":
            errors.append("usable transcript should not use unable_to_assess")
    else:
        if scores not in ({}, None):
            errors.append("unusable transcript must not receive dimension scores")
        if data.get("total_score") is not None:
            errors.append("unusable transcript must not receive total_score")
        if recommendation not in {"unable_to_assess", "request_more_info"}:
            errors.append("unusable transcript allows only unable_to_assess or request_more_info")
        if evidence_refs:
            errors.append("unusable transcript must not cite interview evidence")

    lowered = "\n".join(all_strings(data)).lower()
    if any(term.lower() in lowered for term in PROTECTED_TERMS):
        errors.append("protected attributes must not enter interview evaluation")
    if recommendation in {"hire", "reject", "offer", "passed"}:
        errors.append("AI output cannot be a final hiring decision")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("input root must be an object")
        errors = validate_evaluation(data)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors = [str(exc)]
    print(
        json.dumps(
            {"ok": not errors, "errors": errors, "error_count": len(errors)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
