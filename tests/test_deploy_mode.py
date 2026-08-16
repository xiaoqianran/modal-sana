from __future__ import annotations

import pytest

from modal_sana.core.config import Settings
from modal_sana.core.jobs import JobService
from modal_sana.modal.deploy_mode import (
    DEPLOY_COMMAND,
    DeployedAppMissing,
    clear_deployed_app_cache,
    deployed_app_available,
    ensure_deployed_or_fallback,
    inspect_deploy_target,
    is_deploy_quota_exhausted,
    is_unknown_model_error,
    missing_app_message,
    missing_deployed_models,
    resolve_deploy_mode,
)
from modal_sana.schemas.job import JobConfig, PromptSpec


def test_default_intent_is_deployed(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_DEPLOYED", raising=False)
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    decision = resolve_deploy_mode(None)
    assert decision.use_deployed is True
    assert decision.reason == "default-deployed"


def test_ensure_uses_existing_app(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_app_available",
        lambda app_name=None: (True, None),
    )

    def boom(app_name=None):
        raise AssertionError("must not deploy when SanaWorker already exists")

    monkeypatch.setattr("modal_sana.modal.deploy_mode.deploy_local_app", boom)
    decision = ensure_deployed_or_fallback(None)
    assert decision.use_deployed is True
    assert decision.reason == "auto-found"


def test_ensure_auto_deploys_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    seen = {"deploy": 0}

    def lookup(app_name=None):
        return (seen["deploy"] > 0, None if seen["deploy"] else "missing")

    def deploy(app_name=None):
        seen["deploy"] += 1
        return {"app_name": app_name or "modal-sana"}

    monkeypatch.setattr("modal_sana.modal.deploy_mode.deployed_app_available", lookup)
    monkeypatch.setattr("modal_sana.modal.deploy_mode.deploy_local_app", deploy)
    decision = ensure_deployed_or_fallback(None)
    assert seen["deploy"] == 1
    assert decision.use_deployed is True
    assert decision.reason == "auto-deployed"


def test_ensure_quota_full_without_our_app_falls_back(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_app_available",
        lambda app_name=None: (False, "missing"),
    )

    def fail(app_name=None):
        raise RuntimeError("maximum number of apps reached")

    monkeypatch.setattr("modal_sana.modal.deploy_mode.deploy_local_app", fail)
    decision = ensure_deployed_or_fallback(None)
    assert decision.use_deployed is False
    assert decision.reason == "quota-ephemeral"
    assert "maximum number of apps" in (decision.error or "")


def test_ensure_other_deploy_error_raises(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_app_available",
        lambda app_name=None: (False, "missing"),
    )

    def fail(app_name=None):
        raise RuntimeError("auth exploded")

    monkeypatch.setattr("modal_sana.modal.deploy_mode.deploy_local_app", fail)
    with pytest.raises(DeployedAppMissing, match=DEPLOY_COMMAND):
        ensure_deployed_or_fallback(None)


def test_ensure_redeploys_when_registry_missing_models(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    seen = {"deploy": 0}

    def lookup(app_name=None):
        return True, None

    def deploy(app_name=None):
        seen["deploy"] += 1
        return {"app_name": app_name or "modal-sana"}

    monkeypatch.setattr("modal_sana.modal.deploy_mode.deployed_app_available", lookup)
    monkeypatch.setattr("modal_sana.modal.deploy_mode.deploy_local_app", deploy)
    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_registry_ids",
        lambda app_name=None: {
            "sana-sprint-1.6b",
            "sana-sprint-0.6b",
            "sana-1.6b",
            "sana-1.5-1.6b",
            "sana-1.5-4.8b",
        },
    )
    decision = ensure_deployed_or_fallback(
        None,
        required_models=["sana-sprint-1.6b", "sana-1.6b-2k", "sana-1.6b-4k"],
    )
    assert seen["deploy"] == 1
    assert decision.reason == "auto-redeployed"
    assert decision.use_deployed is True


def test_ensure_does_not_redeploy_when_registry_is_current(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)

    def boom(app_name=None):
        raise AssertionError("must not redeploy when remote already knows the models")

    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_app_available",
        lambda app_name=None: (True, None),
    )
    monkeypatch.setattr("modal_sana.modal.deploy_mode.deploy_local_app", boom)
    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_registry_ids",
        lambda app_name=None: {"sana-sprint-1.6b", "sana-1.6b-4k"},
    )
    decision = ensure_deployed_or_fallback(None, required_models=["sana-1.6b-4k"])
    assert decision.reason == "auto-found"


def test_missing_deployed_models_preserves_order(monkeypatch) -> None:
    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_registry_ids",
        lambda app_name=None: {"sana-sprint-1.6b", "sana-1.5-1.6b"},
    )
    assert missing_deployed_models(
        "modal-sana",
        ["sana-1.6b-2k", "sana-sprint-1.6b", "sana-1.6b-4k"],
    ) == ["sana-1.6b-2k", "sana-1.6b-4k"]
    assert missing_deployed_models("modal-sana", ["sana-sprint-1.6b"]) == []
    assert missing_deployed_models("modal-sana", []) == []


