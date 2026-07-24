#!/usr/bin/env python3
"""Validate the dual Codex and Claude Code plugin package."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
FEISHU_IDENTIFIER = re.compile(r"\b(?:tbl|oc_|om_)[A-Za-z0-9]{10,}\b")
SECRET_TOKEN = re.compile(
    r"(?:gh[opsu]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})"
)
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
ALLOWED_EXAMPLE_DOMAIN = "example.com"


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


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    repo_root = repo_root.resolve()
    plugin_root = repo_root / "plugins" / "chubby-recruitment-ops"
    required_files = [
        repo_root / ".agents" / "plugins" / "marketplace.json",
        repo_root / ".claude-plugin" / "marketplace.json",
        plugin_root / ".codex-plugin" / "plugin.json",
        plugin_root / ".claude-plugin" / "plugin.json",
        plugin_root / "skills" / "recruitment-ops-setup" / "SKILL.md",
        plugin_root / "skills" / "recruitment-ops-run" / "SKILL.md",
        plugin_root / "assets" / "config.example.json",
        plugin_root / "assets" / "feishu-base-schema.json",
        plugin_root / "assets" / "digest-input.example.json",
        plugin_root / "assets" / "integration-acceptance-template.json",
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

    text_extensions = {".md", ".json", ".yaml", ".yml", ".py", ".txt"}
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in text_extensions:
            continue
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        unresolved_todo_marker = "[" + "TODO:"
        if unresolved_todo_marker in text:
            errors.append(f"{path}: unresolved scaffold TODO")
        if SECRET_TOKEN.search(text):
            errors.append(f"{path}: possible secret token")
        if FEISHU_IDENTIFIER.search(text):
            errors.append(f"{path}: possible hard-coded Feishu identifier")
        for email in EMAIL.findall(text):
            if not email.lower().endswith("@" + ALLOWED_EXAMPLE_DOMAIN):
                errors.append(f"{path}: non-example email found: {email}")

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
