"""The reusable numbered selector (`veridian.cli.ui.selector`)."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from veridian.cli.ui.selector import select


def _consoles(*, terminal: bool):
    buf, ebuf = io.StringIO(), io.StringIO()
    con = Console(file=buf, width=80, force_terminal=terminal, color_system=None)
    err = Console(file=ebuf, width=80, force_terminal=terminal, color_system=None)
    return con, err, buf, ebuf


class _Tty:
    @staticmethod
    def isatty() -> bool:
        return True


def test_happy_path_returns_zero_based_index(monkeypatch):
    con, err, _, _ = _consoles(terminal=True)
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr("builtins.input", lambda: "2")
    assert select(["a", "b", "c"], prompt="pick", console=con, err_console=err) == 1


def test_out_of_range_then_valid(monkeypatch):
    con, err, _, ebuf = _consoles(terminal=True)
    monkeypatch.setattr("sys.stdin", _Tty())
    answers = iter(["9", "0", "3"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert select(["a", "b", "c"], prompt="pick", console=con, err_console=err) == 2
    assert "not a choice in 1..3" in ebuf.getvalue()


def test_blank_line_cancels(monkeypatch):
    con, err, _, _ = _consoles(terminal=True)
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr("builtins.input", lambda: "")
    assert select(["a", "b"], prompt="pick", console=con, err_console=err) is None


def test_empty_options_is_none(monkeypatch):
    con, err, _, _ = _consoles(terminal=True)
    called = []
    monkeypatch.setattr("builtins.input", lambda: called.append(1))
    assert select([], prompt="pick", console=con, err_console=err) is None
    assert not called  # never prompted


def test_non_tty_eof_returns_none_without_hanging(monkeypatch):
    con, err, _, ebuf = _consoles(terminal=False)  # not a terminal -> one shot only

    def _eof():
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert select(["a", "b"], prompt="pick", console=con, err_console=err) is None
    assert "stdin is not interactive" in ebuf.getvalue()


def test_non_tty_bad_input_does_not_loop(monkeypatch):
    con, err, _, _ = _consoles(terminal=False)
    calls = {"n": 0}

    def _once():
        calls["n"] += 1
        return "not-a-number"

    monkeypatch.setattr("builtins.input", _once)
    assert select(["a", "b"], prompt="pick", console=con, err_console=err) is None
    assert calls["n"] == 1  # a single read, then give up