def test_missing_models_empty_when_registry_unreadable(monkeypatch) -> None:
    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_registry_ids",
        lambda app_name=None: None,
    )
    assert missing_deployed_models("modal-sana", ["sana-1.6b-4k"]) == []


def test_ensure_skips_redeploy_when_registry_unreadable(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)

    def boom(app_name=None):
        raise AssertionError("unreadable registry must not force a redeploy")

    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_app_available",
        lambda app_name=None: (True, None),
    )
    monkeypatch.setattr("modal_sana.modal.deploy_mode.deploy_local_app", boom)
    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_registry_ids",
        lambda app_name=None: None,
    )
    decision = ensure_deployed_or_fallback(None, required_models=["sana-1.6b-4k"])
    assert decision.reason == "auto-found"


def test_unknown_model_error_detector() -> None:
    assert is_unknown_model_error(
        ValueError(
            "Unknown model 'sana-1.6b-2k'. Known: sana-sprint-1.6b, "
            "sana-sprint-0.6b, sana-1.6b, sana-1.5-1.6b, sana-1.5-4.8b"
        )
    )
    assert not is_unknown_model_error(RuntimeError("volume commit failed"))


def test_ensure_forced_ephemeral_does_not_deploy(monkeypatch) -> None:
    def boom(app_name=None):
        raise AssertionError("forced ephemeral must not look up or deploy")

    monkeypatch.setattr("modal_sana.modal.deploy_mode.deployed_app_available", boom)
    monkeypatch.setattr("modal_sana.modal.deploy_mode.deploy_local_app", boom)
    decision = ensure_deployed_or_fallback(False)
    assert decision.reason == "forced-ephemeral"
    assert decision.use_deployed is False


def test_quota_detector() -> None:
    assert is_deploy_quota_exhausted(RuntimeError("maximum number of apps"))
    assert is_deploy_quota_exhausted(RuntimeError("Resource exhausted: too many deployed apps"))
    assert not is_deploy_quota_exhausted(RuntimeError("auth exploded"))
    assert not is_deploy_quota_exhausted(RuntimeError("NotFound: SanaWorker"))


def test_env_ephemeral_wins_over_default(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_DEPLOYED", raising=False)
    monkeypatch.setenv("MODAL_SANA_EPHEMERAL", "1")
    decision = resolve_deploy_mode(None)
    assert decision.use_deployed is False
    assert decision.reason == "env-ephemeral"


def test_deployed_app_lookup_is_cached(monkeypatch) -> None:
    hits = {"n": 0}

    def probe(name: str):
        hits["n"] += 1
        return True, None

    clear_deployed_app_cache()
    monkeypatch.setattr("modal_sana.modal.deploy_mode._probe_deployed_app", probe)
    assert deployed_app_available("modal-sana") == (True, None)
    assert deployed_app_available("modal-sana") == (True, None)
    assert hits["n"] == 1
    clear_deployed_app_cache("modal-sana")
    assert deployed_app_available("modal-sana") == (True, None)
    assert hits["n"] == 2


def test_inspect_says_auto_deploy_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("MODAL_SANA_DEPLOYED", raising=False)
    monkeypatch.delenv("MODAL_SANA_EPHEMERAL", raising=False)
    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_app_available",
        lambda app_name=None: (False, "absent"),
    )
    info = inspect_deploy_target()
    assert info["available"] is False
    assert info["would_use"] == "deployed"
    assert info["auto_deploy"] is True
    assert DEPLOY_COMMAND in info["note"]
    monkeypatch.setattr(
        "modal_sana.modal.deploy_mode.deployed_app_available",
        lambda app_name=None: (True, None),
    )
    found = inspect_deploy_target()
    assert found["auto_deploy"] is False
    assert found["snapshots"] is True


def test_missing_message_mentions_quota_rule() -> None:
    text = missing_app_message("modal-sana", "Lookup failed")
    assert DEPLOY_COMMAND in text
    assert "quota" in text


def test_job_default_is_auto(service: JobService) -> None:
    job = service.create_job(
        [PromptSpec(prompt="auto path", count=1, seed=1)],
        JobConfig(dry_run=True, seed=1),
    )
    assert job.config.deployed is None
    assert job.config.image_format == "png"


def test_settings_deployed_true_is_required(service: JobService) -> None:
    service.settings = Settings(data_dir=service.settings.data_dir, deployed=True)
    assert service._requested_deployed(JobConfig()) is True
    assert service._requested_deployed(JobConfig(deployed=False)) is False
    assert service._requested_deployed(JobConfig(deployed=True)) is True
    service.settings = Settings(data_dir=service.settings.data_dir, deployed=False)
    assert service._requested_deployed(JobConfig()) is None
