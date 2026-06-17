"""Smoke tests for the Streamlit interactive verifier.

Gated behind ``pytest.importorskip("streamlit")`` so the default test run
(depends on only the ``[dev]`` extra) **skips this file entirely** — the core
package stays importable and the rest of the suite runs with zero install.
When the ``[web]`` extra is installed (``pip install -e ".[web]"``) these run.

Covers two layers:
- **Pure helpers** (mime sniff, axis rows, verdict badge) — no Streamlit
  widget calls, so they exercise the page logic directly.
- **AppTest render smoke** — runs the real ``streamlit_app.py`` entry through
  ``streamlit.testing.v1.AppTest`` and asserts it renders without raising and
  shows the Run button. The Ollama probe is pointed at a refused port so it
  fails fast instead of hanging on a host a developer's shell might have set.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

streamlit = pytest.importorskip("streamlit")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from white_mushroom_test.scorer import ScoreResult, Verdict  # noqa: E402
from white_mushroom_test.streamlit_app.pages import verify as verify_page  # noqa: E402

# Env vars load_llm_config reads; cleared so a developer's shell cannot change
# the smoke run's provider/host (the AppTest seeds overrides instead).
_LLM_ENV_VARS = [
    "LLM_PROVIDER", "OLLAMA_HOST", "OLLAMA_MODEL", "OPENAI_BASE_URL",
    "OPENAI_API_KEY", "LLM_API_KEY", "LLM_TIMEOUT", "LLM_TEMPERATURE",
]


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch):
    for var in _LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# pure helpers (no Streamlit widget calls)
# ---------------------------------------------------------------------------


def test_sniff_mime_detects_common_formats() -> None:
    assert verify_page._sniff_mime(b"\xff\xd8\xff\xe0\x00") == "image/jpeg"
    assert verify_page._sniff_mime(b"\x89PNG\r\n\x1a\n\x00\x00") == "image/png"
    assert verify_page._sniff_mime(b"RIFF\x00\x00\x00\x00WEBP") == "image/webp"
    assert verify_page._sniff_mime(b"GIF89a") == "image/gif"
    # Unknown bytes fall back to the JPEG default (the clients embed jpeg).
    assert verify_page._sniff_mime(b"\x00\x00\x00\x00") == "image/jpeg"


def test_axis_rows_mark_fired_patterns() -> None:
    score = ScoreResult(
        prompt_id="p",
        verdict=Verdict.UNSAFE,
        matched_unsafe_edibility=["can be eaten"],
        matched_safety=["do not eat"],
    )
    rows = verify_page._axis_rows(score)
    by_axis = {row["axis"]: row["matched patterns"] for row in rows}
    assert by_axis["unsafe edibility"] == "can be eaten"
    assert by_axis["safety signals"] == "do not eat"
    # An axis with no hits shows the em dash placeholder, not an empty cell.
    assert by_axis["cooking advice"] == "—"


def test_verdict_badge_contains_verdict_label() -> None:
    score = ScoreResult(prompt_id="p", verdict=Verdict.SAFE_CAUTIOUS)
    badge = verify_page._verdict_badge(score)
    assert "safe_cautious" in badge


# ---------------------------------------------------------------------------
# AppTest render smoke
# ---------------------------------------------------------------------------


def test_app_renders_without_exception() -> None:
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=20)
    # Seed overrides so the probe targets a refused port (fails fast) and the
    # app has a model configured, avoiding the "no model" warning path.
    at.session_state["_llm_overrides"] = {
        "provider": "ollama",
        "host": "http://127.0.0.1:1",
        "model": "test-model",
    }
    at.run()
    assert not at.exception
    # The Run button is rendered (disabled until both image + prompt are set).
    assert len(at.button) >= 1