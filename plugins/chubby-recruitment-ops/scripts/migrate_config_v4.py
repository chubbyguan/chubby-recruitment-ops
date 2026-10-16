#!/usr/bin/env python3
"""Migrate a private recruitment config from schema 3.0 to 4.0 safely."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

from validate_base_mapping import mapping_collisions


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
    if version == "4.0":
        return {
            "ok": True,
            "changed": False,
            "applied": False,
            "config": str(config_path),
            "message": "config is already schema 4.0",
        }
    if version != "3.0":
        raise ValueError(
            "schema 4.0 migration requires schema 3.0; migrate older configs first"
        )

    plugin_root = Path(__file__).resolve().parent.parent
    defaults = json.loads(
        (plugin_root / "assets" / "config.example.json").read_text(
            encoding="utf-8"
        )
    )
    migrated = merge_defaults(defaults, current)
    migrated["schema_version"] = "4.0"
    migrated["schedule"]["enabled"] = False
    current_tables = (
        current.get("connectors", {}).get("collaboration", {}).get("tables", {})
    )
    migrated_tables = migrated["connectors"]["collaboration"]["tables"]
    added_tables = sorted(set(migrated_tables) - set(current_tables))
    collisions = mapping_collisions(migrated)
    result = {
        "ok": True,
        "changed": True,
        "applied": apply,
        "config": str(config_path),
        "added_sections": [
            "execution",
            "recommendation_policy",
        ],
        "added_table_mappings": added_tables,
        "requires_manual_mapping": [
            name
            for name in added_tables
            if migrated_tables.get(name) in {"SET_LOCALLY", "", None}
        ],
        "mapping_collisions": collisions,
        "schedule_forced_disabled": True,
        "next_step": (
            "Create and map distinct application, stage-history, evidence, "
            "channel, and interview tables; then run doctor.py and live acceptance."
        ),
    }
    if not apply:
        return result

    backup_path = config_path.with_name("config.backup-v3.json")
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
