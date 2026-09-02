"""Vercel install routing (Next.js vs FastAPI / PEP 668)."""

from __future__ import annotations

from pathlib import Path

import scripts.vercel_install as vercel_install


def test_pep668_helper_returns_bool() -> None:
    assert isinstance(vercel_install.pep668_externally_managed(), bool)


def test_in_next_app_detects_config(tmp_path: Path) -> None:
    assert vercel_install.in_next_app(tmp_path) is False
    (tmp_path / "next.config.ts").write_text("export default {};\n", encoding="utf-8")
    assert vercel_install.in_next_app(tmp_path) is True


def test_dashboard_cwd_runs_npm_not_pip(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "next.config.ts").write_text("export default {};\n", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}\n", encoding="utf-8")
    called: list[list[str]] = []

    def fake_call(cmd, cwd=None, env=None):
        called.append(list(cmd))
        return 0

    monkeypatch.setattr(vercel_install.subprocess, "check_call", fake_call)
    assert vercel_install.install(tmp_path) == "npm"
    assert called == [["npm", "ci"]]
    assert all("pip" not in part for cmd in called for part in cmd)


def test_api_cwd_runs_pip_with_break_system_packages(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    envs: list[dict] = []

    def fake_call(cmd, cwd=None, env=None):
        envs.append(dict(env or {}))
        assert "--break-system-packages" in cmd
        assert "-m" in cmd and "pip" in cmd
        return 0

    monkeypatch.setattr(vercel_install.subprocess, "check_call", fake_call)
    assert vercel_install.install(tmp_path) == "pip"
    assert envs and envs[0].get("PIP_BREAK_SYSTEM_PACKAGES") == "1"
