"""Tests for the multi-provider vision client + layered config (llm.py)."""

from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from white_mushroom_test.llm import (  # noqa: E402
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OLLAMA_HOST,
    LLMConfig,
    LLMError,
    OllamaVisionClient,
    OpenAIVisionClient,
    load_llm_config,
    make_llm_client,
)

# A config path that never exists, so load_llm_config tests do not depend on
# a developer's repo-root config.yaml.
NO_CONFIG = Path("__nonexistent_config_for_tests__.yaml")

# Every env var load_llm_config reads. Cleared autouse so the developer's
# shell (e.g. a real OPENAI_API_KEY) cannot flake these tests; tests that need
# an env value set it themselves with monkeypatch.setenv.
LLM_ENV_VARS = [
    "LLM_PROVIDER", "OLLAMA_HOST", "OLLAMA_MODEL", "OPENAI_BASE_URL",
    "OPENAI_API_KEY", "LLM_API_KEY", "LLM_TIMEOUT", "LLM_TEMPERATURE",
]


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch):
    for var in LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class _OllamaStub:
    def __init__(self, response="ollama-text", raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, host, payload, timeout):
        self.calls.append((host, payload, timeout))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


class _HttpStub:
    def __init__(self, data=None, raise_exc=None):
        self.data = data if data is not None else {
            "choices": [{"message": {"content": "openai-text"}}]
        }
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, body, headers, timeout))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.data


# ---------------------------------------------------------------------------
# OllamaVisionClient
# ---------------------------------------------------------------------------


def test_ollama_client_builds_payload_and_returns_response() -> None:
    stub = _OllamaStub(response="hello world")
    client = OllamaVisionClient(
        host="http://ollama:11434", model="qwen3.5:9b",
        timeout=30.0, temperature=0.1, call_ollama_fn=stub,
    )
    out = client.generate_text("describe this", "BASE64==")
    assert out == "hello world"
    assert len(stub.calls) == 1
    host, payload, timeout = stub.calls[0]
    assert host == "http://ollama:11434"
    assert timeout == 30.0
    # Payload shape is single-sourced with the batch runner.
    assert payload["model"] == "qwen3.5:9b"
    assert payload["prompt"] == "describe this"
    assert payload["images"] == ["BASE64=="]
    assert payload["stream"] is False
    assert payload["options"]["temperature"] == 0.1


def test_ollama_client_wraps_transport_error() -> None:
    stub = _OllamaStub(raise_exc=urllib.error.URLError("no host"))
    client = OllamaVisionClient(host="http://x", model="m", call_ollama_fn=stub)
    with pytest.raises(LLMError):
        client.generate_text("p", "b")


def test_llm_error_not_double_wrapped() -> None:
    # A call_ollama_fn that already raises LLMError must propagate as-is.
    stub = _OllamaStub(raise_exc=LLMError("already"))
    client = OllamaVisionClient(host="http://x", model="m", call_ollama_fn=stub)
    with pytest.raises(LLMError, match="already"):
        client.generate_text("p", "b")


def test_ollama_client_missing_model_raises() -> None:
    client = OllamaVisionClient(host="http://x", model="", call_ollama_fn=_OllamaStub())
    with pytest.raises(LLMError):
        client.generate_text("p", "b")


# ---------------------------------------------------------------------------
# OpenAIVisionClient
# ---------------------------------------------------------------------------


