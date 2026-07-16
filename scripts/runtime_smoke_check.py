"""Lightweight runtime smoke checks for local clones and Render shells.

Usage:
    python scripts/runtime_smoke_check.py
    python scripts/runtime_smoke_check.py --strict

The script uses only the Python standard library. It focuses on the parts of
the project that are easiest to accidentally break during refactors:

- core Python files still parse
- the WebApp admin monitor shell and hooks still exist
- the media endpoint route still exists
- obvious local DB footguns are surfaced early
"""

from __future__ import annotations

import argparse
import json
import os
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_PY_FILES = [
    REPO_ROOT / "configs" / "config.py",
    REPO_ROOT / "database.py",
    REPO_ROOT / "utils" / "safe_send.py",
    REPO_ROOT / "webhook_server.py",
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compile_python_files() -> None:
    for path in CORE_PY_FILES:
        py_compile.compile(str(path), doraise=True)


def check_webapp_shell() -> None:
    index_html = (REPO_ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
    app_js = (REPO_ROOT / "webapp" / "app.js").read_text(encoding="utf-8")
    webhook_server = (REPO_ROOT / "webhook_server.py").read_text(encoding="utf-8")

    require('id="admin-panel"' in index_html, "webapp/index.html is missing #admin-panel")
    require("loadAdminPanel(" in app_js, "webapp/app.js is missing loadAdminPanel")
    require("queueAdminPanelRefresh(" in app_js, "webapp/app.js is missing queueAdminPanelRefresh")
    require("startAdminPanelAutoRefresh(" in app_js, "webapp/app.js is missing startAdminPanelAutoRefresh")
    require("/api/admin/status" in app_js, "webapp/app.js no longer fetches /api/admin/status")
    require("/api/webapp-session" in app_js, "webapp/app.js is missing /api/webapp-session session exchange")
    require('@app.get("/api/admin/status")' in webhook_server, "webhook_server.py is missing /api/admin/status")
    require(
        '@app.get("/api/catalog/{product_id}/media/{media_index}")' in webhook_server,
        "webhook_server.py is missing the catalog media route",
    )


def _find_node() -> str | None:
    node = shutil.which("node")
    if node:
        return node

    home = Path.home()
    candidates = [
        home / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe",
        home / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def check_webapp_js_parse() -> str:
    node = _find_node()
    if not node:
        return "skipped (node not found)"

    app_js = REPO_ROOT / "webapp" / "app.js"
    script = (
        "const fs=require('fs');"
        f"const src=fs.readFileSync({json.dumps(str(app_js))},'utf8');"
        "new Function(src);"
        "process.stdout.write('ok');"
    )
    completed = subprocess.run(
        [node, "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"webapp/app.js parse failed: {completed.stderr.strip() or completed.stdout.strip()}")
    return "ok"


def check_local_db_warning() -> list[str]:
    warnings: list[str] = []
    local_db = REPO_ROOT / "catalog.db"
    if local_db.exists() and local_db.stat().st_size == 0:
        warnings.append(
            f"warning: local database placeholder detected at {local_db}; "
            "prefer /data/catalog.db on Render or set DB_PATH explicitly."
        )
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Run lightweight Bradycadia runtime checks.")
    parser.add_argument("--strict", action="store_true", help="Fail when warnings are found.")
    args = parser.parse_args()

    compile_python_files()
    check_webapp_shell()
    js_parse = check_webapp_js_parse()
    warnings = check_local_db_warning()

    print("runtime smoke check passed")
    print(f"  python files: {len(CORE_PY_FILES)} parsed")
    print(f"  webapp js parse: {js_parse}")
    if warnings:
        for warning in warnings:
            print(f"  {warning}")
        if args.strict:
            raise AssertionError("strict mode failed due to warnings")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"runtime smoke check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
