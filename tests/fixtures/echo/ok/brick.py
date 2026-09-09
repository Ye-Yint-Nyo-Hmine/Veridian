import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import rawbrick  # noqa: E402

rawbrick.run(name="echo/ok", version="1.0.0")
