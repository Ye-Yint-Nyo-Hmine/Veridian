"""The first run — choosing a provider and an agent, once.

A contributor editing a stack TOML is a reasonable ask. Someone who just installed Veridian with
one command is not in that position: a bare ``veridian`` would otherwise load a stack bound to a
provider they may not use and fail on their first goal. This module asks three questions instead,
writes the answers to ``VERIDIAN_HOME``, and never asks again.

What it writes:

* ``VERIDIAN_HOME/stacks/veridian.toml`` — the chosen agent's stack plus an inference binding.
  The pre-installed autonomous stack deliberately ships without one, so a generated copy is what
  makes it runnable.
* ``VERIDIAN_HOME/config.toml`` — ``default_stack``, so a bare ``veridian`` picks it up.
* ``VERIDIAN_HOME/env`` — only if the user asks for the key to be saved.

It runs only on a terminal. Piped or redirected input skips it entirely rather than blocking, so
scripts and CI keep the previous behaviour.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from veridian.cli.ui.selector import select
from veridian.cli.ui.theme import GLYPHS
from veridian.cli.userconfig import config_path, write_config, write_env
from veridian.plugin_runtime.home import home_stacks

STACK_NAME = "veridian"


@dataclass(frozen=True)
class Provider:
    label: str
    brick: str
    model: str
    env_var: str | None = None
    base_url: str | None = None
    detect: bool = False
    ask_base_url: bool = False
    key_hint: str | None = None


# Gemini, DeepSeek and Moonshot all serve the OpenAI chat-completions API, so they are the same
# adapter behind a different base URL. Each keeps its own conventional key variable, named in the
# generated stack as `api_key_env`, so one provider's key is never handed to another.
PROVIDERS = (
    Provider(
        "Anthropic  (Claude)", "bricks/inference/anthropic", "claude-opus-5",
        env_var="ANTHROPIC_API_KEY", key_hint="console.anthropic.com",
    ),
    Provider(
        "OpenAI  (GPT)", "bricks/inference/openai", "gpt-4o-mini",
        env_var="OPENAI_API_KEY", key_hint="platform.openai.com",
    ),
    Provider(
        "Google  (Gemini)", "bricks/inference/openai", "gemini-2.5-pro",
        env_var="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        key_hint="aistudio.google.com",
    ),
    Provider(
        "DeepSeek", "bricks/inference/openai", "deepseek-chat",
        env_var="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com/v1",
        key_hint="platform.deepseek.com",
    ),
    Provider(
        "Moonshot  (Kimi)", "bricks/inference/openai", "kimi-latest",
        env_var="MOONSHOT_API_KEY", base_url="https://api.moonshot.ai/v1",
        key_hint="platform.moonshot.ai",
    ),
    Provider(
        "Ollama  (local, no key)", "bricks/inference/local", "llama3.2", detect=True,
    ),
    Provider(
        "Other OpenAI-compatible server  (llama.cpp, vLLM, LM Studio)",
        "bricks/inference/local", "llama3.2", ask_base_url=True,
    ),
)

AGENTS = (
    ("Autonomous  — repo-map context, verifying loop, git awareness", "autonomous"),
    ("Reference  — the minimal loop, for seeing what each contract does", "default"),
)

OLLAMA_DEFAULT = "http://localhost:11434/v1"


def should_run(stack_override: str | None) -> bool:
    """Only on a first run, only on a terminal, and never when a stack was named explicitly."""
    if stack_override or os.environ.get("VERIDIAN_NO_ONBOARDING"):
        return False
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    return not config_path().exists()


def _ollama_candidates() -> list[str]:
    """Where Ollama might be listening, best guess first. ``OLLAMA_HOST`` is Ollama's own variable,
    so someone who has already moved the port has usually set it."""
    urls: list[str] = []
    host = os.environ.get("OLLAMA_HOST", "").strip()
    if host:
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        urls.append(host.rstrip("/") + "/v1")
    urls += [OLLAMA_DEFAULT, "http://127.0.0.1:11434/v1"]
    return list(dict.fromkeys(urls))


def probe(urls: list[str], *, timeout: float = 2.0) -> tuple[str, list[str]] | None:
    """The first URL that answers, and the models it reports.

    Every OpenAI-compatible server exposes ``/models``, so asking it is both the reachability check
    and the model list — which is the whole reason not to make someone type either one.
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover — httpx is a core dependency
        return None

    for url in urls:
        try:
            response = httpx.get(f"{url.rstrip('/')}/models", timeout=timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001 — any failure just means "not this one"
            continue
        models = [m["id"] for m in payload.get("data", []) if isinstance(m, dict) and m.get("id")]
        return url, sorted(models)
    return None


def _ask(console: Console, prompt: str, default: str = "") -> str | None:
    hint = f" [{default}]" if default else ""
    console.print(f"[v.prompt]{GLYPHS.caret} {prompt}{hint}[/]", end=" ")
    try:
        raw = input().strip()
    except EOFError:
        console.print()
        return None
    return raw or default


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items()) + " }"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_stack(base: dict, inference: dict, description: str) -> str:
    """Serialise a stack: the base's policy and bindings, with our inference binding added.

    Stack files are a small, flat shape — a name, a grant list, and one binding per contract — so a
    handful of lines beats taking on a TOML-writing dependency.
    """
    lines = [
        "# Written by the Veridian first run. Edit freely, or delete it to be asked again.",
        "",
        "[stack]",
        f'name = "{STACK_NAME}"',
        f"description = {_toml_value(description)}",
        "",
        "[policy]",
        f"grant = {_toml_value(base.get('policy', {}).get('grant', []))}",
    ]
    deny = base.get("policy", {}).get("deny")
    if deny:
        lines.append(f"deny = {_toml_value(deny)}")

    lines += ["", "[bindings]"]
    for contract, value in base.get("bindings", {}).items():
        if contract == "inference":
            continue
        lines.append(f"{contract} = {_toml_value(value)}")
    lines.append(f"inference = {_toml_value(inference)}")
    return "\n".join(lines) + "\n"


