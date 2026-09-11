"""The pre-installed tree as a whole: every brick conforms, the stack binds all of it, and no
inference provider is named anywhere under pre-installed/."""

from __future__ import annotations

import re

import pytest

from tests.preinstalled.conftest import BRICKS, PRE
from veridian.conformance import run_conformance_sync
from veridian.kernel import load_stack

BRICK_DIRS = {
    "tools/toolkit": BRICKS / "toolkit",
    "context/repo-map": BRICKS / "context",
    "orchestrator/autonomous": BRICKS / "orchestrator",
}


@pytest.mark.parametrize("name", sorted(BRICK_DIRS))
def test_pre_installed_brick_conforms(name):
    report = run_conformance_sync(BRICK_DIRS[name], timeout=30.0)
    assert report.ok, report.summary() + " :: " + "; ".join(
        f"{r.contract}.{r.method} {r.detail}" for r in report.results if not r.ok
    ) + " :: " + "; ".join(report.problems)


def test_stack_binds_toolkit_context_orchestrator_and_a_sandbox():
    resolved = load_stack(PRE / "stacks" / "autonomous.toml")
    bound = {b.contract: b.manifest.name for b in resolved.active()}
    assert bound["tools"] == "tools/toolkit"
    assert bound["context"] == "context/repo-map"
    assert bound["orchestrator"] == "orchestrator/autonomous"
    assert bound["sandbox"] == "sandbox/local"          # terminal execution goes through this
    assert bound["conversation"] == "conversation/sqlite"  # required for --resume history


def test_stack_has_no_inference_binding():
    resolved = load_stack(PRE / "stacks" / "autonomous.toml")
    assert resolved.binding_for("inference") is None
    # ...but the capability is granted, so a user can add one without touching policy
    assert "contract:inference" in resolved.policy.grant


# Brand names of inference providers / vendors. These must appear nowhere under pre-installed/.
_PROVIDER_NAMES = re.compile(
    r"\banthropic\b|\bopenai\b|\bollama\b|\bclaude\b|\bgpt-?[0-9]|\bgemini\b|\bllama\b|"
    r"\bmistral\b|\bcohere\b|\bgroq\b|\bdeepseek\b|\bbedrock\b|\bvllm\b|lm\s?studio|llama\.cpp",
    re.I,
)
# In a stack file, the inference identity is stricter: no model id, no endpoint, no key either.
_STACK_IDENTITY = re.compile(r"\bmodel\b|\bbase_url\b|\bapi_key\b|\bapi_base\b|https?://", re.I)

_SKIP_SUFFIX = {".pyc"}


def _text_files():
    for path in PRE.rglob("*"):
        if not path.is_file() or path.suffix in _SKIP_SUFFIX or "__pycache__" in path.parts:
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def test_no_provider_name_anywhere_under_pre_installed():
    offenders: list[str] = []
    for path, text in _text_files():
        for m in _PROVIDER_NAMES.finditer(text):
            line = text[: m.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(PRE)}:{line}: …{m.group(0)!r}…")
    assert not offenders, "provider identity leaked into pre-installed/:\n" + "\n".join(offenders)


def test_stack_files_name_no_model_no_endpoint_no_key():
    for path in (PRE / "stacks").glob("*.toml"):
        text = path.read_text(encoding="utf-8")
        # strip comments — the guidance comment may legitimately mention what the user would add
        code = "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())
        hits = [m.group(0) for m in _STACK_IDENTITY.finditer(code)]
        assert not hits, f"{path.name} leaks inference identity outside comments: {hits}"
