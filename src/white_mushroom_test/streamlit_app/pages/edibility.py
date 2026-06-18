"""The Edibility page — the v0.11 belief probe, in the browser.

Asks the configured vision model, for one photo, whether the mushroom is
poisonous (POISONOUS / EDIBLE / UNCERTAIN) and shows the verdict + the
best-effort species guess + the model's reason. The headline control is
**Compare all pulled models**: it fans the same edibility prompt out across
every installed non-:cloud Ollama model (thinking off, the v0.13 default) and
shows a table of the per-model verdicts, so a viewer can *see* the models
disagree on the same photo — the "don't take my word for it, watch it vary"
thesis made literal.

Reuses :data:`edibility.PROMPT` + :func:`edibility.classify_edibility` via
:mod:`white_mushroom_test.streamlit_app.pages._edibility`; no new prompt or
classifier. Does not identify mushrooms and gives no eating advice — the
caption says so.
"""

from __future__ import annotations

import streamlit as st

from white_mushroom_test import edibility
from white_mushroom_test.llm import LLMError, OllamaVisionClient, make_llm_client
from white_mushroom_test.streamlit_app import state
from white_mushroom_test.streamlit_app.components import image_picker
from white_mushroom_test.streamlit_app.pages._edibility import (
    run_edibility,
    species_line,
)
from white_mushroom_test.vision_probe import _list_ollama_models

# (background, foreground, border, label) per edibility verdict.
_VERDICT_STYLES = {
    edibility.POISONOUS: ("#F6E0DD", "#8E2A22", "#EBC9C4", "POISONOUS"),
    edibility.EDIBLE: ("#E1F0E2", "#3F6B45", "#C8DDC9", "EDIBLE"),
    edibility.UNCERTAIN: ("#FAEFD6", "#7A5A12", "#EAD9A6", "UNCERTAIN"),
}


def _verdict_badge(verdict: str) -> str:
    bg, fg, border, label = _VERDICT_STYLES.get(
        verdict, ("#ECECEC", "#555", "#DDD", verdict or "?")
    )
    return (
        f'<span style="display:inline-flex;align-items:center;gap:8px;'
        f'padding:5px 13px;border-radius:999px;font-size:13px;font-weight:600;'
        f'background:{bg};color:{fg};border:1px solid {border};">'
        f'<span style="width:9px;height:9px;border-radius:50%;'
        f'background:{fg};"></span>{label}</span>'
    )


def _fan_out_models(cfg) -> list[dict]:
    """Run the edibility prompt across every installed non-:cloud Ollama model.

    One call per model, thinking off (the v0.13 default — fast + reliable, and
    matches the CLI probe). Per-model ``LLMError`` is captured into the row so
    one model failing (unpulled, offline, non-thinking error, etc.) does not
    abort the fan-out. Returns a list of row dicts in the pulled-model order.
    """
    try:
        models = [m for m in _list_ollama_models(cfg.host, cfg.timeout)
                  if not m.endswith(":cloud")]
    except LLMError as exc:
        return [{"model": "(list error)", "error": str(exc)}]
    rows: list[dict] = []
    for model in models:
        client = OllamaVisionClient(
            cfg.host, model, timeout=cfg.timeout,
            temperature=cfg.temperature, think=False,
        )
        try:
            verdict = run_edibility(client, st.session_state["_edibility_image_b64"])
        except LLMError as exc:
            rows.append({"model": model, "error": str(exc)})
            continue
        rows.append({
            "model": model,
            "verdict": verdict.verdict,
            "species": species_line(verdict.raw),
            "reason": verdict.reason,
        })
    return rows


def _render_fan_out_table(rows: list[dict]) -> None:
    if not rows:
        st.info("No models ran.")
        return
    has_verdicts = [r for r in rows if "verdict" in r]
    if has_verdicts:
        verdicts = {r["verdict"] for r in has_verdicts}
        disagree = len(verdicts) > 1
        if disagree:
            st.markdown(
                '<div style="font-size:13px;color:#8E2A22;margin-bottom:8px;">'
                '<strong>The models disagree.</strong> Same photo, same prompt, '
                'different verdicts — that is the point of this tool.</div>',
                unsafe_allow_html=True,
            )
        else:
            only = next(iter(verdicts))
            st.markdown(
                f'<div style="font-size:13px;color:#3F6B45;margin-bottom:8px;">'
                f'All {len(has_verdicts)} answering model(s) agree: '
                f'<strong>{only}</strong>. (A unanimous answer is not proof the '
                f'models *saw* the danger — see the Crop tab.)</div>',
                unsafe_allow_html=True,
            )

    table_rows = []
    for r in rows:
        if "error" in r:
            table_rows.append({"model": r["model"], "verdict": "— (error)",
                               "species": "", "reason": r["error"][:140]})
        else:
            table_rows.append({
                "model": r["model"],
                "verdict": r.get("verdict", "?"),
                "species": r.get("species", ""),
                "reason": (r.get("reason") or "")[:160],
            })
    st.table(table_rows)
    errored = [r for r in rows if "error" in r]
    if errored:
        st.caption(
            f"{len(errored)} model(s) errored (unpulled, offline, or a "
            f"non-thinking model hit with an internal issue). Text-only models "
            f"that cannot see the image still produce an answer — read the "
            f"species/reason columns, not just the verdict."
        )


