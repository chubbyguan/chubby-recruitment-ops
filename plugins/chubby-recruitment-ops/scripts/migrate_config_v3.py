#!/usr/bin/env python3
"""Migrate a private recruitment config from schema 2.0 to 3.0 with a backup."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any


def merge_defaults(defaults: Any, current: Any) -> Any:
    if not isinstance(defaults, dict) or not isinstance(current, dict):
        return copy.deepcopy(current)
    merged = copy.deepcopy(defaults)
    for key, value in current.items():
        if key in merged:
            merged[key] = merge_defaults(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def atomic_private_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def migrate(workspace: Path, apply: bool = False) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    config_path = workspace / ".recruitment-ops" / "config.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"missing config: {config_path}")
    current = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(current, dict):
        raise ValueError("config root must be an object")
    version = current.get("schema_version")
    if version == "3.0":
        return {
            "ok": True,
            "changed": False,
            "applied": False,
            "config": str(config_path),
            "message": "config is already schema 3.0",
        }
    if version != "2.0":
        raise ValueError("only schema 2.0 can be migrated automatically")

    plugin_root = Path(__file__).resolve().parent.parent
    defaults = json.loads(
        (plugin_root / "assets" / "config.example.json").read_text(encoding="utf-8")
    )
    defaults["schema_version"] = "3.0"
    defaults.pop("execution", None)
    defaults.pop("recommendation_policy", None)
    defaults["connectors"]["collaboration"]["tables"].pop("evidence_facts", None)
    migrated = merge_defaults(defaults, current)
    migrated["schema_version"] = "3.0"
    added_tables = sorted(
        set(migrated["connectors"]["collaboration"]["tables"])
        - set(current["connectors"]["collaboration"]["tables"])
    )
    added_controls = sorted(
        set(migrated["controls"]) - set(current.get("controls", {}))
    )
    result = {
        "ok": True,
        "changed": True,
        "applied": apply,
        "config": str(config_path),
        "added_table_mappings": added_tables,
        "added_controls": added_controls,
        "requires_manual_mapping": added_tables,
        "next_step": "Map new data tables and probe Calendar, VC, and Minutes, then run doctor.py.",
    }
    if not apply:
        return result

    backup_path = config_path.with_name("config.backup-v2.json")
    if not backup_path.exists():
        atomic_private_write(
            backup_path,
            json.dumps(current, ensure_ascii=False, indent=2) + "\n",
        )
    atomic_private_write(
        config_path,
        json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
    )
    result["backup"] = str(backup_path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = migrate(args.workspace, args.apply)
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
