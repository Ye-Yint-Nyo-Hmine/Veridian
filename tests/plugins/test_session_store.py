"""Session metadata store and its resume listing (`veridian.session_store`)."""

from __future__ import annotations

import json

from veridian import session_store
from veridian.session_store import SessionStore, list_recent, new_session_id
from veridian.session_store import load as load_session


def test_create_writes_meta_json_immediately(tmp_path, monkeypatch):
    monkeypatch.setenv("VERIDIAN_HOME", str(tmp_path))
    sid = new_session_id()
    store = SessionStore.create(sid, workspace=tmp_path, stack="local-ollama", model="qwen", mode="plan")
    meta_path = tmp_path / "sessions" / sid / "meta.json"
    assert meta_path.is_file()
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    assert data["id"] == sid and data["stack"] == "local-ollama" and data["mode"] == "plan"
    assert data["started_at"] and data["started_at"] == data["last_activity_at"]
    assert store.meta.id == sid


def test_incremental_update_survives_without_an_explicit_close(tmp_path, monkeypatch):
    monkeypatch.setenv("VERIDIAN_HOME", str(tmp_path))
    sid = new_session_id()
    store = SessionStore.create(sid, workspace=tmp_path, stack="s", model="m1", mode="plan")
    store.update(model="m2")
    store.update(mode="auto")
    store.set_summary("did a thing\nacross two lines")
    # simulate an abrupt exit: no close/flush call — read straight back from disk
    meta = load_session(sid)
    assert meta.model == "m2" and meta.mode == "auto"
    assert meta.summary == "did a thing across two lines"
    assert meta.started_at == store.meta.started_at  # never rewritten


def test_list_recent_orders_by_last_activity(tmp_path, monkeypatch):
    monkeypatch.setenv("VERIDIAN_HOME", str(tmp_path))
    a = SessionStore.create("aaaa", workspace=tmp_path, stack="s", model="m", mode="plan")
    b = SessionStore.create("bbbb", workspace=tmp_path, stack="s", model="m", mode="plan")
    a.meta.last_activity_at = "2020-01-01T00:00:00Z"
    a._write()
    b.meta.last_activity_at = "2099-01-01T00:00:00Z"
    b._write()
    ids = [m.id for m in list_recent()]
    assert ids[:2] == ["bbbb", "aaaa"]


def test_missing_session_loads_as_none(tmp_path, monkeypatch):
    monkeypatch.setenv("VERIDIAN_HOME", str(tmp_path))
    assert load_session("nope") is None
    assert list_recent() == []
    assert session_store.sessions_dir() == tmp_path / "sessions"
