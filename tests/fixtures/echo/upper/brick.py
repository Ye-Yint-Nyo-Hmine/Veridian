import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import rawbrick  # noqa: E402


def on_say(req_id, params):
    rawbrick.respond(req_id, {"text": params.get("text", "").upper()})


rawbrick.run(name="echo/upper", version="1.0.0", on_say=on_say)
