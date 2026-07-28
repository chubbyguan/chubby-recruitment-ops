#!/usr/bin/env python3
"""Render a redacted recruitment daily digest from structured JSON."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date
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
CHANNEL_LABELS = {
    "xiaohongshu": "小红书",
    "wechat_moments": "朋友圈",
    "wechat_groups": "微信群",
    "wechat_official": "微信公众号",
    "wechat_channels": "视频号",
    "douyin": "抖音",
    "bilibili": "B 站",
    "zhihu": "知乎",
    "maimai": "脉脉",
    "linkedin": "LinkedIn",
}
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
EVIDENCE_LABELS = {
    "outcome_evidence_verified": "第一阶段结果证据已核验",
    "hard_skill_evidence_verified": "硬技能证据已核验",
    "work_sample_verified": "作品样本已核验",
    "ownership_evidence": "有个人负责范围证据",
    "ai_fact_check_evidence": "有 AI 协作与纠错证据",
    "information_incomplete": "关键信息仍不完整",
}
RISK_LABELS = {
    "missing_work_sample": "缺少作品样本",
    "unverified_claim": "关键表述待核验",
    "conflicting_facts": "材料存在冲突",
    "missing_quantified_result": "缺少量化结果",
    "job_gap": "与当前岗位结果存在差距",
}
INTERVIEW_LABELS = {
    "hr_screen": "建议 HR 人工初筛",
    "request_more_info": "建议先人工补材料",
    "manual_review": "建议业务与 HR 联合复核",
    "no_action": "今日无新增面试动作",
}
FOLLOW_UP_LABELS = {
    "same_day": "当日",
    "two_workdays": "两个工作日内",
    "this_week": "本周内",
    "not_applicable": "不适用",
}
PARTICIPANT_LABELS = {
    "hr": "HR",
    "business_reviewer": "业务复核人",
    "hiring_owner": "招聘 Owner",
}
ANOMALY_LABELS = {
    "attribution_missing": "存在无法确认来源的投递",
    "conditional_job_conflict": "存在条件岗或人才寻访口径冲突",
    "duplicate_review": "存在待人工确认的重复记录",
    "owner_missing": "存在缺少 Owner 的记录",
    "overdue": "存在超期待处理记录",
    "connector_or_data_missing": "存在连接器、权限或数据缺失",
    "interview_artifact_missing": "存在已完成面试但无可用纪要",
    "interview_transcript_insufficient": "存在面试纪要时长不足",
    "interview_attendance_unverified": "存在计划已过但出席事实未核验",
    "interview_meeting_fact_incomplete": "存在已完成面试但实际会议时间不完整",
    "interview_decision_overdue": "存在面试决定超时",
    "none": "今日无新增异常",
}
ARTIFACT_LABELS = {
    "intelligent_transcript": "智能纪要可用",
    "minutes_transcript": "妙记逐字稿可用",
    "insufficient_transcript": "纪要时长不足",
    "no_artifact": "无可用纪要",
    "permission_denied": "纪要无权限",
    "processing": "纪要处理中",
}
NEXT_ACTION_LABELS = {
    "generate_interview_evaluation": "生成面试评价",
    "request_manual_interview_note": "补人工面试记录",
    "human_decision": "负责人做决定",
    "human_interview_decision": "负责人做决定",
    "review_candidate_merge": "人工核验重复候选人",
    "none": "无需新增动作",
}
MAX_METRIC_VALUE = 10**15


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


def numeric_value(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value < 0 or value > MAX_METRIC_VALUE:
        return None
    return value


def numeric_metric(value: Any) -> str:
    normalized = numeric_value(value)
    return "不可得" if normalized is None else str(normalized)


def safe_id(value: Any, fallback: str = "不可得") -> str:
    identifier = str(value if value is not None else "").strip()
    return identifier if SAFE_ID_PATTERN.fullmatch(identifier) else fallback


def safe_date(value: Any) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return "日期不可得"


def safe_candidate_alias(value: Any) -> str:
    alias = str(value if value is not None else "").strip()
    if not SAFE_ALIAS_PATTERN.fullmatch(alias):
        return "[候选人代号缺失]"
    return alias


def candidate_codes(candidate: dict[str, Any], field: str) -> str:
    mapping = EVIDENCE_LABELS if field == "evidence_codes" else RISK_LABELS
    values = candidate.get(field)
    if not isinstance(values, list) or not values:
        return "不可得"
    labels = [
        mapping[value]
        for value in values[:3]
        if isinstance(value, str) and value in mapping
    ]
    return "；".join(labels) if labels else "不可得"


def reviewed_anomaly(value: Any) -> str:
    if not isinstance(value, dict):
        return "异常类型不可得"
    code = value.get("code")
    label = ANOMALY_LABELS.get(code, "异常类型不可得")
    count = numeric_value(value.get("count"))
    return f"{label}（{count:g}）" if count is not None else label


def interview_backlog_line(value: Any) -> str:
    if not isinstance(value, dict):
        return "- 面试记录不可得"
    return "- " + "｜".join(
        [
            safe_candidate_alias(value.get("alias")),
            safe_id(value.get("interview_id"), "面试ID未知"),
            safe_id(value.get("interviewed_job_id"), "面试岗位未知"),
            safe_id(value.get("current_matched_job_id"), "当前岗位未知"),
            ARTIFACT_LABELS.get(value.get("artifact_status"), "纪要状态不可得"),
            NEXT_ACTION_LABELS.get(value.get("next_action_code"), "下一步不可得"),
            "已超时" if value.get("overdue") is True else "未超时",
        ]
    )


def object_section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}


def rate(numerator: Any, denominator: Any) -> str:
    valid_numerator = numeric_value(numerator)
    valid_denominator = numeric_value(denominator)
    if valid_numerator is None or valid_denominator is None or valid_denominator <= 0:
        return "不可得"
    return f"{valid_numerator / valid_denominator:.1%}"


def format_by_job(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "不可得"
    items = []
    for job_id, count in sorted(value.items()):
        safe_job_id = safe_id(job_id, "")
        safe_count = numeric_value(count)
        if safe_job_id and safe_count is not None:
            items.append(f"{safe_job_id}: {safe_count:g}")
    return "；".join(items) if items else "不可得"


def channel_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    value = data.get("channels")
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    legacy = object_section(data, "content")
    return [{"channel": "xiaohongshu", **legacy}] if legacy else []


def render(data: dict[str, Any]) -> str:
    channels = channel_rows(data)
    inbox = object_section(data, "inbox")
    quality = object_section(data, "quality")
    candidates = data.get("candidates", [])
    if not isinstance(candidates, list):
        candidates = []
    interviews = object_section(data, "interviews")
    interview_pipeline = object_section(data, "interview_pipeline")
    anomalies = data.get("anomalies", [])
    if not isinstance(anomalies, list):
        anomalies = []

    lines = [
        f"# 招聘日报｜{safe_date(data.get('date'))}",
        "",
        "## 1. 社交渠道",
        "",
    ]
    if channels:
        for channel in channels:
            channel_name = CHANNEL_LABELS.get(
                str(channel.get("channel", "")),
                "渠道未知",
            )
            views = channel.get("views", channel.get("reads"))
            likes = channel.get("likes")
            saves = channel.get("saves")
            comments = channel.get("comments")
            engagement_total = None
            normalized_engagement = [numeric_value(value) for value in (likes, saves, comments)]
            if all(value is not None for value in normalized_engagement):
                engagement_total = sum(value for value in normalized_engagement if value is not None)
            applications = channel.get("attributed_applications")
            qualified = channel.get("qualified_candidates")
            lines.extend(
                [
                    f"### {channel_name}",
                    "",
                    f"- 今日发布 / 在投：{numeric_metric(channel.get('published_today'))} / {numeric_metric(channel.get('active_posts'))}",
                    f"- 阅读或播放：{numeric_metric(views)}",
                    f"- 点赞 / 收藏 / 评论：{numeric_metric(likes)} / {numeric_metric(saves)} / {numeric_metric(comments)}",
                    f"- 互动率：{rate(engagement_total, views)}",
                    f"- 点击 / 点击率：{numeric_metric(channel.get('clicks'))} / {rate(channel.get('clicks'), views)}",
                    f"- 归因投递 / 投递转化率：{numeric_metric(applications)} / {rate(applications, views)}",
                    f"- 合格候选 / 合格率：{numeric_metric(qualified)} / {rate(qualified, applications)}",
                    f"- 表现最好：{safe_id(channel.get('best_publication_id'))}",
                    "",
                ]
            )
    else:
        lines.extend(["- 今日没有可用的渠道快照。", ""])

    publish_queue = data.get("publish_queue", [])
    if isinstance(publish_queue, list) and publish_queue:
        lines.extend(["### 待人工发布", ""])
        for item in publish_queue:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- {CHANNEL_LABELS.get(item.get('channel'), '渠道未知')}｜"
                f"{safe_id(item.get('job_id'))}｜{safe_id(item.get('attribution_code'))}｜"
                f"{safe_id(item.get('status'))}"
            )
        lines.append("")

    lines.extend(
        [
            "## 2. 邮箱与简历",
            "",
            f"- 新邮件：{numeric_metric(inbox.get('new_messages'))}",
            f"- 新候选人：{numeric_metric(inbox.get('new_candidates'))}",
            f"- 重复：{numeric_metric(inbox.get('duplicates'))}",
            f"- 解析失败或待人工：{numeric_metric(inbox.get('failures'))}",
            "",
            "## 3. 候选质量",
            "",
            f"- 建议 HR 初筛：{numeric_metric(quality.get('hr_screen'))}",
            f"- 建议补资料：{numeric_metric(quality.get('request_more_info'))}",
            f"- 人工复核：{numeric_metric(quality.get('manual_review'))}",
            f"- 人才池：{numeric_metric(quality.get('talent_pool'))}",
            f"- 按岗位：{format_by_job(quality.get('by_job'))}",
            "",
            "## 4. 今天最值得看",
            "",
        ]
    )
    if candidates:
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            lines.append(
                "- "
                + "｜".join(
                    [
                        safe_candidate_alias(candidate.get("alias")),
                        safe_id(candidate.get("job_id"), "岗位未知"),
                        f"{numeric_metric(candidate.get('score'))} 分",
                        candidate_codes(candidate, "evidence_codes"),
                        candidate_codes(candidate, "risk_codes"),
                    ]
                )
            )
    else:
        lines.append("- 今日无新增重点候选人")

    lines.extend(
        [
            "",
            "## 5. 面试与决策",
            "",
            f"- 今日计划面试：{numeric_metric(interview_pipeline.get('scheduled_today'))}",
            f"- 今日已完成：{numeric_metric(interview_pipeline.get('completed_today'))}",
            f"- 待生成评价：{numeric_metric(interview_pipeline.get('awaiting_evaluation'))}",
            f"- 无可用纪要：{numeric_metric(interview_pipeline.get('artifact_missing'))}",
            f"- 待人工决定：{numeric_metric(interview_pipeline.get('awaiting_decision'))}",
            f"- 决定已超时：{numeric_metric(interview_pipeline.get('decision_overdue'))}",
            f"- 建议：{INTERVIEW_LABELS.get(interviews.get('suggestion_code'), '不可得')}",
            f"- 最早人工跟进时间：{FOLLOW_UP_LABELS.get(interviews.get('follow_up_code'), '不可得')}",
            f"- 建议参加人：{'、'.join(PARTICIPANT_LABELS[code] for code in interviews.get('participant_codes', []) if code in PARTICIPANT_LABELS) or '不可得'}",
            "",
            "### 面试积压",
            "",
        ]
    )
    backlog = interview_pipeline.get("backlog")
    if isinstance(backlog, list) and backlog:
        lines.extend(interview_backlog_line(item) for item in backlog[:10])
    else:
        lines.append("- 无可用面试积压记录")
    lines.extend(
        [
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
