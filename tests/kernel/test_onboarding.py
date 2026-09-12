"""The first-run wizard's non-interactive parts.

The prompting itself needs a terminal, but the two things that can actually go wrong do not: the
stack it generates has to be a valid stack, and it must never run when nobody is there to answer.
"""

from __future__ import annotations

import io
import tomllib

import pytest
from rich.console import Console

from veridian.cli._common import missing_provider_env
from veridian.cli._common import stacks_root as _stacks_root
from veridian.cli.onboarding import (
    PROVIDERS,
    _ollama_candidates,
    _render_stack,
    probe,
    run_onboarding,
    should_run,
)
from veridian.cli.repl_commands import lookup, render_help
from veridian.cli.userconfig import (
    config_path,
    load_env_file,
    read_config,
    write_config,
    write_env,
)
from veridian.contracts import validate_document
from veridian.kernel.config import find_repo_root, load_stack


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("VERIDIAN_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("VERIDIAN_NO_ONBOARDING", raising=False)
    return tmp_path / "home"


def _base(name: str) -> dict:
    root = find_repo_root()
    path = (
        root / "pre-installed" / "stacks" / "autonomous.toml"
        if name == "autonomous"
        else root / "stacks" / "default.toml"
    )
    return tomllib.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("base_name", ["autonomous", "default"])
@pytest.mark.parametrize("provider", PROVIDERS, ids=lambda p: p.label.split("  ")[0])
def test_generated_stack_is_valid_and_loads(base_name, provider, tmp_path):
    """The generated file has to survive the same schema validation and binding resolution as any
    hand-written stack — including the pre-installed base, which ships with no inference binding."""
    config = {"model": provider.model}
    if provider.base_url:
        config["base_url"] = provider.base_url
    if provider.env_var:
        config["api_key_env"] = provider.env_var

    body = _render_stack(_base(base_name), {"brick": provider.brick, "config": config}, "test")
    raw = tomllib.loads(body)
    validate_document("configuration.schema.json", raw)

    path = tmp_path / "veridian.toml"
    path.write_text(body, encoding="utf-8")
    resolved = load_stack(path)

    binding = resolved.binding_for("inference")
    assert binding is not None
    assert binding.config["model"] == provider.model
    assert resolved.binding_for("orchestrator") is not None


def test_every_provider_names_a_brick_that_exists():
    root = find_repo_root()
    for provider in PROVIDERS:
        assert (root / provider.brick / "veridian.toml").is_file(), provider.brick


def test_key_carrying_providers_are_declared_in_the_bricks_allowlist():
    """A key variable the brick's manifest does not list is scrubbed before the brick ever sees it,
    so the wizard would write a stack that silently cannot authenticate."""
    root = find_repo_root()
    for provider in PROVIDERS:
        if not provider.env_var:
            continue
        manifest = tomllib.loads((root / provider.brick / "veridian.toml").read_text(encoding="utf-8"))
        assert provider.env_var in manifest["env_passthrough"], f"{provider.brick} drops {provider.env_var}"


def test_cloud_providers_declare_a_key_and_local_ones_do_not():
    for provider in PROVIDERS:
        local = provider.detect or provider.ask_base_url
        assert bool(provider.env_var) is not local, provider.label


def test_generated_stack_replaces_an_existing_inference_binding():
    """stacks/default.toml already binds Anthropic; choosing another provider must not leave two."""
    body = _render_stack(
        _base("default"), {"brick": "bricks/inference/openai", "config": {"model": "gpt-4o-mini"}}, "test"
    )
    assert body.count("inference =") == 1
    assert tomllib.loads(body)["bindings"]["inference"]["brick"] == "bricks/inference/openai"


def test_ollama_detection_reports_the_url_and_models(monkeypatch):
    """The point of detecting: the user states neither the URL nor a model name."""
    import httpx

    def fake_get(url, **kwargs):
        assert url == "http://localhost:11434/v1/models"
        return httpx.Response(
            200,
            json={"data": [{"id": "qwen3:8b"}, {"id": "llama3.2"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(httpx, "get", fake_get)
    assert probe(_ollama_candidates()) == ("http://localhost:11434/v1", ["llama3.2", "qwen3:8b"])


def test_probe_returns_none_when_nothing_answers(monkeypatch):
    """A stopped Ollama must not hang or crash the first run — it degrades to a default."""
    import httpx

    def refuse(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", refuse)
    assert probe(["http://localhost:11434/v1"]) is None


def test_ollama_host_is_honoured_first(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "192.168.1.5:11434")
    assert _ollama_candidates()[0] == "http://192.168.1.5:11434/v1"


def test_does_not_run_without_a_terminal(home, monkeypatch):
    """Piped or redirected input must skip the wizard rather than block on a prompt."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert should_run(None) is False


def test_does_not_run_when_a_stack_was_named(home, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert should_run("local-ollama") is False


def test_does_not_run_twice(home, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert should_run(None) is True
    write_config({"default_stack": "veridian"})
    assert should_run(None) is False


def test_env_file_round_trips_without_overriding_the_environment(home, monkeypatch):
    write_env({"ANTHROPIC_API_KEY": "from-file"})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    load_env_file()
    assert __import__("os").environ["ANTHROPIC_API_KEY"] == "from-file"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    load_env_file()
    assert __import__("os").environ["ANTHROPIC_API_KEY"] == "from-shell"


def test_a_local_binding_never_warns_about_a_missing_key(tmp_path, monkeypatch):
    """A local model server needs no credentials, and the shipped local stacks name none. Guessing
    a requirement from the brick's manifest would warn about OPENAI_API_KEY forever."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    resolved = load_stack(find_repo_root() / "stacks" / "local-ollama.toml")
    assert missing_provider_env(resolved) == []


def test_a_declared_key_is_reported_when_unset_and_not_when_set(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resolved = load_stack(find_repo_root() / "stacks" / "default.toml")
    assert missing_provider_env(resolved) == ["ANTHROPIC_API_KEY"]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert missing_provider_env(resolved) == []


def test_setup_is_in_the_registry_and_help(home):
    """`/help` renders from the registry, so a command missing from it is invisible."""
    assert lookup("/setup") is not None
    assert "/setup" in render_help()


def test_setup_writes_nothing_when_cancelled(home, monkeypatch, capsys):
    """Backing out at the first prompt must leave the session and the home directory alone."""
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    result = run_onboarding(Console(), Console(stderr=True), builtin_stacks=_stacks_root(), returning=True)
    assert result is None
    assert not config_path().exists()
    assert not (home / "stacks").exists()


def test_switch_stack_reloads_the_same_path_only_when_forced(tmp_path):
    """`/setup` usually rewrites the stack that is already bound, so the same-path short circuit
    that protects `/stack` from pointless churn has to be bypassable — otherwise re-running setup
    would report "already on veridian" and keep serving the old provider."""
    import asyncio

    from veridian.cli.repl_commands import _SessionState, ReplContext, _switch_stack
    from veridian.cli.ui import Renderer
    from veridian.kernel import Kernel, load_stack

    root = find_repo_root()
    path = tmp_path / "veridian.toml"
    path.write_text(
        _render_stack(_base("default"), {"brick": "bricks/inference/local", "config": {"model": "a"}}, "x"),
        encoding="utf-8",
    )

    loop = asyncio.new_event_loop()
    try:
        resolved = load_stack(path)
        ctx = ReplContext(
            kernel=Kernel(resolved, workspace_root=root),
            resolved=resolved, loop=loop,
            renderer=Renderer(Console(), Console(stderr=True)),
            workspace=root, model="a", session_id="t", session_store=None,
            state=_SessionState("auto"), max_iterations=4,
        )

        # Rewrite the same file with a different model, the way /setup does.
        path.write_text(
            _render_stack(_base("default"), {"brick": "bricks/inference/local", "config": {"model": "b"}}, "x"),
            encoding="utf-8",
        )

        _switch_stack(ctx, str(path))
        assert ctx.resolved.binding_for("inference").config["model"] == "a", "unforced should no-op"

        _switch_stack(ctx, str(path), force=True)
        assert ctx.resolved.binding_for("inference").config["model"] == "b"
    finally:
        loop.close()


def test_a_corrupt_config_does_not_stop_a_run(home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text("this is not = valid = toml", encoding="utf-8")
    assert read_config() == {}
