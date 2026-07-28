#!/usr/bin/env python3
"""Validate local recruitment configuration without changing external systems."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PLACEHOLDERS = {"", "SET_LOCALLY", "replace-with-department-id", "jobs@example.com"}
REQUIRED_TABLES = {
    "jobs",
    "campaigns",
    "content_variants",
    "publications",
    "metric_snapshots",
    "attribution_codes",
    "candidates",
    "applications",
    "application_stage_history",
    "interviews",
    "interview_artifacts",
    "interview_evaluations",
    "decision_queue",
    "mail_log",
    "automation_log",
}
REQUIRED_CONTROLS = {
    "auto_publish_social_media",
    "auto_reject_candidate",
    "auto_invite_interview",
    "auto_send_offer",
    "auto_change_job_status",
    "auto_merge_candidates",
    "auto_mark_interview_passed",
}
EXPECTED_SCHEMA: dict[str, Any] = {
    "schema_version": None,
    "department_id": None,
    "timezone": None,
    "company": {
        "public_name": None,
        "disclose_public_name": None,
        "legal_entity_source": None,
    },
    "public_content": {
        "application_email": None,
        "privacy_notice": None,
    },
    "compensation": {
        "source": None,
        "public_range_field": None,
        "approval_field": None,
        "approval_reference_field": None,
        "require_approved_range": None,
        "fallback": None,
    },
    "channels": {
        "enabled": None,
        "supported": None,
        "publish_mode": None,
        "metric_windows_hours": None,
    },
    "attribution": {
        "subject_code_required": None,
        "gmail_plus_alias": None,
        "allow_inferred_source": None,
    },
    "connectors": {
        "email": {
            "provider": None,
            "adapter": None,
            "account": None,
            "inbox_query": None,
            "processed_label": None,
        },
        "feishu": {
            "adapter": None,
            "calendar_adapter": None,
            "vc_adapter": None,
            "minutes_adapter": None,
            "base_app_token": None,
            "digest_recipient_type": None,
            "digest_recipient_id": None,
            "tables": {table_name: None for table_name in REQUIRED_TABLES},
        },
    },
    "job_policy": {
        "source": None,
        "open_statuses": None,
        "conditional_statuses": None,
        "blocked_statuses": None,
    },
    "schedule": {
        "inbox_hours": None,
        "interview_reconcile_hours": None,
        "digest_hour": None,
        "decision_digest_hour": None,
        "enabled": None,
    },
    "interview_policy": {
        "artifact_min_seconds": None,
        "evaluation_sla_hours": None,
        "decision_sla_hours": None,
        "allow_minutes_search_fallback": None,
        "use_original_transcript_only": None,
    },
    "privacy": {
        "retention_days": None,
        "authorized_reviewers": None,
        "store_resume_attachments_in_feishu": None,
        "redact_digest_contact_details": None,
    },
    "controls": {control: None for control in REQUIRED_CONTROLS},
}


def issue(level: str, code: str, message: str) -> dict[str, str]:
    return {"level": level, "code": code, "message": message}


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def validate_shape(
    value: Any,
    schema: dict[str, Any],
    path: str,
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        issues.append(issue("error", "config-type", f"{path or 'config'} must be an object"))
        return {}
    unknown = sorted(set(value) - set(schema))
    missing = sorted(set(schema) - set(value))
    for key in unknown:
        issues.append(issue("error", "unknown-key", f"unknown config key: {path}{key}"))
    for key in missing:
        issues.append(issue("error", "missing-key", f"missing config key: {path}{key}"))
    for key, child_schema in schema.items():
        if key in value and isinstance(child_schema, dict):
            validate_shape(value[key], child_schema, f"{path}{key}.", issues)
    return value


def find_secret_keys(value: Any, path: str = "") -> list[str]:
    secret_paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            full_path = f"{path}.{key}" if path else key
            normalized = normalize_key(key)
            allowed_identifier = key == "base_app_token"
            looks_secret = (
                "password" in normalized
                or "secret" in normalized
                or "privatekey" in normalized
                or "apikey" in normalized
                or normalized.endswith("accesstoken")
                or normalized.endswith("refreshtoken")
                or (normalized.endswith("token") and not allowed_identifier)
            )
            if looks_secret:
                secret_paths.append(full_path)
            secret_paths.extend(find_secret_keys(child, full_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            secret_paths.extend(find_secret_keys(child, f"{path}[{index}]"))
    return secret_paths


def as_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def is_placeholder(value: Any) -> bool:
    return not isinstance(value, str) or value in PLACEHOLDERS


def validate_config(config: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    validate_shape(config, EXPECTED_SCHEMA, "", issues)

    for key in find_secret_keys(config):
        issues.append(issue("error", "secret-key", f"forbidden secret field in config: {key}"))

    connectors = as_object(config.get("connectors"))
    email_config = as_object(connectors.get("email"))
    email = email_config.get("account", "")
    if (
        not isinstance(email, str)
        or email in PLACEHOLDERS
        or "@" not in email
    ):
        issues.append(issue("error", "email", "configure a dedicated recruitment email"))

    public_content = as_object(config.get("public_content"))
    public_email = public_content.get("application_email", "")
    if not isinstance(public_email, str) or public_email != email:
        issues.append(
            issue("error", "email-mismatch", "public application email must match the bound mailbox")
        )

    if config.get("schema_version") != "3.0":
        issues.append(issue("error", "schema-version", "config schema_version must be 3.0"))

    company = as_object(config.get("company"))
    raw_company_name = company.get("public_name", "")
    company_name = raw_company_name.strip() if isinstance(raw_company_name, str) else ""
    if not company_name or company_name in PLACEHOLDERS:
        issues.append(issue("error", "company-name", "configure the approved public company name"))
    if company.get("disclose_public_name") is not True:
        issues.append(
            issue("error", "company-disclosure", "company name disclosure must be explicitly true")
        )
    if company.get("legal_entity_source") != "job_source":
        issues.append(
            issue("error", "legal-entity-source", "legal employing entity must come from job_source")
        )

    compensation = as_object(config.get("compensation"))
    if compensation.get("source") != "job_source":
        issues.append(issue("error", "salary-source", "salary must come from job_source"))
    if compensation.get("require_approved_range") is not True:
        issues.append(
            issue("error", "salary-approval", "approved public salary range must be required")
        )
    if compensation.get("fallback") != "block_publication":
        issues.append(
            issue("error", "salary-fallback", "missing salary approval must block publication")
        )
    for key in ("public_range_field", "approval_field", "approval_reference_field"):
        value = compensation.get(key, "")
        if not isinstance(value, str) or not value.strip():
            issues.append(issue("error", "salary-field", f"configure compensation.{key}"))

    channels = as_object(config.get("channels"))
    enabled_channels = channels.get("enabled", [])
    supported_channels = channels.get("supported", [])
    valid_enabled = (
        isinstance(enabled_channels, list)
        and bool(enabled_channels)
        and all(
            isinstance(channel, str) and bool(channel.strip())
            for channel in enabled_channels
        )
    )
    valid_supported = (
        isinstance(supported_channels, list)
        and bool(supported_channels)
        and all(
            isinstance(channel, str) and bool(channel.strip())
            for channel in supported_channels
        )
    )
    if not valid_enabled:
        issues.append(issue("error", "channels", "configure at least one enabled channel"))
    if not valid_supported:
        issues.append(issue("error", "channels", "supported channels must be a non-empty list"))
    elif valid_enabled:
        unsupported = sorted(set(enabled_channels) - set(supported_channels))
        if unsupported:
            issues.append(
                issue("error", "channels", f"enabled channels are unsupported: {', '.join(unsupported)}")
            )
    publish_mode = channels.get("publish_mode")
    if publish_mode != "manual":
        issues.append(
            issue("error", "social-publish-mode", "social publishing must remain manual")
        )
    windows = channels.get("metric_windows_hours", [])
    if (
        not isinstance(windows, list)
        or not windows
        or any(type(window) is not int or window <= 0 for window in windows)
    ):
        issues.append(
            issue("error", "metric-windows", "metric windows must contain positive hours")
        )

    attribution = as_object(config.get("attribution"))
    if attribution.get("subject_code_required") is not True:
        issues.append(issue("error", "attribution", "subject attribution codes are required"))
    if attribution.get("allow_inferred_source") is not False:
        issues.append(
            issue(
                "error",
                "attribution",
                "inferred attribution must be disabled by default; record unknown on conflicts",
            )
        )
    if (
        attribution.get("gmail_plus_alias") is True
        and email_config.get("provider") != "gmail"
    ):
        issues.append(
            issue("error", "gmail-alias", "Gmail Plus Alias requires provider=gmail")
        )

    controls = as_object(config.get("controls"))
    for key in sorted(REQUIRED_CONTROLS):
        if controls.get(key) is not False:
            issues.append(issue("error", "unsafe-control", f"{key} must exist and be false"))

    privacy = as_object(config.get("privacy"))
    retention_days = privacy.get("retention_days")
    if type(retention_days) is not int or retention_days <= 0:
        issues.append(issue("error", "retention", "set a positive retention_days value"))

    reviewers = privacy.get("authorized_reviewers", [])
    if not isinstance(reviewers, list) or not reviewers:
        issues.append(
            issue("error", "reviewers", "authorized_reviewers must contain at least one reviewer")
        )
    elif any(not isinstance(reviewer, str) or not reviewer.strip() for reviewer in reviewers):
        issues.append(
            issue("error", "reviewers", "authorized_reviewers must contain non-empty strings")
        )
    if privacy.get("store_resume_attachments_in_feishu") is not False:
        issues.append(
            issue(
                "warning",
                "attachment-storage",
                "resume attachment storage is enabled; confirm necessity and access controls",
            )
        )

    feishu = as_object(connectors.get("feishu"))
    if is_placeholder(feishu.get("base_app_token")):
        issues.append(issue("error", "feishu-base", "map the department Feishu Base"))
    if is_placeholder(feishu.get("digest_recipient_id")):
        issues.append(issue("error", "digest-recipient", "configure the Feishu digest recipient"))

    tables = as_object(feishu.get("tables"))
    missing_tables = sorted(REQUIRED_TABLES - set(tables))
    if missing_tables:
        issues.append(
            issue("error", "table-map", f"missing logical table mappings: {', '.join(missing_tables)}")
        )
    for table_name in sorted(REQUIRED_TABLES):
        if is_placeholder(tables.get(table_name)):
            issues.append(issue("error", "table-map", f"map Feishu table: {table_name}"))

    schedule = as_object(config.get("schedule"))
    hours = schedule.get("inbox_hours", [])
    if (
        not isinstance(hours, list)
        or not hours
        or any(type(hour) is not int or not 0 <= hour <= 23 for hour in hours)
    ):
        issues.append(issue("error", "schedule", "inbox_hours must contain valid hours"))
    interview_hours = schedule.get("interview_reconcile_hours", [])
    if (
        not isinstance(interview_hours, list)
        or not interview_hours
        or any(type(hour) is not int or not 0 <= hour <= 23 for hour in interview_hours)
    ):
        issues.append(
            issue(
                "error",
                "schedule",
                "interview_reconcile_hours must contain valid hours",
            )
        )
    for field in ("digest_hour", "decision_digest_hour"):
        hour = schedule.get(field)
        if type(hour) is not int or not 0 <= hour <= 23:
            issues.append(issue("error", "schedule", f"{field} must be a valid hour"))
    if schedule.get("enabled"):
        issues.append(
            issue(
                "warning",
                "schedule-live",
                "schedule is enabled; confirm end-to-end acceptance before production use",
            )
        )

    interview_policy = as_object(config.get("interview_policy"))
    for field in (
        "artifact_min_seconds",
        "evaluation_sla_hours",
        "decision_sla_hours",
    ):
        value = interview_policy.get(field)
        if type(value) is not int or value <= 0:
            issues.append(
                issue(
                    "error",
                    "interview-policy",
                    f"interview_policy.{field} must be a positive integer",
                )
            )
    if interview_policy.get("allow_minutes_search_fallback") is not True:
        issues.append(
            issue(
                "error",
                "interview-policy",
                "Minutes search fallback must be enabled",
            )
        )
    if interview_policy.get("use_original_transcript_only") is not True:
        issues.append(
            issue(
                "error",
                "interview-policy",
                "interview scoring must use the original transcript",
            )
        )

    adapter_values = (
        email_config.get("adapter", "auto"),
        feishu.get("adapter", "auto"),
        feishu.get("calendar_adapter", "auto"),
        feishu.get("vc_adapter", "auto"),
        feishu.get("minutes_adapter", "auto"),
    )
    valid_adapters = [
        adapter
        for adapter in adapter_values
        if isinstance(adapter, str) and adapter.strip()
    ]
    if len(valid_adapters) != len(adapter_values):
        issues.append(
            issue("error", "adapter", "connector adapters must be non-empty strings")
        )
    adapters = set(valid_adapters)
    if "lark-cli" in adapters and not shutil.which("lark-cli"):
        issues.append(issue("error", "lark-cli", "configured lark-cli is not installed"))
    elif shutil.which("lark-cli") is None:
        issues.append(
            issue(
                "warning",
                "feishu-probe",
                "lark-cli was not found; verify that another Feishu connector is available",
            )
        )

    if not shutil.which("codex") and not shutil.which("claude"):
        issues.append(issue("warning", "agent-runtime", "neither codex nor claude is on PATH"))

    return issues


def git_ignore_status(workspace: Path, config_path: Path) -> dict[str, str] | None:
    result = subprocess.run(
        ["git", "-C", str(workspace), "check-ignore", "-q", str(config_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return None
    repository_probe = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if repository_probe.returncode == 0:
        return issue("error", "git-ignore", f"local config is not ignored: {config_path}")
    return issue("warning", "git-repo", "workspace is not a Git repository; keep local config private")


def run_doctor(workspace: Path) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    config_path = workspace / ".recruitment-ops" / "config.json"
    if not config_path.exists():
        return {
            "ok": False,
            "errors": 1,
            "warnings": 0,
            "issues": [issue("error", "config-missing", f"missing config: {config_path}")],
        }

    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("config root must be a JSON object")
    issues = validate_config(config)
    ignore_issue = git_ignore_status(workspace, config_path)
    if ignore_issue:
        issues.append(ignore_issue)

    errors = sum(item["level"] == "error" for item in issues)
    warnings = sum(item["level"] == "warning" for item in issues)
    return {
        "ok": errors == 0,
        "workspace": str(workspace),
        "config": str(config_path),
        "errors": errors,
        "warnings": warnings,
        "issues": issues,
        "note": "This doctor is local-only; live Gmail, Feishu Base, Calendar, VC, and Minutes authorization still require read-only probes.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_doctor(args.workspace)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result = {"ok": False, "errors": 1, "warnings": 0, "issues": [issue("error", "read", str(exc))]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
