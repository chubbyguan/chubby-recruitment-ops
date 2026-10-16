#!/usr/bin/env python3
"""Safely hash a local resume attachment without parsing or executing it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat as stat_module
import sys
from pathlib import Path
from typing import Any


ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx"}
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


def hash_attachment(
    path: Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ValueError("attachment symlink is not allowed")
    if expanded.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError("attachment type is not allowed")
    try:
        stat = expanded.stat()
    except OSError as exc:
        raise ValueError("attachment is not readable") from exc
    if not expanded.is_file():
        raise ValueError("attachment must be a regular file")
    if stat.st_size > max_bytes:
        raise ValueError("attachment exceeds the configured size limit")

    opened_fd = os.open(expanded, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened_stat = os.fstat(opened_fd)
        if not stat_module.S_ISREG(opened_stat.st_mode):
            raise ValueError("attachment must be a regular file")
        if opened_stat.st_size > max_bytes:
            raise ValueError("attachment exceeds the configured size limit")
        digest = hashlib.sha256()
        bytes_read = 0
        with os.fdopen(opened_fd, "rb") as handle:
            opened_fd = -1
            while chunk := handle.read(CHUNK_BYTES):
                bytes_read += len(chunk)
                if bytes_read > max_bytes:
                    raise ValueError(
                        "attachment exceeds the configured size limit"
                    )
                digest.update(chunk)
    finally:
        if opened_fd >= 0:
            os.close(opened_fd)
    return {
        "ok": True,
        "hash_status": "hashed",
        "sha256": digest.hexdigest(),
        "size_bytes": bytes_read,
        "extension": expanded.suffix.lower(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args(argv)
    try:
        result = hash_attachment(args.path, args.max_bytes)
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "hash_status": "unavailable",
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
