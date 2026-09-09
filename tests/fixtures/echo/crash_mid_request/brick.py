import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import rawbrick  # noqa: E402


def on_say(req_id, params):
    sys.stderr.write("about to crash without responding\n")
    sys.stderr.flush()
    raise SystemExit(1)


rawbrick.run(name="echo/crash-mid-request", version="1.0.0", on_say=on_say)