def test_openai_client_builds_vision_payload_and_parses_content() -> None:
    stub = _HttpStub(data={"choices": [{"message": {"content": "the answer"}}]})
    client = OpenAIVisionClient(
        api_key="sk-test", model="gpt-4o",
        timeout=45.0, temperature=0.2, base_url="https://api.openai.com/v1",
        http_post_fn=stub,
    )
    out = client.generate_text("identify this", "IMG==")
    assert out == "the answer"
    assert len(stub.calls) == 1
    url, body, headers, timeout = stub.calls[0]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert timeout == 45.0
    assert headers["Authorization"] == "Bearer sk-test"
    assert headers["Content-Type"] == "application/json"
    assert body["model"] == "gpt-4o"
    assert body["stream"] is False
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 4096
    content = body["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "identify this"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,IMG=="


def test_openai_client_strips_trailing_slash_from_base_url() -> None:
    stub = _HttpStub()
    client = OpenAIVisionClient(
        api_key="sk", model="gpt-4o",
        base_url="https://api.openai.com/v1/", http_post_fn=stub,
    )
    client.generate_text("p", "b")
    assert client.base_url == "https://api.openai.com/v1"
    assert stub.calls[0][0] == "https://api.openai.com/v1/chat/completions"


def test_openai_client_wraps_transport_error() -> None:
    stub = _HttpStub(raise_exc=OSError("boom"))
    client = OpenAIVisionClient(api_key="k", model="m", http_post_fn=stub)
    with pytest.raises(LLMError):
        client.generate_text("p", "b")


def test_openai_client_missing_api_key_raises() -> None:
    client = OpenAIVisionClient(api_key="", model="m", http_post_fn=_HttpStub())
    with pytest.raises(LLMError):
        client.generate_text("p", "b")


def test_openai_client_missing_model_raises() -> None:
    client = OpenAIVisionClient(api_key="k", model="", http_post_fn=_HttpStub())
    with pytest.raises(LLMError):
        client.generate_text("p", "b")


def test_openai_client_missing_content_raises() -> None:
    stub = _HttpStub(data={"choices": []})
    client = OpenAIVisionClient(api_key="k", model="m", http_post_fn=stub)
    with pytest.raises(LLMError):
        client.generate_text("p", "b")


# ---------------------------------------------------------------------------
# load_llm_config
# ---------------------------------------------------------------------------


def test_load_llm_config_defaults() -> None:
    cfg = load_llm_config(config_path=NO_CONFIG)
    assert cfg.provider == "ollama"
    assert cfg.host == DEFAULT_OLLAMA_HOST
    assert cfg.base_url == DEFAULT_OPENAI_BASE_URL
    assert cfg.model == ""
    assert cfg.api_key == ""
    assert cfg.timeout == 120.0
    assert cfg.temperature == 0.0


def test_load_llm_config_overrides() -> None:
    cfg = load_llm_config(
        overrides={"provider": "openai", "model": "gpt-4o",
                   "api_key": "sk-x", "timeout": "60", "temperature": "0.3"},
        config_path=NO_CONFIG,
    )
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-4o"
    assert cfg.api_key == "sk-x"
    assert cfg.timeout == 60.0
    assert cfg.temperature == 0.3


def test_load_llm_config_env_beats_overrides(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("OLLAMA_MODEL", "env-model")
    cfg = load_llm_config(
        overrides={"provider": "ollama", "model": "override-model", "api_key": "sk-override"},
        config_path=NO_CONFIG,
    )
    assert cfg.provider == "openai"    # env wins
    assert cfg.api_key == "sk-env"     # env wins
    assert cfg.model == "env-model"    # env wins


def test_load_llm_config_llm_api_key_fallback(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-fallback")
    # OPENAI_API_KEY cleared by the autouse fixture.
    cfg = load_llm_config(overrides={"provider": "openai"}, config_path=NO_CONFIG)
    assert cfg.api_key == "sk-fallback"


def test_load_llm_config_env_timeout_parses(monkeypatch) -> None:
    monkeypatch.setenv("LLM_TIMEOUT", "90")
    cfg = load_llm_config(config_path=NO_CONFIG)
    assert cfg.timeout == 90.0


def test_load_llm_config_unknown_provider_raises(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    with pytest.raises(LLMError):
        load_llm_config(config_path=NO_CONFIG)


def test_load_llm_config_overrides_none_values_ignored() -> None:
    cfg = load_llm_config(
        overrides={"provider": None, "model": None, "api_key": None,
                   "timeout": None, "temperature": None},
        config_path=NO_CONFIG,
    )
    assert cfg.provider == "ollama"   # default kept (None ignored)
    assert cfg.model == ""
    assert cfg.api_key == ""
    assert cfg.timeout == 120.0
    assert cfg.temperature == 0.0


def test_load_llm_config_missing_file_is_skipped() -> None:
    # A nonexistent config file must not raise (falls back to defaults).
    cfg = load_llm_config(config_path=NO_CONFIG)
    assert cfg.provider == "ollama"


# ---------------------------------------------------------------------------
# make_llm_client
# ---------------------------------------------------------------------------


def test_make_llm_client_ollama() -> None:
    client = make_llm_client(LLMConfig(provider="ollama", model="qwen", host="http://h"))
    assert isinstance(client, OllamaVisionClient)
    assert client.host == "http://h"
    assert client.model == "qwen"


def test_make_llm_client_openai_defaults_model() -> None:
    client = make_llm_client(LLMConfig(provider="openai", api_key="sk-x", model=""))
    assert isinstance(client, OpenAIVisionClient)
    assert client.model == DEFAULT_OPENAI_MODEL
    assert client.api_key == "sk-x"


def test_make_llm_client_unknown_provider_raises() -> None:
    with pytest.raises(LLMError):
        make_llm_client(LLMConfig(provider="bogus"))