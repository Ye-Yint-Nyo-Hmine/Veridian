"""Phase 6: the CLI surface."""

from __future__ import annotations

from typer.testing import CliRunner

from veridian.cli.main import app

runner = CliRunner()


def test_version():
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0
    assert r.output.strip()


def test_doctor_runs():
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0
    assert "JSON schemas valid" in r.output


def test_stack_list_and_validate():
    assert runner.invoke(app, ["stack", "list"]).exit_code == 0
    r = runner.invoke(app, ["stack", "validate", "stacks/default.toml"])
    assert r.exit_code == 0 and "OK" in r.output


def test_stack_validate_rejects_bad(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text('[stack]\nname="x"\n', encoding="utf-8")
    r = runner.invoke(app, ["stack", "validate", str(bad)])
    assert r.exit_code == 1


def test_brick_list_and_validate_all():
    r = runner.invoke(app, ["brick", "list"])
    assert r.exit_code == 0
    assert "sandbox/local" in r.output
    assert runner.invoke(app, ["brick", "validate", "--all"]).exit_code == 0


def test_brick_inspect():
    r = runner.invoke(app, ["brick", "inspect", "bricks/tools/git"])
    assert r.exit_code == 0
    assert "tools/git" in r.output
    assert "trust" in r.output.lower()


def test_protocol_schema_list_and_show():
    r = runner.invoke(app, ["protocol", "schema"])
    assert r.exit_code == 0 and "inference.schema.json" in r.output
    r2 = runner.invoke(app, ["protocol", "schema", "inference"])
    assert r2.exit_code == 0 and "generate_params" in r2.output


def test_run_rejects_stack_without_orchestrator(tmp_path):
    s = tmp_path / "s.toml"
    s.write_text('[stack]\nname="x"\n[bindings]\ncontext = "bricks/context/default"\n', encoding="utf-8")
    r = runner.invoke(app, ["run", "do a thing", "--stack", str(s)])
    assert r.exit_code == 1
    assert "orchestrator" in r.output
