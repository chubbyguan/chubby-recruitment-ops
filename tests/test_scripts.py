from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "chubby-recruitment-ops"
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from init_workspace import initialize  # noqa: E402
from migrate_config_v3 import migrate  # noqa: E402
from migrate_config_v4 import migrate as migrate_v4  # noqa: E402
from build_campaign import build_campaign, content_hash  # noqa: E402
from doctor import run_doctor  # noqa: E402
from hash_attachment import hash_attachment  # noqa: E402
from plan_execution import build_plan  # noqa: E402
from preflight import run_preflight  # noqa: E402
from recommend_interviews import recommend  # noqa: E402
from run_cycle import execute_cycle  # noqa: E402
from render_decision_notification import (  # noqa: E402
    render as render_decision_notification,
)
from render_digest import render  # noqa: E402
from reconcile_interviews import reconcile  # noqa: E402
from sync_ats import (  # noqa: E402
    AdapterError,
    CollaborationCli,
    build_base_operations,
    build_snapshot,
    normalize_interview,
    sync_base_operations,
    verify_snapshot,
)
from state_store import (  # noqa: E402
    attachment_seen,
    claim_notification,
    claim_run_lock,
    claim_message,
    complete_notification,
    connect,
    enqueue_action,
    enqueue_notification,
    mark_attachment,
    mark_message,
    message_seen,
    notification_seen,
    get_source_watermark,
    record_action_decision,
    release_run_lock,
    set_source_watermark,
)
from validate_base_mapping import validate_mapping  # noqa: E402
from validate_assessment import validate_assessment  # noqa: E402
from validate_campaign import validate_campaign  # noqa: E402
from validate_interview_evaluation import validate_evaluation  # noqa: E402
from validate_package import validate  # noqa: E402


class PreflightTests(unittest.TestCase):
    def test_installed_package_preflight_passes_without_live_connectors(self) -> None:
        available = {
            "codex": "/usr/local/bin/codex",
            "collaboration-cli": "/usr/local/bin/collaboration-cli",
        }
        result = run_preflight(
            PLUGIN_ROOT,
            runtime="codex",
            which=available.get,
        )
        self.assertTrue(result["ok"])
        statuses = {
            item["code"]: item["status"]
            for item in result["checks"]
        }
        self.assertEqual(statuses["package"], "pass")
        self.assertEqual(statuses["runtime"], "pass")
        self.assertEqual(statuses["gmail-adapter"], "manual")
        self.assertTrue(result["live_acceptance_required"])

    def test_preflight_rejects_missing_selected_runtime(self) -> None:
        result = run_preflight(
            PLUGIN_ROOT,
            runtime="claude",
            which=lambda _name: None,
        )
        self.assertFalse(result["ok"])
        self.assertTrue(
            any(
                item["code"] == "runtime" and item["status"] == "error"
                for item in result["checks"]
            )
        )


