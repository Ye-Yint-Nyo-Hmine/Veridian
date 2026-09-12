"""The workspace is whichever directory Veridian was started in, which makes one mistake easy to
make and hard to read once made: starting in a home directory points every workspace-scoped brick
at the whole machine."""

from __future__ import annotations

from pathlib import Path

from veridian.cli.workspace import workspace_warning


def test_a_project_directory_is_fine(tmp_path):
    ws = tmp_path / "project"
    ws.mkdir()
    assert workspace_warning(ws) is None


def test_the_home_directory_is_flagged():
    assert "home directory" in (workspace_warning(Path.home()) or "")


def test_the_directory_homes_live_in_is_flagged():
    parent = Path.home().resolve().parent
    assert workspace_warning(parent) is not None


def test_a_filesystem_root_is_flagged():
    root = Path(Path.home().resolve().anchor)
    assert "filesystem root" in (workspace_warning(root) or "")


def test_a_directory_under_home_is_not_flagged(tmp_path, monkeypatch):
    """Only home itself, not everything inside it — most real projects live under it."""
    home = tmp_path / "home"
    (home / "code" / "proj").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert workspace_warning(home) is not None
    assert workspace_warning(home / "code" / "proj") is None
