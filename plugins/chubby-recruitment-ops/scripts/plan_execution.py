#!/usr/bin/env python3
"""Build a bounded recruitment execution plan without running external actions."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


STABLE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
QUICK_TASKS = {
    "campaign",
    "candidate",
    "digest",
    "hire_sync",
    "inbox",
    "interview",
    "metric",
    "notification",
    "recommendation",
}
SCHEDULED_TASKS = {"cycle"}
MAINTENANCE_TASKS = {"plugin", "docs", "release"}
QUICK_TARGET_REQUIRED = {
    "campaign",
    "candidate",
    "interview",
    "metric",
    "notification",
}

QUICK_STAGES = {
    "campaign": [
        "load_private_config",
        "targeted_job_gate_read",
        "build_channel_campaign",
        "validate_campaign",
        "persist_manual_publication_queue",
        "run_log",
    ],
    "candidate": [
        "load_private_config",
        "targeted_candidate_application_read",
        "targeted_job_gate_read",
        "assess_or_update_target",
        "batch_base_write",
        "single_readback",
        "decision_notification_if_changed",
        "run_log",
    ],
    "inbox": [
        "load_private_config",
        "gmail_profile_check",
        "claim_target_or_watermark_batch",
        "supported_attachment_check",
        "targeted_resume_processing",
        "batch_base_write",
        "single_readback",
        "recommendation_for_changed_candidates",
        "decision_notification_if_changed",
        "run_log",
    ],
    "hire_sync": [
        "load_private_config",
        "bounded_ats_identity_probe",
        "mapped_job_gate_read",
        "ats_incremental_batch",
        "batch_base_write",
        "single_readback",
        "run_log",
    ],
    "interview": [
        "load_private_config",
        "targeted_candidate_application_read",
        "targeted_job_gate_read",
        "targeted_calendar_vc_artifact_read",
        "reconcile_or_evaluate_target",
        "batch_base_write",
        "single_readback",
        "decision_notification_if_changed",
        "run_log",
    ],
    "metric": [
        "load_private_config",
        "targeted_publication_read",
        "append_metric_snapshot",
        "single_readback",
        "run_log",
    ],
    "notification": [
        "load_private_config",
        "targeted_candidate_decision_read",
        "render_complete_decision_notification",
        "send_once",
        "message_readback",
        "run_log",
    ],
    "digest": [
        "load_private_config",
        "read_current_synchronized_summary_facts",
        "render_redacted_ad_hoc_digest",
        "run_log",
    ],
    "recommendation": [
        "load_private_config",
        "read_current_synchronized_recommendation_facts",
        "run_recommendation_dry_run",
        "commit_new_review_actions",
        "batch_base_write",
        "single_readback",
        "decision_notification_if_changed",
        "run_log",
    ],
}

QUICK_FORBIDDEN = [
    "full_connector_doctor",
    "full_mailbox_scan",
    "full_calendar_scan",
    "full_ats_sync",
    "full_refresh_before_digest",
    "repository_edit",
    "package_test_suite",
    "git_commit",
    "git_push",
    "social_media_publish",
]

SCHEDULED_FORBIDDEN = [
    "repository_edit",
    "package_test_suite",
    "plugin_reinstall",
    "git_commit",
    "git_push",
]

MAINTENANCE_FORBIDDEN = [
    "gmail_read",
    "ats_sync",
    "candidate_base_write",
    "calendar_write",
    "candidate_notification",
    "production_watermark_advance",
]


def stable_target(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not STABLE_ID.fullmatch(value.strip()):
        raise ValueError("target_id must be a stable identifier")
    return value.strip()


def quick_plan(task: str, trigger: str, target_id: str | None) -> dict[str, Any]:
    if task not in QUICK_TASKS:
        raise ValueError(f"quick task must be one of: {', '.join(sorted(QUICK_TASKS))}")
    if trigger != "interactive":
        raise ValueError("quick profile requires trigger=interactive")
    if task in QUICK_TARGET_REQUIRED and target_id is None:
        raise ValueError(f"quick {task} requires target_id")
    return {
        "profile": "quick",
        "task": task,
        "trigger": trigger,
        "target_id": target_id,
        "time_budget_seconds": (
            180 if task in {"hire_sync", "inbox"} else 120
        ),
        "connector_retry_limit": 1,
        "source_scope": {
            "hire_sync": "watermark_incremental_batch",
            "inbox": "target_or_watermark_batch_max_5",
            "digest": "current_synchronized_snapshot_only",
            "recommendation": "current_synchronized_snapshot_only",
        }.get(task, "target_only"),
        "stages": list(QUICK_STAGES[task]),
        "forbidden_actions": list(QUICK_FORBIDDEN),
        "cache_policy": {
            "base_schema_ttl_seconds": 86400,
            "eligible_job_snapshot": "reuse_only_when_version_and_gate_unchanged",
        },
        "escalation_policy": (
            "Record maintenance or full-audit work separately. "
            "Do not expand this interactive run."
        ),
    }


def scheduled_plan(task: str, trigger: str, hour: int | None) -> dict[str, Any]:
    if task not in SCHEDULED_TASKS:
        raise ValueError("scheduled task must be cycle")
    if trigger != "timer":
        raise ValueError("scheduled profile requires trigger=timer")
    if type(hour) is not int or not 0 <= hour <= 23:
        raise ValueError("scheduled profile requires hour from 0 to 23")
    stages = [
        "load_private_config",
        "bounded_connector_identity_probe",
        "eligible_job_snapshot",
        "ats_incremental_if_enabled",
        "gmail_incremental_batch",
        "recommendation_for_changed_candidates",
    ]
    if hour in {11, 17, 19}:
        stages.append("interview_reconciliation")
    stages.extend(
        [
            "metrics_incremental",
            "batch_commit_and_readback",
        ]
    )
    if hour == 19:
        stages.append("redacted_daily_digest")
    stages.append("run_log")
    return {
        "profile": "scheduled",
        "task": task,
        "trigger": trigger,
        "hour": hour,
        "time_budget_seconds": 480,
        "connector_retry_limit": 2,
        "source_scope": "watermark_incremental",
        "stages": stages,
        "forbidden_actions": list(SCHEDULED_FORBIDDEN),
        "cache_policy": {
            "base_schema_ttl_seconds": 86400,
            "eligible_job_snapshot": "refresh_once_per_cycle",
        },
        "escalation_policy": (
            "Isolate item failures, finish independent records, and report "
            "maintenance work without editing the repository."
        ),
    }


def maintenance_plan(
    task: str,
    trigger: str,
    explicit_maintenance: bool,
) -> dict[str, Any]:
    if task not in MAINTENANCE_TASKS:
        raise ValueError(
            f"maintenance task must be one of: "
            f"{', '.join(sorted(MAINTENANCE_TASKS))}"
        )
    if trigger != "explicit" or explicit_maintenance is not True:
        raise ValueError(
            "maintenance profile requires trigger=explicit and explicit_maintenance=true"
        )
    stages = [
        "inspect_repository",
        "implement_scoped_change",
        "run_low_cost_checks",
        "run_package_validation",
    ]
    if task == "release":
        stages.extend(["commit_intended_files", "push", "confirm_remote_sha"])
    else:
        stages.append("local_handoff")
    return {
        "profile": "maintenance",
        "task": task,
        "trigger": trigger,
        "time_budget_seconds": 900,
        "connector_retry_limit": 0,
        "source_scope": "repository_only",
        "stages": stages,
        "forbidden_actions": list(MAINTENANCE_FORBIDDEN),
        "cache_policy": {},
        "escalation_policy": (
            "Never mix candidate production processing into a repository "
            "maintenance run."
        ),
    }


def build_plan(data: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema_version",
        "profile",
        "task",
        "trigger",
        "target_id",
        "hour",
        "explicit_maintenance",
    }
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(unknown)}")
    if data.get("schema_version") != "1.0":
        raise ValueError("schema_version must be 1.0")
    profile = data.get("profile")
    task = data.get("task")
    trigger = data.get("trigger")
    if not isinstance(task, str) or not isinstance(trigger, str):
        raise ValueError("task and trigger are required")
    target_id = stable_target(data.get("target_id"))
    if profile == "quick":
        if "hour" in data or "explicit_maintenance" in data:
            raise ValueError(
                "quick profile must not use hour or explicit_maintenance"
            )
        plan = quick_plan(task, trigger, target_id)
    elif profile == "scheduled":
        if target_id is not None:
            raise ValueError("scheduled profile must not use target_id")
        if "explicit_maintenance" in data:
            raise ValueError(
                "scheduled profile must not use explicit_maintenance"
            )
        plan = scheduled_plan(task, trigger, data.get("hour"))
    elif profile == "maintenance":
        if target_id is not None:
            raise ValueError("maintenance profile must not use target_id")
        if "hour" in data:
            raise ValueError("maintenance profile must not use hour")
        plan = maintenance_plan(
            task,
            trigger,
            data.get("explicit_maintenance") is True,
        )
    else:
        raise ValueError("profile must be quick, scheduled, or maintenance")
    return {"ok": True, "schema_version": "1.0", **plan}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("input root must be an object")
        result = build_plan(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