class ExecutionProfileTests(unittest.TestCase):
    def test_quick_interview_is_targeted_and_forbids_maintenance(self) -> None:
        result = build_plan(
            {
                "schema_version": "1.0",
                "profile": "quick",
                "task": "interview",
                "trigger": "interactive",
                "target_id": "APP-001",
            }
        )
        self.assertEqual(result["time_budget_seconds"], 120)
        self.assertEqual(result["source_scope"], "target_only")
        self.assertIn("targeted_calendar_vc_artifact_read", result["stages"])
        self.assertIn("package_test_suite", result["forbidden_actions"])
        self.assertIn("git_push", result["forbidden_actions"])

    def test_quick_inbox_is_a_bounded_watermark_batch(self) -> None:
        result = build_plan(
            {
                "schema_version": "1.0",
                "profile": "quick",
                "task": "inbox",
                "trigger": "interactive",
            }
        )
        self.assertEqual(result["time_budget_seconds"], 180)
        self.assertEqual(
            result["source_scope"],
            "target_or_watermark_batch_max_5",
        )
        self.assertEqual(result["connector_retry_limit"], 1)

    def test_quick_campaign_and_recommendation_do_not_broaden_scope(self) -> None:
        campaign = build_plan(
            {
                "schema_version": "1.0",
                "profile": "quick",
                "task": "campaign",
                "trigger": "interactive",
                "target_id": "JOB-001",
            }
        )
        recommendation = build_plan(
            {
                "schema_version": "1.0",
                "profile": "quick",
                "task": "recommendation",
                "trigger": "interactive",
            }
        )
        self.assertEqual(campaign["source_scope"], "target_only")
        self.assertIn("social_media_publish", campaign["forbidden_actions"])
        self.assertEqual(
            recommendation["source_scope"],
            "current_synchronized_snapshot_only",
        )
        self.assertIn(
            "run_recommendation_dry_run",
            recommendation["stages"],
        )

    def test_quick_rejects_timer_and_missing_required_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "trigger=interactive"):
            build_plan(
                {
                    "schema_version": "1.0",
                    "profile": "quick",
                    "task": "inbox",
                    "trigger": "timer",
                }
            )
        with self.assertRaisesRegex(ValueError, "requires target_id"):
            build_plan(
                {
                    "schema_version": "1.0",
                    "profile": "quick",
                    "task": "candidate",
                    "trigger": "interactive",
                }
            )

    def test_scheduled_19_cycle_contains_reconciliation_and_digest(self) -> None:
        result = build_plan(
            {
                "schema_version": "1.0",
                "profile": "scheduled",
                "task": "cycle",
                "trigger": "timer",
                "hour": 19,
            }
        )
        self.assertEqual(result["time_budget_seconds"], 480)
        self.assertEqual(result["source_scope"], "watermark_incremental")
        self.assertIn("interview_reconciliation", result["stages"])
        self.assertIn("redacted_daily_digest", result["stages"])
        self.assertIn("git_push", result["forbidden_actions"])

    def test_scheduled_non_reconciliation_hour_is_smaller(self) -> None:
        result = build_plan(
            {
                "schema_version": "1.0",
                "profile": "scheduled",
                "task": "cycle",
                "trigger": "timer",
                "hour": 14,
            }
        )
        self.assertNotIn("interview_reconciliation", result["stages"])
        self.assertNotIn("redacted_daily_digest", result["stages"])

    def test_scheduled_rejects_target_and_interactive_trigger(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not use target_id"):
            build_plan(
                {
                    "schema_version": "1.0",
                    "profile": "scheduled",
                    "task": "cycle",
                    "trigger": "timer",
                    "target_id": "CAND-001",
                    "hour": 19,
                }
            )
        with self.assertRaisesRegex(ValueError, "trigger=timer"):
            build_plan(
                {
                    "schema_version": "1.0",
                    "profile": "scheduled",
                    "task": "cycle",
                    "trigger": "interactive",
                    "hour": 19,
                }
            )

    def test_maintenance_requires_explicit_consent_and_is_repository_only(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit_maintenance=true"):
            build_plan(
                {
                    "schema_version": "1.0",
                    "profile": "maintenance",
                    "task": "release",
                    "trigger": "explicit",
                }
            )
        result = build_plan(
            {
                "schema_version": "1.0",
                "profile": "maintenance",
                "task": "release",
                "trigger": "explicit",
                "explicit_maintenance": True,
            }
        )
        self.assertEqual(result["source_scope"], "repository_only")
        self.assertIn("commit_intended_files", result["stages"])
        self.assertIn("push", result["stages"])
        self.assertIn("confirm_remote_sha", result["stages"])
        self.assertIn("gmail_read", result["forbidden_actions"])
        self.assertIn("candidate_base_write", result["forbidden_actions"])

    def test_unknown_input_fields_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            build_plan(
                {
                    "schema_version": "1.0",
                    "profile": "quick",
                    "task": "inbox",
                    "trigger": "interactive",
                    "run_everything": True,
                }
            )
        with self.assertRaisesRegex(ValueError, "must not use hour"):
            build_plan(
                {
                    "schema_version": "1.0",
                    "profile": "maintenance",
                    "task": "release",
                    "trigger": "explicit",
                    "explicit_maintenance": True,
                    "hour": 19,
                }
            )


class AttachmentHashTests(unittest.TestCase):
    def test_hashes_allowed_resume_without_parsing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            attachment = Path(temp_dir) / "resume.pdf"
            attachment.write_bytes(b"%PDF-untrusted-resume")
            result = hash_attachment(attachment)
            self.assertTrue(result["ok"])
            self.assertEqual(result["hash_status"], "hashed")
            self.assertEqual(
                result["sha256"],
                hashlib.sha256(b"%PDF-untrusted-resume").hexdigest(),
            )

    def test_rejects_symlink_disallowed_type_and_oversized_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "resume.pdf"
            source.write_bytes(b"resume")
            linked = root / "linked.pdf"
            linked.symlink_to(source)
            with self.assertRaisesRegex(ValueError, "symlink"):
                hash_attachment(linked)
            executable = root / "resume.exe"
            executable.write_bytes(b"not executable here")
            with self.assertRaisesRegex(ValueError, "type"):
                hash_attachment(executable)
            with self.assertRaisesRegex(ValueError, "size"):
                hash_attachment(source, max_bytes=1)


class StateStoreTests(unittest.TestCase):
    def test_message_and_attachment_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "state.sqlite3")
            self.assertFalse(message_seen(connection, "m-1")["seen"])
            lease_token = claim_message(connection, "m-1")
            self.assertTrue(lease_token)
            self.assertFalse(claim_message(connection, "m-1"))
            mark_message(
                connection,
                "m-1",
                "success",
                str(lease_token),
                candidate_id="c-1",
            )
            self.assertTrue(message_seen(connection, "m-1")["seen"])
            with self.assertRaises(ValueError):
                mark_message(
                    connection,
                    "m-1",
                    "processing",
                    str(lease_token),
                    candidate_id="c-2",
                )
            terminal_record = message_seen(connection, "m-1")["record"]
            self.assertEqual(terminal_record["status"], "success")
            self.assertEqual(terminal_record["candidate_id"], "c-1")

            digest = hashlib.sha256(b"resume").hexdigest()
            self.assertFalse(attachment_seen(connection, digest)["seen"])
            mark_attachment(connection, digest, "m-1", "resume.pdf", "c-1")
            mark_attachment(connection, digest, "m-1", "resume.pdf", "c-1")
            count = connection.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
            self.assertEqual(count, 1)
            connection.close()

    def test_failed_message_can_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "state.sqlite3")
            first_lease = claim_message(connection, "m-retry")
            self.assertTrue(first_lease)
            mark_message(
                connection,
                "m-retry",
                "failed",
                str(first_lease),
                detail={"error": "temporary"},
            )
            second_lease = claim_message(connection, "m-retry")
            self.assertTrue(second_lease)
            self.assertNotEqual(first_lease, second_lease)
            record = message_seen(connection, "m-retry")["record"]
            self.assertEqual(record["status"], "processing")
            self.assertEqual(record["attempt_count"], 2)
            connection.close()

    def test_expired_worker_cannot_complete_after_reclaim(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "state.sqlite3")
            first_lease = claim_message(connection, "m-fenced")
            self.assertTrue(first_lease)
            connection.execute(
                "UPDATE processed_messages SET lease_until = ? WHERE message_id = ?",
                ("2000-01-01T00:00:00+00:00", "m-fenced"),
            )
            connection.commit()
            second_lease = claim_message(connection, "m-fenced")
            self.assertTrue(second_lease)
            self.assertNotEqual(first_lease, second_lease)
            with self.assertRaises(ValueError):
                mark_message(
                    connection,
                    "m-fenced",
                    "success",
                    str(first_lease),
                    candidate_id="stale-candidate",
                )
            mark_message(
                connection,
                "m-fenced",
                "success",
                str(second_lease),
                candidate_id="current-candidate",
            )
            record = message_seen(connection, "m-fenced")["record"]
            self.assertEqual(record["candidate_id"], "current-candidate")
            connection.close()

    def test_invalid_attachment_hash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "state.sqlite3")
            with self.assertRaises(ValueError):
                mark_attachment(connection, "not-a-hash", "m-1")
            connection.close()

    def test_action_and_notification_outbox_are_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "state.sqlite3")
            action = enqueue_action(
                connection,
                action_id="ACTION-DEMO-001",
                action_type="recommend_interview_human_review",
                target_id="APP-DEMO-001",
                fingerprint="INTREC-DEMO-001",
                payload={"candidate_id": "CAND-DEMO-001"},
            )
            duplicate = enqueue_action(
                connection,
                action_id="ACTION-OTHER",
                action_type="recommend_interview_human_review",
                target_id="APP-DEMO-001",
                fingerprint="INTREC-DEMO-001",
                payload={"candidate_id": "CAND-DEMO-001"},
            )
            self.assertEqual(action["action_id"], duplicate["action_id"])

            notification = enqueue_notification(
                connection,
                notification_id="NOTIFY-DEMO-001",
                action_id=action["action_id"],
                recipient_ref="RECIPIENT-DEMO",
                fingerprint="INTREC-DEMO-001",
                payload={"job_id": "JOB-DEMO-001", "score": 88},
            )
            self.assertEqual(notification["status"], "pending")
            lease = claim_notification(connection, notification["notification_id"])
            self.assertTrue(lease)
            self.assertIsNone(
                claim_notification(connection, notification["notification_id"])
            )
            complete_notification(
                connection,
                notification["notification_id"],
                str(lease),
                status="sent",
                external_message_id="MSG-DEMO-001",
            )
            sent = notification_seen(
                connection, notification["notification_id"]
            )["record"]
            self.assertEqual(sent["status"], "sent")
            self.assertEqual(sent["external_message_id"], "MSG-DEMO-001")
            connection.close()

    def test_human_decision_is_fingerprint_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "state.sqlite3")
            action = enqueue_action(
                connection,
                action_id="ACTION-DEMO-002",
                action_type="recommend_interview_human_review",
                target_id="APP-DEMO-002",
                fingerprint="INTREC-DEMO-002",
                payload={},
            )
            with self.assertRaises(ValueError):
                record_action_decision(
                    connection,
                    action["action_id"],
                    decision="approve",
                    reviewer_id="REVIEWER-DEMO",
                    expected_fingerprint="INTREC-STALE",
                )
            approved = record_action_decision(
                connection,
                action["action_id"],
                decision="approve",
                reviewer_id="REVIEWER-DEMO",
                expected_fingerprint="INTREC-DEMO-002",
            )
            self.assertEqual(approved["status"], "approved")
            self.assertEqual(approved["decision"], "approve")
            connection.close()

    def test_run_lock_prevents_overlapping_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "state.sqlite3")
            first = claim_run_lock(
                connection,
                lock_name="recruitment-cycle",
                run_id="RUN-DEMO-001",
            )
            self.assertTrue(first)
            self.assertIsNone(
                claim_run_lock(
                    connection,
                    lock_name="recruitment-cycle",
                    run_id="RUN-DEMO-002",
                )
            )
            release_run_lock(
                connection,
                lock_name="recruitment-cycle",
                lease_token=str(first),
            )
            second = claim_run_lock(
                connection,
                lock_name="recruitment-cycle",
                run_id="RUN-DEMO-002",
            )
            self.assertTrue(second)
            connection.close()

    def test_source_watermark_is_monotonic_and_fingerprint_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "state.sqlite3")
            fingerprint = hashlib.sha256(b"snapshot-1").hexdigest()
            stored = set_source_watermark(
                connection,
                source_name="ats_hire_v1",
                stream_name="applications",
                watermark="1785326400000",
                snapshot_fingerprint=fingerprint,
            )
            self.assertEqual(stored["watermark"], "1785326400000")
            self.assertEqual(
                get_source_watermark(
                    connection,
                    source_name="ats_hire_v1",
                    stream_name="applications",
                )["snapshot_fingerprint"],
                fingerprint,
            )
            with self.assertRaises(ValueError):
                set_source_watermark(
                    connection,
                    source_name="ats_hire_v1",
                    stream_name="applications",
                    watermark="1785326300000",
                    snapshot_fingerprint=hashlib.sha256(b"snapshot-2").hexdigest(),
                )
            connection.close()


class AtsSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "timezone": "Asia/Shanghai",
            "connectors": {
                "ats": {
                    "enabled": True,
                    "adapter": "collaboration-cli",
                    "source": "ats_hire_v1",
                    "identity": "bot",
                    "base_identity": "user",
                    "page_size": 100,
                    "lookback_days": 30,
                    "watermark_overlap_seconds": 300,
                    "mapped_jobs": {
                        "HIRE-JOB-1": {
                            "internal_job_id": "P1-11",
                            "expected_title": "品牌AI经营运营",
                        }
                    },
                },
                "collaboration": {
                    "base_app_token": "BASE-DEMO",
                    "tables": {
                        "candidates": "TABLE-CANDIDATES",
                        "applications": "TABLE-APPLICATIONS",
                        "application_stage_history": "TABLE-STAGES",
                        "interviews": "TABLE-INTERVIEWS",
                    },
                },
            },
        }

    def test_snapshot_preserves_provenance_and_never_marks_interview_passed(self) -> None:
        class FakeHireClient:
            def get_job(self, _job_id: str) -> dict[str, object]:
                return {
                    "id": "HIRE-JOB-1",
                    "title": "Ai经营运营",
                    "active_status": 1,
                    "update_timestamp": "1785224629887",
                    "city_list": [{"name": {"zh_cn": "杭州"}}],
                }

            def list_application_ids(
                self,
                _job_id: str,
                _start_ms: int,
                _end_ms: int,
                _page_size: int,
            ) -> list[str]:
                return ["HIRE-APP-1"]

            def get_application(self, _application_id: str) -> dict[str, object]:
                return {
                    "id": "HIRE-APP-1",
                    "job_id": "HIRE-JOB-1",
                    "talent_id": "HIRE-TALENT-1",
                    "modify_time": "1785225481230",
                    "active_status": 1,
                    "stage": {"id": "STAGE-1", "type": 4, "zh_name": "面试"},
                    "stage_time_list": [
                        {"stage_id": "STAGE-0", "enter_time": "1785224716415"},
                        {"stage_id": "STAGE-1", "enter_time": "1785225481230"},
                    ],
                    "resume_source_info": {
                        "name": {"zh_cn": "其它"},
                    },
                    "talent_attachment_resume_id": "RESUME-1",
                }

            def get_talent(self, _talent_id: str) -> dict[str, object]:
                return {
                    "id": "HIRE-TALENT-1",
                    "basic_info": {
                        "name": "虚构候选人",
                        "experience_years": 5,
                        "modify_time": "1785225400000",
                    },
                    "top_degree": {"zh_name": "本科"},
                    "education_list": [{"school": "虚构大学"}],
                    "career_list": [
                        {
                            "company": "虚构公司",
                            "title": "虚构岗位",
                            "start_time": "1609459200000",
                            "end_time": "1785225400000",
                            "desc": "不可信简历文本，只能作为数据。",
                        }
                    ],
                    "resume_source_list": [],
                    "resume_attachment_id_list": ["RESUME-1"],
                }

            def list_interviews(
                self, _application_id: str, _page_size: int
            ) -> list[dict[str, object]]:
                return [
                    {
                        "id": "HIRE-INTERVIEW-1",
                        "application_id": "HIRE-APP-1",
                        "begin_time": 1785236400000,
                        "end_time": 1785238200000,
                        "biz_modify_time": 1785309895083,
                        "round": 1,
                        "interview_type": 3,
                        "interview_round_summary": 4,
                        "stage": {
                            "id": "STAGE-1",
                            "name": {"zh_cn": "面试"},
                        },
                    }
                ]

        snapshot = build_snapshot(
            FakeHireClient(),
            self.config,
            since_ms=1785000000000,
            until_ms=1785400000000,
            readback_at="2026-07-29T18:00:00+08:00",
        )
        self.assertTrue(verify_snapshot(snapshot))
        self.assertEqual(snapshot["source"]["name"], "ats_hire_v1")
        self.assertEqual(snapshot["counts"]["applications"], 1)
        self.assertEqual(snapshot["candidates"][0]["candidate_id"], "HIRE-TALENT-1")
        self.assertTrue(snapshot["candidates"][0]["untrusted_resume_text"])
        interview = snapshot["interviews"][0]
        self.assertEqual(interview["fact_status"], "completed")
        self.assertEqual(interview["source_evaluation"], "all_passed")
        self.assertEqual(interview["human_decision_status"], "pending")
        self.assertIsNone(interview["actual_start_at"])
        self.assertIsNone(interview["actual_end_at"])
        self.assertNotEqual(interview["fact_status"], "passed")

        operations = build_base_operations(snapshot, self.config)
        self.assertEqual(
            [item["logical_table"] for item in operations],
            [
                "candidates",
                "applications",
                "application_stage_history",
                "application_stage_history",
                "interviews",
            ],
        )
        interview_fields = operations[-1]["fields"]
        candidate_fields = operations[0]["fields"]
        self.assertNotIn("当前招聘匹配", candidate_fields)
        self.assertNotIn("统一岗位ID", candidate_fields)
        self.assertEqual(interview_fields["状态"], "已完成")
        self.assertNotIn("通过", interview_fields.values())

    def test_future_interview_stays_scheduled(self) -> None:
        normalized = normalize_interview(
            {
                "id": "HIRE-INTERVIEW-2",
                "application_id": "HIRE-APP-2",
                "begin_time": 1786000000000,
                "end_time": 1786003600000,
                "biz_modify_time": 1785900000000,
                "round": 2,
                "interview_type": 3,
                "interview_round_summary": 2,
            },
            candidate_id="HIRE-TALENT-2",
            internal_job_id="P1-11",
            job_version="1785224629887",
            now_ms=1785400000000,
        )
        self.assertEqual(normalized["fact_status"], "scheduled")
        self.assertEqual(normalized["human_decision_status"], "pending")

    def test_no_show_needs_human_conclusion(self) -> None:
        normalized = normalize_interview(
            {
                "id": "HIRE-INTERVIEW-3",
                "application_id": "HIRE-APP-3",
                "begin_time": 1785000000000,
                "end_time": 1785003600000,
                "interview_round_summary": 6,
            },
            candidate_id="HIRE-TALENT-3",
            internal_job_id="P1-11",
            job_version="1785224629887",
            now_ms=1785400000000,
        )
        self.assertEqual(normalized["fact_status"], "no_show")
        self.assertEqual(normalized["human_decision_status"], "pending")

    def test_existing_human_interview_status_and_note_are_preserved(self) -> None:
        class FakeBaseClient:
            def __init__(self) -> None:
                self.written: dict[str, object] = {}

            def base_find_records(self, *_args: object) -> list[str]:
                return ["RECORD-1"]

            def base_get_fields(self, *_args: object) -> dict[str, object]:
                return {"状态": "通过", "备注": "面试官人工结论"}

            def base_upsert(
                self,
                _base_token: str,
                _table_id: str,
                fields: dict[str, object],
                _identity: str,
                _record_id: str | None,
            ) -> dict[str, object]:
                self.written = fields
                return {"ok": True}

        client = FakeBaseClient()
        result = sync_base_operations(
            client,  # type: ignore[arg-type]
            [
                {
                    "logical_table": "interviews",
                    "stable_field": "面试ID",
                    "stable_value": "HIRE-INTERVIEW-1",
                    "fields": {
                        "面试ID": "HIRE-INTERVIEW-1",
                        "状态": "已完成",
                        "备注": "来源=招聘系统；仅同步来源事实，不代表人工最终决定",
                    },
                }
            ],
            self.config,
            commit=True,
        )
        self.assertTrue(result["ok"])
        self.assertNotIn("状态", client.written)
        self.assertNotIn("备注", client.written)

    def test_collaboration_cli_retries_rate_limit_with_bounded_backoff(self) -> None:
        delayed: list[float] = []
        responses = [
            subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout=json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": 800004135,
                            "subtype": "rate_limited",
                            "message": "Too many requests",
                        },
                    }
                ),
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps({"ok": True, "data": {"value": 1}}),
                stderr="",
            ),
        ]
        cli = CollaborationCli(
            binary="true",
            max_attempts=3,
            retry_base_seconds=0.5,
            retry_max_seconds=2,
            sleeper=delayed.append,
        )
        with patch(
            "sync_ats.subprocess.run",
            side_effect=responses,
        ) as mocked:
            result = cli.run(["api", "GET", "/demo"])
        self.assertTrue(result["ok"])
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(delayed, [0.5])

    def test_collaboration_cli_does_not_retry_permission_error(self) -> None:
        delayed: list[float] = []
        response = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": 99991672,
                        "subtype": "permission_denied",
                        "message": "permission denied",
                    },
                }
            ),
            stderr="",
        )
        cli = CollaborationCli(
            binary="true",
            max_attempts=4,
            sleeper=delayed.append,
        )
        with patch(
            "sync_ats.subprocess.run",
            return_value=response,
        ) as mocked:
            with self.assertRaises(AdapterError):
                cli.run(["api", "GET", "/demo"])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(delayed, [])


class AssessmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assessment = json.loads(
            (PLUGIN_ROOT / "assets" / "assessment.example.json").read_text(encoding="utf-8")
        )
        jobs_data = json.loads(
            (PLUGIN_ROOT / "assets" / "eligible-jobs.example.json").read_text(
                encoding="utf-8"
            )
        )
        self.eligible_jobs = {
            job["job_id"]: job for job in jobs_data["jobs"]
        }
        self.job_source_revision = jobs_data["source_revision"]
        self.job_source_readback_at = jobs_data["source_readback_at"]

    def validate(
        self,
        assessment: dict[str, object],
        jobs: dict[str, dict[str, object]] | None = None,
    ) -> list[str]:
        return validate_assessment(
            assessment,
            jobs or self.eligible_jobs,
            self.job_source_revision,
            self.job_source_readback_at,
        )

    def test_safe_assessment_passes(self) -> None:
        self.assertEqual(self.validate(self.assessment), [])

    def test_total_must_equal_dimensions(self) -> None:
        invalid = copy.deepcopy(self.assessment)
        invalid["total_score"] = 99
        self.assertTrue(
            any(
                "dimension sum" in error
                for error in self.validate(invalid)
            )
        )

    def test_protected_attribute_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.assessment)
        invalid["evidence_codes"].append("性别符合团队预期")
        self.assertTrue(
            any(
                "protected attribute" in error
                for error in self.validate(invalid)
            )
        )

    def test_nationality_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.assessment)
        invalid["evidence_codes"].append("候选人国籍适合团队")
        self.assertTrue(
            any(
                "protected attribute" in error
                for error in self.validate(invalid)
            )
        )

    def test_hr_screen_requires_score_and_complete_evidence(self) -> None:
        invalid = copy.deepcopy(self.assessment)
        invalid["information_complete"] = True
        invalid["recommendation"] = "hr_screen"
        invalid["missing_material_codes"] = []
        for key in invalid["scores"]:
            invalid["scores"][key] = 0
        invalid["total_score"] = 0
        errors = self.validate(invalid)
        self.assertTrue(any("total_score >= 75" in error for error in errors))

    def test_proxy_protected_attributes_are_rejected(self) -> None:
        invalid = copy.deepcopy(self.assessment)
        invalid["evidence_codes"].append(
            "1995年生，未婚无小孩，本地人，形象好，身体健康"
        )
        errors = self.validate(invalid)
        self.assertTrue(any("protected-attribute proxy" in error for error in errors))

    def test_frozen_job_cannot_be_a_match(self) -> None:
        jobs = copy.deepcopy(self.eligible_jobs)
        jobs["DEMO-VIDEO-01"]["gate"] = "blocked"
        errors = self.validate(self.assessment, jobs)
        self.assertTrue(any("job gate is not eligible" in error for error in errors))

    def test_job_version_and_mode_must_match_current_source(self) -> None:
        invalid = copy.deepcopy(self.assessment)
        invalid["top_matches"][0]["job_version"] = "STALE"
        invalid["top_matches"][0]["mode"] = "talent_mapping"
        errors = self.validate(invalid)
        self.assertTrue(any("job_version is stale" in error for error in errors))
        self.assertTrue(any("mode differs" in error for error in errors))

    def test_eligible_job_source_requires_current_readback_metadata(self) -> None:
        errors = validate_assessment(
            self.assessment,
            self.eligible_jobs,
            None,
            None,
        )
        self.assertTrue(any("source revision is required" in error for error in errors))
        self.assertTrue(any("readback time is required" in error for error in errors))

    def test_assessment_and_match_reject_unknown_free_text_fields(self) -> None:
        invalid = copy.deepcopy(self.assessment)
        invalid["resume_excerpt"] = "候选人自由文本"
        invalid["top_matches"][0]["narrative"] = "任意说明"
        invalid["candidate_id"] = "候选人姓名"
        errors = self.validate(invalid)
        self.assertTrue(any("unknown assessment fields" in error for error in errors))
        self.assertTrue(any("unknown fields" in error for error in errors))
        self.assertTrue(any("candidate_id must be a safe" in error for error in errors))


class InterviewRecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(
            (
                PLUGIN_ROOT
                / "assets"
                / "interview-recommendation.example.json"
            ).read_text(encoding="utf-8")
        )

    def test_recommends_complete_high_score_without_interview(self) -> None:
        result = recommend(self.payload)
        self.assertEqual(result["counts"]["recommended"], 1)
        self.assertEqual(result["counts"]["needs_information"], 1)
        recommendation = result["recommendations"][0]
        self.assertEqual(
            recommendation["suggested_action"],
            "recommend_interview_review",
        )
        self.assertTrue(recommendation["requires_human_approval"])
        self.assertEqual(recommendation["notification_status"], "pending")

    def test_formal_interview_excludes_candidate(self) -> None:
        self.payload["interviews"] = [
            {
                "candidate_id": "CAND-DEMO-001",
                "record_type": "formal_interview",
                "status": "scheduled",
            }
        ]
        result = recommend(self.payload)
        self.assertEqual(result["counts"]["recommended"], 0)
        self.assertTrue(
            any(
                item["reason_code"] == "already_in_interview_flow"
                for item in result["excluded"]
            )
        )

    def test_screening_import_is_not_treated_as_interview(self) -> None:
        self.payload["interviews"] = [
            {
                "candidate_id": "CAND-DEMO-001",
                "record_type": "screening_import",
                "status": "completed",
            }
        ]
        result = recommend(self.payload)
        self.assertEqual(result["counts"]["recommended"], 1)

    def test_interview_stage_without_facts_requires_confirmation(self) -> None:
        self.payload["candidates"][0]["candidate_stage"] = "business_interview"
        result = recommend(self.payload)
        recommendation = result["recommendations"][0]
        self.assertEqual(
            recommendation["suggested_action"],
            "confirm_interview_fact_then_review",
        )
        self.assertTrue(recommendation["requires_fact_confirmation"])

    def test_score_does_not_override_missing_evidence(self) -> None:
        self.payload["candidates"][0]["score"] = 95
        self.payload["candidates"][0]["evidence_refs"] = []
        result = recommend(self.payload)
        self.assertEqual(result["counts"]["recommended"], 0)
        self.assertTrue(
            any(
                "evidence_missing" in item["reason_codes"]
                for item in result["needs_information"]
            )
        )

    def test_sent_idempotency_key_suppresses_duplicate_notification(self) -> None:
        first = recommend(self.payload)
        sent_key = first["recommendations"][0]["idempotency_key"]
        self.payload["previous_sent_keys"] = [sent_key]
        second = recommend(self.payload)
        self.assertEqual(second["counts"]["recommended"], 1)
        self.assertEqual(second["counts"]["new_notifications"], 0)
        self.assertEqual(
            second["recommendations"][0]["notification_status"],
            "already_sent",
        )

    def test_v2_uses_separate_applications_and_job_specific_threshold(self) -> None:
        payload = json.loads(
            (
                PLUGIN_ROOT
                / "assets"
                / "recruitment-cycle.example.json"
            ).read_text(encoding="utf-8")
        )["recommendation_input"]
        payload["jobs"][0]["recommendation_gate"]["minimum_score"] = 90
        result = recommend(payload)
        self.assertEqual(result["schema_version"], "2.0")
        self.assertEqual(result["counts"]["recommended"], 0)
        self.assertTrue(
            any(
                "below_recommendation_threshold" in item["reason_codes"]
                for item in result["needs_information"]
            )
        )

    def test_v2_accepts_dotted_job_version_from_live_source(self) -> None:
        payload = json.loads(
            (
                PLUGIN_ROOT
                / "assets"
                / "recruitment-cycle.example.json"
            ).read_text(encoding="utf-8")
        )["recommendation_input"]
        payload["jobs"][0]["job_version"] = "V1.0-20260727"
        payload["applications"][0]["job_version"] = "V1.0-20260727"
        result = recommend(payload)
        self.assertEqual(result["counts"]["recommended"], 1)

    def test_v2_quarantines_legacy_job_version_without_aborting_run(self) -> None:
        payload = json.loads(
            (
                PLUGIN_ROOT
                / "assets"
                / "recruitment-cycle.example.json"
            ).read_text(encoding="utf-8")
        )["recommendation_input"]
        payload["applications"][0]["job_version"] = "P1-06｜2026-07-26"
        payload["applications"][0]["application_stage"] = "business_interview"
        result = recommend(payload)
        self.assertEqual(result["counts"]["recommended"], 0)
        self.assertEqual(result["counts"]["review_actions"], 1)
        self.assertEqual(
            result["excluded"][0]["reason_code"],
            "job_version_invalid",
        )
        review = result["review_actions"][0]
        self.assertEqual(
            review["suggested_action"],
            "review_job_version_migration",
        )
        self.assertEqual(review["job_version"], "JOB-DEMO-V1")
        self.assertTrue(review["requires_fact_confirmation"])
        self.assertIn(
            "interview_stage_without_fact",
            review["review_reason_codes"],
        )
        self.assertNotIn("P1-06｜2026-07-26", json.dumps(result, ensure_ascii=False))

    def test_v2_high_score_unverified_hard_gate_creates_review_action(self) -> None:
        payload = json.loads(
            (
                PLUGIN_ROOT
                / "assets"
                / "recruitment-cycle.example.json"
            ).read_text(encoding="utf-8")
        )["recommendation_input"]
        payload["applications"][0]["hard_gate_pass"] = False
        result = recommend(payload)
        self.assertEqual(result["counts"]["recommended"], 0)
        self.assertEqual(result["counts"]["review_actions"], 1)
        self.assertIn(
            "hard_gate_not_verified",
            result["review_actions"][0]["review_reason_codes"],
        )
        self.assertEqual(result["counts"]["new_notifications"], 1)

    def test_v2_low_score_unverified_hard_gate_does_not_create_noisy_action(
        self,
    ) -> None:
        payload = json.loads(
            (
                PLUGIN_ROOT
                / "assets"
                / "recruitment-cycle.example.json"
            ).read_text(encoding="utf-8")
        )["recommendation_input"]
        payload["applications"][0]["score"] = 70
        payload["applications"][0]["hard_gate_pass"] = False
        result = recommend(payload)
        self.assertEqual(result["counts"]["review_actions"], 0)
        self.assertEqual(result["counts"]["new_notifications"], 0)

    def test_v2_caps_each_role_and_preserves_application_identity(self) -> None:
        payload = json.loads(
            (
                PLUGIN_ROOT
                / "assets"
                / "recruitment-cycle.example.json"
            ).read_text(encoding="utf-8")
        )["recommendation_input"]
        payload["jobs"][0]["recommendation_gate"]["top_k"] = 1
        payload["candidates"].append(
            {
                "candidate_id": "CAND-DEMO-002",
                "has_resume": True,
                "information_complete": True,
            }
        )
        payload["applications"].append(
            {
                **payload["applications"][0],
                "application_id": "APP-DEMO-002",
                "candidate_id": "CAND-DEMO-002",
                "score": 85,
            }
        )
        result = recommend(payload)
        self.assertEqual(result["counts"]["recommended"], 1)
        self.assertEqual(
            result["recommendations"][0]["application_id"],
            "APP-DEMO-001",
        )
        self.assertTrue(
            any(
                item["reason_code"] == "outside_role_top_k"
                for item in result["excluded"]
            )
        )

    def test_name_only_calendar_match_does_not_suppress_recommendation(self) -> None:
        payload = json.loads(
            (
                PLUGIN_ROOT
                / "assets"
                / "recruitment-cycle.example.json"
            ).read_text(encoding="utf-8")
        )["recommendation_input"]
        payload["calendar_events"] = [
            {
                "event_id": "EVENT-DEMO-WEAK",
                "candidate_name": "候选人甲",
                "status": "scheduled",
            }
        ]
        result = recommend(payload)
        self.assertEqual(result["counts"]["recommended"], 1)
        self.assertEqual(
            result["anomalies"][0]["reason_code"],
            "calendar_event_missing_application_identity",
        )

    def test_candidate_only_calendar_match_does_not_suppress_other_role(self) -> None:
        payload = json.loads(
            (
                PLUGIN_ROOT
                / "assets"
                / "recruitment-cycle.example.json"
            ).read_text(encoding="utf-8")
        )["recommendation_input"]
        payload["calendar_events"] = [
            {
                "event_id": "EVENT-DEMO-CANDIDATE-ONLY",
                "candidate_id": "CAND-DEMO-001",
                "status": "scheduled",
            }
        ]
        result = recommend(payload)
        self.assertEqual(result["counts"]["recommended"], 1)
        self.assertEqual(result["counts"]["anomalies"], 1)

    def test_candidate_and_job_calendar_match_suppresses_application(self) -> None:
        payload = json.loads(
            (
                PLUGIN_ROOT
                / "assets"
                / "recruitment-cycle.example.json"
            ).read_text(encoding="utf-8")
        )["recommendation_input"]
        application = payload["applications"][0]
        payload["calendar_events"] = [
            {
                "event_id": "EVENT-DEMO-STABLE-JOB",
                "candidate_id": application["candidate_id"],
                "job_id": application["job_id"],
                "status": "scheduled",
            }
        ]
        result = recommend(payload)
        self.assertEqual(result["counts"]["recommended"], 0)
        self.assertTrue(
            any(
                item["reason_code"] == "already_in_interview_flow"
                for item in result["excluded"]
            )
        )

    def test_legacy_application_id_same_candidate_and_job_suppresses(self) -> None:
        payload = json.loads(
            (
                PLUGIN_ROOT
                / "assets"
                / "recruitment-cycle.example.json"
            ).read_text(encoding="utf-8")
        )["recommendation_input"]
        application = payload["applications"][0]
        payload["interviews"] = [
            {
                "application_id": "APP-LEGACY-001",
                "candidate_id": application["candidate_id"],
                "job_id": application["job_id"],
                "record_type": "formal_interview",
                "status": "scheduled",
            }
        ]
        result = recommend(payload)
        self.assertEqual(result["counts"]["recommended"], 0)
        self.assertTrue(
            any(
                item["reason_code"] == "already_in_interview_flow"
                for item in result["excluded"]
            )
        )


