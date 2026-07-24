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
    "candidates",
    "interviews_offers",
    "content_performance",
    "mail_log",
    "automation_log",
}
REQUIRED_CONTROLS = {
    "auto_publish_xiaohongshu",
    "auto_reject_candidate",
    "auto_invite_interview",
    "auto_send_offer",
    "auto_change_job_status",
}
EXPECTED_SCHEMA: dict[str, Any] = {
    "schema_version": None,
    "department_id": None,
    "timezone": None,
    "public_content": {
        "application_email": None,
        "disclose_company_name": None,
        "disclose_salary": None,
        "xiaohongshu_publish_mode": None,
        "privacy_notice": None,
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
        "digest_hour": None,
        "enabled": None,
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


def validate_config(config: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    validate_shape(config, EXPECTED_SCHEMA, "", issues)

    for key in find_secret_keys(config):
        issues.append(issue("error", "secret-key", f"forbidden secret field in config: {key}"))

    connectors = as_object(config.get("connectors"))
    email_config = as_object(connectors.get("email"))
    email = email_config.get("account", "")
    if email in PLACEHOLDERS or "@" not in email:
        issues.append(issue("error", "email", "configure a dedicated recruitment email"))

    public_content = as_object(config.get("public_content"))
    public_email = public_content.get("application_email", "")
    if public_email != email:
        issues.append(
            issue("error", "email-mismatch", "public application email must match the bound mailbox")
        )

    publish_mode = public_content.get("xiaohongshu_publish_mode")
    if publish_mode != "manual":
        issues.append(
            issue("error", "xiaohongshu-mode", "Xiaohongshu publishing must remain manual")
        )

    controls = as_object(config.get("controls"))
    for key in sorted(REQUIRED_CONTROLS):
        if controls.get(key) is not False:
            issues.append(issue("error", "unsafe-control", f"{key} must exist and be false"))

    privacy = as_object(config.get("privacy"))
    retention_days = privacy.get("retention_days")
    if not isinstance(retention_days, int) or retention_days <= 0:
        issues.append(issue("error", "retention", "set a positive retention_days value"))

    reviewers = privacy.get("authorized_reviewers", [])
    if not isinstance(reviewers, list) or not reviewers:
        issues.append(
            issue("warning", "reviewers", "authorized_reviewers is empty; confirm access ownership")
        )

    feishu = as_object(connectors.get("feishu"))
    if feishu.get("base_app_token") in PLACEHOLDERS:
        issues.append(issue("error", "feishu-base", "map the department Feishu Base"))
    if feishu.get("digest_recipient_id") in PLACEHOLDERS:
        issues.append(issue("error", "digest-recipient", "configure the Feishu digest recipient"))

    tables = as_object(feishu.get("tables"))
    missing_tables = sorted(REQUIRED_TABLES - set(tables))
    if missing_tables:
        issues.append(
            issue("error", "table-map", f"missing logical table mappings: {', '.join(missing_tables)}")
        )
    for table_name in sorted(REQUIRED_TABLES):
        if tables.get(table_name) in PLACEHOLDERS:
            issues.append(issue("error", "table-map", f"map Feishu table: {table_name}"))

    schedule = as_object(config.get("schedule"))
    hours = schedule.get("inbox_hours", [])
    if not hours or any(not isinstance(hour, int) or not 0 <= hour <= 23 for hour in hours):
        issues.append(issue("error", "schedule", "inbox_hours must contain valid hours"))
    if schedule.get("enabled"):
        issues.append(
            issue(
                "warning",
                "schedule-live",
                "schedule is enabled; confirm end-to-end acceptance before production use",
            )
        )

    adapters = {
        email_config.get("adapter", "auto"),
        feishu.get("adapter", "auto"),
    }
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
    if (workspace / ".git").exists():
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
        "note": "This doctor is local-only; live Gmail and Feishu authorization still require read-only probes.",
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
