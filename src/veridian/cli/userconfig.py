"""User-level settings under ``VERIDIAN_HOME`` — what the first run records, and the optional
environment file it can write.

Two small files, both owned by the user rather than by any stack:

* ``config.toml`` — choices that outlive a session. Today just ``default_stack``, the stack a bare
  ``veridian`` uses when ``--stack`` is not given.
* ``env`` — ``KEY=value`` lines loaded into the process environment at CLI start. Provider bricks
  read their keys from the environment through their manifest's ``env_passthrough`` allowlist, so
  a key placed here reaches exactly the brick that needs it and nothing else. The real environment
  always wins, so ``ANTHROPIC_API_KEY=... veridian`` still overrides the file.

Neither file is required; Veridian runs identically without both.
"""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path

from veridian.plugin_runtime.home import veridian_home

CONFIG_FILENAME = "config.toml"
ENV_FILENAME = "env"


def config_path() -> Path:
    return veridian_home() / CONFIG_FILENAME


def env_path() -> Path:
    return veridian_home() / ENV_FILENAME


def read_config() -> dict:
    """The user's settings, or an empty dict. Never raises — a corrupt file must not stop a run."""
    try:
        return tomllib.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def write_config(values: dict) -> Path:
    """Merge ``values`` into the user's settings and write them back."""
    merged = {**read_config(), **values}
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k} = {v!r}\n" for k, v in sorted(merged.items()) if isinstance(v, str))
    path.write_text(f"# Veridian user settings. Written by the first run; safe to edit.\n{body}", encoding="utf-8")
    return path


def load_env_file() -> None:
    """Load ``VERIDIAN_HOME/env`` into ``os.environ`` without overriding anything already set."""
    try:
        text = env_path().read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def write_env(values: dict[str, str]) -> Path:
    """Merge ``values`` into the env file, readable only by the owner where the OS allows it.

    The file holds secrets in plaintext. ``0600`` is the most the filesystem can do about that on
    POSIX and it is a no-op on Windows, so callers must say where the key is being written rather
    than implying it is protected.
    """
    existing: dict[str, str] = {}
    try:
        for line in env_path().read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()
    except OSError:
        pass

    path = env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {**existing, **values}
    body = "".join(f"{k}={v}\n" for k, v in sorted(merged.items()))
    path.write_text(f"# Loaded into the environment by veridian. Plaintext.\n{body}", encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return path