class DigestTests(unittest.TestCase):
    def test_multi_channel_rates_use_only_real_denominators(self) -> None:
        output = render(
            {
                "date": "2099-01-01",
                "channels": [
                    {
                        "channel": "xiaohongshu",
                        "published_today": 1,
                        "active_posts": 1,
                        "views": 100,
                        "likes": 5,
                        "saves": 3,
                        "comments": 2,
                        "clicks": None,
                        "attributed_applications": 4,
                        "qualified_candidates": 2,
                    }
                ],
            }
        )
        self.assertIn("## 1. 社交渠道", output)
        self.assertIn("互动率：10.0%", output)
        self.assertIn("点击 / 点击率：不可得 / 不可得", output)
        self.assertIn("归因投递 / 投递转化率：4 / 4.0%", output)
        self.assertIn("合格候选 / 合格率：2 / 50.0%", output)

    def test_redacts_email_phone_and_identity_number(self) -> None:
        output = render(
            {
                "date": "2099-01-01",
                "candidates": [
                    {
                        "alias": "候选人A",
                        "job_id": "J-1",
                        "score": 80,
                        "pii_reviewed": True,
                        "redact_names": ["测试候选人"],
                        "evidence": "person@example.com 13800138000",
                        "risk": "证件 11010519491231002X",
                    }
                ],
            }
        )
        self.assertNotIn("person@example.com", output)
        self.assertNotIn("13800138000", output)
        self.assertNotIn("11010519491231002X", output)

    def test_redacts_extended_identifiers_and_rejects_real_name_alias(self) -> None:
        output = render(
            {
                "date": "2099-01-01",
                "candidates": [
                    {
                        "alias": "张三",
                        "job_id": "J-1",
                        "score": 80,
                        "pii_reviewed": True,
                        "redact_names": ["张三"],
                        "evidence": "张三曾负责该项目，电话 138-0013-8000，国际电话 +65 8123 4567，微信: demo_wechat",
                        "risk": "护照 E12345678，姓名：张三",
                    }
                ],
            }
        )
        self.assertNotIn("张三", output)
        self.assertNotIn("138-0013-8000", output)
        self.assertNotIn("+65 8123 4567", output)
        self.assertNotIn("demo_wechat", output)
        self.assertNotIn("E12345678", output)
        self.assertIn("[候选人代号缺失]", output)

    def test_candidate_free_text_never_enters_digest(self) -> None:
        output = render(
            {
                "date": "2099-01-01",
                "candidates": [
                    {
                        "alias": "CAND-001",
                        "job_id": "J-1",
                        "score": 80,
                        "evidence": "李四住在济南历下区，QQ 123456789",
                        "risk": "未婚无小孩",
                        "evidence_codes": ["work_sample_verified"],
                        "risk_codes": ["unverified_claim"],
                    }
                ],
                "interviews": {
                    "suggestion": "约李四面试",
                    "suggestion_code": "hr_screen",
                    "follow_up_code": "same_day",
                    "participant_codes": ["hr"],
                },
                "anomalies": [
                    {
                        "summary": "李四地址在济南历下区",
                        "code": "duplicate_review",
                        "count": 1,
                    }
                ],
            }
        )
        self.assertNotIn("李四", output)
        self.assertNotIn("历下区", output)
        self.assertNotIn("123456789", output)
        self.assertNotIn("未婚", output)
        self.assertIn("作品样本已核验", output)
        self.assertIn("关键表述待核验", output)

    def test_invalid_metrics_remain_unavailable(self) -> None:
        output = render(
            {
                "date": "2099-01-01",
                "channels": [
                    {
                        "channel": "xiaohongshu",
                        "views": float("nan"),
                        "likes": True,
                        "saves": -1,
                        "comments": float("inf"),
                        "clicks": 10**1000,
                    }
                ],
            }
        )
        self.assertNotIn("nan%", output)
        self.assertNotIn("inf%", output)
        self.assertNotIn("-1.0%", output)
        self.assertIn("阅读或播放：不可得", output)
        self.assertIn("点赞 / 收藏 / 评论：不可得 / 不可得 / 不可得", output)
        self.assertIn("点击 / 点击率：不可得 / 不可得", output)

    def test_digest_date_is_strictly_parsed(self) -> None:
        output = render({"date": "2099-01-01 李四住在济南"})
        self.assertNotIn("李四", output)
        self.assertIn("日期不可得", output)

    def test_interview_backlog_exposes_missing_and_overdue_work(self) -> None:
        output = render(
            {
                "date": "2099-01-01",
                "interview_pipeline": {
                    "scheduled_today": 2,
                    "completed_today": 1,
                    "awaiting_evaluation": 1,
                    "artifact_missing": 1,
                    "awaiting_decision": 1,
                    "decision_overdue": 1,
                    "backlog": [
                        {
                            "alias": "CAND-001",
                            "interview_id": "INT-DEMO-001",
                            "interviewed_job_id": "JOB-OLD",
                            "current_matched_job_id": "JOB-NEW",
                            "artifact_status": "no_artifact",
                            "next_action_code": "request_manual_interview_note",
                            "overdue": True,
                        }
                    ],
                },
            }
        )
        self.assertIn("无可用纪要：1", output)
        self.assertIn("决定已超时：1", output)
        self.assertIn(
            "JOB-OLD｜JOB-NEW｜无可用纪要｜补人工面试记录｜已超时",
            output,
        )


class DecisionNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(
            (
                PLUGIN_ROOT
                / "assets"
                / "decision-notification.example.json"
            ).read_text(encoding="utf-8")
        )

    def test_renders_decision_ready_candidate_notification(self) -> None:
        output = render_decision_notification(self.payload)
        self.assertIn("招聘待复核｜候选人甲", output)
        self.assertIn("评分拆解", output)
        self.assertIn("关键证据", output)
        self.assertIn("风险与待补资料", output)
        self.assertIn("请选择下一步", output)
        self.assertIn("查看候选人记录", output)

    def test_rejects_missing_or_unidentifiable_candidate_name(self) -> None:
        self.payload.pop("candidate_display_name")
        with self.assertRaisesRegex(ValueError, "candidate_display_name"):
            render_decision_notification(self.payload)
        self.payload["candidate_display_name"] = self.payload["candidate_id"]
        with self.assertRaisesRegex(ValueError, "identify the person"):
            render_decision_notification(self.payload)

    def test_rejects_contact_data_and_missing_decision_context(self) -> None:
        self.payload["key_evidence"] = ["联系 person@example.com 核验"]
        with self.assertRaisesRegex(ValueError, "disallowed contact"):
            render_decision_notification(self.payload)
        self.payload["key_evidence"] = ["有业务闭环案例"]
        self.payload["decision_options"] = ["继续"]
        with self.assertRaisesRegex(ValueError, "decision_options"):
            render_decision_notification(self.payload)

    def test_rejects_score_breakdown_mismatch(self) -> None:
        self.payload["score"] = 88
        with self.assertRaisesRegex(ValueError, "score_dimensions total"):
            render_decision_notification(self.payload)
        self.payload["score"] = 87
        self.payload["score_dimensions"][0]["maximum"] = 21
        with self.assertRaisesRegex(ValueError, "maximums must total 100"):
            render_decision_notification(self.payload)


class InterviewReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(
            (
                PLUGIN_ROOT
                / "assets"
                / "interview-reconciliation.example.json"
            ).read_text(encoding="utf-8")
        )

    def test_completed_meeting_is_not_treated_as_passed(self) -> None:
        result = reconcile(self.payload)
        self.assertEqual(result["counts"]["completed"], 2)
        self.assertEqual(result["counts"]["usable_transcript"], 1)
        self.assertEqual(result["counts"]["artifact_missing_or_unusable"], 1)
        self.assertEqual(result["record_updates"][0]["interview_status"], "completed")
        self.assertTrue(
            all(
                item["interview_status"] != "passed"
                and item["human_decision_status"] != "passed"
                for item in result["record_updates"]
            )
        )

    def test_short_recording_requests_manual_note(self) -> None:
        result = reconcile(self.payload)
        second = result["record_updates"][1]
        self.assertEqual(second["artifact_status"], "insufficient_transcript")
        self.assertTrue(
            any(
                action["action_type"] == "request_manual_interview_note"
                for action in result["decision_queue"]
            )
        )
        self.assertTrue(
            any(
                action["action_type"] == "review_candidate_merge"
                and action["requires_human_approval"]
                for action in result["decision_queue"]
            )
        )

    def test_interviewed_and_current_role_are_preserved_separately(self) -> None:
        result = reconcile(self.payload)
        second = result["record_updates"][1]
        self.assertEqual(second["interviewed_job_id"], "DEMO-OPS-01")
        self.assertEqual(second["current_matched_job_id"], "DEMO-BRAND-01")

    def test_naive_calendar_time_does_not_crash_reconciliation(self) -> None:
        self.payload["interviews"][0]["calendar"]["planned_end"] = "2099-01-02T15:00:00"
        result = reconcile(self.payload)
        self.assertEqual(result["counts"]["completed"], 2)


class InterviewEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluation = json.loads(
            (
                PLUGIN_ROOT
                / "assets"
                / "interview-evaluation.example.json"
            ).read_text(encoding="utf-8")
        )

    def test_evidence_linked_evaluation_passes(self) -> None:
        self.assertEqual(validate_evaluation(self.evaluation), [])

    def test_dotted_live_job_version_is_supported(self) -> None:
        self.evaluation["job_version"] = "V1.0-20260727"
        self.assertEqual(validate_evaluation(self.evaluation), [])

    def test_nonzero_score_requires_transcript_evidence(self) -> None:
        invalid = copy.deepcopy(self.evaluation)
        invalid["evidence_refs"] = [
            reference
            for reference in invalid["evidence_refs"]
            if reference["dimension"] != "ai_execution"
        ]
        self.assertTrue(
            any(
                "scores.ai_execution lacks transcript evidence" in error
                for error in validate_evaluation(invalid)
            )
        )

    def test_unusable_transcript_cannot_be_scored(self) -> None:
        invalid = copy.deepcopy(self.evaluation)
        invalid["artifact_status"] = "no_artifact"
        errors = validate_evaluation(invalid)
        self.assertTrue(any("must not receive dimension scores" in error for error in errors))
        self.assertTrue(any("must not receive total_score" in error for error in errors))

    def test_read_failure_can_be_recorded_without_scores(self) -> None:
        pending = copy.deepcopy(self.evaluation)
        pending["artifact_status"] = "read_failed"
        pending["evidence_refs"] = []
        pending["scores"] = {}
        pending["total_score"] = None
        pending["recommendation"] = "request_more_info"
        pending["strength_codes"] = []
        pending["risk_codes"] = ["claim_not_verified"]
        pending["missing_material_codes"] = [
            "manual_interviewer_note_missing"
        ]
        self.assertEqual(validate_evaluation(pending), [])

    def test_protected_attribute_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.evaluation)
        invalid["strength_codes"].append("女性优先")
        self.assertTrue(
            any(
                "protected attributes" in error
                for error in validate_evaluation(invalid)
            )
        )


