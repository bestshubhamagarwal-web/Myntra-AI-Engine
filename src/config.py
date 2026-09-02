from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GROQ_DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
BGE_DEFAULT_MODEL_ID = "BAAI/bge-m3"
BGE_M3_DIM = 1024

# Frozen quantification + model ids (Architecture §8.5, Phase 7). Do not change
# silently: update these constants in git, then re-run `python -m src.cli eval`.
FROZEN_C_MAX = 200
FROZEN_S_MAX = 4
FROZEN_GROQ_MODEL = "openai/gpt-oss-120b"
FROZEN_GROQ_MODEL_LIGHT = "openai/gpt-oss-20b"
FROZEN_BGE_MODEL_ID = BGE_DEFAULT_MODEL_ID
FROZEN_EMBEDDING_DIM = BGE_M3_DIM
log = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Runtime config. Groq is generation-only; BGE is local. No OpenAI host."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Empty DATABASE_URL on Render must not revive the localhost default.
        env_ignore_empty=True,
    )

    database_url: str = "postgresql://discovery:discovery@localhost:5432/discovery"
    groq_api_key: str = ""
    groq_base_url: str = GROQ_DEFAULT_BASE_URL
    groq_model: str = FROZEN_GROQ_MODEL
    groq_model_light: str = FROZEN_GROQ_MODEL_LIGHT
    bge_model_id: str = FROZEN_BGE_MODEL_ID
    embedding_dim: int = FROZEN_EMBEDDING_DIM
    hf_home: Path = Path("./data/models")
    author_hmac_secret: str = "change-me-to-a-long-random-string"
    raw_store_path: Path = Path("./data/raw")
    review_dump_path: Path = Path("./data/review")
    play_store_app_id: str = "com.myntra.android"
    play_store_country: str = "in"
    play_store_lang: str = "en"
    play_store_max_reviews: int = 25000
    play_store_page_sleep_seconds: float = 0.2
    play_store_enabled: bool = True
    app_store_app_id: str = "907394059"
    app_store_country: str = "in"
    app_store_countries: str = "in,us,gb,ae,sg,au"
    app_store_max_reviews: int = 20000
    app_store_page_sleep_seconds: float = 0.2
    app_store_enabled: bool = True
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "myntra-discovery-engine/0.1 (research; public read-only)"
    reddit_subreddits: str = (
        "IndianFashionAddicts,IndianStreetwear,FashionReps,femalefashionadvice,"
        "malefashionadvice,india,AskIndia,Mumbai,delhi,bangalore,Pune,Hyderabad,"
        "Chennai,Kolkata,IndiaSpeaks,IndianMakeupAddicts,Frugal_Ind"
    )
    reddit_max_posts: int = 400
    reddit_max_comments_per_post: int = 50
    reddit_page_sleep_seconds: float = 1.2
    reddit_enabled: bool = True
    youtube_api_key: str = ""
    youtube_max_videos: int = 12
    youtube_max_comments_per_video: int = 40
    youtube_page_sleep_seconds: float = 0.4
    youtube_enabled: bool = True
    x_bearer_token: str = ""
    x_max_tweets: int = 50
    x_page_sleep_seconds: float = 0.5
    x_enabled: bool = True
    c_max: int = FROZEN_C_MAX
    s_max: int = FROZEN_S_MAX
    allow_unfrozen_constants: bool = False
    cluster_min_cluster_size: int = 5
    cluster_min_samples: int = 5
    cluster_selection_epsilon: float = 0.0
    cluster_metric: str = "euclidean"
    cluster_allow_single_cluster: bool = True
    cluster_knn_k: int = 5
    cluster_knn_min_similarity: float = 0.55
    cluster_centroid_match_min_similarity: float = 0.70
    cluster_recluster_new_docs: int = 40
    cluster_kmeans_max_k: int = 8
    cluster_kmeans_noise_similarity: float = 0.40
    groq_extract_max_tokens: int = 2048
    groq_label_max_tokens: int = 1024
    groq_max_retries: int = 4
    groq_json_retries: int = 3
    groq_backoff_base_seconds: float = 2.0
    groq_max_tpm: int = 8000
    groq_min_interval_seconds: float = 0.0
    extract_prompt_path: Path | None = None
    embed_batch_size: int = 4
    chunk_max_tokens: int = 400
    chunk_overlap_tokens: int = 50
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_shared_secret: str = ""
    api_cors_origins: str = "http://localhost:3000"
    reports_path: Path = Path("./data/reports")
    small_n_threshold: int = 5
    copilot_max_chunks: int = 12
    copilot_max_tool_rounds: int = 4
    groq_copilot_max_tokens: int = 2048
    copilot_prompt_path: Path | None = None
    report_prompt_path: Path | None = None
    lock_path: Path = Path("./data/locks")
    lock_stale_seconds: int = 7200
    local_store_path: Path = Path("./data/local_store.pkl")
    # Render/production: never fall back to local_store.pkl when Postgres is down.
    require_postgres: bool = False
    postgres_wait_seconds: float = 60.0

    @field_validator("database_url")
    @classmethod
    def strip_database_url(cls, value: str) -> str:
        url = (value or "").strip().strip('"').strip("'")
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        return url

    @field_validator("groq_base_url")
    @classmethod
    def groq_host_only(cls, value: str) -> str:
        url = (value or "").strip().rstrip("/")
        if "api.openai.com" in url:
            raise ValueError("GROQ_BASE_URL must be Groq, not the OpenAI API host")
        if "groq.com" not in url:
            raise ValueError("GROQ_BASE_URL must be a groq.com URL")
        return url

    @field_validator("play_store_app_id")
    @classmethod
    def myntra_play_only(cls, value: str) -> str:
        app_id = (value or "").strip()
        if app_id != "com.myntra.android":
            raise ValueError(
                "Play Store connector is Myntra-only (com.myntra.android). "
                "Competitor app pages are out of scope."
            )
        return app_id

    @field_validator("app_store_app_id")
    @classmethod
    def myntra_app_store_only(cls, value: str) -> str:
        app_id = (value or "").strip()
        if app_id != "907394059":
            raise ValueError(
                "App Store connector is Myntra-only (id 907394059). "
                "Competitor app pages are out of scope."
            )
        return app_id

    @field_validator("embedding_dim")
    @classmethod
    def lock_bge_m3_dim(cls, value: int) -> int:
        if value != BGE_M3_DIM:
            raise ValueError(
                f"EMBEDDING_DIM must be {BGE_M3_DIM} for BGE-M3. "
                "Do not truncate or pad vectors. A different checkpoint needs a full re-embed."
            )
        return value

    def require_hmac_secret(self) -> str:
        secret = (self.author_hmac_secret or "").strip()
        if not secret or secret == "change-me-to-a-long-random-string":
            # Allow default in local/dev so tests run; production should override.
            return secret or "dev-hmac-secret"
        return secret

    def ensure_runtime_dirs(self) -> None:
        for path in (
            self.raw_store_path,
            self.review_dump_path,
            self.hf_home,
            self.reports_path,
            self.lock_path,
            self.local_store_path.parent,
        ):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                log.warning("Could not create runtime dir %s (%s).", path, exc)

    def cors_origin_list(self) -> list[str]:
        return [part.strip() for part in (self.api_cors_origins or "").split(",") if part.strip()]

    def require_api_secret_if_public(self) -> None:
        host = (self.api_host or "").strip()
        secret = (self.api_shared_secret or "").strip()
        public = host not in {"127.0.0.1", "localhost", "::1"}
        if public and not secret:
            # Render injects PORT. Exiting here means nothing listens and Vercel
            # sees Render's 502 Bad Gateway.
            if (os.environ.get("PORT") or "").strip():
                return
            raise ValueError(
                "API_SHARED_SECRET is required when binding beyond localhost "
                f"(API_HOST={host}). Prototype auth is a shared secret."
            )


