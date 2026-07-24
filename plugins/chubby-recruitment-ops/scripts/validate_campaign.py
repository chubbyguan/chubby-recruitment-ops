#!/usr/bin/env python3
"""Validate campaign provenance, content approval, and manual-publication gates."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from build_campaign import build_campaign, canonical_job_hash, content_hash, first


URL_PATTERN = re.compile(
    r"https?://|(?:^|[^\w])www\.|(?:^|[^\w])//[A-Za-z0-9]|\[[^\]]+\]\([^)]+\)|扫码|二维码|跳转",
    re.IGNORECASE,
)
SALARY_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:[kKwW万])"
)
CONDITIONAL_PROMISE_PATTERN = re.compile(
    r"正式(?:岗位|招聘|空缺|成员|员工)|录用|入职|offer|签约|聘用|转正|加入团队",
    re.IGNORECASE,
)
EXTRA_COMPANY_PATTERN = re.compile(
    r"实际用工方|用工方|雇主|招聘主体|另一家公司|某公司"
)
EXTRA_SALARY_PATTERN = re.compile(
    r"薪资|薪酬|工资|月薪|年薪|年包|元/月|万元|百万|"
    r"[零〇一二两三四五六七八九十百千万亿]+(?:元|万)"
)


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def valid_timestamp(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(text(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def timestamp_not_before(later: Any, earlier: Any) -> bool:
    if not valid_timestamp(later) or not valid_timestamp(earlier):
        return False
    later_value = datetime.fromisoformat(text(later).replace("Z", "+00:00"))
    earlier_value = datetime.fromisoformat(text(earlier).replace("Z", "+00:00"))
    return later_value >= earlier_value


def valid_https_url(value: Any) -> bool:
    parsed = urlparse(text(value))
    return parsed.scheme == "https" and bool(parsed.netloc)


def validate_campaign(
    campaign: dict[str, Any],
    config: dict[str, Any],
    job: dict[str, Any] | None = None,
    require_ready_content: bool = False,
) -> list[str]:
    errors: list[str] = []
    if campaign.get("schema_version") != "2.0":
        errors.append("campaign schema_version must be 2.0")
    if campaign.get("status") not in {"draft", "active", "paused", "completed"}:
        errors.append("campaign status is invalid")

    snapshot = campaign.get("job_snapshot")
    public_facts = campaign.get("public_facts")
    content_basis = campaign.get("content_basis")
    variants = campaign.get("content_variants")
    queue = campaign.get("publication_queue")
    if not isinstance(snapshot, dict):
        errors.append("job_snapshot must be an object")
        snapshot = {}
    if not isinstance(public_facts, dict):
        errors.append("public_facts must be an object")
        public_facts = {}
    if not isinstance(content_basis, dict):
        errors.append("content_basis must be an object")
        content_basis = {}
    if not isinstance(variants, list) or not variants:
        errors.append("content_variants must be a non-empty list")
        variants = []
    if not isinstance(queue, list) or not queue:
        errors.append("publication_queue must be a non-empty list")
        queue = []

    company = config.get("company")
    expected_company = company.get("public_name") if isinstance(company, dict) else None
    if public_facts.get("company_name") != expected_company:
        errors.append("company name differs from approved config")
    if public_facts.get("salary_source") != "job_source":
        errors.append("salary source must be job_source")
    for field in (
        "salary_range",
        "salary_approval_reference",
        "salary_approval_actor",
        "salary_approval_at",
    ):
        if not text(public_facts.get(field)):
            errors.append(f"missing approved salary fact: {field}")
    if snapshot.get("gate") not in {"open", "conditional"}:
        errors.append("job gate does not allow a campaign")
    expected_mode = (
        "formal"
        if snapshot.get("gate") == "open"
        else "talent_mapping"
        if snapshot.get("gate") == "conditional"
        else None
    )
    if campaign.get("campaign_mode") != expected_mode:
        errors.append("campaign_mode differs from the job gate")

    ready_items = [
        item
        for item in queue
        if isinstance(item, dict)
        and item.get("status") in {"ready_to_publish", "published"}
    ]
    if require_ready_content and not ready_items:
        errors.append("no ready_to_publish or published item was provided")
    if (require_ready_content or ready_items) and job is None:
        errors.append("current job source is required before publication")

    reference_campaign: dict[str, Any] | None = None
    if job is not None:
        compensation = config.get("compensation")
        compensation = compensation if isinstance(compensation, dict) else {}
        range_field = text(compensation.get("public_range_field")) or "公开薪资范围"
        approval_field = text(compensation.get("approval_field")) or "薪资公开已批准"
        reference_field = (
            text(compensation.get("approval_reference_field")) or "薪资审批记录"
        )
        expected_job_id = text(first(job, "job_id", "岗位ID"))
        expected_version = text(first(job, "job_version", "jd_version", "JD版本"))
        expected_salary = text(first(job, "public_salary_range", range_field))
        expected_approval = first(job, "salary_disclosure_approved", approval_field)
        expected_reference = text(
            first(job, "salary_approval_reference", reference_field)
        )
        expected_salary_actor = text(
            first(job, "salary_approval_actor", "薪资审批人")
        )
        expected_salary_at = text(
            first(job, "salary_approval_at", "薪资审批时间")
        )
        expected_job_company = text(
            first(job, "public_company_name", "公开公司名称")
        )
        expected_legal_entity = text(first(job, "legal_entity", "招聘法律主体"))
        if snapshot.get("job_id") != expected_job_id:
            errors.append("campaign job ID differs from current job source")
        if snapshot.get("job_version") != expected_version:
            errors.append("campaign job version differs from current job source")
        if public_facts.get("company_name") != expected_job_company:
            errors.append("campaign company differs from current job source")
        if snapshot.get("legal_entity") != expected_legal_entity:
            errors.append("campaign legal entity differs from current job source")
        if snapshot.get("job_source_sha256") != canonical_job_hash(job):
            errors.append("campaign job source fingerprint differs from current job source")
        for snapshot_field, job_keys in (
            ("source_record_id", ("source_record_id", "源记录ID")),
            ("source_revision", ("source_revision", "源版本")),
            ("source_modified_at", ("source_modified_at", "源修改时间")),
        ):
            if snapshot.get(snapshot_field) != first(job, *job_keys):
                errors.append(f"campaign {snapshot_field} differs from current job source")
        if public_facts.get("salary_range") != expected_salary:
            errors.append("campaign salary differs from current approved job source")
        if expected_approval is not True:
            errors.append("current job source has no salary publication approval")
        if public_facts.get("salary_approval_reference") != expected_reference:
            errors.append(
                "campaign salary approval reference differs from current job source"
            )
        if public_facts.get("salary_approval_actor") != expected_salary_actor:
            errors.append("campaign salary approval actor differs from current job source")
        if public_facts.get("salary_approval_at") != expected_salary_at:
            errors.append("campaign salary approval time differs from current job source")
        if ready_items:
            for source_field in (
                "source_record_id",
                "source_revision",
                "source_modified_at",
                "source_readback_at",
            ):
                if not text(snapshot.get(source_field)):
                    errors.append(f"job source is missing {source_field}")
            for timestamp_field in ("source_modified_at", "source_readback_at"):
                if not valid_timestamp(snapshot.get(timestamp_field)):
                    errors.append(f"job source {timestamp_field} is invalid")
            if not timestamp_not_before(
                first(job, "source_readback_at", "源回读时间"),
                first(job, "source_modified_at", "源修改时间"),
            ):
                errors.append("job source readback predates the source modification")
            if not valid_timestamp(public_facts.get("salary_approval_at")):
                errors.append("salary approval time is invalid")

        channels: list[str] = []
        for item in queue:
            if not isinstance(item, dict):
                continue
            channel = item.get("channel")
            if isinstance(channel, str) and channel not in channels:
                channels.append(channel)
        try:
            reference_campaign = build_campaign(
                job,
                config,
                channels,
                text(campaign.get("campaign_key")),
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"current job source cannot rebuild campaign: {exc}")

    supported_config = config.get("channels")
    supported_value = (
        supported_config.get("supported", [])
        if isinstance(supported_config, dict)
        else []
    )
    supported = {
        channel
        for channel in supported_value
        if isinstance(channel, str) and channel.strip()
    }
    privacy = config.get("privacy")
    authorized_reviewers = (
        {
            reviewer
            for reviewer in privacy.get("authorized_reviewers", [])
            if isinstance(reviewer, str) and reviewer.strip()
        }
        if isinstance(privacy, dict)
        else set()
    )

    reference_variants: dict[tuple[str, str], dict[str, Any]] = {}
    reference_publications: dict[tuple[str, str], dict[str, Any]] = {}
    if reference_campaign is not None:
        if campaign.get("campaign_id") != reference_campaign.get("campaign_id"):
            errors.append("campaign ID differs from current job facts and channel plan")
        for field in (
            "campaign_key",
            "campaign_mode",
            "owner",
            "target_qualified_candidates",
            "start_at",
            "end_at",
            "content_basis",
        ):
            if campaign.get(field) != reference_campaign.get(field):
                errors.append(f"campaign {field} differs from current job source")
        reference_variants = {
            (item["channel"], item["angle"]): item
            for item in reference_campaign["content_variants"]
        }
        reference_publications = {
            (item["channel"], item["angle"]): item
            for item in reference_campaign["publication_queue"]
        }

    variants_by_id: dict[str, dict[str, Any]] = {}
    seen_variant_keys: set[tuple[str, str]] = set()
    for index, variant in enumerate(variants):
        label = f"content_variants[{index}]"
        if not isinstance(variant, dict):
            errors.append(f"{label} must be an object")
            continue
        variant_id = text(variant.get("content_version_id"))
        key = (text(variant.get("channel")), text(variant.get("angle")))
        if not variant_id or variant_id in variants_by_id:
            errors.append(f"{label} content_version_id is missing or duplicated")
        else:
            variants_by_id[variant_id] = variant
        if key in seen_variant_keys:
            errors.append(f"{label} duplicates a channel and angle")
        seen_variant_keys.add(key)
        if variant.get("campaign_id") != campaign.get("campaign_id"):
            errors.append(f"{label} campaign_id mismatch")
        if variant.get("channel") not in supported:
            errors.append(f"{label} has an unsupported channel")
        reference = reference_variants.get(key)
        if reference is not None:
            for field in ("content_version_id", "campaign_id", "channel", "angle"):
                if variant.get(field) != reference.get(field):
                    errors.append(f"{label} {field} differs from deterministic campaign")

    attribution_codes: set[str] = set()
    publication_ids: set[str] = set()
    seen_publication_keys: set[tuple[str, str]] = set()
    for index, publication in enumerate(queue):
        label = f"publication_queue[{index}]"
        if not isinstance(publication, dict):
            errors.append(f"{label} must be an object")
            continue
        key = (text(publication.get("channel")), text(publication.get("angle")))
        if key in seen_publication_keys:
            errors.append(f"{label} duplicates a channel and angle")
        seen_publication_keys.add(key)
        if publication.get("channel") not in supported:
            errors.append(f"{label} has an unsupported channel")
        if publication.get("publish_mode") != "manual":
            errors.append(f"{label} must remain manual")
        status = publication.get("status")
        if status not in {"draft", "ready_to_publish", "published", "paused"}:
            errors.append(f"{label} has an invalid status")

        code = text(publication.get("attribution_code"))
        publication_id = text(publication.get("publication_id"))
        if not code or code in attribution_codes:
            errors.append(f"{label} attribution_code is missing or duplicated")
        attribution_codes.add(code)
        if not publication_id or publication_id in publication_ids:
            errors.append(f"{label} publication_id is missing or duplicated")
        publication_ids.add(publication_id)

        reference = reference_publications.get(key)
        if reference is not None:
            for field in (
                "publication_id",
                "campaign_id",
                "content_version_id",
                "channel",
                "angle",
                "attribution_code",
                "application_email",
                "subject_format",
                "publish_mode",
            ):
                if publication.get(field) != reference.get(field):
                    errors.append(f"{label} {field} differs from deterministic campaign")
        if publication.get("campaign_id") != campaign.get("campaign_id"):
            errors.append(f"{label} campaign_id mismatch")

        variant_id = text(publication.get("content_version_id"))
        variant = variants_by_id.get(variant_id)
        if variant is None:
            errors.append(f"{label} references a missing content variant")

        needs_approval = status in {"ready_to_publish", "published"}
        if needs_approval and variant is not None:
            title = text(variant.get("title"))
            body = text(variant.get("body"))
            asset_script = variant.get("asset_script")
            if not title:
                errors.append(f"{label} content title is required before publication")
            if not body:
                errors.append(f"{label} content body is required before publication")
            if (
                not isinstance(asset_script, list)
                or not asset_script
                or any(not isinstance(item, str) or not item.strip() for item in asset_script)
            ):
                errors.append(f"{label} asset_script must be a non-empty list")
                asset_script = []
            approved_company = text(public_facts.get("company_name"))
            approved_salary = text(public_facts.get("salary_range"))
            combined_copy = "\n".join([title, body, *asset_script])
            company_marker = f"公司：{approved_company}"
            salary_marker = f"薪资：{approved_salary}"
            for required_fact, fact_label in (
                (company_marker, "company marker"),
                (salary_marker, "salary marker"),
                (text(snapshot.get("job_id")), "job ID"),
                (text(publication.get("application_email")), "application email"),
                (text(publication.get("attribution_code")), "attribution code"),
                (text(snapshot.get("location")), "location"),
                (text(snapshot.get("work_mode")), "work mode"),
                (text(content_basis.get("why_now")), "hiring reason"),
                (
                    text(content_basis.get("outcome_30d")),
                    "first-stage outcome",
                ),
                (
                    text(content_basis.get("hard_requirements")),
                    "hard requirements",
                ),
                (
                    text(content_basis.get("evidence_requirements")),
                    "evidence requirements",
                ),
                (
                    text(content_basis.get("red_lines")),
                    "unsuitable or red-line facts",
                ),
                (text(public_facts.get("privacy_notice")), "privacy notice"),
            ):
                if required_fact and required_fact not in combined_copy:
                    errors.append(f"{label} omits the approved {fact_label}")
            remaining_copy = combined_copy.replace(company_marker, "").replace(
                salary_marker, ""
            )
            approved_salary_normalized = approved_salary.replace(" ", "").casefold()
            for salary_token in SALARY_TOKEN_PATTERN.findall(remaining_copy):
                normalized_token = salary_token.replace(" ", "").casefold()
                if normalized_token not in approved_salary_normalized:
                    errors.append(
                        f"{label} contains a salary token outside the approved range"
                    )
                    break
            if EXTRA_SALARY_PATTERN.search(remaining_copy):
                errors.append(f"{label} contains an unapproved salary claim")
            if EXTRA_COMPANY_PATTERN.search(remaining_copy):
                errors.append(f"{label} contains an unapproved company or employer claim")
            if URL_PATTERN.search(combined_copy):
                errors.append(
                    f"{label} contains an external URL; use the approved application email"
                )
            if campaign.get("campaign_mode") == "talent_mapping":
                if "人才寻访" not in combined_copy:
                    errors.append(f"{label} conditional copy must say 人才寻访")
                if CONDITIONAL_PROMISE_PATTERN.search(combined_copy):
                    errors.append(
                        f"{label} conditional copy promises a formal opening or employment"
                    )

            approval = variant.get("content_approval")
            if not isinstance(approval, dict):
                errors.append(f"{label} content_approval must be an object")
                approval = {}
            if approval.get("status") != "approved":
                errors.append(f"{label} lacks human content approval")
            for approval_field in (
                "approval_record_id",
                "reviewer",
                "reviewed_at",
                "approval_source_revision",
                "approval_readback_at",
            ):
                if not text(approval.get(approval_field)):
                    errors.append(
                        f"{label} content approval is missing {approval_field}"
                    )
            if text(approval.get("job_version")) != text(snapshot.get("job_version")):
                errors.append(f"{label} approval job version is stale")
            if approval.get("salary_approval_reference") != public_facts.get(
                "salary_approval_reference"
            ):
                errors.append(f"{label} approval salary reference is stale")
            if approval.get("reviewed_at") and not valid_timestamp(
                approval.get("reviewed_at")
            ):
                errors.append(f"{label} approval reviewed_at is invalid")
            if approval.get("approval_readback_at") and not valid_timestamp(
                approval.get("approval_readback_at")
            ):
                errors.append(f"{label} approval_readback_at is invalid")
            if not timestamp_not_before(
                approval.get("approval_readback_at"),
                approval.get("reviewed_at"),
            ):
                errors.append(f"{label} approval readback predates review")
            if approval.get("reviewer") not in authorized_reviewers:
                errors.append(f"{label} reviewer is not authorized by config")
            if (
                campaign.get("campaign_mode") == "talent_mapping"
                and approval.get("no_employment_promise_confirmed") is not True
            ):
                errors.append(
                    f"{label} conditional approval lacks no-employment-promise confirmation"
                )
            if approval.get("content_sha256") != content_hash(variant):
                errors.append(f"{label} approval content fingerprint is stale")

        if status == "published":
            if not valid_https_url(publication.get("published_url")):
                errors.append(f"{label} published_url must be a valid HTTPS URL")
            if not valid_timestamp(publication.get("published_at")):
                errors.append(f"{label} published_at must be timezone-aware ISO 8601")
            if not text(publication.get("publisher")):
                errors.append(f"{label} publisher is required")

    if reference_variants and seen_variant_keys != set(reference_variants):
        errors.append("content variants differ from the deterministic channel-angle plan")
    if reference_publications and seen_publication_keys != set(reference_publications):
        errors.append("publication queue differs from the deterministic channel-angle plan")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--job", type=Path)
    parser.add_argument(
        "--ready",
        action="store_true",
        help="Require at least one approved ready_to_publish or published item",
    )
    args = parser.parse_args(argv)
    try:
        campaign = json.loads(args.campaign.read_text(encoding="utf-8"))
        config = json.loads(args.config.read_text(encoding="utf-8"))
        job = (
            json.loads(args.job.read_text(encoding="utf-8"))
            if args.job is not None
            else None
        )
        if not isinstance(campaign, dict) or not isinstance(config, dict):
            raise ValueError("campaign and config roots must be objects")
        if job is not None and not isinstance(job, dict):
            raise ValueError("job root must be an object")
        errors = validate_campaign(campaign, config, job, args.ready)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
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
