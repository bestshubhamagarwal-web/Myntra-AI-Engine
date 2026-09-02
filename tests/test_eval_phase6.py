"""Phase 6 auto evals (docs/eval.md EV-6-12, EV-6-19, view checklist)."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"

FORBIDDEN_METRIC_MATH = (
    re.compile(r"share_of_voice\s*=\s*[^;\n]+/"),
    re.compile(r"impact_score\s*=\s*[^;\n]+\*"),
    re.compile(r"mention_count\s*/\s*eligible"),
    re.compile(r"value_counts\s*\("),
)

REQUIRED_ROUTES = (
    "app/(shell)/overview/page.tsx",
    "app/(shell)/themes/page.tsx",
    "app/(shell)/evidence/page.tsx",
    "app/(shell)/categories/page.tsx",
    "app/(shell)/trends/page.tsx",
    "app/(shell)/segments/page.tsx",
    "app/(shell)/sources/page.tsx",
    "app/(shell)/phrases/page.tsx",
    "app/(shell)/reports/page.tsx",
    "app/(shell)/copilot/page.tsx",
)


def _iter_web_ts() -> list[Path]:
    skip = {".next", "node_modules"}
    files: list[Path] = []
    for path in WEB.rglob("*"):
        if any(part in skip for part in path.parts):
            continue
        if path.suffix in {".ts", ".tsx", ".js", ".jsx"}:
            files.append(path)
    return files


def test_vercel_proxy_rejects_localhost_on_vercel() -> None:
    backend = (WEB / "lib" / "backend.ts").read_text(encoding="utf-8")
    proxy = (WEB / "lib" / "query-proxy.ts").read_text(encoding="utf-8")
    route = (WEB / "app" / "api" / "query" / "[...path]" / "route.ts").read_text(encoding="utf-8")
    overview = (WEB / "app" / "api" / "query" / "metrics" / "overview" / "route.ts").read_text(
        encoding="utf-8"
    )
    assert "isVercelRuntime" in backend
    assert "vercel.app" in backend
    assert "railway.internal" in backend
    assert "rlwy.net" in backend
    assert "dpg-" in backend
    assert "neon.tech" in backend
    assert "proxyQuery" in route
    assert "dynamicParams" in route
    assert "metrics" in overview
    assert "resolveBackendBase" in proxy
    api = (WEB / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "errorMessageFromBody" in api
    assert "application failed to respond" in api.lower()


def test_ev_6_19_nextjs_not_streamlit() -> None:
    package = (WEB / "package.json").read_text(encoding="utf-8")
    assert '"next"' in package
    assert (WEB / "app").is_dir()
    blob = "\n".join(p.read_text(encoding="utf-8") for p in _iter_web_ts())
    assert "streamlit" not in blob.lower()
    assert "metabase" not in blob.lower()


def test_ev_6_01_to_10_views_exist() -> None:
    for rel in REQUIRED_ROUTES:
        assert (WEB / rel).is_file(), rel


def test_ev_6_12_no_client_metric_math() -> None:
    for path in _iter_web_ts():
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_METRIC_MATH:
            assert not pattern.search(text), f"{path}: {pattern.pattern}"


def test_ev_6_04_cloud_respects_api_flag() -> None:
    phrases = (WEB / "app/(shell)/phrases/page.tsx").read_text(encoding="utf-8")
    assert "cloud_eligible" in phrases


def test_ev_6_13_filters_are_url_query() -> None:
    filters = (WEB / "lib/filters.tsx").read_text(encoding="utf-8")
    constants = (WEB / "lib/constants.ts").read_text(encoding="utf-8")
    blob = filters + constants
    assert "useSearchParams" in filters
    assert "date_from" in blob
    assert "source_type" in blob
    assert "product_category" in blob


def test_ev_6_14_copilot_opens_drawer() -> None:
    copilot = (WEB / "app/(shell)/copilot/page.tsx").read_text(encoding="utf-8")
    assert "openDrawer" in copilot
    assert "citations" in copilot


def test_play_store_failure_banner_requires_failed_run() -> None:
    banner = (WEB / "components/UnavailableBanner.tsx").read_text(encoding="utf-8")
    sidebar = (WEB / "components/Sidebar.tsx").read_text(encoding="utf-8")
    constants = (WEB / "lib/constants.ts").read_text(encoding="utf-8")
    sources = (WEB / "lib/sources.ts").read_text(encoding="utf-8")
    overview = (WEB / "app/(shell)/overview/page.tsx").read_text(encoding="utf-8")
    copilot = (WEB / "app/(shell)/copilot/page.tsx").read_text(encoding="utf-8")
    assert 'play?.last_run_status === "failed"' in banner
    assert "if (!blocking.length) return null" in banner
    assert "OUT_OF_SCOPE_SOURCES" in constants
    assert 'play.status !== "live"' not in banner
    assert 's.last_run_status === "failed"' in sidebar
    assert "s.status !== \"live\"" not in sidebar
    assert "ingestedSourceRows" in sources
    assert "operatorUnavailable" in sources
    assert "ingestedSourceRows" in overview
    assert 'uppercase text-error">unavailable' not in overview
    assert "operatorUnavailable" in copilot
    assert 'row.status === "live" ? formatInteger(row.eligible_count) : "unavailable"' not in copilot
    sources_page = (WEB / "app/(shell)/sources/page.tsx").read_text(encoding="utf-8")
    assert "UnavailableBadge" not in sources_page
