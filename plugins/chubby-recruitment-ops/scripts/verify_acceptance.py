#!/usr/bin/env python3
"""Run local synthetic checks; this does not prove live integration readiness."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from build_campaign import build_campaign, content_hash
from recommend_interviews import recommend
from reconcile_interviews import reconcile
from render_digest import render
from run_cycle import execute_cycle
from state_store import (
    attachment_seen,
    claim_notification,
    claim_message,
    complete_notification,
    connect,
    list_notifications,
    mark_attachment,
    mark_message,
    message_seen,
    record_action_decision,
)
from validate_assessment import validate_assessment
from validate_campaign import validate_campaign
from validate_interview_evaluation import validate_evaluation


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

            config = json.loads(
                (plugin_root / "assets" / "config.example.json").read_text(
                    encoding="utf-8"
                )
            )
            config["privacy"]["authorized_reviewers"] = ["SYNTHETIC-REVIEWER"]
            job = {
                "岗位ID": "SYNTHETIC-ROLE-01",
                "岗位名称": "虚构测试岗位",
                "部门": "验收测试",
                "岗位状态": "招聘中",
                "JD版本": "SYNTHETIC-V1",
                "工作地点": "测试地点",
                "办公方式": "现场",
                "公开公司名称": "诺特兰德集团",
                "招聘法律主体": "虚构测试主体",
                "公开薪资范围": "示例公开范围",
                "薪资公开已批准": True,
                "薪资审批记录": "SYNTHETIC-APPROVAL-01",
                "薪资审批人": "SYNTHETIC-SALARY-REVIEWER",
                "薪资审批时间": "2099-01-01T08:00:00+08:00",
                "源记录ID": "SYNTHETIC-JOB-RECORD-01",
                "源版本": "SYNTHETIC-JOB-REV-01",
                "源修改时间": "2099-01-01T07:00:00+08:00",
                "源回读时间": "2099-01-01T09:00:00+08:00",
                "招聘批次ID": "SYNTHETIC-CAMPAIGN-01",
                "为什么现在招": "用于验证招聘 Campaign。",
                "30天结果": "完成现状验证。",
                "60天结果": "完成受控试点。",
                "90天结果": "完成可复盘交付。",
                "硬要求": "有可验证的交付证据。",
                "作品要求": "提交虚构脱敏案例。",
                "一票否决": "伪造经历或作品。",
                "CampaignOwner": "SYNTHETIC-OWNER",
                "目标合格候选数": 2,
                "Campaign开始时间": "2099-01-01T09:00:00+08:00",
                "Campaign结束时间": "2099-01-31T18:00:00+08:00",
            }
            campaign = build_campaign(
                job,
                config,
                ["xiaohongshu", "wechat_groups"],
            )
            check(not validate_campaign(campaign, config), "campaign draft must validate")
            check(
                all(
                    item["publish_mode"] == "manual"
                    for item in campaign["publication_queue"]
                ),
                "all publication tasks must remain manual",
            )
            check(
                len(
                    {
                        item["attribution_code"]
                        for item in campaign["publication_queue"]
                    }
                )
                == len(campaign["publication_queue"]),
                "attribution codes must be unique",
            )
            checks.append("campaign provenance and manual publication gates")

            publication = campaign["publication_queue"][0]
            variant = next(
                item
                for item in campaign["content_variants"]
                if item["content_version_id"] == publication["content_version_id"]
            )
            variant["title"] = "诺特兰德集团虚构测试岗位人才寻访"
            variant["body"] = (
                "公司：诺特兰德集团\n"
                "薪资：示例公开范围\n"
                "岗位 SYNTHETIC-ROLE-01，地点测试地点，办公方式现场。\n"
                "用于验证招聘 Campaign。\n"
                "30 天完成现状验证。\n"
                "硬要求：有可验证的交付证据。\n"
                "作品要求：提交虚构脱敏案例。\n"
                "不适合：伪造经历或作品。\n"
                "投递材料仅用于招聘评估；如只同意用于本岗位，请在邮件中注明。\n"
                "请投递至 "
                f"{publication['application_email']}，主题包含 "
                f"{publication['attribution_code']}。"
            )
            variant["asset_script"] = ["封面", "岗位结果", "投递方式"]
            variant["content_approval"].update(
                {
                    "status": "approved",
                    "approval_record_id": "SYNTHETIC-CONTENT-APPROVAL",
                    "reviewer": "SYNTHETIC-REVIEWER",
                    "reviewed_at": "2099-01-01T10:00:00+08:00",
                    "approval_source_revision": "SYNTHETIC-APPROVAL-REV-01",
                    "approval_readback_at": "2099-01-01T10:05:00+08:00",
                    "content_sha256": content_hash(variant),
                }
            )
            publication["status"] = "ready_to_publish"
            check(
                not validate_campaign(campaign, config, job, True),
                "one approved publication must pass the ready gate",
            )
            checks.append("content fingerprint and human approval gate")

            unapproved_job = copy.deepcopy(job)
            unapproved_job["薪资公开已批准"] = False
            try:
                build_campaign(unapproved_job, config)
            except ValueError:
                pass
            else:
                raise AssertionError("unapproved salary must block campaign creation")
            checks.append("unapproved salary publication rejection")

            assessment = json.loads(
                (plugin_root / "assets" / "assessment.example.json").read_text(encoding="utf-8")
            )
            jobs_data = json.loads(
                (plugin_root / "assets" / "eligible-jobs.example.json").read_text(
                    encoding="utf-8"
                )
            )
            eligible_jobs = {
                item["job_id"]: item for item in jobs_data["jobs"]
            }
            check(
                not validate_assessment(
                    assessment,
                    eligible_jobs,
                    jobs_data["source_revision"],
                    jobs_data["source_readback_at"],
                ),
                "safe assessment must validate",
            )
            checks.append("assessment schema and score validation")

            unsafe_assessment = copy.deepcopy(assessment)
            unsafe_assessment["evidence_codes"].append("候选人年龄适合团队")
            check(
                bool(
                    validate_assessment(
                        unsafe_assessment,
                        eligible_jobs,
                        jobs_data["source_revision"],
                        jobs_data["source_readback_at"],
                    )
                ),
                "protected attribute must be rejected",
            )
            checks.append("protected-attribute rejection")

            recommendation_input = json.loads(
                (
                    plugin_root
                    / "assets"
                    / "interview-recommendation.example.json"
                ).read_text(encoding="utf-8")
            )
            recommendation = recommend(recommendation_input)
            check(
                recommendation["counts"]["recommended"] == 1,
                "one complete high-score candidate must be recommended",
            )
            check(
                recommendation["counts"]["needs_information"] == 1,
                "incomplete high-score evidence must request more information",
            )
            check(
                recommendation["recommendations"][0]["requires_human_approval"],
                "interview recommendation must remain a human gate",
            )
            sent_key = recommendation["recommendations"][0]["idempotency_key"]
            recommendation_input["previous_sent_keys"] = [sent_key]
            replay = recommend(recommendation_input)
            check(
                replay["counts"]["new_notifications"] == 0,
                "recommendation replay must not create a duplicate notification",
            )
            checks.append("high-score no-interview recommendation and notification idempotency")

            cycle_input = json.loads(
                (
                    plugin_root
                    / "assets"
                    / "recruitment-cycle.example.json"
                ).read_text(encoding="utf-8")
            )
            first_cycle = execute_cycle(cycle_input, db_path, commit=True)
            second_cycle = execute_cycle(cycle_input, db_path, commit=True)
            check(
                first_cycle["counts"]["actions_created"] == 1
                and first_cycle["counts"]["notifications_created"] == 1,
                "first v5 cycle must create one action and one notification",
            )
            check(
                second_cycle["counts"]["actions_created"] == 0
                and second_cycle["counts"]["notifications_created"] == 0,
                "v5 cycle replay must remain idempotent",
            )
            connection = connect(db_path)
            pending = list_notifications(connection)
            check(len(pending) == 1, "one notification must be pending")
            notification_id = pending[0]["notification_id"]
            notification_lease = claim_notification(connection, notification_id)
            check(bool(notification_lease), "notification claim must succeed")
            complete_notification(
                connection,
                notification_id,
                str(notification_lease),
                status="sent",
                external_message_id="SYNTHETIC-MESSAGE-ID",
            )
            recommendation_item = first_cycle["recommendation"][
                "recommendations"
            ][0]
            decision = record_action_decision(
                connection,
                recommendation_item["action_id"],
                decision="approve",
                reviewer_id="SYNTHETIC-REVIEWER",
                expected_fingerprint=recommendation_item["idempotency_key"],
            )
            check(
                decision["status"] == "approved",
                "fingerprint-bound human approval must succeed",
            )
            connection.close()
            checks.append(
                "v5 run lease action outbox and fingerprint-bound approval"
            )

            reconciliation_input = json.loads(
                (
                    plugin_root
                    / "assets"
                    / "interview-reconciliation.example.json"
                ).read_text(encoding="utf-8")
            )
            reconciliation = reconcile(reconciliation_input)
            check(
                reconciliation["counts"]["completed"] == 2,
                "completed meeting facts must reconcile",
            )
            check(
                reconciliation["counts"]["usable_transcript"] == 1,
                "only the usable original transcript may be evaluated",
            )
            check(
                reconciliation["counts"]["artifact_missing_or_unusable"] == 1,
                "short transcript must remain unusable",
            )
            check(
                any(
                    action["action_type"] == "review_candidate_merge"
                    and action["requires_human_approval"]
                    for action in reconciliation["decision_queue"]
                ),
                "candidate merge must remain a human gate",
            )
            checks.append("calendar meeting and transcript reconciliation")

            interview_evaluation = json.loads(
                (
                    plugin_root / "assets" / "interview-evaluation.example.json"
                ).read_text(encoding="utf-8")
            )
            check(
                not validate_evaluation(interview_evaluation),
                "evidence-linked interview evaluation must validate",
            )
            no_artifact_evaluation = copy.deepcopy(interview_evaluation)
            no_artifact_evaluation["artifact_status"] = "no_artifact"
            check(
                bool(validate_evaluation(no_artifact_evaluation)),
                "missing transcript must block interview scoring",
            )
            checks.append("interview evidence and missing-artifact gate")

            digest = render(
                {
                    "date": "2099-01-01",
                    "channels": [
                        {
                            "channel": "xiaohongshu",
                            "views": 100,
                            "likes": 5,
                            "saves": 3,
                            "comments": 2,
                            "clicks": None,
                            "attributed_applications": 4,
                            "qualified_candidates": 2,
                        }
                    ],
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
                    "interview_pipeline": {
                        "scheduled_today": 0,
                        "completed_today": 2,
                        "awaiting_evaluation": 1,
                        "artifact_missing": 1,
                        "awaiting_decision": 2,
                        "decision_overdue": 1,
                        "backlog": [],
                    },
                    "anomalies": [],
                }
            )
            check("demo.person@example.com" not in digest, "digest must redact email")
            check("13800138000" not in digest, "digest must redact phone")
            check("点击 / 点击率：不可得 / 不可得" in digest, "missing CTR must stay unavailable")
            check("无可用纪要：1" in digest, "interview artifact gap must appear in digest")
            check("决定已超时：1" in digest, "overdue decision must appear in digest")
            checks.append("digest PII redaction")
    except (AssertionError, OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
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
                "next_step": "Run controlled Gmail and Feishu Base, Calendar, VC, and Minutes probes; then verify Base upsert/readback, duplicate replay, and digest send/readback.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
