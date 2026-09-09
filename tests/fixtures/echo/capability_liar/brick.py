import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import rawbrick  # noqa: E402

# Reports only say + stream, though the manifest also claims "secret".
rawbrick.run(name="echo/capability-liar", version="1.0.0", reported_methods=["say", "stream"])
