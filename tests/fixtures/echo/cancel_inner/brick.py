"""Inner brick for the cancellation-cascade test.

``echo_inner.wait`` records that it started, then blocks. If its handler task is cancelled — which
is what a ``$/cancel`` from the kernel does — it records that too before unwinding. The two marker
files let the test synchronise without racing and then assert the cancel reached this far.
"""

import asyncio
import pathlib

from veridian.sdk import Brick, rpc, run


class CancelInner(Brick):
    name = "echo/cancel-inner"
    version = "1.0.0"
    implements = {"echo_inner": ["wait"]}

    @rpc("echo_inner.wait")
    async def wait(self, params, ctx):
        marker_dir = pathlib.Path(self.workspace_root)
        (marker_dir / "inner_started.marker").write_text("1", encoding="utf-8")
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            (marker_dir / "inner_cancelled.marker").write_text("1", encoding="utf-8")
            raise
        return {"ok": True}


if __name__ == "__main__":
    run(CancelInner())