def resolve_listen_port(explicit: int | None = None, settings: Settings | None = None) -> int:
    """Bind port: CLI --port, then platform PORT (Render), then API_PORT."""
    if explicit is not None:
        return int(explicit)
    raw = (os.environ.get("PORT") or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"PORT must be an integer, got {raw!r}") from exc
    cfg = settings or load_settings()
    return int(cfg.api_port)


def frozen_snapshot(settings: Settings) -> dict:
    """Compare live settings to the git-frozen constants (EV-7-05)."""
    expected = {
        "C_max": FROZEN_C_MAX,
        "S_max": FROZEN_S_MAX,
        "GROQ_MODEL": FROZEN_GROQ_MODEL,
        "GROQ_MODEL_LIGHT": FROZEN_GROQ_MODEL_LIGHT,
        "BGE_MODEL_ID": FROZEN_BGE_MODEL_ID,
        "EMBEDDING_DIM": FROZEN_EMBEDDING_DIM,
    }
    actual = {
        "C_max": int(settings.c_max),
        "S_max": int(settings.s_max),
        "GROQ_MODEL": settings.groq_model,
        "GROQ_MODEL_LIGHT": settings.groq_model_light,
        "BGE_MODEL_ID": settings.bge_model_id,
        "EMBEDDING_DIM": int(settings.embedding_dim),
    }
    mismatches = {key: {"expected": expected[key], "actual": actual[key]} for key in expected if expected[key] != actual[key]}
    return {
        "expected": expected,
        "actual": actual,
        "mismatches": mismatches,
        "matches_frozen": not mismatches,
        "allow_unfrozen_constants": settings.allow_unfrozen_constants,
    }


def require_frozen_constants(settings: Settings) -> None:
    snap = frozen_snapshot(settings)
    if snap["matches_frozen"] or settings.allow_unfrozen_constants:
        return
    parts = [
        f"{key}: {item['actual']!r} != frozen {item['expected']!r}"
        for key, item in snap["mismatches"].items()
    ]
    raise ValueError(
        "Constants must not change silently (Phase 7). "
        + "; ".join(parts)
        + ". Update FROZEN_* in src/config.py and re-run eval, or set "
        "ALLOW_UNFROZEN_CONSTANTS=true for a documented experiment."
    )


def load_settings() -> Settings:
    # Render/Railway images must not load a laptop .env (localhost DATABASE_URL).
    hosted = bool(
        (os.environ.get("RENDER") or "").strip()
        or (os.environ.get("RENDER_SERVICE_ID") or "").strip()
        or (os.environ.get("RAILWAY_ENVIRONMENT") or "").strip()
        or (os.environ.get("RAILWAY_PROJECT_ID") or "").strip()
    )
    if hosted:
        from src.db.connect import apply_hosted_database_env

        apply_hosted_database_env()
        raw = (os.environ.get("DATABASE_URL") or "").strip()
        extra: dict[str, str] = {}
        if not raw:
            # Do not revive the Settings default (localhost) after dropping a laptop DSN.
            extra["database_url"] = ""
        return Settings(_env_file=None, **extra)
    return Settings()