def render() -> None:
    state.init()
    st.subheader("Does the model think this mushroom is poisonous?")
    st.caption(
        "This page does **not** identify mushrooms or give eating advice. It "
        "asks a vision model the neutral edibility question and shows the "
        "verdict (POISONOUS / EDIBLE / UNCERTAIN), the species it names, and its "
        "reason. Run one model, or **Compare all pulled models** to see them "
        "disagree on the same photo."
    )

    cfg = state.load_config()
    if cfg.provider == "ollama" and not cfg.model:
        st.warning(
            "No Ollama model selected. Open **⚙ Model** (top right) and pick a "
            "pulled model, or use **Compare all pulled models** below."
        )

    st.markdown("##### 1 · Image")
    image_b64, image_id = image_picker.render("edibility")
    if image_b64:
        st.session_state["_edibility_image_b64"] = image_b64
    else:
        st.session_state.pop("_edibility_image_b64", None)

    st.markdown("##### 2 · Run")
    can_run = bool(image_b64)

    col_single, col_all = st.columns(2)
    with col_single:
        run_single = st.button(
            "Run this model", type="primary", disabled=not can_run,
            help="Run the edibility prompt against the model selected in ⚙ Model.",
        )
    with col_all:
        run_all = st.button(
            "Compare all pulled models", disabled=not can_run,
            help="Fan the edibility prompt out across every installed "
                 "non-:cloud Ollama model (thinking off).",
        )

    if not can_run:
        st.caption("Pick an image to enable Run.")

    if run_single and image_b64:
        if cfg.provider == "openai" and not cfg.api_key:
            st.error("No OpenAI API key configured. Open **⚙ Model**.")
            return
        if cfg.provider == "ollama" and not cfg.model:
            st.error("No Ollama model selected. Open **⚙ Model**.")
            return
        try:
            client = make_llm_client(cfg)
        except LLMError as exc:
            st.error(f"Could not build model client: {exc}")
            return
        with st.spinner(f"Calling {cfg.model or 'the model'}…"):
            try:
                verdict = run_edibility(client, image_b64)
            except LLMError as exc:
                st.error(f"Model call failed: {exc}")
                return
        st.session_state["_edibility_single"] = {
            "model": cfg.model or "unknown", "verdict": verdict,
        }

    if run_all and image_b64:
        if cfg.provider != "ollama":
            st.info(
                "Compare-all fans out across *local* Ollama models. Switch the "
                "provider to Local Ollama in **⚙ Model** to use it. (For OpenAI, "
                "use **Run this model**.)"
            )
        else:
            with st.spinner("Running the edibility prompt across every pulled model…"):
                rows = _fan_out_models(cfg)
            st.session_state["_edibility_fanout"] = rows

    single = st.session_state.get("_edibility_single")
    if single is not None:
        st.markdown("---")
        st.markdown("##### Result")
        verdict: edibility.EdibilityVerdict = single["verdict"]
        st.markdown(_verdict_badge(verdict.verdict), unsafe_allow_html=True)
        st.markdown(
            f'<div style="margin-top:6px;font-size:13px;color:#8E867B;">'
            f'model: <code>{single["model"]}</code></div>',
            unsafe_allow_html=True,
        )
        st.markdown("**Species guess:** " + (species_line(verdict.raw) or "—"))
        st.markdown("**Reason:**")
        st.write(verdict.reason or "—")
        with st.expander("Raw response"):
            st.text(verdict.raw)

    fanout = st.session_state.get("_edibility_fanout")
    if fanout is not None:
        st.markdown("---")
        st.markdown("##### All pulled models on this photo")
        _render_fan_out_table(fanout)


__all__ = ["render"]