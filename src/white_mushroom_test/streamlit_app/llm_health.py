"""Lightweight LLM health probe for the Streamlit app.

Probes the configured provider so the header status pill and the ⚙ Model
popover can show at a glance whether the model is reachable:

- **Ollama** — ``GET {host}/api/tags`` (returns the pulled-model list, so the
  popover can populate its model picker).
- **OpenAI** — a 1-token chat completion (cheap, and tests the exact
  ``/chat/completions`` endpoint the app actually uses; avoids the
  ``/models`` endpoint which some key types 403 on).

The result is cached in ``st.session_state["_llm_probe"]`` with a 30 s TTL for
Ollama (cheap; reacts quickly when the server goes offline) and a 300 s TTL
for OpenAI (avoids burning free-tier rate-limit quota on routine renders).
:func:`clear_cache` drops the cache so the next call re-checks immediately —
wired to every provider/host/model change and every "Test connection" button.
A provider mismatch with the cached result also forces a re-probe, as a
belt-and-suspenders guard against any path that forgets to clear.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, Tuple

import streamlit as st

from white_mushroom_test.streamlit_app import state

_PROBE_TIMEOUT = 5.0  # generous enough for remote OpenAI over the internet
_CACHE_TTL_LOCAL = 30.0    # Ollama: re-probe every 30 s (cheap, catches offline quickly)
_CACHE_TTL_CLOUD = 300.0   # OpenAI: re-probe every 5 min to avoid burning rate-limit quota


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single health probe."""

    status: str  # "ok" | "offline"
    provider: str
    host: str
    model: str
    detail: str = ""
    available_models: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _probe_ollama(host: str, model: str) -> ProbeResult:
    url = host.rstrip("/") + "/api/tags"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=_PROBE_TIMEOUT) as response:  # nosec
            body = response.read()
    except urllib.error.HTTPError as exc:
        return ProbeResult("offline", "ollama", host, model, f"HTTP {exc.code}: {exc.reason}")
    except urllib.error.URLError as exc:
        return ProbeResult("offline", "ollama", host, model, f"unreachable ({exc.reason})")
    except (TimeoutError, OSError) as exc:
        return ProbeResult("offline", "ollama", host, model, str(exc))

    try:
        envelope = json.loads(body)
    except json.JSONDecodeError:
        return ProbeResult("offline", "ollama", host, model, "non-JSON response from /api/tags")

    available = []
    if isinstance(envelope, dict):
        for entry in envelope.get("models", []) or []:
            name = entry.get("name") if isinstance(entry, dict) else None
            if isinstance(name, str):
                available.append(name)
    models_tuple = tuple(available)

    if available and model and not any(
        name == model or name.startswith(model + ":") for name in available
    ):
        detail = (
            f"server up, but {model!r} is not pulled "
            f"(available: {', '.join(available[:4])}"
            + ("…" if len(available) > 4 else "")
            + ")"
        )
        return ProbeResult("offline", "ollama", host, model, detail, models_tuple)

    return ProbeResult("ok", "ollama", host, model, available_models=models_tuple)


def _probe_openai(base_url: str, api_key: str, model: str) -> ProbeResult:
    if not api_key:
        return ProbeResult(
            "offline", "openai", base_url, model,
            "no API key — paste it in ⚙ Model",
        )
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_PROBE_TIMEOUT) as response:  # nosec
            body = response.read()
    except urllib.error.HTTPError as exc:
        try:
            err_msg = json.loads(exc.read()).get("error", {}).get("message", exc.reason)
        except Exception:  # pragma: no cover — error body not JSON
            err_msg = exc.reason
        return ProbeResult("offline", "openai", base_url, model, f"HTTP {exc.code}: {err_msg}")
    except urllib.error.URLError as exc:
        return ProbeResult("offline", "openai", base_url, model, f"network error: {exc.reason}")
    except (TimeoutError, OSError) as exc:
        return ProbeResult("offline", "openai", base_url, model, str(exc))

    try:
        envelope = json.loads(body)
    except json.JSONDecodeError:
        return ProbeResult("offline", "openai", base_url, model, "non-JSON response")
    if not envelope.get("choices"):
        return ProbeResult(
            "offline", "openai", base_url, model,
            f"unexpected response: {str(envelope)[:120]}",
        )
    return ProbeResult("ok", "openai", base_url, model)


def _do_probe() -> ProbeResult:
    cfg = state.load_config()
    if cfg.provider == "ollama":
        return _probe_ollama(cfg.host, cfg.model)
    return _probe_openai(cfg.base_url, cfg.api_key, cfg.model or "gpt-4o")


def probe(*, force: bool = False) -> ProbeResult:
    """Return the cached probe result, re-probing when the TTL has expired."""
    cfg = state.load_config()
    ttl = _CACHE_TTL_LOCAL if cfg.provider == "ollama" else _CACHE_TTL_CLOUD
    cached: Optional[Tuple[ProbeResult, float]] = st.session_state.get("_llm_probe")
    if cached is not None and not force:
        result, ts = cached
        # Invalidate if the provider changed since the cache was written.
        if result.provider == cfg.provider and time.monotonic() - ts < ttl:
            return result
    result = _do_probe()
    st.session_state["_llm_probe"] = (result, time.monotonic())
    return result


def clear_cache() -> None:
    """Drop any cached probe so the next call re-checks immediately."""
    st.session_state.pop("_llm_probe", None)


__all__ = ["ProbeResult", "probe", "clear_cache"]