"""A brick that speaks exactly ``veridian/1.0`` — it hard-codes the version in its initialize
response instead of echoing what the kernel offered. Used to prove a 1.0 peer still binds against
a 1.1 kernel (major-version negotiation)."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import rawbrick  # noqa: E402


def _initialize(req_id, params):
    rawbrick.respond(
        req_id,
        {"protocol_version": "veridian/1.0", "brick": {"name": "echo/legacy-1-0", "version": "1.0.0"}, "ready": True},
    )


rawbrick.run(
    name="echo/legacy-1-0",
    version="1.0.0",
    reported_methods=["say"],
    extra={"plugin.initialize": _initialize},
)
