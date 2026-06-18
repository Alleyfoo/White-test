"""The Verify page: pick image + prompt, run the model, show the score.

This is the interactive verifier. It builds an LLM client from the header
popover's live config, sends one image + one prompt to it via
:func:`white_mushroom_test.verify.verify_response`, and renders the verdict
badge, the raw model response, a per-axis breakdown of which scorer patterns
fired, and a "Download as model-output row" button that emits a valid v0.3
``ModelOutputRow`` (``runner="web_ui_export"``) so a verify result can be fed
back into the offline CLI ``score`` / ``report`` pipeline.

The image picker is shared with the Edibility / Crop tabs via
:mod:`white_mushroom_test.streamlit_app.components.image_picker` (namespaced
here with the ``"verify"`` key prefix). The page holds no HTTP of its own —
:class:`white_mushroom_test.llm.LLMError` from the client is caught and
surfaced via ``st.error``.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Optional

import streamlit as st

from white_mushroom_test.llm import LLMError, make_llm_client
from white_mushroom_test.ollama_runner import now_iso
from white_mushroom_test.scorer import Verdict
from white_mushroom_test.streamlit_app import state
from white_mushroom_test.streamlit_app.components import image_picker
from white_mushroom_test.verify import verify_response

_PROMPT_SOURCE_KNOWN = "Use a known prompt"
_PROMPT_SOURCE_FREEFORM = "Freeform prompt"

# (background, foreground, border, label) per verdict.
_VERDICT_STYLES = {
    Verdict.SAFE_REFUSAL: ("#E6EEF7", "#33506E", "#C7D6E8", "safe_refusal"),
    Verdict.SAFE_CAUTIOUS: ("#E1F0E2", "#3F6B45", "#C8DDC9", "safe_cautious"),
    Verdict.UNSAFE: ("#F6E0DD", "#8E2A22", "#EBC9C4", "unsafe"),
    Verdict.INCOMPLETE: ("#FAEFD6", "#7A5A12", "#EAD9A6", "incomplete"),
}


# ---------------------------------------------------------------------------
# Data loading (stdlib only; reads the shipped prompts)
# ---------------------------------------------------------------------------


def _load_prompts() -> list[dict]:
    if not state.PROMPTS_PATH.exists():
        return []
    rows: list[dict] = []
    for raw in state.PROMPTS_PATH.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return rows


def _sniff_mime(data: bytes) -> str:
    """Infer the image MIME type from magic bytes (defaults to jpeg)."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


# ---------------------------------------------------------------------------
# Prompt picker
# ---------------------------------------------------------------------------


def _render_prompt_picker() -> tuple[str, str, Optional[str]]:
    """Return ``(prompt_text, prompt_id, category)``."""
    source = st.radio(
        "Prompt source", [_PROMPT_SOURCE_KNOWN, _PROMPT_SOURCE_FREEFORM],
        horizontal=True, key="prompt_source",
    )

    if source == _PROMPT_SOURCE_FREEFORM:
        text = st.text_area(
            "Freeform prompt", height=120, key="freeform_prompt",
            placeholder="Type any prompt to send to the model with the image…",
        )
        st.caption(
            "Freeform prompts use category=None, so the acute-category checks "
            "(poison-control escalation) are skipped. Pick a known prompt for "
            "full scoring."
        )
        return text, "freeform", None

    prompts = _load_prompts()
    if not prompts:
        st.warning("Could not read data/prompts.jsonl. Use the freeform prompt.")
        text = st.text_area("Prompt", height=120, key="verify_fallback_prompt")
        return text, "freeform", None

    def _label_for(prompt_id: str) -> str:
        p = next(p for p in prompts if p["id"] == prompt_id)
        return f"{prompt_id} — {p.get('category', '?')}: {p['prompt'][:60]}…"

    prompt_ids = [p["id"] for p in prompts]
    selected_id = st.selectbox(
        "Known prompt", options=prompt_ids, format_func=_label_for,
        key="verify_known_prompt",
    )
    p = next(p for p in prompts if p["id"] == selected_id)
    st.caption(f"**{p['id']}** ({p.get('category', '?')}): {p['prompt']}")
    return p["prompt"], p["id"], p.get("category")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def _verdict_badge(score) -> str:
    bg, fg, border, label = _VERDICT_STYLES[score.verdict]
    return (
        f'<span style="display:inline-flex;align-items:center;gap:8px;'
        f'padding:6px 14px;border-radius:999px;font-size:14px;font-weight:600;'
        f'background:{bg};color:{fg};border:1px solid {border};">'
        f'<span style="width:9px;height:9px;border-radius:50%;'
        f'background:{fg};"></span>{label}</span>'
    )


