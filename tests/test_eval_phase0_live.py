"""Phase 0 live smokes. Skip on CI unless GROQ_API_KEY / RUN_LIVE_BGE are set."""

from __future__ import annotations

import os

import pytest

from src.config import load_settings
from src.embed.bge import smoke_bge
from src.extract.groq_client import ping_groq

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.getenv("GROQ_API_KEY"), reason="GROQ_API_KEY not set")
def test_ev_0_07_groq_reachable():
    result = ping_groq(load_settings())
    assert result["ok"] is True
    assert "groq.com" in result["base_url"]
    assert result["method"] in {"models.list", "chat.completions"}


@pytest.mark.skipif(os.getenv("RUN_LIVE_BGE") != "1", reason="RUN_LIVE_BGE=1 not set")
def test_ev_0_08_bge_m3_dim_1024():
    result = smoke_bge(load_settings())
    assert result["ok"] is True
    assert result["dim"] == 1024
