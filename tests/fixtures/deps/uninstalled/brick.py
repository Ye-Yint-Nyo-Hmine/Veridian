"""Never actually executed: the kernel refuses to spawn this brick because it declares
``[dependencies]`` with no installed environment. Present only so the manifest resolves to a
real directory."""

import six  # noqa: F401  -- the pin that would fail against the kernel interpreter

raise SystemExit("deps/uninstalled should never be spawned")
