import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import rawbrick  # noqa: E402


def on_say(req_id, params):
    # Three malformed stdout lines (below the codec's consecutive-bad threshold), then a good one.
    sys.stdout.write("this is not json\n")
    sys.stdout.write("{ also not: valid\n")
    sys.stdout.write("<html>nope</html>\n")
    sys.stdout.flush()
    rawbrick.respond(req_id, {"text": params.get("text", "")})


rawbrick.run(name="echo/garbage-then-recovers", version="1.0.0", on_say=on_say)
