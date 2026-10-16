#!/usr/bin/env python3
"""Check whether an installed recruitment plugin is ready for setup."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Callable


MINIMUM_PYTHON = (3, 9)
REQUIRED_PACKAGE_PATHS = (
    ".codex-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    "assets/config.example.json",
    "assets/execution-profiles.example.json",
    "assets/recruitment-data-schema.json",
    "scripts/doctor.py",
    "scripts/hash_attachment.py",
    "scripts/init_workspace.py",
    "scripts/migrate_config_v3.py",
    "scripts/migrate_config_v4.py",
    "scripts/plan_execution.py",
    "scripts/run_cycle.py",
    "scripts/reconcile_interviews.py",
    "scripts/render_decision_notification.py",
    "scripts/state_store.py",
    "scripts/sync_ats.py",
    "scripts/validate_base_mapping.py",
    "scripts/validate_interview_evaluation.py",
    "skills/recruitment-interview-review/SKILL.md",
    "skills/recruitment-ops-setup/SKILL.md",
    "skills/recruitment-ops-run/SKILL.md",
    "skills/social-recruitment-campaign/SKILL.md",
)


def run_preflight(
    plugin_root: Path,
    runtime: str = "auto",
    workspace: Path | None = None,
    require_collaboration_cli: bool = False,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, object]:
    root = plugin_root.expanduser().resolve()
    checks: list[dict[str, str]] = []

    def add(code: str, status: str, detail: str) -> None:
        checks.append({"code": code, "status": status, "detail": detail})

    if sys.version_info[:2] >= MINIMUM_PYTHON:
        add(
            "python",
            "pass",
            f"Python {sys.version_info.major}.{sys.version_info.minor}",
        )
    else:
        add("python", "error", "需要 Python 3.9 或更高版本")

    missing_paths = [
        relative_path
        for relative_path in REQUIRED_PACKAGE_PATHS
        if not (root / relative_path).is_file()
    ]
    if missing_paths:
        add(
            "package",
            "error",
            "插件包缺少文件：" + "、".join(missing_paths),
        )
    else:
        add("package", "pass", "插件清单、脚本、资产和 4 个 Skill 均存在")

    runtime_names = ("codex", "claude") if runtime == "auto" else (runtime,)
    available_runtimes = [name for name in runtime_names if which(name)]
    if runtime == "auto":
        if available_runtimes:
            add("runtime", "pass", "已发现：" + "、".join(available_runtimes))
        else:
            add("runtime", "error", "未发现 Codex 或 Claude Code CLI")
    elif available_runtimes:
        add("runtime", "pass", f"已发现 {runtime} CLI")
    else:
        add("runtime", "error", f"未发现 {runtime} CLI")

    collaboration_cli = which("collaboration-cli")
    if collaboration_cli:
        add("collaboration-adapter", "pass", "已发现 collaboration-cli")
    elif require_collaboration_cli:
        add("collaboration-adapter", "error", "未发现 collaboration-cli")
    else:
        add(
            "collaboration-adapter",
            "manual",
            "未发现 collaboration-cli；可改用已批准的协作连接器",
        )

    add(
        "gmail-adapter",
        "manual",
        "插件不会自动获得 Gmail 权限；初始化时必须核验授权账号",
    )
    add(
        "interview-adapters",
        "manual",
        "必须分别核验日历、视频会议和会议纪要权限；数据表权限不能替代这些权限",
    )

    if workspace is None:
        add(
            "workspace",
            "manual",
            "尚未指定业务工作区；安装后需运行 recruitment-ops-setup",
        )
    else:
        workspace_path = workspace.expanduser().resolve()
        config_path = workspace_path / ".recruitment-ops" / "config.json"
        state_path = workspace_path / ".recruitment-ops" / "state.sqlite3"
        if not workspace_path.is_dir():
            add("workspace", "error", "业务工作区不存在")
        elif not config_path.is_file():
            add("workspace", "error", "缺少 .recruitment-ops/config.json")
        elif not state_path.is_file():
            add("workspace", "error", "缺少 .recruitment-ops/state.sqlite3")
        else:
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                add("workspace", "error", "本地配置无法读取或不是有效 JSON")
            else:
                if isinstance(config, dict) and config.get("schema_version") == "4.0":
                    add("workspace", "pass", "已发现 schema_version=4.0 的私有工作区")
                elif isinstance(config, dict) and config.get("schema_version") == "3.0":
                    add(
                        "workspace",
                        "error",
                        "工作区仍为 schema 3.0；请先预览并执行 v4 迁移",
                    )
                else:
                    add("workspace", "error", "工作区配置版本不是 4.0")

    errors = [item for item in checks if item["status"] == "error"]
    return {
        "ok": not errors,
        "plugin_root": str(root),
        "runtime": runtime,
        "checks": checks,
        "live_acceptance_required": True,
        "next_step": (
            "运行 recruitment-ops-setup，完成 Gmail 账号探针、招聘数据表写入回读、"
            "日历/会议/会议纪要探针、重复邮件重放和日报送达验收"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        choices=("auto", "codex", "claude"),
        default="auto",
    )
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--require-collaboration-cli", action="store_true")
    args = parser.parse_args(argv)
    result = run_preflight(
        Path(__file__).resolve().parent.parent,
        runtime=args.runtime,
        workspace=args.workspace,
        require_collaboration_cli=args.require_collaboration_cli,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
