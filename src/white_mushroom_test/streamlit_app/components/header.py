"""Topbar: brand + the ⚙ Model popover (provider / host / model / API key).

The popover writes live choices to ``st.session_state["_llm_overrides"]`` and
clears the health-probe cache on every change so the status pill and model
list refresh immediately. API keys are **session-only** — never written to
disk; a caption points users at ``.streamlit/secrets.toml`` for persistence.

Mirrors the sql-editor ⚙ LLM popover idiom: provider ``selectbox`` that
re-runs on change, a per-provider section, and a coloured status pill driven
by :mod:`white_mushroom_test.streamlit_app.llm_health`.
"""

from __future__ import annotations

import streamlit as st

from white_mushroom_test.llm import DEFAULT_OLLAMA_HOST, DEFAULT_OPENAI_MODEL
from white_mushroom_test.streamlit_app import state
from white_mushroom_test.streamlit_app.llm_health import clear_cache, probe

_PROVIDERS = {"ollama": "Local Ollama", "openai": "OpenAI (cloud)"}
_OPENAI_MODELS = ["gpt-4o", "gpt-4o-mini", "gpt-4.1"]


def _save_override(field: str, value: str) -> None:
    """Record a live UI choice in session overrides (clears the probe cache)."""
    overrides: dict = st.session_state.get("_llm_overrides", {})
    overrides[field] = value
    st.session_state["_llm_overrides"] = overrides
    clear_cache()


def _rerun() -> None:
    """Clear the probe cache and re-run so the new config takes effect."""
    clear_cache()
    st.rerun()


def _label(text: str) -> None:
    st.markdown(
        f'<div style="font-size:10.5px;font-weight:600;letter-spacing:.07em;'
        f'text-transform:uppercase;color:#8E867B;margin-bottom:4px;">{text}</div>',
        unsafe_allow_html=True,
    )


def _status_pill(probe_result) -> None:
    if probe_result.ok:
        st.markdown(
            '<div style="font-size:11px;color:#3F6B45;margin-top:6px;">'
            '✓ Connected</div>',
            unsafe_allow_html=True,
        )
    else:
        detail = (probe_result.detail or "not connected")
        detail = detail.replace("<", "&lt;").replace(">", "&gt;")
        st.markdown(
            f'<div style="font-size:11px;color:#8A4A11;margin-top:6px;'
            f'word-break:break-word;">✗ {detail}</div>',
            unsafe_allow_html=True,
        )


def _render_ollama(cfg) -> None:
    _label("Host")
    new_host = st.text_input(
        "Host", value=cfg.host, key="llm_host",
        label_visibility="collapsed", placeholder=DEFAULT_OLLAMA_HOST,
    )
    if new_host and new_host != cfg.host:
        _save_override("host", new_host)
        _rerun()

    probe_result = probe()
    models = list(probe_result.available_models)
    _label("Model")

    if not models:
        st.markdown(
            f'<div style="font-size:12px;color:#8E867B;margin-bottom:8px;">'
            f'Ollama not reachable at <code>{cfg.host}</code>.</div>',
            unsafe_allow_html=True,
        )
        new_model = st.text_input(
            "Model (free text)", value=cfg.model, key="llm_model_text",
            label_visibility="collapsed", placeholder="e.g. qwen3.5:9b",
        )
        if new_model and new_model != cfg.model:
            _save_override("model", new_model)
            _rerun()
        if st.button("↺ Retry", key="llm_retry", use_container_width=True):
            _rerun()
        return

    if cfg.model and cfg.model not in models:
        options = [cfg.model] + models
    else:
        options = models
    selected = st.selectbox(
        "Model", options=options,
        index=options.index(cfg.model) if cfg.model in options else 0,
        key="llm_model_select", label_visibility="collapsed",
    )
    if selected != cfg.model:
        _save_override("model", selected)
        _rerun()

    st.markdown(
        f'<div style="font-size:11px;color:#8E867B;margin-top:4px;">'
        f'{len(models)} model{"s" if len(models) != 1 else ""} pulled</div>',
        unsafe_allow_html=True,
    )
    _status_pill(probe_result)
    if st.button("↺ Refresh", key="llm_refresh", use_container_width=True):
        _rerun()


def _render_openai(cfg) -> None:
    _label("API key")
    current_key = cfg.api_key or ""
    new_key = st.text_input(
        "API key", value=current_key, type="password",
        key="openai_api_key_input", label_visibility="collapsed",
        placeholder="sk-…",
    )
    if new_key != current_key:
        _save_override("api_key", new_key)
        _rerun()
    st.markdown(
        '<div style="font-size:10px;color:#8E867B;margin-bottom:4px;">'
        'Get a key at platform.openai.com/api-keys</div>',
        unsafe_allow_html=True,
    )

    _label("Model")
    current_model = cfg.model if cfg.model in _OPENAI_MODELS else DEFAULT_OPENAI_MODEL
    selected = st.selectbox(
        "Model", options=_OPENAI_MODELS,
        index=_OPENAI_MODELS.index(current_model),
        key="openai_model_select", label_visibility="collapsed",
    )
    if selected != cfg.model:
        _save_override("model", selected)
        _rerun()

    _status_pill(probe())
    if st.button("↺ Test connection", key="openai_refresh", use_container_width=True):
        _rerun()

    st.markdown(
        '<div style="font-size:10px;color:#8E867B;margin-top:8px;line-height:1.4;">'
        'Key is session-only. For persistence add it to '
        '<code>.streamlit/secrets.toml</code>.</div>',
        unsafe_allow_html=True,
    )


def render() -> None:
    """Render the compact header: brand on the left, ⚙ Model popover on the right."""
    cfg = state.load_config()

    cols = st.columns([0.55, 0.15, 0.3])
    with cols[0]:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:8px;padding:4px 0;">'
            '<span style="font-size:18px;">\U0001F344</span>'
            '<span style="font-weight:600;font-size:16px;">White Mushroom Test</span>'
            '<span style="font-size:11px;color:#8E867B;border:1px solid #E4DFD2;'
            'border-radius:999px;padding:1px 7px;">verifier</span>'
            '</div>',
            unsafe_allow_html=True,
        )

    with cols[2]:
        with st.popover("⚙ Model", use_container_width=True):
            _label("Provider")
            provider_keys = list(_PROVIDERS.keys())
            current = cfg.provider if cfg.provider in _PROVIDERS else "ollama"
            selected_provider = st.selectbox(
                "Provider", options=provider_keys,
                index=provider_keys.index(current),
                format_func=lambda k: _PROVIDERS[k],
                key="llm_provider_select", label_visibility="collapsed",
            )
            if selected_provider != current:
                _save_override("provider", selected_provider)
                _rerun()

            st.markdown('<div style="margin-top:8px;"></div>', unsafe_allow_html=True)
            if selected_provider == "openai":
                _render_openai(cfg)
            else:
                _render_ollama(cfg)


__all__ = ["render"]