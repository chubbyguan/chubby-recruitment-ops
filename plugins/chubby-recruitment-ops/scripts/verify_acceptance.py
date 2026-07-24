#!/usr/bin/env python3
"""Run local synthetic checks; this does not prove live integration readiness."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from render_digest import render
from state_store import (
    attachment_seen,
    claim_message,
    connect,
    mark_attachment,
    mark_message,
    message_seen,
)
from validate_assessment import validate_assessment


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    plugin_root = Path(__file__).resolve().parent.parent
    checks: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="recruitment-ops-") as temp_dir:
            db_path = Path(temp_dir) / "state.sqlite3"
            connection = connect(db_path)

            message_id = "synthetic-message-001"
            check(not message_seen(connection, message_id)["seen"], "new message must be unseen")
            lease_token = claim_message(connection, message_id)
            check(bool(lease_token), "new message claim must succeed")
            check(not claim_message(connection, message_id), "second message claim must fail")
            mark_message(
                connection,
                message_id,
                "success",
                str(lease_token),
                candidate_id="SYNTHETIC-CANDIDATE-001",
                detail={"source": "acceptance"},
            )
            check(message_seen(connection, message_id)["seen"], "processed message must be seen")
            checks.append("message-id idempotency")

            attachment_hash = hashlib.sha256(b"synthetic resume").hexdigest()
            check(
                not attachment_seen(connection, attachment_hash)["seen"],
                "new attachment must be unseen",
            )
            mark_attachment(
                connection,
                attachment_hash,
                message_id,
                filename="synthetic-resume.pdf",
                candidate_id="SYNTHETIC-CANDIDATE-001",
            )
            check(
                attachment_seen(connection, attachment_hash)["seen"],
                "processed attachment must be seen",
            )
            checks.append("attachment hash idempotency")
            connection.close()

            assessment = json.loads(
                (plugin_root / "assets" / "assessment.example.json").read_text(encoding="utf-8")
            )
            check(not validate_assessment(assessment), "safe assessment must validate")
            checks.append("assessment schema and score validation")

            unsafe_assessment = copy.deepcopy(assessment)
            unsafe_assessment["evidence_summary"].append("候选人年龄适合团队")
            check(
                bool(validate_assessment(unsafe_assessment)),
                "protected attribute must be rejected",
            )
            checks.append("protected-attribute rejection")

            digest = render(
                {
                    "date": "2099-01-01",
                    "content": {},
                    "inbox": {},
                    "quality": {},
                    "candidates": [
                        {
                            "alias": "候选人A",
                            "job_id": "DEMO-01",
                            "score": 69,
                            "pii_reviewed": True,
                            "redact_names": ["测试候选人"],
                            "evidence": "联系 demo.person@example.com",
                            "risk": "电话 13800138000 待核验",
                        }
                    ],
                    "interviews": {},
                    "anomalies": [],
                }
            )
            check("demo.person@example.com" not in digest, "digest must redact email")
            check("13800138000" not in digest, "digest must redact phone")
            checks.append("digest PII redaction")
    except (AssertionError, OSError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "local_checks_passed": False,
                    "production_ready": False,
                    "integration_acceptance": "not_run",
                    "checks": checks,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "local_checks_passed": True,
                "production_ready": False,
                "integration_acceptance": "not_run",
                "checks": checks,
                "next_step": "Run controlled Gmail and Feishu probes, Base upsert/readback, duplicate replay, and digest send/readback.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
