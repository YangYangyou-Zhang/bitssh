from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Callable

log = logging.getLogger("bitssh.transfers")


class TransferManager:
    """Runs SFTP transfers as tasks and exposes their progress for polling.

    The asyncssh progress handler is synchronous and called once per block,
    so it only writes into the item dict; the frontend polls at ~2 Hz.
    """

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.tasks: dict[str, asyncio.Task] = {}

    def start(
        self,
        kind: str,
        label: str,
        job: Callable[[Callable], object],
        target: str | None = None,
    ) -> str:
        transfer_id = uuid.uuid4().hex
        item = {
            "id": transfer_id,
            "kind": kind,
            "label": label,
            "target": target,
            "status": "running",
            "copied": 0,
            "total": 0,
            "error": None,
        }
        self.items[transfer_id] = item

        def progress(_src, _dst, copied: int, total: int) -> None:
            item["copied"] = copied
            item["total"] = total

        self.tasks[transfer_id] = asyncio.create_task(self._run(transfer_id, item, job, progress))
        return transfer_id

    async def _run(self, transfer_id: str, item: dict, job, progress) -> None:
        try:
            await job(progress)
        except asyncio.CancelledError:
            item["status"] = "cancelled"
            raise
        except Exception as exc:
            log.warning("transfer %s failed: %s", transfer_id, exc)
            item["status"] = "failed"
            item["error"] = str(exc)
        else:
            item["status"] = "done"
            if item["total"]:
                item["copied"] = item["total"]
        finally:
            self.tasks.pop(transfer_id, None)

    def get(self, transfer_id: str) -> dict | None:
        return self.items.get(transfer_id)

    def list(self) -> list[dict]:
        return list(self.items.values())

    def cancel(self, transfer_id: str) -> bool:
        task = self.tasks.get(transfer_id)
        if task is None:
            return False
        task.cancel()
        return True
