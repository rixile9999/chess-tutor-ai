"""Tiny in-process job runner. Analysis is CPU-bound and runs in a thread; the API stays async."""

from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

JobFn = Callable[[], Awaitable[None]]


@dataclass
class Job:
    key: str
    fn: JobFn
    status: str = "pending"  # pending | running | done | failed
    error: str | None = None


@dataclass
class JobRunner:
    _jobs: dict[str, Job] = field(default_factory=dict)
    _queue: asyncio.Queue[Job] | None = None
    _worker: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Bind to the running loop. Safe to call repeatedly; a stopped runner starts fresh."""
        if self._worker is None or self._worker.done():
            self._queue = asyncio.Queue()
            self._worker = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        self._worker = None
        self._queue = None

    def submit(self, key: str, fn: JobFn) -> Job:
        existing = self._jobs.get(key)
        if existing and existing.status in ("pending", "running"):
            return existing
        self.start()
        assert self._queue is not None
        job = Job(key=key, fn=fn)
        self._jobs[key] = job
        self._queue.put_nowait(job)
        return job

    def get(self, key: str) -> Job | None:
        return self._jobs.get(key)

    async def wait(self, key: str, timeout: float = 60.0) -> Job:
        """Poll until the job leaves pending/running (tests and CLI use)."""
        job = self._jobs[key]
        waited = 0.0
        while job.status in ("pending", "running") and waited < timeout:
            await asyncio.sleep(0.05)
            waited += 0.05
        return job

    async def _run(self) -> None:
        assert self._queue is not None
        queue = self._queue
        while True:
            job = await queue.get()
            job.status = "running"
            try:
                await job.fn()
                job.status = "done"
            except Exception as exc:  # noqa: BLE001 - job errors are recorded, not raised
                job.status = "failed"
                job.error = f"{exc}\n{traceback.format_exc()}"
                log.exception("job %s failed", job.key)
            finally:
                queue.task_done()


runner = JobRunner()
