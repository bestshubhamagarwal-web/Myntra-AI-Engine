"""File-backed MemoryRepository when Postgres is not running.

CLI ingest and `python -m src.cli serve` share `local_store_path` so Play Store
(and other connectors) can persist without Docker. Not a substitute for Postgres
in a multi-process production setup.

Chat sessions stay in memory for the process lifetime so Copilot turns do not
rewrite a multi-tens-of-thousands-row pickle on every message.
"""

from __future__ import annotations

import logging
import os
import pickle
from pathlib import Path

from src.db.memory import MemoryRepository
from src.db.repository import (
    ChatMessage,
    ChatSession,
    ClusterRun,
    DocumentTheme,
    EmbedRun,
    ExtractRun,
    IngestRun,
    NgramRow,
    ReportArtifact,
    ThemeMetricsSnapshot,
    ThemeRecord,
)

log = logging.getLogger(__name__)


class PersistentMemoryRepository(MemoryRepository):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._suppress_save = False
        self._loaded_mtime = 0.0
        if self._path.is_file() and self._path.stat().st_size > 0:
            self.load()

    def load(self) -> None:
        with self._path.open("rb") as handle:
            state = pickle.load(handle)
        if not isinstance(state, dict):
            raise TypeError(f"invalid local store at {self._path}")
        self._suppress_save = True
        try:
            for key, value in state.items():
                if key.startswith("_"):
                    continue
                setattr(self, key, value)
        finally:
            self._suppress_save = False
        log.info(
            "Loaded local store %s (%s raw, %s normalized)",
            self._path,
            len(self.raw),
            len(self.normalized),
        )
        try:
            self._loaded_mtime = self._path.stat().st_mtime
        except OSError:
            self._loaded_mtime = 0.0

    def save(self) -> None:
        if self._suppress_save:
            return
        if self._path.is_file():
            try:
                size = self._path.stat().st_size
                mtime = self._path.stat().st_mtime
            except OSError:
                size = 0
                mtime = 0.0
            # Stale Query API processes that loaded an 80-row snapshot must not
            # clobber a multi-MB ingest. Do not unpickle the whole corpus here.
            if size > 80_000 and len(self.raw) < 200:
                log.warning(
                    "Refusing to overwrite local store (%.1f MB on disk, %s raw in memory). "
                    "Restart the Query API after ingest so it reloads the corpus.",
                    size / (1024 * 1024),
                    len(self.raw),
                )
                return
            if (
                self._loaded_mtime
                and mtime > self._loaded_mtime + 0.05
                and size > 80_000
                and len(self.raw) < 200
            ):
                log.warning(
                    "Refusing to overwrite a newer local store (mtime changed, %s raw in memory).",
                    len(self.raw),
                )
                return
        state = {key: value for key, value in self.__dict__.items() if not key.startswith("_")}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        with tmp.open("wb") as handle:
            pickle.dump(state, handle, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, self._path)
        try:
            self._loaded_mtime = self._path.stat().st_mtime
        except OSError:
            pass

    def set_enabled(self, source_type: str, enabled: bool) -> None:
        super().set_enabled(source_type, enabled)
        self.save()

    def finish_ingest_run(self, run: IngestRun) -> None:
        super().finish_ingest_run(run)
        self.save()

    def finish_normalize_run(
        self,
        run_id,
        finished_at,
        rows_accepted: int,
        rows_rejected: int,
        status: str,
    ) -> None:
        super().finish_normalize_run(
            run_id,
            finished_at,
            rows_accepted,
            rows_rejected,
            status,
        )
        self.save()

    def finish_extract_run(self, run: ExtractRun) -> None:
        super().finish_extract_run(run)
        self.save()

    def finish_embed_run(self, run: EmbedRun) -> None:
        super().finish_embed_run(run)
        self.save()

    def finish_cluster_run(self, run: ClusterRun) -> None:
        super().finish_cluster_run(run)
        self.save()

    def replace_ngrams(self, cluster_run_id, rows: list[NgramRow]) -> None:
        super().replace_ngrams(cluster_run_id, rows)
        self.save()

    def replace_theme_metrics(self, cluster_run_id, rows: list[ThemeMetricsSnapshot]) -> None:
        super().replace_theme_metrics(cluster_run_id, rows)
        self.save()

    def replace_document_themes(self, cluster_run_id, rows: list[DocumentTheme]) -> None:
        super().replace_document_themes(cluster_run_id, rows)
        self.save()

    def upsert_theme(self, theme: ThemeRecord) -> None:
        super().upsert_theme(theme)
        self.save()

    def insert_report(self, artifact: ReportArtifact) -> None:
        super().insert_report(artifact)
        self.save()

    def insert_chat_session(self, session: ChatSession) -> None:
        super().insert_chat_session(session)

    def insert_chat_message(self, message: ChatMessage) -> None:
        super().insert_chat_message(message)
