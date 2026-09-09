from __future__ import annotations

from pathlib import Path

import pytest

from veridian.kernel import Kernel, RestartPolicy, load_stack

STACKS = Path(__file__).resolve().parents[1] / "fixtures" / "stacks"


def make_kernel(stack_name: str, workspace: Path, **kw) -> Kernel:
    stack = load_stack(STACKS / stack_name)
    kw.setdefault("restart_policy", RestartPolicy(max_restarts=2, backoff_base=0.2, window_seconds=30))
    return Kernel(stack, workspace_root=workspace, **kw)


@pytest.fixture
def kernel_factory(tmp_path):
    created: list[Kernel] = []

    async def _factory(stack_name: str, **kw):
        k = make_kernel(stack_name, tmp_path, **kw)
        created.append(k)
        await k.start()
        return k

    yield _factory

    # teardown handled per-test; nothing to do if start failed
