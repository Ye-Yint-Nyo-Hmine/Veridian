"""Session metadata and resumable sessions.

Every interactive session gets ``~/.veridian/sessions/<id>/meta.json`` (via :func:`veridian_home`,
never :func:`Path.home` directly). The ``<id>`` is the same one shown on the boot screen *and* the
one the conversation brick keys history under, so ``veridian --resume <id>`` brings back the
conversation itself, not just the header line.

The record is rewritten after every turn, every ``/mode`` / ``/model`` change, and at exit, each
write atomic (temp file + replace), so an abrupt kill still leaves a current, resumable record.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from veridian.plugin_runtime.home import veridian_home

_FIELDS = ("id", "workspace", "stack", "model", "mode", "started_at", "last_activity_at", "summary")


def sessions_dir() -> Path:
    """``VERIDIAN_HOME/sessions`` — resolved, not created."""
    return veridian_home() / "sessions"


def new_session_id() -> str:
    return uuid.uuid4().hex


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class SessionMeta:
    id: str
    workspace: str
    stack: str
    model: str
    mode: str
    started_at: str
    last_activity_at: str
    summary: str = ""

    @classmethod
    def from_json(cls, data: dict) -> "SessionMeta":
        return cls(**{f: str(data.get(f, "")) for f in _FIELDS})


class SessionStore:
    def __init__(self, meta: SessionMeta) -> None:
        self.meta = meta

    @property
    def dir(self) -> Path:
        return sessions_dir() / self.meta.id

    @property
    def path(self) -> Path:
        return self.dir / "meta.json"

    @classmethod
    def create(
        cls, session_id: str, *, workspace, stack: str, model: str, mode: str
    ) -> "SessionStore":
        now = _now()
        store = cls(
            SessionMeta(
                id=session_id,
                workspace=str(workspace),
                stack=stack,
                model=model,
                mode=mode,
                started_at=now,
                last_activity_at=now,
            )
        )
        store._write()
        return store

    def _write(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name("meta.json.tmp")
        tmp.write_text(json.dumps(asdict(self.meta), indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def update(self, **fields) -> None:
        for k, v in fields.items():
            if k in _FIELDS and k not in ("id", "started_at"):
                setattr(self.meta, k, v)
        self.meta.last_activity_at = _now()
        self._write()

    def touch(self) -> None:
        self.meta.last_activity_at = _now()
        self._write()

    def set_summary(self, text: str) -> None:
        self.update(summary=" ".join((text or "").split())[:140])


def load(session_id: str) -> SessionMeta | None:
    p = sessions_dir() / session_id / "meta.json"
    try:
        return SessionMeta.from_json(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_recent(limit: int = 20) -> list[SessionMeta]:
    root = sessions_dir()
    if not root.is_dir():
        return []
    metas = [m for d in root.iterdir() if d.is_dir() and (m := load(d.name)) is not None]
    metas.sort(key=lambda m: m.last_activity_at, reverse=True)
    return metas[:limit]


__all__ = [
    "SessionMeta",
    "SessionStore",
    "sessions_dir",
    "new_session_id",
    "load",
    "list_recent",
]
