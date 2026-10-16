#!/usr/bin/env python3
"""Validate and render a human-decision-ready recruitment notification."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
IDENTITY_NUMBER = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
MAX_LIST_ITEMS = 6


def as_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def stable_id(value: Any, field: str) -> str:
    text = str(value if value is not None else "").strip()
    if not SAFE_ID.fullmatch(text):
        raise ValueError(f"{field} must be a stable identifier")
    return text


def visible_text(value: Any, field: str, limit: int = 500) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    text = " ".join(value.split()).strip()
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    if EMAIL.search(text) or PHONE.search(text) or IDENTITY_NUMBER.search(text):
        raise ValueError(f"{field} contains disallowed contact or identity data")
    return (
        text.replace("<", "＜")
        .replace(">", "＞")
        .replace("[", "［")
        .replace("]", "］")
    )


def visible_list(value: Any, field: str, minimum: int = 1) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    if not minimum <= len(value) <= MAX_LIST_ITEMS:
        raise ValueError(
            f"{field} must contain {minimum} to {MAX_LIST_ITEMS} items"
        )
    return [
        visible_text(item, f"{field}[{index}]")
        for index, item in enumerate(value)
    ]


def score_value(value: Any, field: str, maximum: float = 100) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    score = float(value)
    if not math.isfinite(score) or not 0 <= score <= maximum:
        raise ValueError(f"{field} is out of range")
    return score


def record_url(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a URL")
    parsed = urlparse(value)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host or "/record/" not in parsed.path:
        raise ValueError(f"{field} must be an https record URL")
    return value


def render(payload: dict[str, Any]) -> str:
    if payload.get("schema_version") != "1.0":
        raise ValueError("schema_version must be 1.0")
    candidate_id = stable_id(payload.get("candidate_id"), "candidate_id")
    candidate_name = visible_text(
        payload.get("candidate_display_name"),
        "candidate_display_name",
        80,
    )
    if candidate_name == candidate_id:
        raise ValueError("candidate_display_name must identify the person")
    application_id = stable_id(
        payload.get("application_id"),
        "application_id",
    )
    job_id = stable_id(payload.get("job_id"), "job_id")
    action_id = stable_id(payload.get("action_id"), "action_id")
    job_title = visible_text(payload.get("job_title"), "job_title", 120)
    current_stage = visible_text(
        payload.get("current_stage"),
        "current_stage",
        80,
    )
    interview_status = visible_text(
        payload.get("interview_fact_status"),
        "interview_fact_status",
        160,
    )
    total_score = score_value(payload.get("score"), "score")
    dimensions_raw = payload.get("score_dimensions")
    if not isinstance(dimensions_raw, list) or not 3 <= len(dimensions_raw) <= 8:
        raise ValueError("score_dimensions must contain 3 to 8 items")
    dimensions: list[str] = []
    dimension_score_total = 0.0
    dimension_maximum_total = 0.0
    for index, raw_dimension in enumerate(dimensions_raw):
        dimension = as_object(raw_dimension, f"score_dimensions[{index}]")
        label = visible_text(
            dimension.get("label"),
            f"score_dimensions[{index}].label",
            80,
        )
        maximum = score_value(
            dimension.get("maximum"),
            f"score_dimensions[{index}].maximum",
        )
        if maximum <= 0:
            raise ValueError(
                f"score_dimensions[{index}].maximum must be positive"
            )
        score = score_value(
            dimension.get("score"),
            f"score_dimensions[{index}].score",
            maximum,
        )
        dimension_score_total += score
        dimension_maximum_total += maximum
        summary = visible_text(
            dimension.get("summary"),
            f"score_dimensions[{index}].summary",
            300,
        )
        dimensions.append(f"- {label}：{score:g}/{maximum:g}——{summary}")
    if not math.isclose(dimension_score_total, total_score):
        raise ValueError("score must equal the score_dimensions total")
    if not math.isclose(dimension_maximum_total, 100):
        raise ValueError("score_dimensions maximums must total 100")

    evidence = visible_list(payload.get("key_evidence"), "key_evidence")
    risks = visible_list(payload.get("risks"), "risks")
    missing = visible_list(
        payload.get("missing_information"),
        "missing_information",
    )
    options = visible_list(
        payload.get("decision_options"),
        "decision_options",
        minimum=2,
    )
    candidate_url = record_url(
        payload.get("candidate_record_url"),
        "candidate_record_url",
    )
    decision_url = record_url(
        payload.get("decision_record_url"),
        "decision_record_url",
    )

    lines = [
        f"## 招聘待复核｜{candidate_name}",
        "",
        f"**候选人**：{candidate_name}",
        f"**岗位**：{job_id}｜{job_title}",
        f"**初筛分**：{total_score:g}/100",
        f"**当前阶段**：{current_stage}",
        f"**面试事实**：{interview_status}",
        "",
        "### 评分拆解",
        "",
        *dimensions,
        "",
        "### 关键证据",
        "",
        *[f"- {item}" for item in evidence],
        "",
        "### 风险与待补资料",
        "",
        *[f"- 风险：{item}" for item in risks],
        *[f"- 待补：{item}" for item in missing],
        "",
        "### 请选择下一步",
        "",
        *[
            f"{index}. {item}"
            for index, item in enumerate(options, start=1)
        ],
        "",
        f"[查看候选人记录]({candidate_url})  ",
        f"[打开待审批动作]({decision_url})",
        "",
        f"候选人ID：{candidate_id}",
        f"投递ID：{application_id}",
        f"动作ID：{action_id}",
        "",
        "系统只等待人工判断，不会自动邀请、淘汰或修改候选人结论。",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = render(as_object(payload, "input"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
