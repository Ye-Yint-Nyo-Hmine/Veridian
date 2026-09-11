"""``@path`` mention resolution (`veridian.cli.mentions`)."""

from __future__ import annotations

from veridian.cli import mentions
from veridian.cli.mentions import MAX_BYTES, build_turn, notes, resolve


def test_line_without_a_mention_is_unchanged(tmp_path):
    found, err = resolve("just a plain goal", tmp_path)
    assert found == [] and err is None
    assert build_turn("just a plain goal", found) == "just a plain goal"


def test_file_mention_is_resolved_and_embedded(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    found, err = resolve("explain @pkg/a.py please", tmp_path)
    assert err is None and len(found) == 1
    m = found[0]
    assert m.kind == "file" and not m.truncated
    assert m.bytes == len((tmp_path / "pkg" / "a.py").read_bytes())
    assert "x = 1" in m.content
    turn = build_turn("explain @pkg/a.py please", found)
    assert "explain @pkg/a.py please" in turn
    assert "# @pkg/a.py" in turn and "file" in turn and "x = 1" in turn


def test_oversize_file_is_truncated_and_noted(tmp_path):
    (tmp_path / "big.txt").write_text("A" * (MAX_BYTES + 500), encoding="utf-8")
    found, err = resolve("@big.txt", tmp_path)
    assert err is None
    m = found[0]
    assert m.truncated and m.bytes == MAX_BYTES
    assert any("truncated" in n for n in notes(found))
    assert "(truncated to" in build_turn("@big.txt", found)


def test_directory_is_listed_not_read(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "one.py").write_text("", encoding="utf-8")
    (tmp_path / "d" / "sub").mkdir()
    found, err = resolve("look at @d", tmp_path)
    assert err is None
    m = found[0]
    assert m.kind == "dir" and m.bytes == 2
    assert set(m.names) == {"one.py", "sub/"}
    assert any("directory listed" in n for n in notes(found))


def test_missing_path_is_an_error_before_the_turn(tmp_path):
    found, err = resolve("@does/not/exist do the thing", tmp_path)
    assert found == []
    assert err is not None and "not found" in err


def test_path_escaping_the_workspace_is_rejected(tmp_path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    found, err = resolve("@../secret.txt", tmp_path)
    assert found == []
    assert err is not None and "outside the workspace" in err


def test_quoted_path_with_spaces(tmp_path):
    (tmp_path / "a b").mkdir()
    (tmp_path / "a b" / "c.txt").write_text("hi", encoding="utf-8")
    found, err = resolve('read @"a b/c.txt"', tmp_path)
    assert err is None and found[0].kind == "file" and found[0].content == "hi"
