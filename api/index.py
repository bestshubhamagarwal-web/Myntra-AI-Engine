"""Vercel FastAPI entrypoint.

The Python runtime looks for a FastAPI instance named `app` in `api/index.py`.
Do not add other `.py` files under `api/` — this app handles every route.
"""

from __future__ import annotations

from src.api.app import create_app

app = create_app(migrate_on_boot=True)
