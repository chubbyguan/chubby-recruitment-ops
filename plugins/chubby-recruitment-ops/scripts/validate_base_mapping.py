#!/usr/bin/env python3
"""Validate that logical recruitment entities use auditable Feishu table mappings."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


DISTINCT_TABLE_GROUPS = {
    "candidate_lifecycle": (
        "candidates",
        "applications",
        "application_stage_history",
        "evidence_facts",
    ),
    "channel_attribution": (
        "campaigns",
        "content_variants",
        "publications",
        "metric_snapshots",
        "attribution_codes",
    ),
    "interview_decision": (
        "interviews",
        "interview_artifacts",
        "interview_evaluations",
        "decision_queue",
    ),
}

PLACEHOLDERS = {"", "SET_LOCALLY", None}


def as_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def mapping_collisions(config: dict[str, Any]) -> list[dict[str, Any]]:
    connectors = as_object(config.get("connectors"))
    feishu = as_object(connectors.get("feishu"))
    tables = as_object(feishu.get("tables"))
    collisions: list[dict[str, Any]] = []
    for group_name, logical_names in DISTINCT_TABLE_GROUPS.items():
        by_physical_id: dict[str, list[str]] = {}
        for logical_name in logical_names:
            physical_id = tables.get(logical_name)
            if physical_id in PLACEHOLDERS or not isinstance(physical_id, str):
                continue
            by_physical_id.setdefault(physical_id, []).append(logical_name)
        for physical_id, aliases in sorted(by_physical_id.items()):
            if len(aliases) > 1:
                collisions.append(
                    {
                        "group": group_name,
                        "physical_table_id": physical_id,
                        "logical_tables": sorted(aliases),
                    }
                )
    return collisions


def validate_mapping(
    config: dict[str, Any],
    schema_snapshot: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    collisions = mapping_collisions(config)
    missing_fields: list[dict[str, Any]] = []
    if schema_snapshot is not None and contract is not None:
        physical_tables = as_object(schema_snapshot.get("tables"))
        logical_contracts = as_object(contract.get("tables"))
        configured = as_object(
            as_object(as_object(config.get("connectors")).get("feishu")).get(
                "tables"
            )
        )
        for logical_name, table_contract in sorted(logical_contracts.items()):
            physical_id = configured.get(logical_name)
            if not isinstance(physical_id, str) or physical_id in PLACEHOLDERS:
                continue
            physical = as_object(physical_tables.get(physical_id))
            actual_fields = {
                str(field)
                for field in physical.get("fields", [])
                if isinstance(field, str)
            }
            required_fields = {
                str(field)
                for field in as_object(table_contract).get("required_fields", [])
                if isinstance(field, str)
            }
            missing = sorted(required_fields - actual_fields)
            if missing:
                missing_fields.append(
                    {
                        "logical_table": logical_name,
                        "physical_table_id": physical_id,
                        "missing_fields": missing,
                    }
                )
    return {
        "ok": not collisions and not missing_fields,
        "collisions": collisions,
        "missing_fields": missing_fields,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--schema-snapshot", type=Path)
    parser.add_argument("--contract", type=Path)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        schema_snapshot = (
            json.loads(args.schema_snapshot.read_text(encoding="utf-8"))
            if args.schema_snapshot
            else None
        )
        contract = (
            json.loads(args.contract.read_text(encoding="utf-8"))
            if args.contract
            else None
        )
        if (schema_snapshot is None) != (contract is None):
            raise ValueError(
                "--schema-snapshot and --contract must be supplied together"
            )
        result = validate_mapping(config, schema_snapshot, contract)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
