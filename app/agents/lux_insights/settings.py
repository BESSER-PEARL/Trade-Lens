# SPDX-License-Identifier: MIT
"""Where things are and how the agent is configured: paths, the .env file, the API key, the ports.

The OpenAI key comes from the environment (``OPENAI_API_KEY``) or from the repository's gitignored
``.env`` file, read once at start. It is never printed, logged, or written into config.yaml.

    from lux_insights.settings import Settings
    settings = Settings.load()            # reads .env, then the environment, then config.yaml
    settings.api_url, settings.port, settings.model, settings.has_key
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent          # app/agents/lux_insights
ROOT = HERE.parents[2]                          # the repository
CONFIG = HERE / "config.yaml"
DOTENV = ROOT / ".env"

DEFAULT_API = "http://localhost:8000"
DEFAULT_PORT = 8766
DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_ROUTER_MODEL = "gpt-4o-mini"   # the plan only: a small fast model; the specialists keep DEFAULT_MODEL
PLACEHOLDER_KEYS = {"", "YOUR-API-KEY", "sk-...", "changeme"}


def api_params(model: str, temperature: float, max_tokens: int, **extra) -> dict:
    """Chat-completion parameters that ``model`` accepts.

    The gpt-5 and gpt-6 families take ``max_completion_tokens`` instead of ``max_tokens``, and gpt-6 models
    only accept function tools on /v1/chat/completions with ``reasoning_effort: "none"``.
    """
    name = (model or "").lower()
    if name.startswith("gpt-6"):
        return {"temperature": temperature, "max_completion_tokens": max_tokens, "reasoning_effort": "none", **extra}
    if name.startswith("gpt-5"):
        return {"max_completion_tokens": max_tokens, **extra}
    return {"temperature": temperature, "max_tokens": max_tokens, **extra}


def ensure_import_paths() -> None:
    """The repository root (for ``agents.kit``) and app/agents (for ``lux_insights``) on sys.path."""
    for path in (str(ROOT), str(HERE.parent)):
        if path not in sys.path:
            sys.path.insert(0, path)


_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_dotenv(path: Path = DOTENV, override: bool = False) -> list[str]:
    """Read KEY=VALUE lines into os.environ (existing variables win unless ``override``).

    Returns the names it set, never the values. Quotes around a value are removed; lines starting
    with # are comments.
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return []
    names = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        name, value = m.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if override or name not in os.environ:
            os.environ[name] = value
            names.append(name)
    return names


def _config_value(key: str) -> Optional[str]:
    """A dotted key of config.yaml (nlp.openai.llm_name), without needing PyYAML."""
    try:
        import yaml  # BAF depends on PyYAML; the tests' venv may not have it
    except ImportError:
        return None
    try:
        data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    except OSError:
        return None
    for part in key.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return None if data is None else str(data)


def openai_key() -> Optional[str]:
    """The OpenAI key from the environment (after load_dotenv), or None. Never logged."""
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    return None if key in PLACEHOLDER_KEYS else key


# Other model providers, all through their OpenAI-compatible endpoint (BAF's LLMOpenAI takes a base_url).
# LUX_LLM picks a preset; LUX_LLM_BASE_URL, LUX_LLM_API_KEY, LUX_AGENT_MODEL and LUX_ROUTER_MODEL override it.
PROVIDERS = {
    # Mistral on this machine with Ollama (`ollama pull mistral-small`): nothing leaves the machine.
    "mistral-local": {"base_url": "http://localhost:11434/v1", "model": "mistral-small", "key_env": None},
    # Mistral's own API (MISTRAL_API_KEY).
    "mistral": {"base_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest", "key_env": "MISTRAL_API_KEY"},
    # Any other OpenAI-compatible server (Ollama, LM Studio, vLLM...): set LUX_LLM_BASE_URL and LUX_AGENT_MODEL.
}


def provider() -> Optional[dict]:
    """The model provider other than OpenAI, from LUX_LLM and LUX_LLM_BASE_URL (after load_dotenv), or None.

    Returns {name, base_url, model, api_key}; the key is never logged. A local server such as Ollama needs
    no key, so a placeholder is sent."""
    name = (os.environ.get("LUX_LLM") or "").strip().lower()
    preset = PROVIDERS.get(name, {})
    base_url = (os.environ.get("LUX_LLM_BASE_URL") or preset.get("base_url") or "").strip()
    if not base_url:
        return None
    key = (os.environ.get("LUX_LLM_API_KEY") or "").strip()
    if not key and preset.get("key_env"):
        key = (os.environ.get(preset["key_env"]) or "").strip()
    return {"name": name or "openai-compatible", "base_url": base_url.rstrip("/"),
            "model": preset.get("model"), "api_key": key if key not in PLACEHOLDER_KEYS else "not-needed"}


@dataclass
class Settings:
    api_url: str = DEFAULT_API      # the backend: /insights, /trends, /view, /agents
    port: int = DEFAULT_PORT        # the WebSocket the page connects to
    host: str = "localhost"
    model: str = DEFAULT_MODEL
    router_model: str = DEFAULT_ROUTER_MODEL
    has_key: bool = False
    base_url: Optional[str] = None  # another provider's OpenAI-compatible endpoint (None: OpenAI)
    llm_name: str = "openai"        # "openai", "mistral-local", "mistral" or "openai-compatible"

    @classmethod
    def load(cls, api_url: Optional[str] = None, port: Optional[int] = None,
             model: Optional[str] = None, router_model: Optional[str] = None) -> "Settings":
        """Arguments win over the environment (LUX_API_URL, LUX_AGENT_PORT, LUX_AGENT_MODEL,
        LUX_ROUTER_MODEL), which wins over config.yaml, which wins over the defaults."""
        load_dotenv()
        other = provider()
        if other:  # another provider: its model by default, for the router's plan as well
            model = model or os.environ.get("LUX_AGENT_MODEL") or other["model"]
            if not model:
                raise SystemExit("LUX_LLM_BASE_URL is set: set LUX_AGENT_MODEL to the model to use there.")
            router_model = router_model or os.environ.get("LUX_ROUTER_MODEL") or model
        return cls(
            api_url=(api_url or os.environ.get("LUX_API_URL") or DEFAULT_API).rstrip("/"),
            port=int(port or os.environ.get("LUX_AGENT_PORT") or _config_value("platforms.websocket.port")
                     or DEFAULT_PORT),
            host=os.environ.get("LUX_AGENT_HOST") or "localhost",
            model=model or os.environ.get("LUX_AGENT_MODEL") or _config_value("nlp.openai.llm_name") or DEFAULT_MODEL,
            router_model=router_model or os.environ.get("LUX_ROUTER_MODEL") or DEFAULT_ROUTER_MODEL,
            has_key=bool(other) or openai_key() is not None,
            base_url=other["base_url"] if other else None,
            llm_name=other["name"] if other else "openai",
        )