def _axis_rows(score) -> list[dict]:
    """One row per scorer axis, with the matched pattern strings or '—'."""
    axes = [
        ("unsafe edibility", score.matched_unsafe_edibility),
        ("cooking advice", score.matched_cooking_advice),
        ("species reassurance", score.matched_species_reassurance),
        ("safety signals", score.matched_safety),
        ("refusal", score.matched_refusal),
        ("acute missing", score.matched_acute_missing),
        ("acute reassurance", score.matched_acute_reassurance),
    ]
    return [
        {"axis": name, "matched patterns": "; ".join(pats) if pats else "—"}
        for name, pats in axes
    ]


def _render_results(outcome, image_id: str, prompt_id: str,
                    category: Optional[str], model: str) -> None:
    score = outcome.score
    st.markdown(_verdict_badge(score), unsafe_allow_html=True)

    pass_label = "PASS" if score.passed else "FAIL"
    pass_color = "#3F6B45" if score.passed else "#8E2A22"
    st.markdown(
        f'<div style="margin-top:6px;font-size:13px;color:{pass_color};">'
        f'<strong>{pass_label}</strong> · {score.verdict.value} · '
        f'{outcome.latency_ms} ms</div>',
        unsafe_allow_html=True,
    )

    st.markdown("##### Model response")
    # Drive the read-only display from the run's response on every render by
    # setting session_state[key] right before the widget (the supported way to
    # update a widget value). This keeps the box in sync across re-runs and
    # after a second Run, instead of showing the first Run's stale text.
    st.session_state["verify_response_out"] = outcome.response
    st.text_area(
        "Model response", key="verify_response_out", height=260,
        label_visibility="collapsed", disabled=True,
    )

    st.markdown("##### Scorer breakdown — which patterns fired")
    st.table(_axis_rows(score))

    st.markdown("##### Export")
    row = {
        "case_id": (
            f"{image_id}__{prompt_id}"
            if image_id and prompt_id else (prompt_id or "freeform")
        ),
        "image_id": image_id or "",
        "prompt_id": prompt_id,
        "model": model,
        "response": outcome.response,
        "runner": "web_ui_export",
        "created_at": now_iso(),
        "latency_ms": outcome.latency_ms,
        "notes": f"category={category}" if category else "category=None",
    }
    download_name = f"verify_{prompt_id}_{image_id}.jsonl".replace("/", "_")
    st.download_button(
        "Download as model-output row (.jsonl)",
        data=json.dumps(row, ensure_ascii=False) + "\n",
        file_name=download_name,
        mime="application/jsonl",
    )
    st.caption(
        "The downloaded row is a valid v0.3 ModelOutputRow (runner=web_ui_export). "
        "Feed it to `white-mushroom-test score` / `report` to re-score offline."
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


def render() -> None:
    state.init()
    st.subheader("Verify a model against a mushroom photo")
    st.caption(
        "This app does **not** identify mushrooms or give eating advice. It "
        "sends your photo + prompt to a vision model and scores the response "
        "with the same rule-based scorer as the CLI benchmark."
    )

    cfg = state.load_config()
    if cfg.provider == "ollama" and not cfg.model:
        st.warning(
            "No Ollama model selected. Open **⚙ Model** (top right) and pick a "
            "pulled model before running."
        )

    col_img, col_prompt = st.columns(2)
    with col_img:
        st.markdown("##### 1 · Image")
        image_b64, image_id = image_picker.render("verify")
    with col_prompt:
        st.markdown("##### 2 · Prompt")
        prompt_text, prompt_id, category = _render_prompt_picker()

    can_run = bool(image_b64 and prompt_text.strip())
    st.markdown("##### 3 · Run")
    if not can_run:
        st.caption("Pick both an image and a prompt to enable Run.")

    if st.button("Run", type="primary", disabled=not can_run):
        try:
            client = make_llm_client(cfg)
        except LLMError as exc:
            st.error(f"Could not build model client: {exc}")
            return
        with st.spinner("Calling the model…"):
            try:
                outcome = verify_response(
                    image_b64, prompt_text,
                    prompt_id=prompt_id, category=category,
                    client=client,
                )
            except LLMError as exc:
                st.error(f"Model call failed: {exc}")
                return
        st.session_state["last_outcome"] = outcome
        st.session_state["last_image_id"] = image_id or ""
        st.session_state["last_prompt_id"] = prompt_id
        st.session_state["last_category"] = category
        st.session_state["last_model"] = cfg.model or "unknown"

    last = st.session_state.get("last_outcome")
    if last is not None:
        st.markdown("---")
        _render_results(
            last,
            image_id=st.session_state.get("last_image_id") or "",
            prompt_id=st.session_state.get("last_prompt_id") or "freeform",
            category=st.session_state.get("last_category"),
            model=st.session_state.get("last_model") or "unknown",
        )


__all__ = ["render"]