"""Resolving the distribution tree — checkout, installed tree, or explicit override.

These are the regression net for making ``veridian`` runnable outside a checkout. The ordering
matters as much as the lookups: a development checkout must keep winning over an installed tree on
the same machine, or a contributor's edits would silently stop taking effect.
"""

from __future__ import annotations

import pytest

from veridian._paths import (
    ENV_HOME,
    ENV_ROOT,
    install_mode,
    installed_root,
    is_dist_root,
    veridian_root,
)
from veridian.contracts import _schemas
from veridian.contracts._schemas import find_schema_dir
from veridian.kernel.config import find_repo_root


def make_tree(base, *, pyproject: bool = True):
    """A directory that looks like a Veridian distribution tree."""
    (base / "schemas" / "protocol").mkdir(parents=True)
    (base / "schemas" / "protocol" / "envelope.schema.json").write_text("{}", encoding="utf-8")
    (base / "bricks").mkdir()
    if pyproject:
        (base / "pyproject.toml").write_text("[project]\nname='veridian'\n", encoding="utf-8")
    return base


def make_install(home, version="9.9.9"):
    """An installed layout: ``versions/<v>/app`` plus the ``current`` pointer."""
    app = make_tree(home / "versions" / version / "app")
    (home / "current").write_text(f"{version}\n", encoding="utf-8")
    return app


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """No inherited VERIDIAN_* env, and a cwd that is not inside any checkout."""
    monkeypatch.delenv(ENV_ROOT, raising=False)
    monkeypatch.delenv("VERIDIAN_SCHEMA_DIR", raising=False)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv(ENV_HOME, str(tmp_path / "home"))
    monkeypatch.chdir(elsewhere)
    return elsewhere


def test_finds_a_checkout_by_walking_up(isolated, tmp_path):
    checkout = make_tree(tmp_path / "checkout")
    nested = checkout / "src" / "veridian"
    nested.mkdir(parents=True)
    assert veridian_root(nested) == checkout


def test_finds_the_installed_tree_from_an_unrelated_directory(isolated, tmp_path):
    app = make_install(tmp_path / "home")
    assert veridian_root() == app
    assert install_mode() == "installed"


def test_a_checkout_beats_an_installed_tree(isolated, tmp_path):
    """The whole reason the walk is ordered first: a contributor inside a checkout must get that
    checkout's bricks and schemas, even with Veridian installed on the same machine."""
    make_install(tmp_path / "home")
    checkout = make_tree(tmp_path / "checkout")
    assert veridian_root(checkout) == checkout
    assert install_mode(checkout) == "dev checkout"


def test_explicit_root_beats_everything(isolated, tmp_path, monkeypatch):
    make_install(tmp_path / "home")
    checkout = make_tree(tmp_path / "checkout")
    override = make_tree(tmp_path / "override")
    monkeypatch.setenv(ENV_ROOT, str(override))
    assert veridian_root(checkout) == override
    assert install_mode(checkout) == ENV_ROOT


def test_a_bad_explicit_root_raises_rather_than_falling_back(isolated, tmp_path, monkeypatch):
    make_install(tmp_path / "home")
    monkeypatch.setenv(ENV_ROOT, str(tmp_path / "nope"))
    with pytest.raises(RuntimeError, match=ENV_ROOT):
        veridian_root()


def test_nothing_installed_and_no_checkout_is_none(isolated):
    assert veridian_root() is None
    assert install_mode() == "not found"


def test_find_repo_root_still_falls_back_to_cwd(isolated):
    """``find_repo_root`` keeps its total signature: callers resolving a relative path against it
    must still get *somewhere* rather than ``None``."""
    assert find_repo_root() == isolated.resolve()


@pytest.mark.parametrize("version", ["", "  ", ".", "..", "../evil", "a/b", "a\\b"])
def test_a_malformed_pointer_is_ignored(isolated, tmp_path, version):
    home = tmp_path / "home"
    make_install(home)
    (home / "current").write_text(version, encoding="utf-8")
    assert installed_root() is None


def test_pointer_to_a_missing_version_is_ignored(isolated, tmp_path):
    home = tmp_path / "home"
    make_install(home)
    (home / "current").write_text("0.0.0\n", encoding="utf-8")
    assert installed_root() is None


def test_half_installed_tree_is_not_selected(isolated, tmp_path):
    """``current`` is written last, but an interrupted extraction should not be usable either."""
    home = tmp_path / "home"
    (home / "versions" / "1.0.0" / "app").mkdir(parents=True)
    (home / "current").write_text("1.0.0\n", encoding="utf-8")
    assert installed_root() is None


def test_is_dist_root_wants_both_schemas_and_bricks(tmp_path):
    partial = tmp_path / "partial"
    (partial / "schemas" / "protocol").mkdir(parents=True)
    assert not is_dist_root(partial)
    (partial / "bricks").mkdir()
    assert is_dist_root(partial)


def test_schemas_resolve_from_an_installed_tree(isolated, tmp_path, monkeypatch):
    """The fatal case before the installer existed: every stack and manifest load validates, so a
    schema dir that cannot be found is a startup crash, not a degraded mode.

    An installed Veridian lives in a venv's site-packages, unrelated to the distribution tree, so
    neither the cwd nor ``__file__``'s ancestors lead anywhere. Relocating ``__file__`` is what
    reproduces that here — inside a checkout the walk would always find the checkout first.
    """
    app = make_install(tmp_path / "home")
    site_packages = tmp_path / "venv" / "site-packages" / "veridian" / "contracts"
    site_packages.mkdir(parents=True)
    monkeypatch.setattr(_schemas, "__file__", str(site_packages / "_schemas.py"))

    assert find_schema_dir() == app / "schemas"


def test_schemas_unfindable_raises_naming_both_overrides(isolated, tmp_path, monkeypatch):
    site_packages = tmp_path / "venv" / "site-packages" / "veridian" / "contracts"
    site_packages.mkdir(parents=True)
    monkeypatch.setattr(_schemas, "__file__", str(site_packages / "_schemas.py"))

    with pytest.raises(RuntimeError, match="VERIDIAN_SCHEMA_DIR"):
        find_schema_dir()
