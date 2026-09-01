"""Local BGE-M3 encode (Architecture §5.1). Vectors never leave the machine.

EC-EM-01: if dim != 1024, fail fast — do not truncate, pad, or insert.
EC-EM-08: if Hugging Face is blocked, load from ./data/models (or BGE_MODEL_ID
as a local path) and fail with a clear error. Never fall back to random vectors
or another embedding host.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Sequence

from src.config import BGE_M3_DIM, Settings, load_settings

SMOKE_SENTENCE = "Myntra wishlist sizing is confusing."
EN_V15_QUERY_PREFIX = "Represent this sentence for searching: "


class EmbeddingDimensionError(RuntimeError):
    """BGE returned a vector whose length is not the frozen pgvector dim."""


class ZeroEmbeddingError(RuntimeError):
    """Refusing to insert a zero or NaN vector (EC-EM-04)."""


class BgeLoadError(RuntimeError):
    """Weights missing or Hugging Face unreachable."""


class EmbeddingCollectionError(RuntimeError):
    """Mixing BGE checkpoints in one chunks table is forbidden (EC-EM-02)."""


def resolve_model_source(settings: Settings) -> tuple[str, bool]:
    """Return (model_id_or_path, local_files_only)."""
    raw = str(settings.bge_model_id).strip()
    path = Path(raw).expanduser()
    if path.exists():
        return str(path.resolve()), True
    offline = os.environ.get("HF_HUB_OFFLINE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    return raw, offline


def assert_embedding_dim(vector: Sequence[float] | Any, expected: int = BGE_M3_DIM) -> int:
    """Fail fast on dim mismatch. Never truncate or pad into vector(1024)."""
    if hasattr(vector, "shape"):
        dim = int(vector.shape[-1])
    else:
        dim = len(vector)
    if dim != expected:
        raise EmbeddingDimensionError(
            f"BGE returned dim={dim}, expected {expected}. "
            "Failing fast; will not truncate, pad, or insert into pgvector."
        )
    return dim


def l2_norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(float(x) * float(x) for x in vector))


def l2_normalize(vector: Sequence[float]) -> list[float]:
    values = [float(x) for x in vector]
    if any(math.isnan(x) or math.isinf(x) for x in values):
        raise ZeroEmbeddingError("BGE returned NaN/Inf; refusing to insert")
    norm = l2_norm(values)
    if norm < 1e-12:
        raise ZeroEmbeddingError("BGE returned a zero vector; refusing to insert")
    return [x / norm for x in values]


def is_bge_m3(model_id: str) -> bool:
    return "bge-m3" in (model_id or "").lower()


def uses_en_v15_query_prefix(model_id: str) -> bool:
    lowered = (model_id or "").lower()
    return "bge-small-en-v1.5" in lowered or "bge-base-en-v1.5" in lowered or "bge-large-en-v1.5" in lowered


def query_text_for_model(query: str, model_id: str) -> str:
    """M3 does not use the en-v1.5 instruction prefix (Architecture §5.1, EC-EM-06)."""
    stripped = (query or "").strip()
    if not stripped:
        raise ValueError("refusing to embed an empty query")
    if uses_en_v15_query_prefix(model_id):
        return f"{EN_V15_QUERY_PREFIX}{stripped}"
    return stripped


def embedding_revision(model: Any) -> str:
    try:
        first = model[0]
        auto = getattr(first, "auto_model", None)
        cfg = getattr(auto, "config", None)
        commit = getattr(cfg, "_commit_hash", None)
        if commit:
            return str(commit)
    except Exception:
        pass
    return "unknown"


def _as_rows(raw: Any, n_texts: int) -> list[Any]:
    if hasattr(raw, "tolist"):
        raw = raw.tolist()
    if raw is None:
        raise EmbeddingDimensionError("BGE returned no vectors")
    if n_texts == 1 and raw and isinstance(raw[0], (int, float)):
        return [raw]
    return list(raw)


def encode_texts(
    model: Any,
    texts: Sequence[str],
    *,
    expected_dim: int = BGE_M3_DIM,
    batch_size: int = 8,
) -> list[list[float]]:
    """Encode texts with L2 normalization. Asserts every vector is expected_dim."""
    if not texts:
        raise ValueError("refusing to encode an empty batch")
    cleaned = [t if (t or "").strip() else None for t in texts]
    if any(item is None for item in cleaned):
        raise ValueError("refusing to encode empty chunk text")
    raw = model.encode(
        list(texts),
        normalize_embeddings=True,
        convert_to_numpy=True,
        batch_size=max(1, batch_size),
        show_progress_bar=False,
    )
    rows = _as_rows(raw, len(texts))
    out: list[list[float]] = []
    for row in rows:
        assert_embedding_dim(row, expected=expected_dim)
        out.append(l2_normalize(row))
    if len(out) != len(texts):
        raise EmbeddingDimensionError(
            f"BGE returned {len(out)} vectors for {len(texts)} texts"
        )
    return out


def encode_query(
    model: Any,
    query: str,
    *,
    model_id: str,
    expected_dim: int = BGE_M3_DIM,
) -> list[float]:
    text = query_text_for_model(query, model_id)
    return encode_texts(model, [text], expected_dim=expected_dim, batch_size=1)[0]


def load_bge_model(settings: Settings | None = None):
    """Load BGE-M3 from Hugging Face or a vendored local path."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise BgeLoadError(
            "sentence-transformers is required for local BGE-M3. "
            "Install project dependencies with pip install -e ."
        ) from exc

    cfg = settings or load_settings()
    cfg.ensure_runtime_dirs()
    cache = cfg.hf_home.expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache))

    model_id, local_only = resolve_model_source(cfg)
    try:
        return SentenceTransformer(
            model_id,
            cache_folder=str(cache),
            local_files_only=local_only,
        )
    except Exception as exc:
        raise BgeLoadError(
            f"Failed to load BGE model {model_id!r}. "
            "If Hugging Face is blocked, vendor BAAI/bge-m3 under "
            f"{cache} and set BGE_MODEL_ID to that folder. "
            "Do not fall back to another embedding host or random vectors. "
            f"Original error: {exc}"
        ) from exc


def smoke_bge(settings: Settings | None = None) -> dict[str, Any]:
    """EV-0-08: load BGE-M3, encode one sentence, assert dim == 1024."""
    cfg = settings or load_settings()
    if cfg.embedding_dim != BGE_M3_DIM:
        raise EmbeddingDimensionError(
            f"EMBEDDING_DIM is {cfg.embedding_dim}, frozen BGE-M3 dim is {BGE_M3_DIM}"
        )
    model = load_bge_model(cfg)
    vectors = encode_texts(model, [SMOKE_SENTENCE], expected_dim=cfg.embedding_dim)
    dim = len(vectors[0])
    return {
        "ok": True,
        "dim": dim,
        "model_id": cfg.bge_model_id,
        "sentence": SMOKE_SENTENCE,
    }
