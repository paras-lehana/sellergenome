"""Lifespan-owned source refresh and index delivery workers.

Only configured read-only adapters run here. No model calls, audio downloads,
external writes or seller contacts are part of a refresh job.
"""

import asyncio
import logging
import json

from backend.connectors import ConnectorError, ConnectorService
from backend.jobs import JobConflict, JobRepository
from backend.persona import utcnow

logger = logging.getLogger(__name__)
RETRYABLE = {
    "timeout",
    "unavailable",
    "network_unreachable",
    "job_pending",
    "upstream_408",
    "upstream_429",
    "upstream_500",
    "upstream_502",
    "upstream_503",
    "upstream_504",
}


class SyncWorker:
    """Run one source job and one delivery at a time with durable restart state."""

    def __init__(self, store, search, config, service_factory=None) -> None:
        self.config, self.search = config, search
        self.settings = config.values["jobs"]
        self.service_factory = service_factory or (
            lambda: ConnectorService(config.values.get("connectors", {}))
        )
        self.repository = JobRepository(
            store,
            self.settings,
            policy_check=(
                lambda source, seller_id: self.service_factory().ensure_target(
                    source, seller_id
                )
            )
            if service_factory is None
            else None,
        )
        self.tasks: list[asyncio.Task] = []
        self.last_tick_at: str | None = None
        self.error_code: str | None = None

    async def start(self) -> None:
        """No recurring source work exists until an operator enables a schedule."""
        if not self.tasks:
            self.tasks = [
                asyncio.create_task(self._source_loop()),
                asyncio.create_task(self._index_loop()),
            ]

    async def stop(self) -> None:
        """Cancel network waits and leave recoverable durable receipts."""
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []

    def summary(self) -> dict:
        """Expose aggregate readiness without private seller IDs."""
        return {
            **self.repository.summary(),
            "worker": {
                "enabled": self.settings["enabled"],
                "status": "running"
                if self.settings["enabled"]
                and self.tasks
                and all(not t.done() for t in self.tasks)
                else "paused",
                "last_tick_at": self.last_tick_at,
                "error_code": self.error_code,
            },
        }

    async def fetch_apply(
        self, source: str, seller_id: str, job: dict | None = None
    ) -> dict:
        """Normalize a source once; the repository applies only real revisions."""
        seller = self.repository.store.get("sellers", seller_id)
        if not seller:
            raise ConnectorError(
                source,
                "invalid_seller",
                "Select an existing approved or synthetic source target.",
            )
        service = self.service_factory()
        if hasattr(service, "ensure_target"):
            service.ensure_target(source, seller_id)
        if job and isinstance(service, ConnectorService):
            service.resume = (
                json.loads(job["remote_progress"])
                if job.get("remote_progress")
                else None
            )
            service.checkpoint = lambda progress: self.repository.checkpoint(
                job, progress
            )
        events = await service.fetch(source, seller_id)
        if events and not any(
            set(e.get("attributes", {})) - {"observed_at"} for e in events
        ):
            raise ConnectorError(
                source,
                "mapping_required",
                "Source field mapping requires validation.",
            )
        if hasattr(service, "ensure_target"):
            service.ensure_target(source, seller_id)
        return self.repository.apply_events(
            seller_id, events, job, getattr(service, "last_receipt", {})
        )

    async def _renew(self, job: dict) -> None:
        """Heartbeat during slow network work; cancellation fences commit separately."""
        while True:
            await asyncio.sleep(self.settings["lease_seconds"] / 3)
            if not self.repository.heartbeat(job):
                return

    async def run_once(self) -> bool:
        """Execute one due claim; safe failures do not kill the worker loop."""
        if not self.settings["enabled"]:
            return False
        job = self.repository.claim()
        if not job:
            return False
        renewal = asyncio.create_task(self._renew(job))
        try:
            await self.fetch_apply(job["source"], job["seller_id"], job)
        except ConnectorError as error:
            self.repository.fail(job, error.code, error.code in RETRYABLE)
            self.repository.store.put(
                "metadata",
                "connector-" + job["source"],
                {"status": error.code, "at": utcnow()},
            )
        except JobConflict:
            # Cancelled or fenced work cannot overwrite its terminal state.
            self.repository.fail(job, "revision_conflict", False)
        except asyncio.CancelledError:
            self.repository.release(job)
            raise
        except Exception:
            self.repository.fail(job, "worker_error", False)
            logger.warning(
                "Source job failed with worker_error; private exception omitted."
            )
        finally:
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)
        return True

    async def deliver_once(self) -> bool:
        """Retry current local documents; generation-aware receipts preserve new writes."""
        item = self.repository.claim_index()
        if not item:
            return False
        try:
            success = item["payload"] is not None and await self.search.put(
                item["kind"], item["document_id"], item["payload"]
            )
        except asyncio.CancelledError:
            self.repository.finish_index(item, False)
            raise
        except Exception:
            success = False
        self.repository.finish_index(item, success)
        return True

    async def _source_loop(self) -> None:
        """Dispatch bounded schedules and continue after safe storage failures."""
        while True:
            self.last_tick_at = utcnow()
            try:
                if self.settings["enabled"]:
                    self.repository.dispatch_schedules()
                    await self.run_once()
                self.error_code = None
            except Exception:
                self.error_code = "worker_unavailable"
                logger.warning(
                    "Source worker unavailable; private exception omitted."
                )
            await asyncio.sleep(self.settings["poll_seconds"])

    async def _index_loop(self) -> None:
        """Keep delivery independent so a slow source never blocks indexing."""
        while True:
            try:
                if self.settings["enabled"] and self.config.es_url:
                    await self.deliver_batch()
            except Exception:
                logger.warning(
                    "Index delivery unavailable; durable queue retained."
                )
            await asyncio.sleep(self.settings["poll_seconds"])

    async def deliver_batch(self) -> int:
        """Use bounded ES bulk requests and generation-aware per-item receipts."""
        items = []
        for _ in range(self.settings["index_batch_size"]):
            item = self.repository.claim_index()
            if not item:
                break
            if item["payload"] is None:
                self.repository.finish_index(item, False)
            else:
                items.append(item)
        if not items:
            return 0
        try:
            receipts = await self.search.deliver_batch(items)
        except asyncio.CancelledError:
            for item in items:
                self.repository.finish_index(item, False)
            raise
        except Exception:
            receipts = [False] * len(items)
        if len(receipts) != len(items):
            receipts = [False] * len(items)
        for item, success in zip(items, receipts):
            self.repository.finish_index(item, success)
        return len(items)
