"""Settings loading: .env honor, diagnostics, aliases, validation."""

from __future__ import annotations

import pytest

from ordigovernance.gateway.config import GatewaySettings, load_env_file


def test_missing_env_file_reports_zero_keys(tmp_path):
    source, loaded = load_env_file(tmp_path / ".env")
    assert "no .env" in source
    assert loaded == 0


def test_env_file_loads_and_process_env_wins(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'GATEWAY_AUTH_TOKEN="from-file"\n'
        "export GATEWAY_BUDGET_MAX_UNITS=1234\n"
        "# comment\n"
        "GATEWAY_POLL_INTERVAL=0.5\n"
    )
    monkeypatch.setenv("GATEWAY_POLL_INTERVAL", "9.9")
    settings = GatewaySettings.from_env(env_file)
    assert settings.auth_token == "from-file"
    assert settings.default_budget_max_units == 1234
    # Existing process environment always wins over .env.
    assert settings.poll_interval == 9.9


def test_openai_api_base_alias_accepted(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_BASE", "http://alias/v1")
    monkeypatch.setenv("GATEWAY_AUTH_TOKEN", "t")
    settings = GatewaySettings.from_env(tmp_path / ".env")
    assert settings.base_url == "http://alias/v1"


def test_missing_auth_token_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("GATEWAY_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="GATEWAY_AUTH_TOKEN"):
        GatewaySettings.from_env(tmp_path / ".env")


def test_malformed_semaphores_json_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH_TOKEN", "t")
    monkeypatch.setenv("GATEWAY_SEMAPHORES", "{not json")
    with pytest.raises(ValueError, match="GATEWAY_SEMAPHORES"):
        GatewaySettings.from_env(tmp_path / ".env")