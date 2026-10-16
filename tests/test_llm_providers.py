# SPDX-License-Identifier: MIT
"""The agent's model provider: OpenAI by default, Mistral (local with Ollama, or its API) or any
OpenAI-compatible server through LUX_LLM / LUX_LLM_BASE_URL (app/agents/lux_insights/settings.py)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "agents"))
from lux_insights import settings as S  # noqa: E402

VARS = ["LUX_LLM", "LUX_LLM_BASE_URL", "LUX_LLM_API_KEY", "LUX_AGENT_MODEL", "LUX_ROUTER_MODEL",
        "MISTRAL_API_KEY", "OPENAI_API_KEY"]


@pytest.fixture
def env(monkeypatch):
    for name in VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(S, "load_dotenv", lambda *a, **k: [])  # the repository's .env stays out of the tests
    monkeypatch.setattr(S, "_config_value", lambda key: None)
    return monkeypatch


def test_openai_by_default(env):
    env.setenv("OPENAI_API_KEY", "sk-test")
    s = S.Settings.load()
    assert (s.llm_name, s.base_url, s.model, s.router_model, s.has_key) == \
        ("openai", None, S.DEFAULT_MODEL, S.DEFAULT_ROUTER_MODEL, True)
    assert S.provider() is None


def test_mistral_on_this_machine_needs_no_key(env):
    env.setenv("LUX_LLM", "mistral-local")
    s = S.Settings.load()
    assert s.llm_name == "mistral-local" and s.base_url == "http://localhost:11434/v1"
    assert s.model == s.router_model == "mistral-small" and s.has_key
    assert S.provider()["api_key"] == "not-needed"


def test_mistral_api_takes_its_key(env):
    env.setenv("LUX_LLM", "mistral")
    env.setenv("MISTRAL_API_KEY", "m-test")
    s = S.Settings.load()
    assert s.base_url == "https://api.mistral.ai/v1" and s.model == "mistral-small-latest"
    assert S.provider()["api_key"] == "m-test"


def test_any_openai_compatible_server(env):
    env.setenv("LUX_LLM_BASE_URL", "http://localhost:1234/v1/")
    env.setenv("LUX_AGENT_MODEL", "mistral-nemo")
    s = S.Settings.load()
    assert (s.llm_name, s.base_url, s.model, s.router_model) == \
        ("openai-compatible", "http://localhost:1234/v1", "mistral-nemo", "mistral-nemo")


def test_a_server_without_a_model_is_refused(env):
    env.setenv("LUX_LLM_BASE_URL", "http://localhost:1234/v1")
    with pytest.raises(SystemExit):
        S.Settings.load()


def test_parameters_follow_the_model():
    assert S.api_params("mistral-small", 0.2, 900) == {"temperature": 0.2, "max_tokens": 900}
    assert S.api_params("gpt-6-luna", 0.2, 900)["reasoning_effort"] == "none"
    assert "max_completion_tokens" in S.api_params("gpt-5.5", 0.2, 900)
