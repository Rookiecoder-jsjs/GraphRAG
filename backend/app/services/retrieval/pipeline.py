"""Pipeline runner: executes a named list of retrieval steps."""
import time
from typing import List, Sequence

from app.services.retrieval.context import RetrievalContext
from app.services.retrieval.steps import STEP_REGISTRY, RetrievalStep


def parse_pipeline(csv: str) -> List[str]:
    """Split a comma-separated step list (config) into step names.

    Empty/whitespace entries are dropped so a trailing comma in .env
    does not produce a bogus step name.
    """
    return [s.strip() for s in csv.split(",") if s.strip()]


def build_pipeline(step_names: Sequence[str]) -> List[RetrievalStep]:
    """Instantiate steps by name, failing loudly on unknown names.

    Raises:
        ValueError: on an unknown step name — a typo here would otherwise
            surface only on the first retrieval request.
    """
    steps: List[RetrievalStep] = []
    for name in step_names:
        try:
            steps.append(STEP_REGISTRY[name]())
        except KeyError:
            raise ValueError(
                f"Unknown retrieval step {name!r}; "
                f"registered: {sorted(STEP_REGISTRY)}"
            ) from None
    return steps


async def run_pipeline(
    steps: Sequence[RetrievalStep], ctx: RetrievalContext
) -> RetrievalContext:
    """Run steps in order, timing each into ``ctx.timings[step.name]``."""
    for step in steps:
        t0 = time.perf_counter()
        ctx = await step(ctx)
        ctx.timings[step.name] = time.perf_counter() - t0
    return ctx
