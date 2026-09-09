import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import depbrick  # noqa: E402

depbrick.run(name="deps/six-new", contract="workspace")
