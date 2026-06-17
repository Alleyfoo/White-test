"""Session-state init + LLM-config resolution for the Streamlit app.

Mirrors the sql-editor ``state.init()`` idiom (``ss.setdefault(...)``) and
centralises the layered LLM-config build so the header popover and the verify
page resolve config the same way. Layering (highest wins):

    environment variables
      > live session overrides  (st.session_state["_llm_overrides"])
      > .streamlit/secrets.toml [llm]          (persistence, e.g. on Cloud)
      > config.yaml llm:                         (local dev defaults)
      > built-in defaults

Live overrides sit *above* secrets so the user's most recent popover choice
wins, but a blank field falls back to a persisted secret (e.g. an API key
stored in ``.streamlit/secrets.toml`` on Streamlit Community Cloud).
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from white_mushroom_test.llm import LLMConfig, load_llm_config

# Repo root = this file lives at src/white_mushroom_test/streamlit_app/state.py
#   .parents[0] = streamlit_app
#   .parents[1] = white_mushroom_test
#   .parents[2] = src
#   .parents[3] = repo root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
PROMPTS_PATH = PROJECT_ROOT / "data" / "prompts.jsonl"
MANIFEST_PATH = PROJECT_ROOT / "data" / "images" / "manifest.jsonl"
IMAGE_DIR = PROJECT_ROOT / "data" / "images" / "local"


def init() -> None:
    """Ensure every non-widget session_state key the app reads exists.

    Widget keys (the radios / selectboxes / file uploader) are intentionally
    NOT setdefault'd here — Streamlit owns those and pre-setting them with a
    value that is not a valid option would raise. Only free-form state used
    across reruns is seeded.
    """
    ss = st.session_state
    ss.setdefault("_llm_overrides", {})   # live UI choices (provider/host/model/api_key)
    ss.setdefault("_llm_probe", None)     # (ProbeResult, monotonic_ts) cache
    ss.setdefault("last_outcome", None)   # VerifyOutcome from the last Run
    ss.setdefault("last_image_id", "")    # image_id captured at run time
    ss.setdefault("last_prompt_id", "")   # prompt_id captured at run time
    ss.setdefault("last_category", None)  # category captured at run time
    ss.setdefault("last_model", "")        # model name captured at run time


def _secrets_llm() -> dict:
    """Read the optional ``[llm]`` section from ``.streamlit/secrets.toml``."""
    try:
        section = st.secrets["llm"]
    except (KeyError, FileNotFoundError):
        return {}
    out: dict = {}
    try:
        for key in section:
            value = section[key]
            if value is not None:
                out[key] = value
    except Exception:  # pragma: no cover — never break config resolution
        return {}
    return out


def build_overrides() -> dict:
    """Merge ``st.secrets["llm"]`` with the live session overrides.

    Session overrides win (the user's most recent popover choice), but fall
    back to persisted secrets when a field is blank — e.g. an API key stored
    in ``.streamlit/secrets.toml``. ``None`` values in the overrides are
    ignored so a popover field the user cleared does not clobber a secret.
    """
    merged = _secrets_llm()
    for key, value in st.session_state["_llm_overrides"].items():
        if value is not None:
            merged[key] = value
    return merged


def load_config() -> LLMConfig:
    """Resolve the current :class:`LLMConfig` from secrets + session + config.yaml."""
    return load_llm_config(overrides=build_overrides(), config_path=CONFIG_PATH)


__all__ = [
    "PROJECT_ROOT",
    "CONFIG_PATH",
    "PROMPTS_PATH",
    "MANIFEST_PATH",
    "IMAGE_DIR",
    "init",
    "build_overrides",
    "load_config",
]