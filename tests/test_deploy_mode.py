from __future__ import annotations

import pytest

from modal_sana.core.config import Settings
from modal_sana.core.jobs import JobService
from modal_sana.modal.deploy_mode import (
    DEPLOY_COMMAND,
    DeployedAppMissing,
    inspect_deploy_target,
    resolve_deploy_mode,
)
from modal_sana.schemas.job import JobConfig, PromptSpec


def _patch_lookup(monkeypatch, available: bool, error: str | None = "missing") -> None:
    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_app_available",
        lambda app_name=None: (available, None if available else error),
    )


def test_auto_uses_deployed_when_present(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_DEPLOYED", raising=False)
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    _patch_lookup(monkeypatch, True)
    decision = resolve_deploy_mode(None)
    assert decision.use_deployed is True
    assert decision.reason == "auto-found"
    assert decision.mode == "deployed"
    assert decision.snapshots is True


def test_auto_falls_back_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_DEPLOYED", raising=False)
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    _patch_lookup(monkeypatch, False, "Lookup failed")
    decision = resolve_deploy_mode(None)
    assert decision.use_deployed is False
    assert decision.reason == "auto-missing"
    assert decision.mode == "ephemeral"
    assert "Lookup failed" in (decision.error or "")


def test_deployed_zero_is_still_auto(monkeypatch) -> None:
    monkeypatch.setenv("MODAL_SANA_DEPLOYED", "0")
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    _patch_lookup(monkeypatch, True)
    decision = resolve_deploy_mode(None)
    assert decision.use_deployed is True
    assert decision.reason == "auto-found"


def test_env_deployed_requires_app(monkeypatch) -> None:
    monkeypatch.setenv("MODAL_SANA_DEPLOYED", "1")
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    _patch_lookup(monkeypatch, False, "nope")
    with pytest.raises(DeployedAppMissing, match=DEPLOY_COMMAND):
        resolve_deploy_mode(None)
    _patch_lookup(monkeypatch, True)
    assert resolve_deploy_mode(None).reason == "env-required"


def test_env_ephemeral_wins_over_auto(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_DEPLOYED", raising=False)
    monkeypatch.setenv("MODAL_SANA_EPHEMERAL", "1")
    _patch_lookup(monkeypatch, True)
    decision = resolve_deploy_mode(None)
    assert decision.use_deployed is False
    assert decision.reason == "env-ephemeral"


def test_requested_false_skips_lookup(monkeypatch) -> None:
    monkeypatch.setenv("MODAL_SANA_DEPLOYED", "1")
    called = {"n": 0}

    def boom(app_name=None):
        called["n"] += 1
        raise AssertionError("should not probe when forced ephemeral")

    monkeypatch.setattr("modal_sana.modal.deploy_mode.deployed_app_available", boom)
    decision = resolve_deploy_mode(False)
    assert decision.reason == "forced-ephemeral"
    assert called["n"] == 0


def test_requested_true_requires(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    _patch_lookup(monkeypatch, False)
    with pytest.raises(DeployedAppMissing):
        resolve_deploy_mode(True)


def test_inspect_never_raises(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_DEPLOYED", raising=False)
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    _patch_lookup(monkeypatch, False, "absent")
    info = inspect_deploy_target()
    assert info["available"] is False
    assert info["would_use"] == "ephemeral"
    assert info["snapshots"] is False
    assert info["not_modal_serve"] is True
    assert DEPLOY_COMMAND in info["deploy_command"]
    _patch_lookup(monkeypatch, True)
    found = inspect_deploy_target()
    assert found["would_use"] == "deployed"
    assert found["snapshots"] is True


def test_job_default_is_auto(service: JobService) -> None:
    job = service.create_job(
        [PromptSpec(prompt="auto path", count=1, seed=1)],
        JobConfig(dry_run=True, seed=1),
    )
    assert job.config.deployed is None


def test_settings_deployed_true_is_required(service: JobService, monkeypatch) -> None:
    service.settings = Settings(data_dir=service.settings.data_dir, deployed=True)
    assert service._requested_deployed(JobConfig()) is True
    assert service._requested_deployed(JobConfig(deployed=False)) is False
    assert service._requested_deployed(JobConfig(deployed=True)) is True
    service.settings = Settings(data_dir=service.settings.data_dir, deployed=False)
    assert service._requested_deployed(JobConfig()) is None