def run_onboarding(
    console: Console,
    err_console: Console,
    *,
    builtin_stacks: Path,
    returning: bool = False,
) -> Path | None:
    """Ask, write, and return the stack path — or ``None`` if the user backed out.

    ``returning`` is set by ``/setup``, where the questions are a deliberate re-run rather than a
    welcome. Backing out at any prompt writes nothing, so the caller's session survives intact.
    """
    console.print()
    if returning:
        console.print("[v.step]Setting up again.[/] The answers replace your current ones.")
    else:
        console.print("[v.step]Welcome to Veridian.[/] Two questions, then you're set up.")
        console.print("[v.meta]Answers are saved under your Veridian home; you won't be asked again.[/]")
    console.print()

    console.print("[v.meta]Which model provider should the agent use?[/]")
    idx = select(
        [p.label for p in PROVIDERS],
        prompt="provider",
        console=console,
        err_console=err_console,
    )
    if idx is None:
        return None
    provider = PROVIDERS[idx]

    config: dict[str, object] = {"model": provider.model}
    env_to_save: dict[str, str] = {}
    if provider.base_url:
        config["base_url"] = provider.base_url

    console.print()
    if provider.detect:
        # Never ask for a URL we can find. Ollama's port is fixed by convention, and the server
        # will also tell us which models are actually pulled — better than defaulting to a name
        # the user may not have.
        console.print("[v.meta]Looking for a running Ollama…[/]")
        found = probe(_ollama_candidates())
        if found is None:
            console.print(
                f"[v.warn]No server answered.[/] Using {OLLAMA_DEFAULT} anyway — "
                f"start Ollama (or set OLLAMA_HOST) and it will connect."
            )
        else:
            base_url, models = found
            config["base_url"] = base_url
            console.print(f"[v.ok]Found one at {base_url}.[/]")
            if not models:
                console.print("[v.warn]It has no models pulled.[/] Run [v.meta]ollama pull llama3.2[/] first.")
            elif len(models) == 1:
                config["model"] = models[0]
                console.print(f"[v.meta]Using the only model it has: {models[0]}[/]")
            else:
                console.print("[v.meta]Which model?[/]")
                pick = select(models, prompt="model", console=console, err_console=err_console)
                if pick is None:
                    return None
                config["model"] = models[pick]
    elif provider.ask_base_url:
        base_url = _ask(console, "server URL", OLLAMA_DEFAULT)
        if base_url is None:
            return None
        config["base_url"] = base_url
        found = probe([base_url])
        models = found[1] if found else []
        if models:
            console.print("[v.meta]Which model?[/]")
            pick = select(models, prompt="model", console=console, err_console=err_console)
            if pick is None:
                return None
            config["model"] = models[pick]
        else:
            console.print("[v.warn]Could not reach that server to list its models.[/]")
            model = _ask(console, "model name", provider.model)
            if model is None:
                return None
            config["model"] = model
    else:
        config["api_key_env"] = provider.env_var
        present = bool(os.environ.get(provider.env_var))
        if present and not returning:
            console.print(f"[v.ok]Found {provider.env_var} in your environment.[/] Using it.")
        else:
            if present:
                console.print(
                    f"[v.ok]{provider.env_var} is already set.[/] "
                    f"Leave blank to keep it, or paste a new key to replace it."
                )
            else:
                if provider.key_hint:
                    console.print(f"[v.meta]Get a key at {provider.key_hint}.[/]")
                console.print(
                    f"[v.meta]Paste it, or leave blank to set {provider.env_var} yourself later.[/]"
                )
            key = _ask(console, provider.env_var)
            if key is None:
                return None
            if key:
                env_to_save[provider.env_var] = key
                if present and provider.env_var in os.environ:
                    # A shell export outranks the saved file on the next start (see
                    # userconfig.load_env_file), so replacing one here is not the whole story.
                    console.print(
                        f"[v.warn]Note:[/] if {provider.env_var} is exported by your shell, that "
                        f"value wins in a new session — update it there too."
                    )

    console.print()
    console.print("[v.meta]Which agent should a bare `veridian` run?[/]")
    idx = select(
        [label for label, _ in AGENTS],
        prompt="agent",
        console=console,
        err_console=err_console,
    )
    if idx is None:
        return None
    _, agent = AGENTS[idx]

    name = provider.label.split("  ")[0]
    if agent == "autonomous":
        base_path = builtin_stacks.parent / "pre-installed" / "stacks" / "autonomous.toml"
        description = f"Autonomous coding agent on {name}."
    else:
        base_path = builtin_stacks / "default.toml"
        description = f"Reference agent loop on {name}."

    try:
        base = tomllib.loads(base_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        err_console.print(f"[v.err]could not read {base_path}:[/] {exc}")
        return None

    body = _render_stack(base, {"brick": provider.brick, "config": config}, description)
    target = home_stacks() / f"{STACK_NAME}.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")

    written = [str(target)]
    if env_to_save:
        env_file = write_env(env_to_save)
        # Assigned, not defaulted: on a re-run through /setup the key just typed is the one meant,
        # and the brick about to be rebound reads it from this process's environment.
        os.environ.update(env_to_save)
        written.append(f"{env_file}  (plaintext — anyone who can read this file can read the key)")
    written.append(str(write_config({"default_stack": STACK_NAME})))

    console.print()
    console.print("[v.ok]Set up.[/] Wrote:")
    for line in written:
        console.print(f"  [v.meta]{line}[/]")
    console.print()
    console.print("[v.meta]Change any of it with `/stack`, `/model`, or by editing those files.[/]")
    console.print()
    return target
