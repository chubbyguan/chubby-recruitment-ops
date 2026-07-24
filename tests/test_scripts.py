from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "chubby-recruitment-ops"
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from init_workspace import initialize  # noqa: E402
from doctor import run_doctor  # noqa: E402
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
from validate_package import validate  # noqa: E402


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

    def test_safe_assessment_passes(self) -> None:
        self.assertEqual(validate_assessment(self.assessment), [])

    def test_total_must_equal_dimensions(self) -> None:
        invalid = copy.deepcopy(self.assessment)
        invalid["total_score"] = 99
        self.assertTrue(
            any("dimension sum" in error for error in validate_assessment(invalid))
        )

    def test_protected_attribute_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.assessment)
        invalid["evidence_summary"].append("性别符合团队预期")
        self.assertTrue(
            any("protected attribute" in error for error in validate_assessment(invalid))
        )

    def test_nationality_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.assessment)
        invalid["evidence_summary"].append("候选人国籍适合团队")
        self.assertTrue(
            any("protected attribute" in error for error in validate_assessment(invalid))
        )

    def test_hr_screen_requires_score_and_complete_evidence(self) -> None:
        invalid = copy.deepcopy(self.assessment)
        invalid["information_complete"] = True
        invalid["recommendation"] = "hr_screen"
        invalid["missing_materials"] = []
        for key in invalid["scores"]:
            invalid["scores"][key] = 0
        invalid["total_score"] = 0
        errors = validate_assessment(invalid)
        self.assertTrue(any("total_score >= 75" in error for error in errors))


class DigestTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
