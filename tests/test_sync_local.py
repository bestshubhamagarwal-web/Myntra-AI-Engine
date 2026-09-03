"""Tests for pushing local pickle corpus into Postgres."""

from src.api.rag import retrieve_quotes
from src.db.memory import MemoryRepository
from src.db.sync_local import sync_local_to_postgres
from src.models.envelope import RawEnvelope, SourceType
from src.timeutil import utcnow


class _RecordingPostgres:
    def __init__(self) -> None:
        self.raw: list[RawEnvelope] = []
        self.normalized = []
        self.extractions = []
        self.enabled: list[tuple[str, bool]] = []

    def set_enabled(self, source_type: str, enabled: bool) -> None:
        self.enabled.append((source_type, enabled))

    def connect(self):
        return _FakeConn(self)

    def upsert_raw(self, envelope: RawEnvelope):
        self.raw.append(envelope)
        return envelope.id, True

    def upsert_normalized(self, record) -> None:
        self.normalized.append(record)

    def upsert_extraction(self, record) -> None:
        self.extractions.append(record)


class _FakeConn:
    def __init__(self, repo: _RecordingPostgres) -> None:
        self._repo = repo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return _FakeCursor(self._repo)

    def commit(self) -> None:
        return None


class _FakeCursor:
    def __init__(self, repo: _RecordingPostgres) -> None:
        self._repo = repo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def executemany(self, sql: str, rows: list) -> None:
        if "raw_documents" in sql:
            for row in rows:
                self._repo.raw.append(
                    RawEnvelope(
                        source_type=SourceType.play_store,
                        source_id=row[2],
                        url=row[3],
                        fetched_at=row[4],
                        published_at=row[5],
                        platform=row[6],
                        raw_text=row[7],
                        raw_title=row[8],
                        star_rating=row[9],
                        parent_context={},
                        author_hash=row[11],
                        content_hash=row[15],
                        ingest_run_id=None,
                        date_anomaly=row[17],
                    )
                )


def test_sync_local_to_postgres_copies_raw_rows():
    local = MemoryRepository()
    envelope = RawEnvelope(
        source_type=SourceType.play_store,
        source_id="r1",
        url="https://example.test/r1",
        fetched_at=utcnow(),
        published_at=None,
        platform="android",
        raw_text="wishlist save for later",
        raw_title=None,
        star_rating=4,
        parent_context={},
        author_hash="hash",
        content_hash="abc",
        ingest_run_id=None,
        date_anomaly=False,
    )
    local.raw[envelope.id] = envelope
    target = _RecordingPostgres()
    counts = sync_local_to_postgres(local, target)  # type: ignore[arg-type]
    assert counts["raw"] == 1
    assert len(target.raw) == 1


def test_retrieve_quotes_still_works_on_memory_repo():
    repo = MemoryRepository()
    rows = retrieve_quotes(repo, "wishlist save for later", limit=3)
    assert isinstance(rows, list)
