"""conversation/sqlite — local chat history in one SQLite file.

An append-only turn log, keyed by session. Distinct from ``memory``: this is the verbatim
transcript, not a curated recollection. The file lives under the workspace (``.veridian/
conversation.sqlite`` by default) and is never sent anywhere — the brick has no ``network``
capability and could not open a socket if it tried.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from veridian.sdk import Brick, rpc, run


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _preview(content) -> str:
    if isinstance(content, str):
        text = content
    else:
        text = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    text = " ".join(text.split())
    return text[:120]


class ConversationSQLite(Brick):
    name = "conversation/sqlite"
    version = "0.1.0"
    implements = {"conversation": ["append", "load", "list_sessions", "delete"]}

    async def on_initialize(self) -> bool:
        if self.config.get("in_memory"):
            self.db = sqlite3.connect(":memory:")
        else:
            if self.config.get("path"):
                path = Path(self.config["path"]).expanduser()
            else:
                root = Path(self.config.get("root") or self.workspace_root).resolve()
                path = root / ".veridian" / "conversation.sqlite"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(path))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS turn("
            "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, seq INTEGER NOT NULL, "
            "content TEXT NOT NULL, metadata TEXT, created_at TEXT NOT NULL)"
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS turn_session ON turn(session_id, seq)")
        self.db.commit()
        return True

    def _entry(self, row) -> dict:
        # row columns: (id, session_id, seq, content, metadata, created_at)
        entry = {
            "id": row[0],
            "seq": row[2],
            "created_at": row[5],
            "message": json.loads(row[3]),
        }
        if row[4]:
            entry["metadata"] = json.loads(row[4])
        return entry

    @rpc("conversation.append")
    async def append(self, params, ctx):
        session_id = params["session_id"]
        row = self.db.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM turn WHERE session_id=?", (session_id,)
        ).fetchone()
        seq = int(row[0]) + 1
        turn_id = uuid.uuid4().hex
        metadata = params.get("metadata")
        self.db.execute(
            "INSERT INTO turn VALUES (?,?,?,?,?,?)",
            (
                turn_id,
                session_id,
                seq,
                json.dumps(params["message"]),
                json.dumps(metadata) if metadata is not None else None,
                _now(),
            ),
        )
        self.db.commit()
        return {"id": turn_id, "seq": seq}

    @rpc("conversation.load")
    async def load(self, params, ctx):
        session_id = params["session_id"]
        sql = "SELECT * FROM turn WHERE session_id=?"
        args: list = [session_id]
        if params.get("before_seq"):
            sql += " AND seq < ?"
            args.append(int(params["before_seq"]))
        sql += " ORDER BY seq ASC"
        rows = self.db.execute(sql, args).fetchall()
        if params.get("limit"):
            rows = rows[-int(params["limit"]) :]
        return {"session_id": session_id, "messages": [self._entry(r) for r in rows]}

    @rpc("conversation.list_sessions")
    async def list_sessions(self, params, ctx):
        rows = self.db.execute(
            "SELECT session_id, MIN(created_at), MAX(created_at), COUNT(*) "
            "FROM turn GROUP BY session_id ORDER BY MAX(created_at) DESC"
        ).fetchall()
        if params.get("limit"):
            rows = rows[: int(params["limit"])]
        sessions = []
        for sid, created, updated, count in rows:
            first = self.db.execute(
                "SELECT content FROM turn WHERE session_id=? ORDER BY seq ASC LIMIT 1", (sid,)
            ).fetchone()
            info = {
                "session_id": sid,
                "created_at": created,
                "updated_at": updated,
                "message_count": count,
            }
            if first:
                info["preview"] = _preview(json.loads(first[0]).get("content", ""))
            sessions.append(info)
        return {"sessions": sessions}

    @rpc("conversation.delete")
    async def delete(self, params, ctx):
        cur = self.db.execute("DELETE FROM turn WHERE session_id=?", (params["session_id"],))
        self.db.commit()
        return {"deleted": cur.rowcount}


if __name__ == "__main__":
    run(ConversationSQLite())
