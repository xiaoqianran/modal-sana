from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from modal_sana.cli.app import app

runner = CliRunner()


def test_generate_dry_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MODAL_SANA_DATA_DIR", str(tmp_path / "data"))
    result = runner.invoke(app, ["generate", "a white cat", "--dry-run", "-n", "2", "--seed", "1"])
    assert result.exit_code == 0, result.output
    assert "Submitted job:" in result.output
    assert "2 images" in result.output


def test_batch_txt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MODAL_SANA_DATA_DIR", str(tmp_path / "data"))
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("a forest\na city\n", encoding="utf-8")
    result = runner.invoke(app, ["batch", str(prompts), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Submitted job:" in result.output


def test_models_and_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MODAL_SANA_DATA_DIR", str(tmp_path / "data"))
    assert runner.invoke(app, ["models"]).exit_code == 0
    assert runner.invoke(app, ["gpus"]).exit_code == 0
    listed = runner.invoke(app, ["jobs"])
    assert listed.exit_code == 0
    help_prefetch = runner.invoke(app, ["prefetch", "--help"])
    assert help_prefetch.exit_code == 0, help_prefetch.output
    assert "CPU" in help_prefetch.output or "Volume" in help_prefetch.output
    assert "every" in help_prefetch.output.lower() or "all" in help_prefetch.output.lower()
    assert "--ephemeral" in help_prefetch.output
    help_gen = runner.invoke(app, ["generate", "--help"])
    assert help_gen.exit_code == 0, help_gen.output
    assert "--deployed" in help_gen.output
    assert "--ephemeral" in help_gen.output


def test_trace_and_cost(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MODAL_SANA_DATA_DIR", str(tmp_path / "data"))
    created = runner.invoke(app, ["generate", "a white cat", "--dry-run", "-n", "1", "--seed", "1"])
    assert created.exit_code == 0, created.output
    job_id = None
    for line in created.output.splitlines():
        if "Submitted job:" in line:
            job_id = line.split()[-1]
    assert job_id
    traced = runner.invoke(app, ["trace", job_id])
    assert traced.exit_code == 0, traced.output
    assert "job.run" in traced.output
    priced = runner.invoke(app, ["cost", job_id])
    assert priced.exit_code == 0, priced.output
    assert "estimated cost" in priced.output.lower() or "$" in priced.output
