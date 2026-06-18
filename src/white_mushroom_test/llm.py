"""Multi-provider vision client + layered config for the White Mushroom Test.

This module is the **consumer-facing seam** the Streamlit verifier (and any
other caller) uses to send a prompt + an image to a vision model and get text
back. It supports two providers:

- ``ollama`` — a local Ollama host (default ``http://localhost:11434``).
  The client delegates to :mod:`white_mushroom_test.ollama_runner` for payload
  building and HTTP, so the Ollama request shape is single-sourced with the
  batch runner.
- ``openai`` — the OpenAI Chat Completions API (``gpt-4o`` and friends) via an
  OpenAI-compatible ``/chat/completions`` endpoint, using stdlib ``urllib``.

No third-party runtime dependencies. No ``import streamlit`` — the config
layer takes an ``overrides`` dict, so the UI reads ``st.secrets`` /
``st.session_state`` and merges them into ``overrides`` before calling
:func:`load_llm_config`.

The single exception :class:`LLMError` wraps every transport / parse /
validation failure with a human-readable message, so callers only need to
catch one error type.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from white_mushroom_test.ollama_runner import build_ollama_payload, call_ollama


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o"
DEFAULT_TIMEOUT = 120.0
DEFAULT_TEMPERATURE = 0.0

_PROVIDER_OLLAMA = "ollama"
_PROVIDER_OPENAI = "openai"
_KNOWN_PROVIDERS = (_PROVIDER_OLLAMA, _PROVIDER_OPENAI)


class LLMError(RuntimeError):
    """Single exception wrapping every transport / parse / validation
    failure from this module with a human-readable message.
    """


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class LLMConfig:
    """Resolved LLM configuration.

    ``provider`` is ``"ollama"`` or ``"openai"``. ``host`` is the Ollama base
    URL (ignored for OpenAI). ``base_url`` is the OpenAI-compatible base URL
    (ignored for Ollama). ``api_key`` is the OpenAI key (ignored for Ollama).
    ``model``, ``timeout``, ``temperature`` apply to both providers.
    """

    provider: str = _PROVIDER_OLLAMA
    host: str = DEFAULT_OLLAMA_HOST
    base_url: str = DEFAULT_OPENAI_BASE_URL
    model: str = ""
    api_key: str = ""
    timeout: float = DEFAULT_TIMEOUT
    temperature: float = DEFAULT_TEMPERATURE
    think: bool = False


def _parse_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_llm_config(
    *,
    overrides: dict | None = None,
    config_path: Path | str | None = None,
) -> LLMConfig:
    """Resolve an :class:`LLMConfig` with layered precedence.

    Precedence (highest wins): environment variables > ``overrides`` dict >
    ``config.yaml`` ``llm:`` section > built-in defaults.

    Environment variables: ``LLM_PROVIDER``, ``OLLAMA_HOST``, ``OLLAMA_MODEL``,
    ``OPENAI_BASE_URL``, ``OPENAI_API_KEY`` (or ``LLM_API_KEY``),
    ``LLM_TIMEOUT``, ``LLM_TEMPERATURE``, ``LLM_THINK``.

    ``config_path`` defaults to ``config.yaml`` in the current directory. The
    file is optional; if ``pyyaml`` is not installed (the core is stdlib-only)
    or the file is absent / unparseable, it is silently skipped — env /
    overrides / defaults still apply. ``pyyaml`` ships with the ``[web]`` extra.

    Raises :class:`LLMError` if the resolved provider is not recognised.
    """
    cfg = LLMConfig()

    if config_path is None:
        config_path = Path("config.yaml")
    file_layer = _read_config_file(config_path)
    layers: list[dict] = [file_layer]
    if overrides:
        layers.append({k: v for k, v in overrides.items() if v is not None})

    for layer in layers:
        if layer.get("provider"):
            cfg.provider = str(layer["provider"])
        if layer.get("host"):
            cfg.host = str(layer["host"])
        if layer.get("base_url"):
            cfg.base_url = str(layer["base_url"])
        if layer.get("model"):
            cfg.model = str(layer["model"])
        if layer.get("api_key"):
            cfg.api_key = str(layer["api_key"])
        if layer.get("timeout") is not None:
            cfg.timeout = _parse_float(layer["timeout"], cfg.timeout)
        if layer.get("temperature") is not None:
            cfg.temperature = _parse_float(layer["temperature"], cfg.temperature)
        if layer.get("think") is not None:
            cfg.think = bool(layer["think"])

    # Environment variables — highest precedence.
    if v := os.environ.get("LLM_PROVIDER"):
        cfg.provider = v
    if v := os.environ.get("OLLAMA_HOST"):
        cfg.host = v
    if v := os.environ.get("OLLAMA_MODEL"):
        cfg.model = v
    if v := os.environ.get("OPENAI_BASE_URL"):
        cfg.base_url = v
    if v := os.environ.get("OPENAI_API_KEY"):
        cfg.api_key = v
    elif v := os.environ.get("LLM_API_KEY"):
        cfg.api_key = v
    if v := os.environ.get("LLM_TIMEOUT"):
        cfg.timeout = _parse_float(v, cfg.timeout)
    if v := os.environ.get("LLM_TEMPERATURE"):
        cfg.temperature = _parse_float(v, cfg.temperature)
    if v := os.environ.get("LLM_THINK"):
        cfg.think = v.lower() in ("1", "true", "yes", "on")

    if cfg.provider not in _KNOWN_PROVIDERS:
        raise LLMError(
            f"unknown LLM provider {cfg.provider!r}; "
            f"expected one of {_KNOWN_PROVIDERS}"
        )
    return cfg


def _read_config_file(path: Path | str) -> dict:
    """Best-effort read of a ``config.yaml`` ``llm:`` section.

    Returns ``{}`` if the file is absent, ``pyyaml`` is not installed, or the
    file is unparseable. The core stays importable without ``pyyaml``.
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if isinstance(data, dict):
        llm = data.get("llm")
        if isinstance(llm, dict):
            return llm
    return {}


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


