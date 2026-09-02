"""Vercel install for this monorepo: Next.js dashboard or FastAPI Query API.

The dashboard must never `pip install`. Vercel's Node/Next image ships Debian
Python with PEP 668 (`externally-managed-environment`). The repo-root
installCommand used to be pip, so a frontend project that still reads root
`vercel.json` failed before `next` could run.

Rules:
- Current directory is the Next.js app (`next.config.*`) → `npm ci` / `npm install`
- Otherwise (FastAPI project at repo root) → pip with PEP 668 disabled
"""

from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
from pathlib import Path

NEXT_CONFIG_NAMES = ("next.config.ts", "next.config.js", "next.config.mjs")


def pep668_externally_managed() -> bool:
    marker = Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED"
    return marker.is_file()


def in_next_app(cwd: Path) -> bool:
    return any((cwd / name).is_file() for name in NEXT_CONFIG_NAMES)


def npm_install(app_dir: Path) -> None:
    lock = app_dir / "package-lock.json"
    cmd = ["npm", "ci"] if lock.is_file() else ["npm", "install"]
    subprocess.check_call(cmd, cwd=app_dir)


def pip_install(root: Path) -> None:
    env = os.environ.copy()
    # Node/Next images (and some system Pythons) reject pip without this.
    env["PIP_BREAK_SYSTEM_PACKAGES"] = "1"
    py = sys.executable
    subprocess.check_call(
        [
            py,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--break-system-packages",
            "-r",
            str(root / "requirements.txt"),
        ],
        cwd=root,
        env=env,
    )
    subprocess.check_call(
        [
            py,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--break-system-packages",
            "--no-deps",
            "-e",
            ".",
        ],
        cwd=root,
        env=env,
    )


def install(cwd: Path | None = None) -> str:
    """Run the install for `cwd`. Returns `npm` or `pip` so tests can assert."""
    cwd = (cwd or Path.cwd()).resolve()
    if in_next_app(cwd):
        npm_install(cwd)
        return "npm"
    pip_install(cwd)
    return "pip"


def main() -> int:
    install()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
