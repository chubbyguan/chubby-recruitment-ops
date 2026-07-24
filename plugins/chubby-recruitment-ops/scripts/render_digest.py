#!/usr/bin/env python3
"""Render a redacted recruitment daily digest from structured JSON."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9](?:[- ]?\d){9}(?!\d)")
INTERNATIONAL_PHONE_PATTERN = re.compile(
    r"(?<![\w+])\+\d{1,3}(?:[- ().]?\d){7,14}(?!\d)"
)
LABELED_PHONE_PATTERN = re.compile(
    r"(?i)(?:(?:电话|手机|phone|mobile|tel)\s*[:：]?\s*)"
    r"(?:\+?\d{1,3}[- ]?)?(?:\d[- ().]?){7,14}\d"
)
IDENTITY_PATTERN = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
WECHAT_PATTERN = re.compile(
    r"(?i)(?:(?:微信|wechat|wx)\s*[:：]?\s*)[A-Za-z][-_A-Za-z0-9]{5,19}"
)
PASSPORT_PATTERN = re.compile(r"(?<![A-Za-z0-9])[EeGgDdSsPp]\d{7,8}(?![A-Za-z0-9])")
LABELED_NAME_PATTERN = re.compile(
    r"(?:候选人姓名|姓名)\s*[:：]\s*[\u4e00-\u9fff·]{2,12}"
)
SAFE_ALIAS_PATTERN = re.compile(
    r"^(?:候选人[A-Z0-9]{1,8}|CAND(?:IDATE)?-[A-Z0-9]{1,16})$",
    re.IGNORECASE,
)


def redact(value: Any, known_names: list[str] | None = None) -> str:
    text = str(value if value is not None else "")
    for name in known_names or []:
        normalized_name = name.strip()
        if normalized_name:
            text = text.replace(normalized_name, "[姓名已隐藏]")
    text = LABELED_NAME_PATTERN.sub("候选人代号：[姓名已隐藏]", text)
    text = EMAIL_PATTERN.sub("[邮箱已隐藏]", text)
    text = LABELED_PHONE_PATTERN.sub("[电话已隐藏]", text)
    text = INTERNATIONAL_PHONE_PATTERN.sub("[电话已隐藏]", text)
    text = PHONE_PATTERN.sub("[电话已隐藏]", text)
    text = IDENTITY_PATTERN.sub("[证件号已隐藏]", text)
    text = WECHAT_PATTERN.sub("[微信号已隐藏]", text)
    text = PASSPORT_PATTERN.sub("[护照号已隐藏]", text)
    residual_patterns = (
        EMAIL_PATTERN,
        LABELED_PHONE_PATTERN,
        INTERNATIONAL_PHONE_PATTERN,
        PHONE_PATTERN,
        IDENTITY_PATTERN,
        WECHAT_PATTERN,
        PASSPORT_PATTERN,
        LABELED_NAME_PATTERN,
    )
    if any(pattern.search(text) for pattern in residual_patterns):
        raise ValueError("digest contains residual direct identifier")
    return text


def metric(value: Any) -> str:
    return "不可得" if value is None else redact(value)


def safe_candidate_alias(value: Any) -> str:
    alias = str(value if value is not None else "").strip()
    if not SAFE_ALIAS_PATTERN.fullmatch(alias):
        return "[候选人代号缺失]"
    return alias


def reviewed_candidate_text(candidate: dict[str, Any], field: str, fallback: str) -> str:
    names = candidate.get("redact_names")
    if (
        candidate.get("pii_reviewed") is not True
        or not isinstance(names, list)
        or not names
        or any(not isinstance(name, str) or not name.strip() for name in names)
    ):
        return "[详情已隐藏：未完成 PII 校验]"
    return redact(candidate.get(field, fallback), names)


def reviewed_anomaly(value: Any) -> str:
    if not isinstance(value, dict) or value.get("pii_reviewed") is not True:
        return "[异常详情已隐藏：未完成 PII 校验]"
    names = value.get("redact_names", [])
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        return "[异常详情已隐藏：PII 配置无效]"
    return redact(value.get("summary", "异常摘要缺失"), names)


def object_section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}


def render(data: dict[str, Any]) -> str:
    content = object_section(data, "content")
    inbox = object_section(data, "inbox")
    quality = object_section(data, "quality")
    candidates = data.get("candidates", [])
    if not isinstance(candidates, list):
        candidates = []
    interviews = object_section(data, "interviews")
    anomalies = data.get("anomalies", [])
    if not isinstance(anomalies, list):
        anomalies = []

    lines = [
        f"# 招聘日报｜{redact(data.get('date', '日期未提供'))}",
        "",
        "## 1. 小红书",
        "",
        f"- 今日发布：{metric(content.get('published_today'))}",
        f"- 累计在投：{metric(content.get('active_posts'))}",
        f"- 阅读量：{metric(content.get('reads'))}",
        f"- 点赞 / 收藏 / 评论：{metric(content.get('likes'))} / {metric(content.get('saves'))} / {metric(content.get('comments'))}",
        f"- 表现最好：{metric(content.get('best_post'))}",
        "",
        "## 2. 邮箱与简历",
        "",
        f"- 新邮件：{metric(inbox.get('new_messages'))}",
        f"- 新候选人：{metric(inbox.get('new_candidates'))}",
        f"- 重复：{metric(inbox.get('duplicates'))}",
        f"- 解析失败或待人工：{metric(inbox.get('failures'))}",
        "",
        "## 3. 候选质量",
        "",
        f"- 建议 HR 初筛：{metric(quality.get('hr_screen'))}",
        f"- 建议补资料：{metric(quality.get('request_more_info'))}",
        f"- 人工复核：{metric(quality.get('manual_review'))}",
        f"- 人才池：{metric(quality.get('talent_pool'))}",
        f"- 按岗位：{metric(quality.get('by_job'))}",
        "",
        "## 4. 今天最值得看",
        "",
    ]
    if candidates:
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            lines.append(
                "- "
                + "｜".join(
                    [
                        safe_candidate_alias(candidate.get("alias")),
                        redact(candidate.get("job_id", "岗位未知")),
                        f"{metric(candidate.get('score'))} 分",
                        reviewed_candidate_text(candidate, "evidence", "证据待补"),
                        reviewed_candidate_text(candidate, "risk", "风险待复核"),
                    ]
                )
            )
    else:
        lines.append("- 今日无新增重点候选人")

    lines.extend(
        [
            "",
            "## 5. 面试建议",
            "",
            f"- 建议：{metric(interviews.get('suggestion'))}",
            f"- 最早人工跟进时间：{metric(interviews.get('earliest_follow_up'))}",
            f"- 建议参加人：{metric(interviews.get('participants'))}",
            "",
            "## 6. 异常与待决策",
            "",
        ]
    )
    if anomalies:
        lines.extend(f"- {reviewed_anomaly(item)}" for item in anomalies)
    else:
        lines.append("- 无新增异常")
    lines.extend(
        [
            "",
            "> 仅展示通过 PII 校验的结构化摘要，候选人只使用代号。AI 建议不构成淘汰、约面或录用决定。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("input root must be an object")
        output = render(data)
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
