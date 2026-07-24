#!/usr/bin/env python3
"""Initialize a department-local recruitment operations workspace."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from state_store import connect


LOCAL_GITIGNORE = """*
!.gitignore
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--department-id", default="replace-with-department-id")
    parser.add_argument("--company-name", default="诺特兰德集团")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace the existing local config only; preserve local templates and state",
    )
    return parser


def validate_email(value: str) -> str:
    local, separator, domain = value.strip().partition("@")
    if not separator or not local or "." not in domain:
        raise ValueError("email must be a valid dedicated recruitment address")
    return value.strip()


def atomic_private_write(path: Path, content: str) -> None:
    temp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def initialize(args: argparse.Namespace) -> dict[str, object]:
    workspace = args.workspace.expanduser().resolve()
    plugin_root = Path(__file__).resolve().parent.parent
    template_path = plugin_root / "assets" / "config.example.json"
    operations_dir = workspace / ".recruitment-ops"
    config_path = operations_dir / "config.json"

    if config_path.exists() and not args.force:
        raise FileExistsError(f"config already exists: {config_path}")

    email = validate_email(args.email)
    config = json.loads(template_path.read_text(encoding="utf-8"))
    config["department_id"] = args.department_id
    config["timezone"] = args.timezone
    company_name = str(
        getattr(args, "company_name", config["company"]["public_name"])
    ).strip()
    if not company_name:
        raise ValueError("company name is required for public recruitment content")
    config["company"]["public_name"] = company_name
    config["public_content"]["application_email"] = email
    config["connectors"]["email"]["account"] = email

    workspace.mkdir(parents=True, exist_ok=True)
    operations_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(operations_dir, 0o700)
    for directory in (
        "campaigns",
        "drafts",
        "publications",
        "reports",
        "fixtures",
        "attachments",
    ):
        local_directory = operations_dir / directory
        local_directory.mkdir(exist_ok=True, mode=0o700)
        os.chmod(local_directory, 0o700)

    atomic_private_write(
        config_path,
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
    )
    gitignore_path = operations_dir / ".gitignore"
    if not gitignore_path.exists():
        atomic_private_write(gitignore_path, LOCAL_GITIGNORE)

    state_db = operations_dir / "state.sqlite3"
    connection = connect(state_db)
    connection.close()

    for source_name, destination_name in (
        ("job-template.md", "job-template.md"),
        ("privacy-notice-template.md", "privacy-notice-template.md"),
        ("social-campaign-template.md", "social-campaign-template.md"),
        ("channel-playbook.md", "channel-playbook.md"),
    ):
        destination = operations_dir / destination_name
        if not destination.exists():
            shutil.copy2(plugin_root / "assets" / source_name, destination)
            os.chmod(destination, 0o600)

    return {
        "ok": True,
        "workspace": str(workspace),
        "config": str(config_path),
        "state_db": str(state_db),
        "schedule_enabled": False,
        "next_steps": [
            "map the department job, salary approval source, and Feishu Base tables",
            "authorize Gmail and Feishu adapters",
            "run doctor.py",
            "complete campaign and synthetic end-to-end acceptance",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = initialize(args)
    except (ValueError, FileExistsError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
