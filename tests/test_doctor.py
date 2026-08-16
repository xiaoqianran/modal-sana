from __future__ import annotations

from modal_sana.core.doctor import _proxy_env, _proxy_extra, _redact_proxy, run_doctor


def test_redact_proxy_password() -> None:
    assert _redact_proxy("http://user:secret@127.0.0.1:7890") == "http://127.0.0.1:7890"


def test_proxy_env_reports_https(monkeypatch) -> None:
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    monkeypatch.delenv("MODAL_DISABLE_API_PROXY", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pw@127.0.0.1:7890")
    ok, detail = _proxy_env()
    assert ok
    assert "HTTPS_PROXY=http://127.0.0.1:7890" in detail
    assert "pw" not in detail


def test_doctor_includes_proxy_extra() -> None:
    report = run_doctor()
    names = {check.name for check in report.checks}
    assert "api-proxy-support" in names
    assert "Modal API proxy" in names
    extra_ok, extra_detail = _proxy_extra()
    assert extra_ok, extra_detail
