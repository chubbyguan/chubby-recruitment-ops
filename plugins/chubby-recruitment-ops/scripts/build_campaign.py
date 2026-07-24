#!/usr/bin/env python3
"""Build a traceable, manual-publish social recruitment campaign from one job."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


CHANNEL_PREFIXES = {
    "xiaohongshu": "XHS",
    "wechat_moments": "WXM",
    "wechat_groups": "WXG",
    "wechat_official": "WXO",
    "wechat_channels": "WXV",
    "douyin": "DY",
    "bilibili": "BILI",
    "zhihu": "ZH",
    "maimai": "MM",
    "linkedin": "LI",
}
ANGLES = ("project_opportunity", "first_stage_outcomes", "case_challenge")
SALARY_PLACEHOLDERS = {"待定", "面议", "按公司标准", "tbd", "to be determined"}


def first(job: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = job.get(key)
        if value is not None and value != "":
            return value
    return None


def safe_code(value: Any, fallback: str = "JOB") -> str:
    code = re.sub(r"[^A-Za-z0-9]+", "", str(value or "")).upper()
    return code[:16] or fallback


def plus_alias(email: str, attribution_code: str) -> str:
    local, separator, domain = email.partition("@")
    if not separator:
        raise ValueError("application email is invalid")
    base_local = local.split("+", 1)[0]
    return f"{base_local}+{attribution_code.lower()}@{domain}"


def classify_gate(status: str, policy: dict[str, Any]) -> str:
    normalized = status.strip().casefold()
    groups = (
        ("open", policy.get("open_statuses", [])),
        ("conditional", policy.get("conditional_statuses", [])),
        ("blocked", policy.get("blocked_statuses", [])),
    )
    for gate, values in groups:
        if any(normalized == str(value).strip().casefold() for value in values):
            return gate
    return "unknown"


def require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing or non-text required job field: {label}")
    return value.strip()


def require_positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def canonical_job_hash(job: dict[str, Any]) -> str:
    stable_job = {
        key: value
        for key, value in job.items()
        if key not in {"source_readback_at", "源回读时间"}
    }
    canonical = json.dumps(
        stable_job,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def content_hash(variant: dict[str, Any]) -> str:
    approved_content = {
        "content_version_id": variant.get("content_version_id"),
        "title": variant.get("title"),
        "body": variant.get("body"),
        "asset_script": variant.get("asset_script"),
    }
    canonical = json.dumps(
        approved_content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_campaign(
    job: dict[str, Any],
    config: dict[str, Any],
    requested_channels: list[str] | None = None,
    campaign_key: str | None = None,
) -> dict[str, Any]:
    company = config.get("company")
    if not isinstance(company, dict):
        raise ValueError("company config must be an object")
    if company.get("disclose_public_name") is not True:
        raise ValueError("company public disclosure must be explicitly approved")
    company_name = require_text(company.get("public_name"), "company.public_name")
    job_company_name = require_text(
        first(job, "public_company_name", "公开公司名称"),
        "public_company_name/公开公司名称",
    )
    if job_company_name != company_name:
        raise ValueError("job public company name differs from approved config")
    legal_entity = require_text(
        first(job, "legal_entity", "招聘法律主体"),
        "legal_entity/招聘法律主体",
    )

    job_id = require_text(first(job, "job_id", "岗位ID"), "job_id/岗位ID")
    title = require_text(first(job, "title", "岗位名称"), "title/岗位名称")
    status = require_text(first(job, "status", "岗位状态"), "status/岗位状态")
    job_version = require_text(
        first(job, "job_version", "jd_version", "JD版本"), "JD版本"
    )
    job_policy = config.get("job_policy")
    if not isinstance(job_policy, dict):
        raise ValueError("job_policy config must be an object")
    gate = classify_gate(status, job_policy)
    if gate == "blocked":
        raise ValueError("job is frozen or closed; campaign creation is blocked")
    if gate == "unknown":
        raise ValueError("job status is unknown; human review is required")

    source_record_id = require_text(
        first(job, "source_record_id", "源记录ID"), "source_record_id/源记录ID"
    )
    source_revision = require_text(
        first(job, "source_revision", "源版本"), "source_revision/源版本"
    )
    source_modified_at = require_text(
        first(job, "source_modified_at", "源修改时间"),
        "source_modified_at/源修改时间",
    )
    source_readback_at = require_text(
        first(job, "source_readback_at", "源回读时间"),
        "source_readback_at/源回读时间",
    )
    resolved_campaign_key = require_text(
        campaign_key or first(job, "campaign_key", "招聘批次ID"),
        "campaign_key/招聘批次ID",
    )

    compensation = config.get("compensation")
    if not isinstance(compensation, dict):
        raise ValueError("compensation config must be an object")
    range_field = require_text(
        compensation.get("public_range_field"), "compensation.public_range_field"
    )
    approval_field = require_text(
        compensation.get("approval_field"), "compensation.approval_field"
    )
    reference_field = require_text(
        compensation.get("approval_reference_field"),
        "compensation.approval_reference_field",
    )
    salary_range = require_text(
        first(job, "public_salary_range", range_field),
        f"public_salary_range/{range_field}",
    )
    if salary_range.casefold() in SALARY_PLACEHOLDERS:
        raise ValueError("public salary range must be an approved concrete value")
    salary_approved = first(job, "salary_disclosure_approved", approval_field)
    salary_reference = require_text(
        first(job, "salary_approval_reference", reference_field),
        f"salary_approval_reference/{reference_field}",
    )
    salary_approval_actor = require_text(
        first(job, "salary_approval_actor", "薪资审批人"),
        "salary_approval_actor/薪资审批人",
    )
    salary_approval_at = require_text(
        first(job, "salary_approval_at", "薪资审批时间"),
        "salary_approval_at/薪资审批时间",
    )
    if compensation.get("require_approved_range") is not True or salary_approved is not True:
        raise ValueError("salary range lacks explicit publication approval")
    if compensation.get("source") != "job_source":
        raise ValueError("salary must come from the approved job source")
    if compensation.get("fallback") != "block_publication":
        raise ValueError("missing salary approval must block publication")

    content_basis = {
        "why_now": require_text(
            first(job, "why_now", "为什么现在招"), "why_now/为什么现在招"
        ),
        "outcome_30d": require_text(
            first(job, "outcome_30d", "30天结果"), "outcome_30d/30天结果"
        ),
        "outcome_60d": require_text(
            first(job, "outcome_60d", "60天结果"), "outcome_60d/60天结果"
        ),
        "outcome_90d": require_text(
            first(job, "outcome_90d", "90天结果"), "outcome_90d/90天结果"
        ),
        "hard_requirements": require_text(
            first(job, "hard_requirements", "硬要求"),
            "hard_requirements/硬要求",
        ),
        "evidence_requirements": require_text(
            first(job, "evidence_requirements", "作品要求"),
            "evidence_requirements/作品要求",
        ),
        "red_lines": require_text(
            first(job, "red_lines", "一票否决"), "red_lines/一票否决"
        ),
    }
    campaign_owner = require_text(
        first(job, "campaign_owner", "CampaignOwner"), "campaign_owner/CampaignOwner"
    )
    target_qualified_candidates = require_positive_int(
        first(job, "target_qualified_candidates", "目标合格候选数"),
        "target_qualified_candidates/目标合格候选数",
    )
    campaign_start_at = require_text(
        first(job, "campaign_start_at", "Campaign开始时间"),
        "campaign_start_at/Campaign开始时间",
    )
    campaign_end_at = require_text(
        first(job, "campaign_end_at", "Campaign结束时间"),
        "campaign_end_at/Campaign结束时间",
    )

    channel_config = config.get("channels")
    if not isinstance(channel_config, dict):
        raise ValueError("channels config must be an object")
    supported = channel_config.get("supported", [])
    channels = requested_channels or channel_config.get("enabled", [])
    if not isinstance(channels, list) or not channels:
        raise ValueError("at least one social channel is required")
    if any(not isinstance(channel, str) or not channel.strip() for channel in channels):
        raise ValueError("channels must contain non-empty strings")
    if not isinstance(supported, list) or any(
        not isinstance(channel, str) or not channel.strip() for channel in supported
    ):
        raise ValueError("supported channels must contain non-empty strings")
    unsupported = sorted(set(channels) - set(supported))
    if unsupported:
        raise ValueError(f"unsupported channels: {', '.join(unsupported)}")
    if channel_config.get("publish_mode") != "manual":
        raise ValueError("social publishing must remain manual")
    if len(channels) != len(set(channels)):
        raise ValueError("channels must not contain duplicates")

    connectors = config.get("connectors")
    email_config = connectors.get("email") if isinstance(connectors, dict) else None
    if not isinstance(email_config, dict):
        raise ValueError("email connector config must be an object")
    public_content = config.get("public_content")
    if not isinstance(public_content, dict):
        raise ValueError("public_content config must be an object")
    email = require_text(
        public_content.get("application_email"), "public_content.application_email"
    )
    attribution = config.get("attribution")
    if not isinstance(attribution, dict):
        raise ValueError("attribution config must be an object")
    use_alias = attribution.get("gmail_plus_alias") is True
    if use_alias and email_config.get("provider") != "gmail":
        raise ValueError("Gmail Plus Alias requires the gmail provider")

    campaign_seed = "|".join(
        [
            resolved_campaign_key,
            job_id,
            job_version,
            source_record_id,
            source_revision,
            company_name,
            salary_range,
            salary_reference,
            ",".join(sorted(channels)),
        ]
    )
    campaign_id = (
        f"CMP-{hashlib.sha256(campaign_seed.encode()).hexdigest()[:12].upper()}"
    )
    campaign_code = campaign_id.removeprefix("CMP-")[:6]
    job_code = safe_code(job_id)
    variants: list[dict[str, Any]] = []
    publications: list[dict[str, Any]] = []
    for channel in channels:
        prefix = CHANNEL_PREFIXES.get(channel)
        if not prefix:
            raise ValueError(f"channel prefix is not configured: {channel}")
        for index, angle in enumerate(ANGLES, start=1):
            content_version_id = f"CNT-{campaign_code}-{prefix}-A{index:02d}"
            attribution_code = f"{prefix}-{job_code}-{campaign_code}-A{index:02d}"
            variants.append(
                {
                    "content_version_id": content_version_id,
                    "campaign_id": campaign_id,
                    "channel": channel,
                    "angle": angle,
                    "title": "",
                    "body": "",
                    "asset_script": [],
                    "content_approval": {
                        "status": "pending",
                        "approval_record_id": None,
                        "reviewer": None,
                        "reviewed_at": None,
                        "approval_source_revision": None,
                        "approval_readback_at": None,
                        "job_version": job_version,
                        "salary_approval_reference": salary_reference,
                        "content_sha256": None,
                        "no_employment_promise_confirmed": False,
                    },
                }
            )
            publications.append(
                {
                    "publication_id": f"PUB-{campaign_code}-{prefix}-A{index:02d}",
                    "campaign_id": campaign_id,
                    "content_version_id": content_version_id,
                    "channel": channel,
                    "angle": angle,
                    "attribution_code": attribution_code,
                    "application_email": (
                        plus_alias(email, attribution_code) if use_alias else email
                    ),
                    "subject_format": f"应聘｜{job_id}｜{attribution_code}｜姓名",
                    "publish_mode": "manual",
                    "status": "draft",
                    "planned_publish_at": None,
                    "published_url": None,
                    "published_at": None,
                    "publisher": None,
                }
            )

    return {
        "schema_version": "2.0",
        "campaign_id": campaign_id,
        "campaign_key": resolved_campaign_key,
        "campaign_mode": "formal" if gate == "open" else "talent_mapping",
        "status": "draft",
        "owner": campaign_owner,
        "target_qualified_candidates": target_qualified_candidates,
        "start_at": campaign_start_at,
        "end_at": campaign_end_at,
        "job_snapshot": {
            "job_id": job_id,
            "title": title,
            "department": first(job, "department", "部门"),
            "job_status": status,
            "gate": gate,
            "job_version": job_version,
            "job_source_sha256": canonical_job_hash(job),
            "source_record_id": source_record_id,
            "source_revision": source_revision,
            "source_modified_at": source_modified_at,
            "source_readback_at": source_readback_at,
            "legal_entity": legal_entity,
            "location": first(job, "location", "工作地点"),
            "work_mode": first(job, "work_mode", "办公方式"),
        },
        "public_facts": {
            "company_name": company_name,
            "salary_range": salary_range,
            "salary_approval_reference": salary_reference,
            "salary_approval_actor": salary_approval_actor,
            "salary_approval_at": salary_approval_at,
            "salary_source": "job_source",
            "privacy_notice": public_content.get("privacy_notice"),
        },
        "content_basis": content_basis,
        "content_variants": variants,
        "publication_queue": publications,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--campaign-key")
    parser.add_argument("--channel", action="append", dest="channels")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        job = json.loads(args.job.read_text(encoding="utf-8"))
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(job, dict) or not isinstance(config, dict):
            raise ValueError("job and config roots must be objects")
        campaign = build_campaign(job, config, args.channels, args.campaign_key)
        rendered = json.dumps(campaign, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