class InitializationTests(unittest.TestCase):
    def test_initialization_creates_private_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            result = initialize(
                argparse.Namespace(
                    workspace=workspace,
                    email="jobs-test@example.com",
                    timezone="Asia/Shanghai",
                    workspace_id="demo",
                    force=False,
                )
            )
            self.assertTrue(result["ok"])
            operations = workspace / ".recruitment-ops"
            self.assertTrue((operations / "config.json").is_file())
            self.assertTrue((operations / "state.sqlite3").is_file())
            config = json.loads(
                (operations / "config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(config["schema_version"], "4.0")
            self.assertEqual(config["company"]["public_name"], "诺特兰德集团")
            self.assertTrue(config["company"]["disclose_public_name"])
            self.assertEqual(config["compensation"]["source"], "job_source")
            self.assertEqual(config["channels"]["publish_mode"], "manual")
            self.assertTrue((operations / "campaigns").is_dir())
            self.assertTrue((operations / "publications").is_dir())
            self.assertTrue((operations / "interviews").is_dir())
            self.assertEqual((operations / ".gitignore").read_text(encoding="utf-8"), "*\n!.gitignore\n")
            self.assertEqual(stat.S_IMODE(operations.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((operations / "config.json").stat().st_mode), 0o600
            )
            self.assertEqual(
                stat.S_IMODE((operations / "state.sqlite3").stat().st_mode), 0o600
            )
            self.assertEqual(
                stat.S_IMODE((operations / "attachments").stat().st_mode), 0o700
            )

    def test_schema_two_config_migrates_with_private_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            initialize(
                argparse.Namespace(
                    workspace=workspace,
                    email="jobs-test@example.com",
                    timezone="Asia/Shanghai",
                    workspace_id="demo",
                    force=False,
                )
            )
            config_path = workspace / ".recruitment-ops" / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["schema_version"] = "2.0"
            config.pop("execution")
            config.pop("recommendation_policy")
            config["connectors"]["collaboration"]["tables"].pop("evidence_facts")
            for key in (
                "calendar_adapter",
                "vc_adapter",
                "minutes_adapter",
            ):
                del config["connectors"]["collaboration"][key]
            for key in (
                "interviews",
                "interview_artifacts",
                "interview_evaluations",
                "decision_queue",
            ):
                del config["connectors"]["collaboration"]["tables"][key]
            del config["interview_policy"]
            del config["schedule"]["interview_reconcile_hours"]
            del config["schedule"]["decision_digest_hour"]
            del config["controls"]["auto_merge_candidates"]
            del config["controls"]["auto_mark_interview_passed"]
            config_path.write_text(json.dumps(config), encoding="utf-8")

            preview = migrate(workspace)
            self.assertTrue(preview["changed"])
            self.assertFalse(preview["applied"])
            self.assertEqual(
                set(preview["added_table_mappings"]),
                {
                    "interviews",
                    "interview_artifacts",
                    "interview_evaluations",
                    "decision_queue",
                },
            )
            applied = migrate(workspace, apply=True)
            self.assertTrue(applied["applied"])
            migrated = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["schema_version"], "3.0")
            self.assertTrue(
                (workspace / ".recruitment-ops" / "config.backup-v2.json").is_file()
            )

    def test_schema_three_config_migrates_to_v4_and_reports_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            initialize(
                argparse.Namespace(
                    workspace=workspace,
                    email="jobs-test@example.com",
                    timezone="Asia/Shanghai",
                    workspace_id="demo",
                    force=False,
                )
            )
            config_path = workspace / ".recruitment-ops" / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["schema_version"] = "3.0"
            config.pop("execution")
            config.pop("recommendation_policy")
            config["connectors"]["collaboration"]["tables"].pop("evidence_facts")
            shared_table = "demo-shared-candidate-table"
            for name in (
                "candidates",
                "applications",
                "application_stage_history",
            ):
                config["connectors"]["collaboration"]["tables"][name] = shared_table
            config_path.write_text(json.dumps(config), encoding="utf-8")

            preview = migrate_v4(workspace)
            self.assertTrue(preview["changed"])
            self.assertFalse(preview["applied"])
            self.assertIn("evidence_facts", preview["requires_manual_mapping"])
            self.assertTrue(preview["mapping_collisions"])
            applied = migrate_v4(workspace, apply=True)
            self.assertTrue(applied["applied"])
            migrated = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["schema_version"], "4.0")
            self.assertFalse(migrated["schedule"]["enabled"])
            self.assertTrue(
                (workspace / ".recruitment-ops" / "config.backup-v3.json").is_file()
            )

    def test_mapping_validator_rejects_collapsed_candidate_tables(self) -> None:
        config = json.loads(
            (PLUGIN_ROOT / "assets" / "config.example.json").read_text(
                encoding="utf-8"
            )
        )
        tables = config["connectors"]["collaboration"]["tables"]
        for name in tables:
            tables[name] = f"demo-{name}"
        for name in (
            "candidates",
            "applications",
            "application_stage_history",
            "evidence_facts",
        ):
            tables[name] = "demo-collapsed"
        result = validate_mapping(config)
        self.assertFalse(result["ok"])
        self.assertTrue(result["collisions"])

    def test_commit_cycle_creates_one_action_and_one_outbox_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = json.loads(
                (
                    PLUGIN_ROOT
                    / "assets"
                    / "recruitment-cycle.example.json"
                ).read_text(encoding="utf-8")
            )
            state_db = Path(temp_dir) / "state.sqlite3"
            first = execute_cycle(payload, state_db=state_db, commit=True)
            second = execute_cycle(payload, state_db=state_db, commit=True)
            self.assertTrue(first["ok"])
            self.assertEqual(first["counts"]["actions_created"], 1)
            self.assertEqual(first["counts"]["notifications_created"], 1)
            self.assertEqual(second["counts"]["actions_created"], 0)
            self.assertEqual(second["counts"]["notifications_created"], 0)

    def test_commit_cycle_persists_hard_gate_review_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = json.loads(
                (
                    PLUGIN_ROOT
                    / "assets"
                    / "recruitment-cycle.example.json"
                ).read_text(encoding="utf-8")
            )
            payload["recommendation_input"]["applications"][0][
                "hard_gate_pass"
            ] = False
            state_db = Path(temp_dir) / "state.sqlite3"
            first = execute_cycle(payload, state_db=state_db, commit=True)
            second = execute_cycle(payload, state_db=state_db, commit=True)
            self.assertEqual(first["counts"]["actions_created"], 1)
            self.assertEqual(first["counts"]["notifications_created"], 1)
            self.assertEqual(
                first["recommendation"]["review_actions"][0][
                    "suggested_action"
                ],
                "verify_hard_gate_and_missing_information",
            )
            self.assertEqual(second["counts"]["actions_created"], 0)
            self.assertEqual(second["counts"]["notifications_created"], 0)
            connection = connect(state_db)
            action_type = connection.execute(
                "SELECT action_type FROM actions"
            ).fetchone()[0]
            connection.close()
            self.assertEqual(action_type, "verify_hard_gate_human_review")

    def test_doctor_passes_completed_local_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            initialize(
                argparse.Namespace(
                    workspace=workspace,
                    email="jobs-test@example.com",
                    timezone="Asia/Shanghai",
                    workspace_id="demo",
                    force=False,
                )
            )
            config_path = workspace / ".recruitment-ops" / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["privacy"]["authorized_reviewers"] = ["reviewer-demo"]
            collaboration = config["connectors"]["collaboration"]
            collaboration["base_app_token"] = "demo-base"
            collaboration["digest_recipient_id"] = "demo-recipient"
            for table_name in collaboration["tables"]:
                collaboration["tables"][table_name] = f"demo-{table_name}"
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            result = run_doctor(workspace)
            self.assertTrue(result["ok"])
            self.assertEqual(result["errors"], 0)

    def test_doctor_rejects_missing_controls_secret_and_bad_nested_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            initialize(
                argparse.Namespace(
                    workspace=workspace,
                    email="jobs-test@example.com",
                    timezone="Asia/Shanghai",
                    workspace_id="demo",
                    force=False,
                )
            )
            config_path = workspace / ".recruitment-ops" / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["controls"] = {}
            config["connectors"]["email"]["app_secret"] = "must-not-be-here"
            config["privacy"] = []
            config_path.write_text(json.dumps(config), encoding="utf-8")

            result = run_doctor(workspace)
            self.assertFalse(result["ok"])
            codes = {item["code"] for item in result["issues"]}
            self.assertIn("unsafe-control", codes)
            self.assertIn("secret-key", codes)
            self.assertIn("config-type", codes)

    def test_doctor_rejects_unapproved_company_and_salary_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            initialize(
                argparse.Namespace(
                    workspace=workspace,
                    email="jobs-test@example.com",
                    timezone="Asia/Shanghai",
                    workspace_id="demo",
                    company_name="诺特兰德集团",
                    force=False,
                )
            )
            config_path = workspace / ".recruitment-ops" / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["company"]["disclose_public_name"] = False
            config["compensation"]["source"] = "model_estimate"
            config["compensation"]["fallback"] = "guess"
            config["channels"]["publish_mode"] = "automatic"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            result = run_doctor(workspace)
            codes = {item["code"] for item in result["issues"]}
            self.assertFalse(result["ok"])
            self.assertIn("company-disclosure", codes)
            self.assertIn("salary-source", codes)
            self.assertIn("salary-fallback", codes)
            self.assertIn("social-publish-mode", codes)

    def test_doctor_reports_malformed_leaf_types_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            initialize(
                argparse.Namespace(
                    workspace=workspace,
                    email="jobs-test@example.com",
                    timezone="Asia/Shanghai",
                    workspace_id="demo",
                    force=False,
                )
            )
            config_path = workspace / ".recruitment-ops" / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["connectors"]["email"]["account"] = []
            config["channels"]["enabled"] = [1]
            config["channels"]["metric_windows_hours"] = [True]
            config["connectors"]["collaboration"]["base_app_token"] = []
            config["connectors"]["ats"]["enabled"] = 1
            config_path.write_text(json.dumps(config), encoding="utf-8")

            result = run_doctor(workspace)
            self.assertFalse(result["ok"])
            codes = {item["code"] for item in result["issues"]}
            self.assertIn("email", codes)
            self.assertIn("channels", codes)
            self.assertIn("metric-windows", codes)
            self.assertIn("data-table", codes)
            self.assertIn("ats", codes)

    def test_force_preserves_local_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            arguments = argparse.Namespace(
                workspace=workspace,
                email="jobs-test@example.com",
                timezone="Asia/Shanghai",
                workspace_id="demo",
                force=False,
            )
            initialize(arguments)
            template_path = workspace / ".recruitment-ops" / "job-template.md"
            template_path.write_text("local edit\n", encoding="utf-8")
            os.chmod(template_path, 0o600)
            arguments.force = True
            initialize(arguments)
            self.assertEqual(template_path.read_text(encoding="utf-8"), "local edit\n")

    def test_doctor_detects_unignored_config_in_parent_git_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir) / "repo"
            repository.mkdir()
            subprocess.run(
                ["git", "init", "-q", str(repository)],
                check=True,
                capture_output=True,
            )
            workspace = repository / "nested" / "workspace"
            initialize(
                argparse.Namespace(
                    workspace=workspace,
                    email="jobs-test@example.com",
                    timezone="Asia/Shanghai",
                    workspace_id="demo",
                    force=False,
                )
            )
            config_path = workspace / ".recruitment-ops" / "config.json"
            local_gitignore = workspace / ".recruitment-ops" / ".gitignore"
            local_gitignore.unlink()
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["privacy"]["authorized_reviewers"] = ["reviewer-demo"]
            collaboration = config["connectors"]["collaboration"]
            collaboration["base_app_token"] = "demo-base"
            collaboration["digest_recipient_id"] = "demo-recipient"
            for table_name in collaboration["tables"]:
                collaboration["tables"][table_name] = f"demo-{table_name}"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            result = run_doctor(workspace)
            self.assertFalse(result["ok"])
            self.assertTrue(
                any(item["code"] == "git-ignore" for item in result["issues"])
            )


class CampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(
            (PLUGIN_ROOT / "assets" / "config.example.json").read_text(
                encoding="utf-8"
            )
        )
        self.config["privacy"]["authorized_reviewers"] = ["CONTENT-REVIEWER-01"]
        self.job = {
            "岗位ID": "AI-PM-01",
            "岗位名称": "AI 产品经理",
            "组织": "创新业务",
            "岗位状态": "招聘中",
            "JD版本": "V2.1",
            "工作地点": "济南",
            "办公方式": "现场",
            "公开公司名称": "诺特兰德集团",
            "招聘法律主体": "示例招聘主体",
            "公开薪资范围": "20k–30k·13薪",
            "薪资公开已批准": True,
            "薪资审批记录": "SALARY-APPROVAL-V2",
            "薪资审批人": "SALARY-REVIEWER-01",
            "薪资审批时间": "2099-01-01T08:00:00+08:00",
            "源记录ID": "JOB-RECORD-01",
            "源版本": "JOB-REV-02",
            "源修改时间": "2099-01-01T07:00:00+08:00",
            "源回读时间": "2099-01-01T09:00:00+08:00",
            "招聘批次ID": "SPRINT-2099-01",
            "为什么现在招": "为了交付已批准的 AI 产品里程碑。",
            "30天结果": "完成需求和基线验证。",
            "60天结果": "完成受控试点。",
            "90天结果": "交付可复盘的业务结果。",
            "硬要求": "有复杂产品交付证据。",
            "作品要求": "提交一份脱敏案例。",
            "一票否决": "伪造经历或作品。",
            "CampaignOwner": "OWNER-01",
            "目标合格候选数": 5,
            "Campaign开始时间": "2099-01-01T09:00:00+08:00",
            "Campaign结束时间": "2099-01-31T18:00:00+08:00",
        }

    def approve_variant(
        self,
        campaign: dict[str, object],
        publication_index: int = 0,
        body_suffix: str = "",
    ) -> tuple[dict[str, object], dict[str, object]]:
        publications = campaign["publication_queue"]
        variants = campaign["content_variants"]
        assert isinstance(publications, list)
        assert isinstance(variants, list)
        publication = publications[publication_index]
        assert isinstance(publication, dict)
        variant = next(
            item
            for item in variants
            if isinstance(item, dict)
            and item["content_version_id"] == publication["content_version_id"]
        )
        variant["title"] = "诺特兰德集团招聘 AI 产品经理"
        variant["body"] = (
            "公司：诺特兰德集团\n"
            "薪资：20k–30k·13薪\n"
            "岗位 AI-PM-01，地点济南，办公方式现场。\n"
            "为了交付已批准的 AI 产品里程碑。\n"
            "30 天完成需求和基线验证。\n"
            "硬要求：有复杂产品交付证据。\n"
            "作品要求：提交一份脱敏案例。\n"
            "不适合：伪造经历或作品。\n"
            "投递材料仅用于招聘评估；如只同意用于本岗位，请在邮件中注明。\n"
            f"请投递至 {publication['application_email']}，主题包含 "
            f"{publication['attribution_code']}。{body_suffix}"
        )
        variant["asset_script"] = ["封面", "岗位结果", "投递方式"]
        approval = variant["content_approval"]
        assert isinstance(approval, dict)
        approval.update(
            {
                "status": "approved",
                "approval_record_id": "CONTENT-APPROVAL-01",
                "reviewer": "CONTENT-REVIEWER-01",
                "reviewed_at": "2099-01-01T10:00:00+08:00",
                "approval_source_revision": "CONTENT-APPROVAL-REV-01",
                "approval_readback_at": "2099-01-01T10:05:00+08:00",
                "no_employment_promise_confirmed": True,
                "content_sha256": content_hash(variant),
            }
        )
        publication["status"] = "ready_to_publish"
        return publication, variant

    def test_builds_attributed_manual_campaign(self) -> None:
        campaign = build_campaign(
            self.job,
            self.config,
            ["xiaohongshu", "wechat_groups"],
        )
        self.assertEqual(campaign["public_facts"]["company_name"], "诺特兰德集团")
        self.assertEqual(campaign["public_facts"]["salary_source"], "job_source")
        self.assertEqual(campaign["campaign_mode"], "formal")
        self.assertEqual(len(campaign["content_variants"]), 6)
        self.assertEqual(len(campaign["publication_queue"]), 6)
        first_publication = campaign["publication_queue"][0]
        self.assertEqual(first_publication["publish_mode"], "manual")
        self.assertTrue(first_publication["attribution_code"].startswith("XHS-"))
        self.assertIn("+xhs-", first_publication["application_email"])
        self.assertEqual(validate_campaign(campaign, self.config, self.job), [])

    def test_missing_salary_approval_blocks_campaign(self) -> None:
        self.job["薪资公开已批准"] = False
        with self.assertRaisesRegex(ValueError, "publication approval"):
            build_campaign(self.job, self.config)

    def test_blocked_job_cannot_create_campaign(self) -> None:
        self.job["岗位状态"] = "冻结"
        with self.assertRaisesRegex(ValueError, "blocked"):
            build_campaign(self.job, self.config)

    def test_ready_campaign_requires_content_and_manual_mode(self) -> None:
        campaign = build_campaign(self.job, self.config, ["xiaohongshu"])
        campaign["publication_queue"][0]["status"] = "ready_to_publish"
        campaign["publication_queue"][0]["publish_mode"] = "automatic"
        errors = validate_campaign(campaign, self.config, self.job)
        self.assertTrue(any("must remain manual" in error for error in errors))
        self.assertTrue(any("content title is required" in error for error in errors))
        self.assertTrue(any("content body is required" in error for error in errors))
        self.assertTrue(any("lacks human content approval" in error for error in errors))

    def test_ready_campaign_rejects_tampered_salary(self) -> None:
        campaign = build_campaign(self.job, self.config, ["xiaohongshu"])
        self.approve_variant(campaign)
        campaign["public_facts"]["salary_range"] = "99k–199k"
        errors = validate_campaign(campaign, self.config, self.job, True)
        self.assertTrue(
            any("salary differs from current approved job source" in error for error in errors)
        )

    def test_ready_campaign_accepts_one_approved_item(self) -> None:
        campaign = build_campaign(self.job, self.config, ["xiaohongshu"])
        self.approve_variant(campaign)
        self.assertEqual(
            validate_campaign(campaign, self.config, self.job, True),
            [],
        )

    def test_ready_campaign_rejects_extra_salary_and_external_url(self) -> None:
        campaign = build_campaign(self.job, self.config, ["xiaohongshu"])
        _, variant = self.approve_variant(
            campaign,
            body_suffix="另有 99k 承诺，访问 https://example.com 立即入职。",
        )
        approval = variant["content_approval"]
        assert isinstance(approval, dict)
        approval["content_sha256"] = content_hash(variant)
        errors = validate_campaign(campaign, self.config, self.job)
        self.assertTrue(any("salary token outside" in error for error in errors))
        self.assertTrue(any("external URL" in error for error in errors))

    def test_ready_campaign_scans_title_and_asset_script(self) -> None:
        campaign = build_campaign(self.job, self.config, ["xiaohongshu"])
        _, variant = self.approve_variant(campaign)
        asset_script = variant["asset_script"]
        assert isinstance(asset_script, list)
        asset_script.append(
            "实际用工方为另一家公司，月薪玖万，扫码访问 https://evil.example"
        )
        approval = variant["content_approval"]
        assert isinstance(approval, dict)
        approval["content_sha256"] = content_hash(variant)
        errors = validate_campaign(campaign, self.config, self.job)
        self.assertTrue(any("unapproved salary claim" in error for error in errors))
        self.assertTrue(any("unapproved company" in error for error in errors))
        self.assertTrue(any("external URL" in error for error in errors))

    def test_reviewer_must_be_authorized(self) -> None:
        campaign = build_campaign(self.job, self.config, ["xiaohongshu"])
        _, variant = self.approve_variant(campaign)
        approval = variant["content_approval"]
        assert isinstance(approval, dict)
        approval["reviewer"] = "UNAUTHORIZED"
        errors = validate_campaign(campaign, self.config, self.job)
        self.assertTrue(any("reviewer is not authorized" in error for error in errors))

    def test_new_readback_time_does_not_change_stable_job_fingerprint(self) -> None:
        campaign = build_campaign(self.job, self.config, ["xiaohongshu"])
        self.approve_variant(campaign)
        refreshed_job = copy.deepcopy(self.job)
        refreshed_job["源回读时间"] = "2099-01-01T11:00:00+08:00"
        self.assertEqual(
            validate_campaign(campaign, self.config, refreshed_job, True),
            [],
        )

    def test_conditional_campaign_rejects_formal_employment_promise(self) -> None:
        self.job["岗位状态"] = "人才寻访"
        campaign = build_campaign(self.job, self.config, ["xiaohongshu"])
        _, variant = self.approve_variant(
            campaign,
            body_suffix="这是人才寻访，通过评估后可签约并成为团队正式成员。",
        )
        approval = variant["content_approval"]
        assert isinstance(approval, dict)
        approval["content_sha256"] = content_hash(variant)
        errors = validate_campaign(campaign, self.config, self.job)
        self.assertEqual(campaign["campaign_mode"], "talent_mapping")
        self.assertTrue(any("promises a formal opening" in error for error in errors))

    def test_attribution_fields_are_deterministic(self) -> None:
        campaign = build_campaign(self.job, self.config, ["xiaohongshu"])
        publication = campaign["publication_queue"][0]
        publication["attribution_code"] = "XHS-TAMPERED"
        publication["application_email"] = "jobs+xhs-tampered@example.com"
        publication["subject_format"] = "应聘｜AI-PM-01｜XHS-TAMPERED｜姓名"
        errors = validate_campaign(campaign, self.config, self.job)
        self.assertTrue(any("attribution_code differs" in error for error in errors))
        self.assertTrue(any("application_email differs" in error for error in errors))
        self.assertTrue(any("subject_format differs" in error for error in errors))

    def test_campaign_key_separates_recruitment_cycles(self) -> None:
        first_campaign = build_campaign(self.job, self.config, ["xiaohongshu"])
        self.job["招聘批次ID"] = "SPRINT-2099-02"
        second_campaign = build_campaign(self.job, self.config, ["xiaohongshu"])
        self.assertNotEqual(
            first_campaign["campaign_id"],
            second_campaign["campaign_id"],
        )

    def test_missing_legal_entity_blocks_campaign(self) -> None:
        del self.job["招聘法律主体"]
        with self.assertRaisesRegex(ValueError, "legal_entity"):
            build_campaign(self.job, self.config)


class PackageTests(unittest.TestCase):
    def test_package_is_valid(self) -> None:
        self.assertEqual(validate(REPO_ROOT), [])

    def test_manifest_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            copied_repo = Path(temp_dir) / "repo"
            shutil.copytree(REPO_ROOT, copied_repo, ignore=shutil.ignore_patterns(".git"))
            manifest_path = (
                copied_repo
                / "plugins"
                / "chubby-recruitment-ops"
                / ".claude-plugin"
                / "plugin.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = "9.9.9"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertTrue(
                any("differ on version" in error for error in validate(copied_repo))
            )

    def test_unapproved_env_and_candidate_file_types_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            copied_repo = Path(temp_dir) / "repo"
            shutil.copytree(REPO_ROOT, copied_repo, ignore=shutil.ignore_patterns(".git"))
            (copied_repo / ".env.production").write_text(
                "CONNECTOR_APP_SECRET=synthetic-secret-value\n",
                encoding="utf-8",
            )
            (copied_repo / "candidate.csv").write_text(
                "email,phone\nperson@example.com,13800138000\n",
                encoding="utf-8",
            )
            errors = validate(copied_repo)
            self.assertTrue(any(".env.production" in error for error in errors))
            self.assertTrue(any("candidate.csv" in error for error in errors))

    def test_internal_collaboration_document_url_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            copied_repo = Path(temp_dir) / "repo"
            shutil.copytree(REPO_ROOT, copied_repo, ignore=shutil.ignore_patterns(".git"))
            (copied_repo / "internal-link.md").write_text(
                "https://"
                + "tenant"
                + ".collaboration.cn"
                + "/docx/"
                + "Abcdef1234567890\n",
                encoding="utf-8",
            )
            errors = validate(copied_repo)
            self.assertTrue(
                any("internal document URL" in error for error in errors)
            )


if __name__ == "__main__":
    unittest.main()
