#!/usr/bin/env python3
"""Validate the dual Codex and Claude Code plugin package."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
FEISHU_IDENTIFIER = re.compile(
    r"\b(?:tbl|oc_|om_|ou_|bascn)[A-Za-z0-9_-]{10,}\b"
)
FEISHU_DOCUMENT_URL = re.compile(
    r"https://[^\s/]+(?:\.feishu\.cn|\.larksuite\.com)"
    r"/(?:docx|wiki)/[A-Za-z0-9]+"
)
SECRET_TOKEN = re.compile(
    r"(?:"
    r"gh[opsu]_[A-Za-z0-9]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r")"
)
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
IDENTITY_NUMBER = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
ALLOWED_EXAMPLE_DOMAIN = "example.com"
ALLOWED_SYNTHETIC_PII = {"13800138000", "11010519491231002X"}
FORBIDDEN_ARTIFACT_SUFFIXES = {
    ".db",
    ".doc",
    ".docx",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".sqlite",
    ".sqlite3",
}
ALLOWED_TEXT_SUFFIXES = {".md", ".json", ".yaml", ".yml", ".py", ".txt"}
ALLOWED_SPECIAL_FILES = {".gitignore", "LICENSE"}


def load_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path}: root must be an object")
        return {}
    return value


def parse_skill_frontmatter(path: Path, errors: list[str]) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        errors.append(f"{path}: missing YAML frontmatter")
        return {}
    try:
        frontmatter = text.split("---\n", 2)[1]
    except IndexError:
        errors.append(f"{path}: malformed YAML frontmatter")
        return {}
    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            errors.append(f"{path}: malformed frontmatter line: {line}")
            continue
        fields[key.strip()] = value.strip()
    if set(fields) != {"name", "description"}:
        errors.append(f"{path}: frontmatter must contain only name and description")
    if path.parent.name != fields.get("name"):
        errors.append(f"{path}: folder name and skill name differ")
    if len(fields.get("description", "")) < 80:
        errors.append(f"{path}: description is too short for reliable triggering")
    return fields


def package_files(repo_root: Path) -> list[Path]:
    if (repo_root / ".git").exists():
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return [
                repo_root / raw.decode("utf-8")
                for raw in result.stdout.split(b"\0")
                if raw
            ]
    return [
        path
        for path in repo_root.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and "__pycache__" not in path.parts
    ]


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    repo_root = repo_root.resolve()
    plugin_root = repo_root / "plugins" / "chubby-recruitment-ops"
    required_files = [
        repo_root / "LICENSE",
        repo_root / "SECURITY.md",
        repo_root / ".agents" / "plugins" / "marketplace.json",
        repo_root / ".claude-plugin" / "marketplace.json",
        plugin_root / ".codex-plugin" / "plugin.json",
        plugin_root / ".claude-plugin" / "plugin.json",
        plugin_root / "skills" / "recruitment-ops-setup" / "SKILL.md",
        plugin_root / "skills" / "recruitment-ops-run" / "SKILL.md",
        plugin_root / "skills" / "social-recruitment-campaign" / "SKILL.md",
        plugin_root / "skills" / "recruitment-interview-review" / "SKILL.md",
        plugin_root / "assets" / "config.example.json",
        plugin_root / "assets" / "job.example.json",
        plugin_root / "assets" / "eligible-jobs.example.json",
        plugin_root / "assets" / "feishu-base-schema.json",
        plugin_root / "assets" / "social-campaign-template.md",
        plugin_root / "assets" / "channel-playbook.md",
        plugin_root / "assets" / "digest-input.example.json",
        plugin_root / "assets" / "interview-reconciliation.example.json",
        plugin_root / "assets" / "interview-recommendation.example.json",
        plugin_root / "assets" / "recruitment-cycle.example.json",
        plugin_root / "assets" / "interview-evaluation.example.json",
        plugin_root / "assets" / "interview-scorecard-template.md",
        plugin_root / "assets" / "integration-acceptance-template.json",
        plugin_root / "scripts" / "build_campaign.py",
        plugin_root / "scripts" / "preflight.py",
        plugin_root / "scripts" / "migrate_config_v3.py",
        plugin_root / "scripts" / "migrate_config_v4.py",
        plugin_root / "scripts" / "run_cycle.py",
        plugin_root / "scripts" / "state_store.py",
        plugin_root / "scripts" / "sync_feishu_hire.py",
        plugin_root / "scripts" / "validate_base_mapping.py",
        plugin_root / "scripts" / "reconcile_interviews.py",
        plugin_root / "scripts" / "recommend_interviews.py",
        plugin_root / "scripts" / "validate_interview_evaluation.py",
        plugin_root / "scripts" / "validate_campaign.py",
    ]
    for path in required_files:
        if not path.is_file():
            errors.append(f"missing required file: {path}")

    if errors:
        return errors

    codex_manifest = load_json(plugin_root / ".codex-plugin" / "plugin.json", errors)
    claude_manifest = load_json(plugin_root / ".claude-plugin" / "plugin.json", errors)
    codex_marketplace = load_json(
        repo_root / ".agents" / "plugins" / "marketplace.json", errors
    )
    claude_marketplace = load_json(
        repo_root / ".claude-plugin" / "marketplace.json", errors
    )
    load_json(plugin_root / "assets" / "config.example.json", errors)
    load_json(plugin_root / "assets" / "feishu-base-schema.json", errors)
    load_json(plugin_root / "assets" / "assessment.example.json", errors)
    load_json(plugin_root / "assets" / "digest-input.example.json", errors)
    load_json(plugin_root / "assets" / "interview-reconciliation.example.json", errors)
    load_json(plugin_root / "assets" / "interview-recommendation.example.json", errors)
    load_json(plugin_root / "assets" / "recruitment-cycle.example.json", errors)
    load_json(plugin_root / "assets" / "interview-evaluation.example.json", errors)
    load_json(plugin_root / "assets" / "integration-acceptance-template.json", errors)

    expected_name = "chubby-recruitment-ops"
    if codex_manifest.get("name") != expected_name:
        errors.append("Codex manifest name mismatch")
    if claude_manifest.get("name") != expected_name:
        errors.append("Claude manifest name mismatch")
    if not SEMVER.fullmatch(str(codex_manifest.get("version", ""))):
        errors.append("Codex manifest version must be strict semver")
    if not SEMVER.fullmatch(str(claude_manifest.get("version", ""))):
        errors.append("Claude manifest version must be strict semver")
    for shared_field in ("name", "version", "description", "repository"):
        if codex_manifest.get(shared_field) != claude_manifest.get(shared_field):
            errors.append(f"Codex and Claude manifests differ on {shared_field}")
    if codex_marketplace.get("name") != "chubby-recruitment":
        errors.append("Codex marketplace name mismatch")
    if claude_marketplace.get("name") != "chubby-recruitment":
        errors.append("Claude marketplace name mismatch")

    codex_plugins = codex_marketplace.get("plugins", [])
    if len(codex_plugins) != 1 or codex_plugins[0].get("name") != expected_name:
        errors.append("Codex marketplace must contain the plugin exactly once")
    elif codex_plugins[0].get("source") != {
        "source": "local",
        "path": "./plugins/chubby-recruitment-ops",
    }:
        errors.append("Codex marketplace source path mismatch")
    claude_plugins = claude_marketplace.get("plugins", [])
    if len(claude_plugins) != 1 or claude_plugins[0].get("name") != expected_name:
        errors.append("Claude marketplace must contain the plugin exactly once")
    elif claude_plugins[0].get("source") != "./plugins/chubby-recruitment-ops":
        errors.append("Claude marketplace source path mismatch")

    for skill_path in sorted((plugin_root / "skills").glob("*/SKILL.md")):
        parse_skill_frontmatter(skill_path, errors)

    for path in package_files(repo_root):
        if path.is_symlink():
            errors.append(f"{path}: symbolic links are not allowed in the plugin package")
            continue
        if path.suffix.lower() in FORBIDDEN_ARTIFACT_SUFFIXES:
            errors.append(f"{path}: binary or local-data artifact is not allowed")
            continue
        if (
            path.name not in ALLOWED_SPECIAL_FILES
            and path.suffix.lower() not in ALLOWED_TEXT_SUFFIXES
        ):
            errors.append(f"{path}: unapproved package file type")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{path}: cannot safely inspect text: {exc}")
            continue
        unresolved_todo_marker = "[" + "TODO:"
        if unresolved_todo_marker in text:
            errors.append(f"{path}: unresolved scaffold TODO")
        if SECRET_TOKEN.search(text):
            errors.append(f"{path}: possible secret token")
        if FEISHU_IDENTIFIER.search(text):
            errors.append(f"{path}: possible hard-coded Feishu identifier")
        if FEISHU_DOCUMENT_URL.search(text):
            errors.append(f"{path}: internal Feishu document URL is not allowed")
        for email in EMAIL.findall(text):
            if not email.lower().endswith("@" + ALLOWED_EXAMPLE_DOMAIN):
                errors.append(f"{path}: non-example email found: {email}")
        for phone in PHONE.findall(text):
            if phone not in ALLOWED_SYNTHETIC_PII:
                errors.append(f"{path}: possible real phone number")
        for identity_number in IDENTITY_NUMBER.findall(text):
            if identity_number not in ALLOWED_SYNTHETIC_PII:
                errors.append(f"{path}: possible real identity number")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root", type=Path)
    args = parser.parse_args(argv)
    errors = validate(args.repo_root)
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
