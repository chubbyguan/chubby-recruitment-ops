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


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "chubby-recruitment-ops"
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from init_workspace import initialize  # noqa: E402
from build_campaign import build_campaign, content_hash  # noqa: E402
from doctor import run_doctor  # noqa: E402
from preflight import run_preflight  # noqa: E402
from render_digest import render  # noqa: E402
from state_store import (  # noqa: E402
    attachment_seen,
    claim_message,
    connect,
    mark_attachment,
    mark_message,
    message_seen,
)
from validate_assessment import validate_assessment  # noqa: E402
from validate_campaign import validate_campaign  # noqa: E402
from validate_package import validate  # noqa: E402


class PreflightTests(unittest.TestCase):
    def test_installed_package_preflight_passes_without_live_connectors(self) -> None:
        available = {
            "codex": "/usr/local/bin/codex",
            "lark-cli": "/usr/local/bin/lark-cli",
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


class InitializationTests(unittest.TestCase):
    def test_initialization_creates_private_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "department"
            result = initialize(
                argparse.Namespace(
                    workspace=workspace,
                    email="jobs-test@example.com",
                    timezone="Asia/Shanghai",
                    department_id="demo",
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
            self.assertEqual(config["schema_version"], "2.0")
            self.assertEqual(config["company"]["public_name"], "诺特兰德集团")
            self.assertTrue(config["company"]["disclose_public_name"])
            self.assertEqual(config["compensation"]["source"], "job_source")
            self.assertEqual(config["channels"]["publish_mode"], "manual")
            self.assertTrue((operations / "campaigns").is_dir())
            self.assertTrue((operations / "publications").is_dir())
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

    def test_doctor_passes_completed_local_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "department"
            initialize(
                argparse.Namespace(
                    workspace=workspace,
                    email="jobs-test@example.com",
                    timezone="Asia/Shanghai",
                    department_id="demo",
                    force=False,
                )
            )
            config_path = workspace / ".recruitment-ops" / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["privacy"]["authorized_reviewers"] = ["reviewer-demo"]
            feishu = config["connectors"]["feishu"]
            feishu["base_app_token"] = "demo-base"
            feishu["digest_recipient_id"] = "demo-recipient"
            for table_name in feishu["tables"]:
                feishu["tables"][table_name] = f"demo-{table_name}"
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            result = run_doctor(workspace)
            self.assertTrue(result["ok"])
            self.assertEqual(result["errors"], 0)

    def test_doctor_rejects_missing_controls_secret_and_bad_nested_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "department"
            initialize(
                argparse.Namespace(
                    workspace=workspace,
                    email="jobs-test@example.com",
                    timezone="Asia/Shanghai",
                    department_id="demo",
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
            workspace = Path(temp_dir) / "department"
            initialize(
                argparse.Namespace(
                    workspace=workspace,
                    email="jobs-test@example.com",
                    timezone="Asia/Shanghai",
                    department_id="demo",
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
            workspace = Path(temp_dir) / "department"
            initialize(
                argparse.Namespace(
                    workspace=workspace,
                    email="jobs-test@example.com",
                    timezone="Asia/Shanghai",
                    department_id="demo",
                    force=False,
                )
            )
            config_path = workspace / ".recruitment-ops" / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["connectors"]["email"]["account"] = []
            config["channels"]["enabled"] = [1]
            config["channels"]["metric_windows_hours"] = [True]
            config["connectors"]["feishu"]["base_app_token"] = []
            config_path.write_text(json.dumps(config), encoding="utf-8")

            result = run_doctor(workspace)
            self.assertFalse(result["ok"])
            codes = {item["code"] for item in result["issues"]}
            self.assertIn("email", codes)
            self.assertIn("channels", codes)
            self.assertIn("metric-windows", codes)
            self.assertIn("feishu-base", codes)

    def test_force_preserves_local_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "department"
            arguments = argparse.Namespace(
                workspace=workspace,
                email="jobs-test@example.com",
                timezone="Asia/Shanghai",
                department_id="demo",
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
            workspace = repository / "nested" / "department"
            initialize(
                argparse.Namespace(
                    workspace=workspace,
                    email="jobs-test@example.com",
                    timezone="Asia/Shanghai",
                    department_id="demo",
                    force=False,
                )
            )
            config_path = workspace / ".recruitment-ops" / "config.json"
            local_gitignore = workspace / ".recruitment-ops" / ".gitignore"
            local_gitignore.unlink()
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["privacy"]["authorized_reviewers"] = ["reviewer-demo"]
            feishu = config["connectors"]["feishu"]
            feishu["base_app_token"] = "demo-base"
            feishu["digest_recipient_id"] = "demo-recipient"
            for table_name in feishu["tables"]:
                feishu["tables"][table_name] = f"demo-{table_name}"
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
            "部门": "创新业务",
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
                "LARK_APP_SECRET=synthetic-secret-value\n",
                encoding="utf-8",
            )
            (copied_repo / "candidate.csv").write_text(
                "email,phone\nperson@example.com,13800138000\n",
                encoding="utf-8",
            )
            errors = validate(copied_repo)
            self.assertTrue(any(".env.production" in error for error in errors))
            self.assertTrue(any("candidate.csv" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
