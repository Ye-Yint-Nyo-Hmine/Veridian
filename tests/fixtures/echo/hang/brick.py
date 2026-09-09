import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import rawbrick  # noqa: E402


def on_say(req_id, params):
    sys.stderr.write("received echo.say; hanging forever, will not respond\n")
    sys.stderr.flush()
    while True:
        time.sleep(3600)


rawbrick.run(name="echo/hang", version="1.0.0", on_say=on_say)
