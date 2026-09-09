import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import rawbrick  # noqa: E402


def via_host(req_id, params):
    rawbrick.host_call("host.log", {"level": "info", "message": "about to call echo.say via host"})
    out = rawbrick.host_call(
        "host.contract.call",
        {"contract": "echo", "method": "say", "params": {"text": params.get("text", "")}},
    )
    rawbrick.respond(req_id, {"text": out["result"]["text"], "via": "host"})


def needs_perm(req_id, params):
    res = rawbrick.host_call(
        "host.permission.request", {"capability": params.get("capability", "network"), "reason": "test"}
    )
    rawbrick.respond(req_id, {"granted": res["granted"]})


rawbrick.run(
    name="echo/roundtrip",
    version="1.0.0",
    reported_methods=["say", "stream", "via_host", "needs_perm"],
    extra={"echo.via_host": via_host, "echo.needs_perm": needs_perm},
)