class OllamaVisionClient:
    """Vision client for a local Ollama host.

    Delegates payload building to
    :func:`white_mushroom_test.ollama_runner.build_ollama_payload` and HTTP to
    :func:`white_mushroom_test.ollama_runner.call_ollama`, so the Ollama
    request shape is single-sourced with the batch runner. ``call_ollama_fn``
    is injectable for tests.
    """

    def __init__(
        self,
        host: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
        temperature: float = DEFAULT_TEMPERATURE,
        *,
        think: bool = False,
        call_ollama_fn: Callable[[str, dict, float], str] | None = None,
    ) -> None:
        self.host = host
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.think = think
        self._call = call_ollama_fn or call_ollama

    def generate_text(self, prompt: str, image_b64: str) -> str:
        if not self.model:
            raise LLMError("no Ollama model configured (set model=...)")
        payload = build_ollama_payload(
            {"prompt": prompt}, self.model, image_b64, self.temperature,
            think=self.think,
        )
        try:
            return self._call(self.host, payload, self.timeout)
        except LLMError:
            raise
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                json.JSONDecodeError, KeyError, OSError) as exc:
            raise LLMError(
                f"Ollama request to {self.host!r} failed: {exc}"
            ) from exc


class OpenAIVisionClient:
    """Vision client for the OpenAI Chat Completions API (OpenAI-compatible).

    Uses stdlib ``urllib``. ``http_post_fn(url, body, headers, timeout) -> dict``
    is injectable for tests; the default performs the real POST and returns the
    parsed JSON ``data``.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
        temperature: float = DEFAULT_TEMPERATURE,
        *,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        http_post_fn: Callable[..., dict] | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.base_url = base_url.rstrip("/")
        self._post = http_post_fn or _default_openai_post

    def generate_text(self, prompt: str, image_b64: str) -> str:
        if not self.api_key:
            raise LLMError("no OpenAI API key configured (set api_key=...)")
        if not self.model:
            raise LLMError("no OpenAI model configured (set model=...)")
        url = self.base_url + "/chat/completions"
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}"
                            },
                        },
                    ],
                }
            ],
            "temperature": self.temperature,
            "max_tokens": 4096,
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        try:
            data = self._post(url, body, headers, self.timeout)
            return _extract_openai_content(data)
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 — wrap any transport/parse error
            raise LLMError(
                f"OpenAI request to {self.base_url!r} failed: {exc}"
            ) from exc


def _default_openai_post(url: str, body: dict, headers: dict, timeout: float) -> dict:
    encoded = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw)


def _extract_openai_content(data: dict) -> str:
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(
            f"OpenAI response missing choices[0].message.content: {exc}"
        ) from exc


def make_llm_client(cfg: LLMConfig):
    """Factory: build the right client for ``cfg.provider``.

    Returns an :class:`OllamaVisionClient` or :class:`OpenAIVisionClient`. For
    OpenAI, defaults ``model`` to ``gpt-4o`` when unset.
    """
    if cfg.provider == _PROVIDER_OLLAMA:
        return OllamaVisionClient(
            host=cfg.host, model=cfg.model, timeout=cfg.timeout,
            temperature=cfg.temperature, think=cfg.think,
        )
    if cfg.provider == _PROVIDER_OPENAI:
        return OpenAIVisionClient(
            api_key=cfg.api_key, model=cfg.model or DEFAULT_OPENAI_MODEL,
            timeout=cfg.timeout, temperature=cfg.temperature,
            base_url=cfg.base_url,
        )
    raise LLMError(f"unknown LLM provider {cfg.provider!r}")