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
