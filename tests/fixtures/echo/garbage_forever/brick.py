import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import rawbrick  # noqa: E402


def on_say(req_id, params):
    for i in range(1000):
        sys.stdout.write(f"garbage line {i} <<not json>>\n")
        sys.stdout.flush()
        time.sleep(0.001)


rawbrick.run(name="echo/garbage-forever", version="1.0.0", on_say=on_say)
