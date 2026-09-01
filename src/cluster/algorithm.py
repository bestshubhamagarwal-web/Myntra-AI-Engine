"""HDBSCAN on L2-normalized BGE vectors; k-means only if HDBSCAN errors.

Tiny corpora and all-noise results stay 0–few themes. Never force k=10 (EC-CL-01).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Sequence
from uuid import UUID

logger = logging.getLogger(__name__)

NOISE_LABEL = -1


@dataclass
class ClusterParams:
    min_cluster_size: int = 5
    min_samples: int = 5
    cluster_selection_epsilon: float = 0.0
    metric: str = "euclidean"
    allow_single_cluster: bool = True
    knn_k: int = 5
    knn_min_similarity: float = 0.55
    centroid_match_min_similarity: float = 0.70
    recluster_new_docs: int = 40
    kmeans_max_k: int = 8
    kmeans_noise_similarity: float = 0.40

    def as_dict(self) -> dict[str, float | int | str | bool]:
        return {
            "min_cluster_size": self.min_cluster_size,
            "min_samples": self.min_samples,
            "cluster_selection_epsilon": self.cluster_selection_epsilon,
            "metric": self.metric,
            "allow_single_cluster": self.allow_single_cluster,
            "knn_k": self.knn_k,
            "knn_min_similarity": self.knn_min_similarity,
            "centroid_match_min_similarity": self.centroid_match_min_similarity,
            "recluster_new_docs": self.recluster_new_docs,
            "kmeans_max_k": self.kmeans_max_k,
            "kmeans_noise_similarity": self.kmeans_noise_similarity,
        }


def params_from_settings(settings) -> ClusterParams:
    return ClusterParams(
        min_cluster_size=int(settings.cluster_min_cluster_size),
        min_samples=int(settings.cluster_min_samples),
        cluster_selection_epsilon=float(settings.cluster_selection_epsilon),
        metric=str(settings.cluster_metric),
        allow_single_cluster=bool(settings.cluster_allow_single_cluster),
        knn_k=int(settings.cluster_knn_k),
        knn_min_similarity=float(settings.cluster_knn_min_similarity),
        centroid_match_min_similarity=float(settings.cluster_centroid_match_min_similarity),
        recluster_new_docs=int(settings.cluster_recluster_new_docs),
        kmeans_max_k=int(settings.cluster_kmeans_max_k),
        kmeans_noise_similarity=float(settings.cluster_kmeans_noise_similarity),
    )


def l2_normalize(vector: Sequence[float]) -> list[float]:
    values = [float(x) for x in vector]
    norm = math.sqrt(sum(v * v for v in values))
    if norm < 1e-12:
        return values
    return [v / norm for v in values]


def mean_vector(vectors: Sequence[Sequence[float]]) -> list[float] | None:
    usable = [list(v) for v in vectors if v]
    if not usable:
        return None
    dim = len(usable[0])
    acc = [0.0] * dim
    n = 0
    for item in usable:
        if len(item) != dim:
            continue
        for i, value in enumerate(item):
            acc[i] += float(value)
        n += 1
    if n == 0:
        return None
    return l2_normalize([value / n for value in acc])


def cosine(a: Sequence[float] | None, b: Sequence[float] | None) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    return float(sum(float(a[i]) * float(b[i]) for i in range(n)))


@dataclass
class ClusterFit:
    algorithm: str
    labels: list[int]
    centroids: dict[int, list[float]]
    n_noise: int
    n_clusters: int
    caveat: str | None = None


def _kmeans_k(n: int, params: ClusterParams) -> int:
    """Data-dependent k. Never forced to 10."""
    if n < max(params.min_cluster_size, 2):
        return 0
    by_size = max(2, n // max(params.min_cluster_size, 2))
    return int(max(2, min(params.kmeans_max_k, by_size, n)))


def _centroids_from_labels(
    vectors: Sequence[Sequence[float]],
    labels: Sequence[int],
) -> dict[int, list[float]]:
    buckets: dict[int, list[list[float]]] = {}
    for vector, label in zip(vectors, labels):
        if label == NOISE_LABEL:
            continue
        buckets.setdefault(int(label), []).append(list(vector))
    out: dict[int, list[float]] = {}
    for label, members in buckets.items():
        centroid = mean_vector(members)
        if centroid is not None:
            out[label] = centroid
    return out


def _fit_hdbscan(matrix, params: ClusterParams) -> list[int]:
    from sklearn.cluster import HDBSCAN

    kwargs: dict = {
        "min_cluster_size": max(int(params.min_cluster_size), 2),
        "min_samples": max(int(params.min_samples or params.min_cluster_size), 1),
        "metric": params.metric or "euclidean",
        "cluster_selection_epsilon": float(params.cluster_selection_epsilon or 0.0),
    }
    try:
        model = HDBSCAN(
            allow_single_cluster=bool(params.allow_single_cluster),
            copy=True,
            **kwargs,
        )
    except TypeError:
        try:
            model = HDBSCAN(allow_single_cluster=bool(params.allow_single_cluster), **kwargs)
        except TypeError:
            model = HDBSCAN(**kwargs)
    raw = model.fit_predict(matrix)
    return [int(x) for x in raw]


def _fit_kmeans(
    matrix,
    vectors: Sequence[Sequence[float]],
    params: ClusterParams,
) -> list[int]:
    from sklearn.cluster import KMeans

    n = len(vectors)
    k = _kmeans_k(n, params)
    if k < 2:
        return [NOISE_LABEL] * n
    model = KMeans(n_clusters=k, n_init=10, random_state=0)
    raw = [int(x) for x in model.fit_predict(matrix)]
    centroids = _centroids_from_labels(vectors, raw)
    out: list[int] = []
    for vector, label in zip(vectors, raw):
        centroid = centroids.get(label)
        if cosine(vector, centroid) < params.kmeans_noise_similarity:
            out.append(NOISE_LABEL)
        else:
            out.append(label)
    return out


def cluster_vectors(
    vectors: Sequence[Sequence[float]],
    params: ClusterParams,
    *,
    force_algorithm: str | None = None,
) -> ClusterFit:
    n = len(vectors)
    if n == 0:
        return ClusterFit("none", [], {}, 0, 0, caveat="empty_corpus")

    normalized = [l2_normalize(v) for v in vectors]
    if n < params.min_cluster_size:
        return ClusterFit(
            "none",
            [NOISE_LABEL] * n,
            {},
            n_noise=n,
            n_clusters=0,
            caveat="tiny_corpus",
        )

    import numpy as np

    matrix = np.asarray(normalized, dtype=np.float64)
    algorithm = "hdbscan"
    labels: list[int]
    caveat: str | None = None

    requested = (force_algorithm or "hdbscan").lower()
    if requested == "kmeans":
        algorithm = "kmeans"
        labels = _fit_kmeans(matrix, normalized, params)
        caveat = "kmeans_requested"
    else:
        try:
            labels = _fit_hdbscan(matrix, params)
        except Exception as exc:
            logger.warning("HDBSCAN failed (%s); k-means fallback", exc)
            algorithm = "kmeans"
            labels = _fit_kmeans(matrix, normalized, params)
            caveat = f"hdbscan_error:{type(exc).__name__}"

    centroids = _centroids_from_labels(normalized, labels)
    n_clusters = len(centroids)
    n_noise = sum(1 for label in labels if label == NOISE_LABEL)
    if n_clusters == 0:
        caveat = caveat or "all_noise"
    return ClusterFit(
        algorithm=algorithm,
        labels=labels,
        centroids=centroids,
        n_noise=n_noise,
        n_clusters=n_clusters,
        caveat=caveat,
    )


@dataclass
class CentroidMatch:
    label_to_theme_id: dict[int, UUID] = field(default_factory=dict)
    unmatched_labels: list[int] = field(default_factory=list)


def match_centroids(
    new_centroids: dict[int, list[float]],
    previous: Sequence[tuple[UUID, Sequence[float] | None]],
    min_similarity: float,
) -> CentroidMatch:
    """Greedy cosine match so reclusters keep theme_id (EC-CL-05)."""
    pairs: list[tuple[float, int, UUID]] = []
    for label, centroid in new_centroids.items():
        for theme_id, old in previous:
            sim = cosine(centroid, old)
            if sim >= min_similarity:
                pairs.append((sim, int(label), theme_id))
    pairs.sort(key=lambda item: item[0], reverse=True)
    used_labels: set[int] = set()
    used_themes: set[UUID] = set()
    mapping: dict[int, UUID] = {}
    for _sim, label, theme_id in pairs:
        if label in used_labels or theme_id in used_themes:
            continue
        mapping[label] = theme_id
        used_labels.add(label)
        used_themes.add(theme_id)
    unmatched = [label for label in new_centroids if label not in mapping]
    return CentroidMatch(label_to_theme_id=mapping, unmatched_labels=unmatched)


def knn_assign(
    vector: Sequence[float],
    labeled: Sequence[tuple[UUID, Sequence[float]]],
    *,
    k: int,
    min_similarity: float,
) -> tuple[UUID | None, float]:
    """Assign a new doc to an existing theme by k nearest labeled neighbors."""
    if not labeled or k <= 0:
        return None, 0.0
    scored: list[tuple[float, UUID]] = []
    for theme_id, other in labeled:
        scored.append((cosine(vector, other), theme_id))
    scored.sort(key=lambda item: item[0], reverse=True)
    top = scored[:k]
    if not top or top[0][0] < min_similarity:
        return None, top[0][0] if top else 0.0
    votes: dict[UUID, float] = {}
    for sim, theme_id in top:
        if sim < min_similarity:
            continue
        votes[theme_id] = votes.get(theme_id, 0.0) + sim
    if not votes:
        return None, top[0][0]
    winner = max(votes.items(), key=lambda item: item[1])[0]
    best = max(sim for sim, theme_id in top if theme_id == winner)
    return winner, best
