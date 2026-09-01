"""Exclusive file lock so overlapping cron/n8n jobs do not double-write (EC-IN-16, EC-OP-06)."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class ExclusiveFileLock:
    """O_EXCL lock file. Stale locks (mtime older than TTL) are stolen.

    Re-entrant for the same PID so `pipeline` can call `ingest` without deadlock.
    """

    def __init__(self, path: Path, *, stale_seconds: int = 7200) -> None:
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self.fd: int | None = None
        self.acquired = False
        self.reentrant = False

    def _pid_owns(self) -> bool:
        try:
            text = self.path.read_text(encoding="utf-8").strip().split()[0]
            return int(text) == os.getpid()
        except (OSError, ValueError, IndexError):
            return False

    def _stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
            return age > self.stale_seconds
        except OSError:
            return True

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self._pid_owns():
            self.acquired = True
            self.reentrant = True
            return True
        for _ in range(3):
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(self.fd, f"{os.getpid()}\n".encode("utf-8"))
                self.acquired = True
                self.reentrant = False
                return True
            except FileExistsError:
                if self._stale():
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                return False
        return False

    def release(self) -> None:
        if self.reentrant:
            self.acquired = False
            self.reentrant = False
            return
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        if self.acquired:
            try:
                self.path.unlink()
            except OSError:
                pass
        self.acquired = False


@contextmanager
def exclusive_lock(path: Path, *, stale_seconds: int = 7200) -> Iterator[bool]:
    lock = ExclusiveFileLock(path, stale_seconds=stale_seconds)
    got = lock.acquire()
    try:
        yield got
    finally:
        if got:
            lock.release()
