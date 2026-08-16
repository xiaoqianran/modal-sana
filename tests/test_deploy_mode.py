from __future__ import annotations

from modal_sana.core.config import Settings
from modal_sana.core.jobs import JobService
from modal_sana.modal.deploy_mode import (
    DEPLOY_COMMAND,
    inspect_deploy_target,
    missing_app_message,
    resolve_deploy_mode,
)
from modal_sana.schemas.job import JobConfig, PromptSpec


def test_default_uses_deployed_without_lookup(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_DEPLOYED", raising=False)
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)

    def boom(app_name=None):
        raise AssertionError("generate path must not probe before calling from_name")

    monkeypatch.setattr("modal_sana.modal.deploy_mode.deployed_app_available", boom)
    decision = resolve_deploy_mode(None)
    assert decision.use_deployed is True
    assert decision.reason == "default-deployed"
    assert decision.mode == "deployed"
    assert decision.snapshots is True


def test_deployed_zero_still_defaults_to_deployed(monkeypatch) -> None:
    monkeypatch.setenv("MODAL_SANA_DEPLOYED", "0")
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    decision = resolve_deploy_mode(None)
    assert decision.use_deployed is True
    assert decision.reason == "default-deployed"


def test_env_deployed_selects_deployed(monkeypatch) -> None:
    monkeypatch.setenv("MODAL_SANA_DEPLOYED", "1")
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    decision = resolve_deploy_mode(None)
    assert decision.use_deployed is True
    assert decision.reason == "env-required"


def test_env_ephemeral_wins_over_default(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_DEPLOYED", raising=False)
    monkeypatch.setenv("MODAL_SANA_EPHEMERAL", "1")
    decision = resolve_deploy_mode(None)
    assert decision.use_deployed is False
    assert decision.reason == "env-ephemeral"


def test_requested_false_is_ephemeral(monkeypatch) -> None:
    monkeypatch.setenv("MODAL_SANA_DEPLOYED", "1")
    decision = resolve_deploy_mode(False)
    assert decision.reason == "forced-ephemeral"
    assert decision.use_deployed is False


def test_requested_true_is_deployed(monkeypatch) -> None:
    monkeypatch.setenv("MODAL_SANA_EPHEMERAL", "1")
    decision = resolve_deploy_mode(True)
    assert decision.use_deployed is True
    assert decision.reason == "required"


def test_inspect_never_raises(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_DEPLOYED", raising=False)
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_app_available",
        lambda app_name=None: (False, "absent"),
    )
    info = inspect_deploy_target()
    assert info["available"] is False
    assert info["would_use"] == "deployed"
    assert info["snapshots"] is False
    assert info["not_modal_serve"] is True
    assert DEPLOY_COMMAND in info["deploy_command"]
    assert "ephemeral" in info["note"]
    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_app_available",
        lambda app_name=None: (True, None),
    )
    found = inspect_deploy_target()
    assert found["would_use"] == "deployed"
    assert found["snapshots"] is True


def test_missing_message_mentions_no_silent_fallback() -> None:
    text = missing_app_message("modal-sana", "Lookup failed")
    assert DEPLOY_COMMAND in text
    assert "ephemeral" in text


def test_job_default_is_auto(service: JobService) -> None:
    job = service.create_job(
        [PromptSpec(prompt="auto path", count=1, seed=1)],
        JobConfig(dry_run=True, seed=1),
    )
    assert job.config.deployed is None
    assert job.config.image_format == "jpg"


def test_settings_deployed_true_is_required(service: JobService) -> None:
    service.settings = Settings(data_dir=service.settings.data_dir, deployed=True)
    assert service._requested_deployed(JobConfig()) is True
    assert service._requested_deployed(JobConfig(deployed=False)) is False
    assert service._requested_deployed(JobConfig(deployed=True)) is True
    service.settings = Settings(data_dir=service.settings.data_dir, deployed=False)
    assert service._requested_deployed(JobConfig()) is None
