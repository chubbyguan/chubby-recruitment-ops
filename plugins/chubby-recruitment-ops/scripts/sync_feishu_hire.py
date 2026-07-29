#!/usr/bin/env python3
"""Synchronize Feishu Hire facts into the recruitment workflow and Base.

The adapter is deliberately read-only against Feishu Hire. It writes only
source-owned, minimal fields into the configured recruitment Base and advances
the local watermark only after every Base upsert succeeds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from state_store import (
    connect,
    get_source_watermark,
    set_source_watermark,
)


SOURCE_NAME = "feishu_hire_v1"
STREAM_NAME = "applications"
SNAPSHOT_SCHEMA_VERSION = "1.0"
INTERVIEW_SUMMARY_LABELS = {
    2: "not_started",
    3: "all_unsubmitted",
    4: "all_passed",
    5: "all_failed",
    6: "no_show",
    7: "partial_all_passed",
    8: "partial_mixed_pass_fail",
    9: "partial_all_failed",
    10: "complete_mixed_pass_fail",
    11: "partial_pass_pending",
    12: "partial_fail_pending",
    13: "partial_all_pending",
    14: "partial_pass_pending_fail",
    15: "complete_pass_pending",
    16: "complete_pending_fail",
    17: "complete_all_pending",
    18: "complete_pass_pending_fail",
}


class AdapterError(RuntimeError):
    """Raised when a connector call fails or returns an invalid envelope."""


def as_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def clean_text(value: Any, limit: int = 1000) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def localized_name(value: Any) -> str:
    data = as_object(value)
    return clean_text(data.get("zh_cn") or data.get("zh_name") or data.get("en_us"))


def timestamp_text(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(int(value))
    if isinstance(value, str) and value.isdigit():
        return value
    return ""


def iso_from_ms(value: Any, timezone_name: str) -> str | None:
    raw = timestamp_text(value)
    if not raw:
        return None
    return datetime.fromtimestamp(
        int(raw) / 1000,
        tz=ZoneInfo(timezone_name),
    ).isoformat(timespec="seconds")


def base_datetime(value: str | None) -> str | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    unsigned = dict(snapshot)
    unsigned.pop("fingerprint", None)
    return hashlib.sha256(stable_json(unsigned).encode("utf-8")).hexdigest()


def private_json_write(path: Path, payload: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


class LarkCli:
    RETRYABLE_CODES = {"429", "800004135"}
    RETRYABLE_SUBTYPES = {
        "rate_limited",
        "timeout",
        "temporarily_unavailable",
    }

    def __init__(
        self,
        binary: str = "lark-cli",
        timeout_seconds: int = 60,
        max_attempts: int = 4,
        retry_base_seconds: float = 1,
        retry_max_seconds: float = 8,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        resolved = shutil.which(binary)
        if not resolved:
            raise AdapterError(f"lark-cli not found: {binary}")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if retry_base_seconds < 0 or retry_max_seconds < 0:
            raise ValueError("retry delays must not be negative")
        self.binary = resolved
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.sleeper = sleeper

    @classmethod
    def is_retryable_error(
        cls,
        code: Any,
        subtype: Any,
        message: Any,
    ) -> bool:
        normalized_code = str(code).strip()
        normalized_subtype = str(subtype).strip().lower()
        normalized_message = clean_text(message, 600).lower()
        return (
            normalized_code in cls.RETRYABLE_CODES
            or normalized_subtype in cls.RETRYABLE_SUBTYPES
            or "too many requests" in normalized_message
            or "rate limit" in normalized_message
            or "temporarily unavailable" in normalized_message
        )

    def wait_before_retry(self, attempt: int) -> None:
        delay = min(
            self.retry_base_seconds * (2 ** max(0, attempt - 1)),
            self.retry_max_seconds,
        )
        if delay:
            self.sleeper(delay)

    def run(self, arguments: list[str]) -> dict[str, Any]:
        environment = os.environ.copy()
        environment["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
        environment["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = subprocess.run(
                    [self.binary, *arguments],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=self.timeout_seconds,
                    env=environment,
                )
            except subprocess.TimeoutExpired as exc:
                if attempt < self.max_attempts:
                    self.wait_before_retry(attempt)
                    continue
                raise AdapterError(
                    f"lark-cli timeout after {self.timeout_seconds}s "
                    f"({self.max_attempts} attempts)"
                ) from exc
            raw = result.stdout.strip() or result.stderr.strip()
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AdapterError(
                    f"lark-cli returned non-JSON output (exit {result.returncode})"
                ) from exc
            if result.returncode == 0 and envelope.get("ok"):
                return envelope
            error = as_object(envelope.get("error"))
            code = error.get("code", "unknown")
            subtype = error.get("subtype", "unknown")
            message = clean_text(error.get("message"), 600) or "connector call failed"
            if (
                attempt < self.max_attempts
                and self.is_retryable_error(code, subtype, message)
            ):
                self.wait_before_retry(attempt)
                continue
            raise AdapterError(
                f"{subtype}({code}): {message} "
                f"(attempt {attempt}/{self.max_attempts})"
            )
        raise AdapterError("lark-cli retry loop ended unexpectedly")

    def api_get(
        self,
        path: str,
        params: dict[str, Any] | None,
        identity: str,
    ) -> dict[str, Any]:
        arguments = ["api", "GET", path, "--as", identity]
        if params:
            arguments.extend(
                [
                    "--params",
                    json.dumps(params, ensure_ascii=False, separators=(",", ":")),
                ]
            )
        return as_object(self.run(arguments).get("data"))

    def base_find_records(
        self,
        base_token: str,
        table_id: str,
        key_field: str,
        key_value: str,
        identity: str,
    ) -> list[str]:
        filter_json = {
            "logic": "and",
            "conditions": [[key_field, "==", key_value]],
        }
        envelope = self.run(
            [
                "base",
                "+record-list",
                "--base-token",
                base_token,
                "--table-id",
                table_id,
                "--field-id",
                key_field,
                "--filter-json",
                json.dumps(filter_json, ensure_ascii=False, separators=(",", ":")),
                "--limit",
                "10",
                "--as",
                identity,
                "--format",
                "json",
            ]
        )
        data = as_object(envelope.get("data"))
        return [
            str(item)
            for item in as_list(data.get("record_id_list"))
            if isinstance(item, str) and item
        ]

    def base_upsert(
        self,
        base_token: str,
        table_id: str,
        fields: dict[str, Any],
        identity: str,
        record_id: str | None = None,
    ) -> dict[str, Any]:
        arguments = [
            "base",
            "+record-upsert",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--json",
            json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
            "--as",
            identity,
            "--format",
            "json",
        ]
        if record_id:
            arguments.extend(["--record-id", record_id])
        return self.run(arguments)

    def base_get_fields(
        self,
        base_token: str,
        table_id: str,
        record_id: str,
        field_names: list[str],
        identity: str,
    ) -> dict[str, Any]:
        arguments = [
            "base",
            "+record-get",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--record-id",
            record_id,
            "--as",
            identity,
            "--format",
            "json",
        ]
        for field_name in field_names:
            arguments.extend(["--field-id", field_name])
        data = as_object(self.run(arguments).get("data"))
        fields = [
            str(item)
            for item in as_list(data.get("fields"))
            if isinstance(item, str)
        ]
        rows = as_list(data.get("data"))
        if not rows or not isinstance(rows[0], list):
            return {}

        def unwrap(value: Any) -> Any:
            while isinstance(value, list) and len(value) == 1:
                value = value[0]
            return value

        return {
            field_name: unwrap(rows[0][index])
            for index, field_name in enumerate(fields)
            if index < len(rows[0])
        }


class FeishuHireClient:
    def __init__(self, cli: LarkCli, identity: str = "bot") -> None:
        self.cli = cli
        self.identity = identity

    def get_job(self, job_id: str) -> dict[str, Any]:
        data = self.cli.api_get(
            f"/open-apis/hire/v1/jobs/{job_id}",
            None,
            self.identity,
        )
        job = as_object(data.get("job"))
        if not job:
            raise AdapterError(f"empty job payload: {job_id}")
        return job

    def list_application_ids(
        self,
        job_id: str,
        start_ms: int,
        end_ms: int,
        page_size: int,
    ) -> list[str]:
        page_token = ""
        identifiers: list[str] = []
        while True:
            params: dict[str, Any] = {
                "job_id": job_id,
                "active_status": "3",
                "update_start_time": str(start_ms),
                "update_end_time": str(end_ms),
                "page_size": min(page_size, 200),
            }
            if page_token:
                params["page_token"] = page_token
            data = self.cli.api_get(
                "/open-apis/hire/v1/applications",
                params,
                self.identity,
            )
            identifiers.extend(
                str(item)
                for item in as_list(data.get("items"))
                if isinstance(item, str) and item
            )
            if not data.get("has_more"):
                break
            page_token = clean_text(data.get("page_token"), 2000)
            if not page_token:
                raise AdapterError("applications pagination omitted page_token")
        return list(dict.fromkeys(identifiers))

    def get_application(self, application_id: str) -> dict[str, Any]:
        data = self.cli.api_get(
            f"/open-apis/hire/v1/applications/{application_id}",
            None,
            self.identity,
        )
        application = as_object(data.get("application"))
        if not application:
            raise AdapterError(f"empty application payload: {application_id}")
        return application

    def get_talent(self, talent_id: str) -> dict[str, Any]:
        data = self.cli.api_get(
            f"/open-apis/hire/v1/talents/{talent_id}",
            None,
            self.identity,
        )
        talent = as_object(data.get("talent"))
        if not talent:
            raise AdapterError(f"empty talent payload: {talent_id}")
        return talent

    def list_interviews(
        self,
        application_id: str,
        page_size: int,
    ) -> list[dict[str, Any]]:
        page_token = ""
        interviews: list[dict[str, Any]] = []
        while True:
            params: dict[str, Any] = {
                "application_id": application_id,
                "page_size": min(page_size, 100),
            }
            if page_token:
                params["page_token"] = page_token
            data = self.cli.api_get(
                "/open-apis/hire/v1/interviews",
                params,
                self.identity,
            )
            interviews.extend(
                as_object(item)
                for item in as_list(data.get("items"))
                if isinstance(item, dict)
            )
            if not data.get("has_more"):
                break
            page_token = clean_text(data.get("page_token"), 2000)
            if not page_token:
                raise AdapterError("interviews pagination omitted page_token")
        return interviews


def normalize_job(
    job: dict[str, Any],
    mapping: dict[str, Any],
    timezone_name: str,
) -> dict[str, Any]:
    city_list = []
    for city in as_list(job.get("city_list")):
        name = localized_name(as_object(city).get("name"))
        if name:
            city_list.append(name)
    update_timestamp = timestamp_text(
        job.get("update_timestamp") or job.get("update_time")
    )
    return {
        "hire_job_id": clean_text(job.get("id"), 100),
        "internal_job_id": clean_text(mapping.get("internal_job_id"), 100),
        "expected_title": clean_text(mapping.get("expected_title"), 300),
        "hire_title": clean_text(job.get("title"), 300),
        "active_status": job.get("active_status"),
        "job_version": update_timestamp or "unknown",
        "updated_at": iso_from_ms(update_timestamp, timezone_name),
        "cities": city_list,
        "source": SOURCE_NAME,
    }


def current_career(careers: list[Any]) -> dict[str, Any]:
    normalized = [as_object(item) for item in careers if isinstance(item, dict)]
    if not normalized:
        return {}

    def order(item: dict[str, Any]) -> tuple[int, int]:
        end_time = int(timestamp_text(item.get("end_time")) or 9_999_999_999_999)
        start_time = int(timestamp_text(item.get("start_time")) or 0)
        return end_time, start_time

    return max(normalized, key=order)


def normalize_talent(
    talent: dict[str, Any],
    application: dict[str, Any],
    job: dict[str, Any],
    timezone_name: str,
) -> dict[str, Any]:
    basic = as_object(talent.get("basic_info"))
    careers = as_list(talent.get("career_list"))
    latest = current_career(careers)
    top_degree = as_object(talent.get("top_degree"))
    education = as_list(talent.get("education_list"))
    career_evidence = []
    for item in careers:
        record = as_object(item)
        career_evidence.append(
            {
                "career_id": clean_text(record.get("id"), 100),
                "company": clean_text(record.get("company"), 300),
                "title": clean_text(record.get("title"), 300),
                "start_time": iso_from_ms(record.get("start_time"), timezone_name),
                "end_time": iso_from_ms(record.get("end_time"), timezone_name),
                "description": clean_text(record.get("desc"), 2000),
            }
        )
    source_info = as_object(application.get("resume_source_info"))
    source_name = localized_name(source_info.get("name"))
    candidate_id = clean_text(talent.get("id"), 100)
    return {
        "candidate_id": candidate_id,
        "source_talent_id": candidate_id,
        "name": clean_text(basic.get("name"), 200),
        "experience_years": basic.get("experience_years"),
        "current_company": clean_text(latest.get("company"), 300),
        "current_title": clean_text(latest.get("title"), 300),
        "top_degree": (
            localized_name(top_degree.get("name"))
            or clean_text(top_degree.get("zh_name"), 100)
            or clean_text(top_degree.get("en_name"), 100)
        ),
        "education_count": len(education),
        "career_count": len(careers),
        "resume_source": source_name or "飞书招聘",
        "has_resume": bool(
            application.get("talent_attachment_resume_id")
            or as_list(talent.get("resume_attachment_id_list"))
        ),
        "target_hire_job_id": clean_text(job.get("hire_job_id"), 100),
        "target_internal_job_id": clean_text(job.get("internal_job_id"), 100),
        "target_job_title": clean_text(job.get("expected_title"), 300)
        or clean_text(job.get("hire_title"), 300),
        "source_modified_at": iso_from_ms(basic.get("modify_time"), timezone_name),
        "career_evidence": career_evidence,
        "untrusted_resume_text": True,
        "source": SOURCE_NAME,
    }


def normalize_application(
    application: dict[str, Any],
    job: dict[str, Any],
    timezone_name: str,
) -> dict[str, Any]:
    application_id = clean_text(application.get("id"), 100)
    candidate_id = clean_text(application.get("talent_id"), 100)
    stage = as_object(application.get("stage"))
    stage_times = sorted(
        [
            as_object(item)
            for item in as_list(application.get("stage_time_list"))
            if isinstance(item, dict)
        ],
        key=lambda item: int(timestamp_text(item.get("enter_time")) or 0),
    )
    history = []
    previous_stage = ""
    current_stage_id = clean_text(stage.get("id"), 100)
    current_stage_name = clean_text(
        stage.get("zh_name")
        or localized_name(stage.get("name"))
        or stage.get("en_name"),
        200,
    )
    for item in stage_times:
        stage_id = clean_text(item.get("stage_id"), 100)
        stage_name = (
            current_stage_name
            if stage_id and stage_id == current_stage_id
            else f"stage:{stage_id}"
        )
        history.append(
            {
                "stage_history_id": (
                    f"HIRE-STAGE-{application_id}-{stage_id}-"
                    f"{timestamp_text(item.get('enter_time')) or 'unknown'}"
                ),
                "application_id": application_id,
                "from_stage": previous_stage,
                "to_stage": stage_name,
                "changed_at": iso_from_ms(item.get("enter_time"), timezone_name),
                "source": SOURCE_NAME,
                "human_confirmed": False,
            }
        )
        previous_stage = stage_name
    return {
        "application_id": application_id,
        "candidate_id": candidate_id,
        "hire_job_id": clean_text(application.get("job_id"), 100),
        "internal_job_id": clean_text(job.get("internal_job_id"), 100),
        "hire_job_title": clean_text(job.get("hire_title"), 300),
        "expected_job_title": clean_text(job.get("expected_title"), 300),
        "job_version": clean_text(job.get("job_version"), 100),
        "active_status": application.get("active_status"),
        "stage_id": current_stage_id,
        "stage_name": current_stage_name,
        "stage_type": stage.get("type"),
        "resume_source": localized_name(
            as_object(application.get("resume_source_info")).get("name")
        )
        or "飞书招聘",
        "has_resume": bool(application.get("talent_attachment_resume_id")),
        "modified_at": iso_from_ms(application.get("modify_time"), timezone_name),
        "stage_history": history,
        "source": SOURCE_NAME,
    }


def normalize_interview(
    interview: dict[str, Any],
    candidate_id: str,
    internal_job_id: str,
    job_version: str,
    now_ms: int,
    timezone_name: str = "Asia/Shanghai",
) -> dict[str, Any]:
    summary_code = interview.get("interview_round_summary")
    begin_ms = int(timestamp_text(interview.get("begin_time")) or 0)
    summary_label = INTERVIEW_SUMMARY_LABELS.get(summary_code, "unknown")
    if summary_code == 2 or (begin_ms and begin_ms > now_ms):
        fact_status = "scheduled"
    elif summary_code == 6:
        fact_status = "no_show"
    else:
        fact_status = "completed"
    actual_start = (
        interview.get("actual_begin_time")
        or interview.get("actual_start_time")
    )
    actual_end = (
        interview.get("actual_end_time")
        or interview.get("actual_finish_time")
    )
    return {
        "interview_id": clean_text(interview.get("id"), 100),
        "application_id": clean_text(interview.get("application_id"), 100),
        "candidate_id": candidate_id,
        "internal_job_id": internal_job_id,
        "job_version": job_version,
        "round": interview.get("round"),
        "interview_type": interview.get("interview_type"),
        "planned_start_at": iso_from_ms(interview.get("begin_time"), timezone_name),
        "planned_end_at": iso_from_ms(interview.get("end_time"), timezone_name),
        "actual_start_at": iso_from_ms(actual_start, timezone_name),
        "actual_end_at": iso_from_ms(actual_end, timezone_name),
        "fact_status": fact_status,
        "source_evaluation_code": summary_code,
        "source_evaluation": summary_label,
        "human_decision_status": "pending",
        "source_modified_at": iso_from_ms(
            interview.get("biz_modify_time"),
            timezone_name,
        ),
        "source": SOURCE_NAME,
    }


def connector_config(config: dict[str, Any]) -> dict[str, Any]:
    connectors = as_object(config.get("connectors"))
    hire = as_object(connectors.get("feishu_hire"))
    if hire.get("enabled") is not True:
        raise ValueError("connectors.feishu_hire.enabled must be true")
    if hire.get("adapter") != "lark-cli":
        raise ValueError("connectors.feishu_hire.adapter must be lark-cli")
    if hire.get("identity") != "bot":
        raise ValueError("connectors.feishu_hire.identity must be bot")
    if hire.get("base_identity") not in {"user", "bot"}:
        raise ValueError("connectors.feishu_hire.base_identity must be user or bot")
    if hire.get("source") != SOURCE_NAME:
        raise ValueError(f"connectors.feishu_hire.source must be {SOURCE_NAME}")
    for field in ("page_size", "lookback_days", "watermark_overlap_seconds"):
        value = hire.get(field)
        if type(value) is not int or value <= 0:
            raise ValueError(f"connectors.feishu_hire.{field} must be positive")
    mappings = as_object(hire.get("mapped_jobs"))
    if not mappings:
        raise ValueError("connectors.feishu_hire.mapped_jobs must not be empty")
    for hire_job_id, mapping in mappings.items():
        record = as_object(mapping)
        if (
            not str(hire_job_id).strip()
            or not clean_text(record.get("internal_job_id"), 100)
            or not clean_text(record.get("expected_title"), 300)
        ):
            raise ValueError(
                "every Feishu Hire job mapping needs internal_job_id and expected_title"
            )
    return hire


def build_snapshot(
    client: Any,
    config: dict[str, Any],
    since_ms: int,
    until_ms: int,
    readback_at: str | None = None,
    application_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    hire = connector_config(config)
    timezone_name = clean_text(config.get("timezone"), 100) or "Asia/Shanghai"
    mappings = as_object(hire.get("mapped_jobs"))
    page_size = int(hire.get("page_size", 100))
    jobs: dict[str, dict[str, Any]] = {}
    applications_raw: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []

    explicit_application_ids = [
        str(item).strip()
        for item in (application_ids or [])
        if str(item).strip()
    ]
    if explicit_application_ids:
        for application_id in explicit_application_ids:
            raw_application = client.get_application(application_id)
            hire_job_id = clean_text(raw_application.get("job_id"), 100)
            if hire_job_id not in mappings:
                errors.append(
                    {
                        "entity_type": "application",
                        "entity_id": application_id,
                        "code": "unmapped_job",
                    }
                )
                continue
            applications_raw[application_id] = raw_application
    else:
        for hire_job_id in sorted(mappings):
            for application_id in client.list_application_ids(
                hire_job_id,
                since_ms,
                until_ms,
                page_size,
            ):
                applications_raw.setdefault(
                    application_id,
                    client.get_application(application_id),
                )

    referenced_job_ids = set(mappings) if not explicit_application_ids else {
        clean_text(item.get("job_id"), 100)
        for item in applications_raw.values()
    }
    for hire_job_id in sorted(referenced_job_ids):
        mapping = as_object(mappings.get(hire_job_id))
        if not mapping:
            continue
        jobs[hire_job_id] = normalize_job(
            client.get_job(hire_job_id),
            mapping,
            timezone_name,
        )

    candidates: dict[str, dict[str, Any]] = {}
    applications: list[dict[str, Any]] = []
    interviews: dict[str, dict[str, Any]] = {}
    for application_id in sorted(applications_raw):
        raw_application = applications_raw[application_id]
        hire_job_id = clean_text(raw_application.get("job_id"), 100)
        job = jobs.get(hire_job_id)
        if not job:
            errors.append(
                {
                    "entity_type": "application",
                    "entity_id": application_id,
                    "code": "unmapped_job",
                }
            )
            continue
        talent_id = clean_text(raw_application.get("talent_id"), 100)
        try:
            normalized_application = normalize_application(
                raw_application,
                job,
                timezone_name,
            )
            normalized_candidate = candidates.get(talent_id)
            if normalized_candidate is None:
                normalized_candidate = normalize_talent(
                    client.get_talent(talent_id),
                    raw_application,
                    job,
                    timezone_name,
                )
            normalized_interviews = []
            for raw_interview in client.list_interviews(application_id, page_size):
                normalized_interviews.append(
                    normalize_interview(
                        raw_interview,
                        candidate_id=talent_id,
                        internal_job_id=clean_text(
                            job.get("internal_job_id"),
                            100,
                        ),
                        job_version=clean_text(job.get("job_version"), 100),
                        now_ms=until_ms,
                        timezone_name=timezone_name,
                    )
                )
            applications.append(normalized_application)
            candidates.setdefault(talent_id, normalized_candidate)
            for normalized in normalized_interviews:
                interviews[normalized["interview_id"]] = normalized
        except (AdapterError, ValueError) as exc:
            errors.append(
                {
                    "entity_type": "application",
                    "entity_id": application_id,
                    "code": "read_failed",
                    "detail": clean_text(str(exc), 500),
                }
            )

    readback = readback_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshot: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source": {
            "name": SOURCE_NAME,
            "provenance": "official_feishu_openapi",
            "identity": clean_text(hire.get("identity"), 20) or "bot",
            "readback_at": readback,
            "since_ms": str(since_ms),
            "until_ms": str(until_ms),
            "watermark_overlap_seconds": int(
                hire.get("watermark_overlap_seconds", 300)
            ),
        },
        "jobs": [jobs[key] for key in sorted(jobs)],
        "candidates": [candidates[key] for key in sorted(candidates)],
        "applications": applications,
        "interviews": [interviews[key] for key in sorted(interviews)],
        "errors": errors,
    }
    snapshot["counts"] = {
        "jobs": len(snapshot["jobs"]),
        "candidates": len(snapshot["candidates"]),
        "applications": len(snapshot["applications"]),
        "interviews": len(snapshot["interviews"]),
        "errors": len(errors),
    }
    snapshot["fingerprint"] = snapshot_fingerprint(snapshot)
    return snapshot


def verify_snapshot(snapshot: dict[str, Any]) -> bool:
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported Feishu Hire snapshot schema")
    source = as_object(snapshot.get("source"))
    if source.get("name") != SOURCE_NAME:
        raise ValueError("snapshot source is not Feishu Hire")
    for field in ("readback_at", "since_ms", "until_ms"):
        if not clean_text(source.get(field), 200):
            raise ValueError(f"snapshot source.{field} is required")
    for field in ("jobs", "candidates", "applications", "interviews", "errors"):
        if not isinstance(snapshot.get(field), list):
            raise ValueError(f"snapshot {field} must be a list")
    required_identifiers = {
        "jobs": ("hire_job_id", "internal_job_id"),
        "candidates": ("candidate_id",),
        "applications": ("application_id", "candidate_id", "internal_job_id"),
        "interviews": ("interview_id", "application_id", "candidate_id"),
    }
    for collection, fields in required_identifiers.items():
        for item in as_list(snapshot.get(collection)):
            record = as_object(item)
            for field in fields:
                if not clean_text(record.get(field), 300):
                    raise ValueError(f"snapshot {collection}.{field} is required")
    fingerprint = clean_text(snapshot.get("fingerprint"), 100)
    if fingerprint != snapshot_fingerprint(snapshot):
        raise ValueError("snapshot fingerprint mismatch")
    return True


def mapped_title_differences(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "hire_job_id": clean_text(item.get("hire_job_id"), 100),
            "hire_title": clean_text(item.get("hire_title"), 300),
            "expected_title": clean_text(item.get("expected_title"), 300),
        }
        for raw_item in as_list(snapshot.get("jobs"))
        for item in [as_object(raw_item)]
        if clean_text(item.get("hire_title"), 300)
        and clean_text(item.get("expected_title"), 300)
        and clean_text(item.get("hire_title"), 300)
        != clean_text(item.get("expected_title"), 300)
    ]


def stage_operations(application: dict[str, Any]) -> list[dict[str, Any]]:
    operations = []
    for item in as_list(application.get("stage_history")):
        stage = as_object(item)
        fields = {
            "阶段记录ID": clean_text(stage.get("stage_history_id"), 300),
            "投递ID": clean_text(application.get("application_id"), 100),
            "变更前阶段": clean_text(stage.get("from_stage"), 300),
            "变更后阶段": clean_text(stage.get("to_stage"), 300),
            "变更人": SOURCE_NAME,
            "决策理由": "飞书招聘阶段事实同步；不代表自动决策",
        }
        changed_at = base_datetime(clean_text(stage.get("changed_at"), 100))
        if changed_at:
            fields["变更时间"] = changed_at
        operations.append(
            {
                "logical_table": "application_stage_history",
                "stable_field": "阶段记录ID",
                "stable_value": fields["阶段记录ID"],
                "fields": fields,
            }
        )
    return operations


def build_base_operations(
    snapshot: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    verify_snapshot(snapshot)
    operations: list[dict[str, Any]] = []
    applications = {
        clean_text(item.get("application_id"), 100): as_object(item)
        for item in snapshot["applications"]
    }
    jobs = {
        clean_text(item.get("hire_job_id"), 100): as_object(item)
        for item in snapshot["jobs"]
    }
    for candidate in snapshot["candidates"]:
        item = as_object(candidate)
        fields: dict[str, Any] = {
            "候选人唯一ID": clean_text(item.get("candidate_id"), 100),
            "候选人": clean_text(item.get("name"), 200) or "待核验候选人",
            "数据来源": "飞书招聘",
            "简历来源": clean_text(item.get("resume_source"), 300),
            "工作年限": str(item.get("experience_years") or ""),
            "现任公司": clean_text(item.get("current_company"), 300),
            "现任职位": clean_text(item.get("current_title"), 300),
            "学历": clean_text(item.get("top_degree"), 100),
        }
        fields = {key: value for key, value in fields.items() if value not in {"", None}}
        operations.append(
            {
                "logical_table": "candidates",
                "stable_field": "候选人唯一ID",
                "stable_value": fields["候选人唯一ID"],
                "fields": fields,
            }
        )

    for application in snapshot["applications"]:
        item = as_object(application)
        fields = {
            "投递ID": clean_text(item.get("application_id"), 100),
            "候选人唯一ID": clean_text(item.get("candidate_id"), 100),
            "岗位ID": clean_text(item.get("internal_job_id"), 100),
            "来源渠道": "飞书招聘",
            "明确投递岗位": clean_text(
                item.get("expected_job_title") or item.get("hire_job_title"),
                300,
            ),
            "岗位版本": clean_text(item.get("job_version"), 100),
        }
        operations.append(
            {
                "logical_table": "applications",
                "stable_field": "投递ID",
                "stable_value": fields["投递ID"],
                "fields": fields,
            }
        )
        operations.extend(stage_operations(item))

    for interview in snapshot["interviews"]:
        item = as_object(interview)
        application = applications.get(clean_text(item.get("application_id"), 100), {})
        job = jobs.get(clean_text(application.get("hire_job_id"), 100), {})
        fields: dict[str, Any] = {
            "面试ID": clean_text(item.get("interview_id"), 100),
            "事项ID": clean_text(item.get("interview_id"), 100),
            "投递ID": clean_text(item.get("application_id"), 100),
            "候选人唯一ID": clean_text(item.get("candidate_id"), 100),
            "面试岗位ID": clean_text(item.get("internal_job_id"), 100),
            "当前匹配岗位ID": clean_text(item.get("internal_job_id"), 100),
            "统一岗位ID": clean_text(item.get("internal_job_id"), 100),
            "岗位版本": clean_text(item.get("job_version"), 100),
            "状态": (
                "已安排"
                if item.get("fact_status") == "scheduled"
                else "待结论"
                if item.get("fact_status") == "no_show"
                else "已完成"
            ),
            "记录类型": "正式面试",
            "备注": (
                f"来源=飞书招聘；轮次={item.get('round') or '未标记'}；"
                f"ATS面评状态={clean_text(item.get('source_evaluation'), 100)}；"
                "仅同步来源事实，不代表人工最终决定"
            ),
        }
        planned = base_datetime(clean_text(item.get("planned_start_at"), 100))
        actual_start = base_datetime(clean_text(item.get("actual_start_at"), 100))
        actual_end = base_datetime(clean_text(item.get("actual_end_at"), 100))
        if planned:
            fields["计划时间"] = planned
            fields["计划开始时间"] = planned
        if actual_start:
            fields["实际开始时间"] = actual_start
        if actual_end:
            fields["实际结束时间"] = actual_end
        operations.append(
            {
                "logical_table": "interviews",
                "stable_field": "面试ID",
                "stable_value": fields["面试ID"],
                "fields": fields,
            }
        )
    return operations


def sync_base_operations(
    cli: LarkCli,
    operations: list[dict[str, Any]],
    config: dict[str, Any],
    commit: bool,
) -> dict[str, Any]:
    connectors = as_object(config.get("connectors"))
    feishu = as_object(connectors.get("feishu"))
    hire = connector_config(config)
    base_token = clean_text(feishu.get("base_app_token"), 200)
    tables = as_object(feishu.get("tables"))
    identity = clean_text(hire.get("base_identity"), 20) or "user"
    if not base_token:
        raise ValueError("connectors.feishu.base_app_token is required")
    created = 0
    updated = 0
    planned = 0
    errors: list[dict[str, str]] = []
    for operation in operations:
        logical_table = clean_text(operation.get("logical_table"), 100)
        table_id = clean_text(tables.get(logical_table), 100)
        stable_field = clean_text(operation.get("stable_field"), 100)
        stable_value = clean_text(operation.get("stable_value"), 300)
        if not logical_table or not stable_field or not stable_value:
            errors.append(
                {
                    "logical_table": logical_table or "unknown",
                    "stable_value": stable_value,
                    "code": "invalid_operation",
                }
            )
            continue
        if not table_id:
            errors.append(
                {
                    "logical_table": logical_table,
                    "stable_value": stable_value,
                    "code": "missing_table_mapping",
                }
            )
            continue
        try:
            record_ids = cli.base_find_records(
                base_token,
                table_id,
                stable_field,
                stable_value,
                identity,
            )
            if len(record_ids) > 1:
                raise AdapterError(
                    f"duplicate Base stable key {logical_table}:{stable_value}"
                )
            planned += 1
            if not commit:
                continue
            write_fields = dict(as_object(operation.get("fields")))
            if record_ids and logical_table == "interviews":
                current = cli.base_get_fields(
                    base_token,
                    table_id,
                    record_ids[0],
                    ["状态", "备注"],
                    identity,
                )
                current_status = clean_text(current.get("状态"), 100)
                incoming_status = clean_text(write_fields.get("状态"), 100)
                source_managed_statuses = {"", "待安排", "已安排", "已完成"}
                if (
                    current_status not in source_managed_statuses
                    or (
                        current_status == "已完成"
                        and incoming_status != "已完成"
                    )
                ):
                    write_fields.pop("状态", None)
                current_note = clean_text(current.get("备注"), 2000)
                if current_note and not (
                    current_note.startswith("来源=飞书招聘；")
                    and current_note.endswith("仅同步来源事实，不代表人工最终决定")
                ):
                    write_fields.pop("备注", None)
            cli.base_upsert(
                base_token,
                table_id,
                write_fields,
                identity,
                record_ids[0] if record_ids else None,
            )
            if record_ids:
                updated += 1
            else:
                created += 1
        except (AdapterError, ValueError) as exc:
            errors.append(
                {
                    "logical_table": logical_table,
                    "stable_value": stable_value,
                    "code": "base_write_failed",
                    "detail": clean_text(str(exc), 500),
                }
            )
    return {
        "ok": not errors,
        "committed": commit,
        "planned": planned,
        "created": created,
        "updated": updated,
        "errors": errors,
    }


def load_config(workspace: Path) -> tuple[dict[str, Any], Path, Path]:
    workspace = workspace.expanduser().resolve()
    config_path = workspace / ".recruitment-ops" / "config.json"
    state_path = workspace / ".recruitment-ops" / "state.sqlite3"
    if not config_path.is_file():
        raise FileNotFoundError(f"missing config: {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("config root must be an object")
    return config, config_path, state_path


def default_snapshot_path(workspace: Path, until_ms: int) -> Path:
    return (
        workspace.expanduser().resolve()
        / ".recruitment-ops"
        / "snapshots"
        / f"feishu-hire-{until_ms}.json"
    )


def resolve_window(
    config: dict[str, Any],
    state_path: Path,
    now_ms: int,
) -> tuple[int, int]:
    hire = connector_config(config)
    lookback_days = int(hire.get("lookback_days", 30))
    overlap_ms = int(hire.get("watermark_overlap_seconds", 300)) * 1000
    connection = connect(state_path)
    try:
        stored = get_source_watermark(connection, SOURCE_NAME, STREAM_NAME)
    finally:
        connection.close()
    if stored:
        start_ms = max(0, int(stored["watermark"]) - overlap_ms)
    else:
        start_ms = max(0, now_ms - lookback_days * 86_400_000)
    return start_ms, now_ms


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=["probe", "fetch", "sync"],
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--application-id", action="append", default=[])
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--since-ms", type=int)
    parser.add_argument("--until-ms", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = args.workspace.expanduser().resolve()
    try:
        config, _config_path, state_path = load_config(workspace)
        hire = connector_config(config)
        timeout_seconds = int(
            as_object(config.get("execution")).get("connector_timeout_seconds", 60)
        )
        execution = as_object(config.get("execution"))
        cli = LarkCli(
            timeout_seconds=timeout_seconds,
            max_attempts=int(execution.get("connector_max_attempts", 4)),
            retry_base_seconds=float(
                execution.get("connector_retry_base_seconds", 1)
            ),
            retry_max_seconds=float(
                execution.get("connector_retry_max_seconds", 8)
            ),
        )
        client = FeishuHireClient(
            cli,
            identity=clean_text(hire.get("identity"), 20) or "bot",
        )
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        default_since, default_until = resolve_window(config, state_path, now_ms)
        since_ms = args.since_ms if args.since_ms is not None else default_since
        until_ms = args.until_ms if args.until_ms is not None else default_until
        if since_ms < 0 or until_ms <= since_ms:
            raise ValueError("invalid Feishu Hire time window")
        snapshot = build_snapshot(
            client,
            config,
            since_ms=since_ms,
            until_ms=until_ms,
            application_ids=args.application_id,
        )
        output_path = args.output or default_snapshot_path(workspace, until_ms)
        private_json_write(output_path, snapshot)
        if args.mode in {"probe", "fetch"}:
            result = {
                "ok": not snapshot["errors"],
                "mode": args.mode,
                "source": SOURCE_NAME,
                "snapshot": str(output_path),
                "fingerprint": snapshot["fingerprint"],
                "counts": snapshot["counts"],
                "mapped_title_differences": mapped_title_differences(snapshot),
                "watermark_advanced": False,
            }
        else:
            operations = build_base_operations(snapshot, config)
            sync_result = sync_base_operations(
                cli,
                operations,
                config,
                commit=args.commit,
            )
            can_advance = (
                args.commit
                and not args.application_id
                and not snapshot["errors"]
                and sync_result["ok"]
            )
            if can_advance:
                connection = connect(state_path)
                try:
                    watermark = set_source_watermark(
                        connection,
                        SOURCE_NAME,
                        STREAM_NAME,
                        str(until_ms),
                        snapshot["fingerprint"],
                    )
                finally:
                    connection.close()
            else:
                watermark = None
            result = {
                "ok": not snapshot["errors"] and sync_result["ok"],
                "mode": args.mode,
                "source": SOURCE_NAME,
                "snapshot": str(output_path),
                "fingerprint": snapshot["fingerprint"],
                "counts": snapshot["counts"],
                "mapped_title_differences": mapped_title_differences(snapshot),
                "base_sync": sync_result,
                "watermark_advanced": bool(watermark),
                "watermark": watermark,
            }
    except (
        AdapterError,
        FileNotFoundError,
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        result = {"ok": False, "error": clean_text(str(exc), 1000)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
