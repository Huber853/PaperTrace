from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

from .harness import AgentHarness
from .model_provider import DeepSeekModelProvider
from .repository import AgentRepository
from .tools import build_default_tool_registry


def build_harness(repository: AgentRepository) -> AgentHarness:
    return AgentHarness(
        repository=repository,
        registry=build_default_tool_registry(),
        model_provider=DeepSeekModelProvider(),
    )


class AgentWorker:
    def __init__(
        self,
        repository: AgentRepository,
        harness_factory: Callable[[], AgentHarness] | None = None,
        poll_interval: float = 1.0,
    ):
        self.repository = repository
        self.harness_factory = harness_factory or (lambda: build_harness(repository))
        self.poll_interval = poll_interval
        self._stopping = asyncio.Event()

    async def run_once(self) -> bool:
        run = self.repository.claim_next_run()
        if run is None:
            return False
        await self.harness_factory().run(run.id)
        return True

    async def serve(self) -> None:
        self.repository.recover_running()
        while not self._stopping.is_set():
            worked = await self.run_once()
            if not worked:
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(),
                        timeout=self.poll_interval,
                    )
                except TimeoutError:
                    pass

    def stop(self) -> None:
        self._stopping.set()


async def main() -> None:
    poll_interval = float(os.getenv("AGENT_WORKER_POLL_SECONDS", "1"))
    worker = AgentWorker(AgentRepository(), poll_interval=poll_interval)
    await worker.serve()


if __name__ == "__main__":
    asyncio.run(main())

