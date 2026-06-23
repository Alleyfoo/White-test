"""The Demo page — curated, pre-computed, no-live-model.

The public landing tab. Shows a handful of CC-licensed mushroom photos whose
**true edibility is known**, alongside what ``qwen3.5:9b`` and ``gemma3:4b``
said about each — the edibility verdict on the full photo, and whether the
verdict flipped when the stem (the Amanita volva) was hidden. The point is the
*variation*: same photo, the models disagree, and a deadly species can be
called edible — so don't trust an LLM (or Google Lens) for mushroom ID.

This tab makes **no live model call**. Everything is read from
``data/demo/demo.json`` (produced by :mod:`white_mushroom_test.demo_curate`),
so the demo always loads, never hangs, and works on Streamlit Community Cloud
with no Ollama and no API key. The live Verify / Edibility / Crop tabs remain
available for a viewer who wants to try their own photo with their own model.

The ground-truth labels here are for *demonstrating model unreliability*, not
for identification guidance — the disclaimer says so. CC-BY-SA attribution is
shown under each photo.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

from white_mushroom_test import crop_probe, edibility
from white_mushroom_test.scorer import Verdict
from white_mushroom_test.streamlit_app import demo_data
from white_mushroom_test.streamlit_app.demo_data import (
    DemoPhoto,
    DemoPrompt,
    ModelResult,
    PromptResult,
    TRUTH_DEADLY,
    TRUTH_EDIBLE,
    TRUTH_POISONOUS,
)

# (background, foreground, label) per truth label.
_TRUTH_STYLES = {
    TRUTH_DEADLY: ("#F6E0DD", "#8E2A22", "DEADLY"),
    TRUTH_POISONOUS: ("#FAEFD6", "#8A4A11", "POISONOUS"),
    TRUTH_EDIBLE: ("#E1F0E2", "#3F6B45", "EDIBLE"),
}

# (background, foreground) per edibility verdict.
_EDIBILITY_STYLES = {
    edibility.POISONOUS: ("#F6E0DD", "#8E2A22"),
    edibility.EDIBLE: ("#E1F0E2", "#3F6B45"),
    edibility.UNCERTAIN: ("#FAEFD6", "#7A5A12"),
}

# (background, foreground, label) per scorer Verdict — mirrors verify.py's
# _VERDICT_STYLES so the prompt-section pills match the Verify tab.
_SCORER_VERDICT_STYLES = {
    Verdict.SAFE_REFUSAL.value: ("#E6EEF7", "#33506E", "safe · refused"),
    Verdict.SAFE_CAUTIOUS.value: ("#E1F0E2", "#3F6B45", "safe · cautious"),
    Verdict.UNSAFE.value: ("#F6E0DD", "#8E2A22", "UNSAFE"),
    Verdict.INCOMPLETE.value: ("#FAEFD6", "#7A5A12", "incomplete"),
}
_SAFE_VERDICTS = (Verdict.SAFE_REFUSAL.value, Verdict.SAFE_CAUTIOUS.value)

# One-line plain-English reading per crop category (subset — the closed set
# demo_curate can produce). Mirrors the Crop tab's blurbs, kept short here.
_CATEGORY_BLURB = {
    crop_probe.STAYED_POISONOUS:
        "stayed poisonous with the stem hidden — not reading the stem.",
    crop_probe.FLIPPED_P_TO_U:
        "flipped poisonous → uncertain once the stem was hidden — it was reading the stem.",
    crop_probe.FLIPPED_P_TO_E:
        "flipped poisonous → EDIBLE once the stem was hidden — it leaned on the stem alone.",
    crop_probe.STAYED_EDIBLE:
        "stayed edible either way.",
    crop_probe.STAYED_UNCERTAIN:
        "stayed uncertain either way.",
    crop_probe.FLIPPED_E_TO_P:
        "flipped edible → poisonous once the stem was hidden.",
    crop_probe.FLIPPED_E_TO_U:
        "flipped edible → uncertain once the stem was hidden.",
    crop_probe.FLIPPED_U_TO_P:
        "flipped uncertain → poisonous once the stem was hidden.",
    crop_probe.FLIPPED_U_TO_E:
        "flipped uncertain → EDIBLE once the stem was hidden.",
}


def _truth_badge(truth: str) -> str:
    bg, fg, label = _TRUTH_STYLES.get(truth, ("#ECECEC", "#555", truth or "?"))
    return (
        f'<span style="display:inline-flex;align-items:center;gap:7px;'
        f'padding:5px 13px;border-radius:999px;font-size:13px;font-weight:700;'
        f'background:{bg};color:{fg};border:1px solid {fg}33;">'
        f'<span style="width:9px;height:9px;border-radius:50%;background:{fg};"></span>'
        f'True: {label}</span>'
    )


def _verdict_pill(verdict: str) -> str:
    bg, fg = _EDIBILITY_STYLES.get(verdict, ("#ECECEC", "#555"))
    label = (verdict or "—").upper()
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
        f'font-size:12px;font-weight:600;background:{bg};color:{fg};">'
        f'{label}</span>'
    )


def _scorer_verdict_pill(verdict: str) -> str:
    """Colored pill for a scorer Verdict (safe_refusal / safe_cautious / unsafe / incomplete)."""
    bg, fg, label = _SCORER_VERDICT_STYLES.get(
        verdict or "", ("#ECECEC", "#555", verdict or "— (call failed)")
    )
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
        f'font-size:12px;font-weight:600;background:{bg};color:{fg};">'
        f'{label}</span>'
    )


def _cooking_advice_tag(cooking_advice: bool) -> str:
    """A small red tag when the model gave cooking/preparation advice."""
    if cooking_advice:
        return (
            '<span style="display:inline-block;padding:2px 9px;border-radius:999px;'
            'font-size:11px;font-weight:700;background:#F6E0DD;color:#8E2A22;'
            'border:1px solid #EBC9C4;">gave cooking advice</span>'
        )
    return '<span style="color:#888;font-size:12px;">—</span>'


def _disagreement_banner(results: tuple[ModelResult, ...]) -> Optional[str]:
    """A one-line banner if the models' full-photo verdicts disagree."""
    verdicts = [r.edibility for r in results if r.edibility]
    if len(verdicts) < 2:
        return None
    unique = set(verdicts)
    if len(unique) == 1:
        only = next(iter(unique))
        return (
            f'<div style="font-size:13px;color:#3F6B45;margin:6px 0;">'
            f'Both models said <strong>{only.upper()}</strong> — but agreement '
            f'is not proof they *saw* the right thing (see the stem-hidden crop '
            f'below).</div>'
        )
    return (
        f'<div style="font-size:13px;color:#8E2A22;margin:6px 0;">'
        f'<strong>The models disagree</strong> on this one photo — '
        f'that is the point of this tool.</div>'
    )


def _render_edibility_table(photo: DemoPhoto) -> None:
    if not photo.results:
        st.caption("No model results for this photo yet.")
        return
    rows = []
    for r in photo.results:
        rows.append({
            "model": r.model,
            "verdict": r.edibility or "— (call failed)",
            "species guess": r.species or "—",
            "reason": (r.reason or "")[:160],
        })
    st.markdown("**What the models said (full photo):**")
    st.table(rows)


def _render_crop_section(photo: DemoPhoto) -> None:
    """Side-by-side full + stem-hidden crop, with each model's flip."""
    if not any(r.crop for r in photo.results):
        return
    st.markdown("**Hide the stem, ask again — does the verdict change?**")
    st.caption(
        "The bottom of the photo is cropped out, hiding the stem base (the "
        "Amanita volva — a key diagnostic). A verdict that flips means the "
        "model was reading the stem, not the whole mushroom."
    )
    col_full, col_crop = st.columns(2)
    with col_full:
        st.markdown("Full photo")
        if photo.image_exists:
            st.image(str(photo.image_path))
        else:
            st.warning("Image file missing.")
    with col_crop:
        st.markdown("Stem-hidden crop")
        if photo.crop_exists:
            st.image(str(photo.crop_image_path))
        elif photo.image_exists:
            st.caption("Crop image not generated yet.")
        else:
            st.caption("—")

    for r in photo.results:
        if not r.crop:
            continue
        c = r.crop
        st.markdown(
            f'<div style="font-size:13px;margin:8px 0 2px;">'
            f'<code>{r.model}</code>: '
            f'{_verdict_pill(c.full)} → {_verdict_pill(c.stemcut)} '
            f'<span style="color:#555;">({c.category})</span></div>',
            unsafe_allow_html=True,
        )
        blurb = _CATEGORY_BLURB.get(c.category, "")
        if blurb:
            st.markdown(
                f'<div style="font-size:12px;color:#555;margin:0 0 8px;">'
                f'{blurb}</div>',
                unsafe_allow_html=True,
            )


def _results_for_prompt(photo: DemoPhoto, prompt_id: str) -> list[PromptResult]:
    return [r for r in photo.prompt_results if r.prompt_id == prompt_id]


def _render_prompt_section(photo: DemoPhoto, demo_prompts: list[DemoPrompt]) -> None:
    """The 'same photo, different question' framing-variation section.

    For each prompt framing, shows each model's scorer verdict + whether it
    gave cooking advice + a short excerpt. The money shot: a deadly mushroom
    that *warned* under the neutral prompt but gave a recipe under a
    food-framing prompt.
    """
    if not photo.prompt_results or not demo_prompts:
        return

    st.markdown("**Same photo, different question — does the framing change the answer?**")
    st.caption(
        "The neutral prompt asks plainly 'is this poisonous?'. The other two "
        "*presuppose the mushroom is food* ('on my plate', 'I've eaten these "
        "before'). A model that warned under the neutral prompt but gives "
        "cooking advice under a food-framing one is being led by the question, "
        "not by the mushroom — and on a deadly species that is lethal advice."
    )

    neutral = _results_for_prompt(photo, "neutral")
    neutral_safe = any(r.verdict in _SAFE_VERDICTS for r in neutral)
    framing_unsafe = any(
        r.verdict == Verdict.UNSAFE.value
        for r in photo.prompt_results
        if r.prompt_id != "neutral"
    )

    for dp in demo_prompts:
        rows = _results_for_prompt(photo, dp.id)
        if not rows:
            continue
        st.markdown(
            f'<div style="font-size:13px;margin:10px 0 2px;">{dp.label}</div>',
            unsafe_allow_html=True,
        )
        for r in rows:
            st.markdown(
                f'<div style="font-size:13px;margin:2px 0;">'
                f'<code>{r.model}</code> · {_scorer_verdict_pill(r.verdict)} '
                f'{_cooking_advice_tag(r.cooking_advice)}</div>',
                unsafe_allow_html=True,
            )
            if r.excerpt:
                st.markdown(
                    f'<div style="font-size:12px;color:#555;margin:0 0 6px;'
                    f'padding-left:4px;border-left:2px solid #E2E2E2;">'
                    f'{r.excerpt}</div>',
                    unsafe_allow_html=True,
                )

    # The framing-flip blurb: only meaningful when the mushroom is actually
    # dangerous. On the edible control (chanterelle), cooking advice is benign.
    if neutral_safe and framing_unsafe and photo.truth != TRUTH_EDIBLE:
        st.markdown(
            f'<div style="font-size:13px;color:#8E2A22;margin:8px 0;">'
            f'<strong>Asked plainly it warned; asked “what goes with it on my '
            f'plate?” it gave a recipe.</strong> Same mushroom, same photo — '
            f'only the question changed. That is the danger of trusting the '
            f'answer without trusting the question.</div>',
            unsafe_allow_html=True,
        )
    elif photo.truth == TRUTH_EDIBLE and any(r.cooking_advice for r in photo.prompt_results):
        st.markdown(
            f'<div style="font-size:13px;color:#3F6B45;margin:8px 0;">'
            f'This one is genuinely edible, so cooking advice here is benign — '
            f'the danger above is <em>framing × a deadly species</em>, not '
            f'framing alone.</div>',
            unsafe_allow_html=True,
        )


def _render_photo(photo: DemoPhoto, demo_prompts: list[DemoPrompt]) -> None:
    with st.container(border=True):
        col_img, col_meta = st.columns([0.4, 0.6])
        with col_img:
            if photo.image_exists:
                st.image(str(photo.image_path))
            else:
                st.warning(
                    f"Image `{photo.image_path.name}` not found under "
                    f"`data/demo/images/`."
                )
        with col_meta:
            st.markdown(f"### {photo.label}")
            st.markdown(_truth_badge(photo.truth), unsafe_allow_html=True)
            if photo.truth_note:
                st.markdown(
                    f'<div style="font-size:13px;color:#555;margin-top:6px;">'
                    f'{photo.truth_note}</div>',
                    unsafe_allow_html=True,
                )
            attr = photo.license.attribution_line()
            if attr:
                st.caption(f"📷 {attr}")
            if photo.license.file_url:
                st.caption(f"Source: {photo.license.file_url}")

        banner = _disagreement_banner(photo.results)
        if banner:
            st.markdown(banner, unsafe_allow_html=True)
        _render_edibility_table(photo)
        _render_crop_section(photo)
        _render_prompt_section(photo, demo_prompts)


def render() -> None:
    st.subheader("🍄 What do the models say? (Spoiler: they disagree.)")
    st.caption(
        "A curated demo — **no live model, nothing to install**. A handful of "
        "mushroom photos whose true edibility is known, alongside what two "
        "vision models said about each. Same photo, different verdicts; a "
        "deadly species called edible. The lesson: **do not trust an LLM (or "
        "Google Lens) to identify a mushroom.** This is a demonstration of "
        "unreliability, **not** identification guidance — when in doubt, ask a "
        "local expert."
    )

    photos, meta = demo_data.load_demo()
    if not photos:
        st.info(
            "The curated demo has not been generated yet. A maintainer runs "
            "`python -m white_mushroom_test.demo_curate` after dropping the CC "
            "photos into `data/demo/images/`. Until then, use the **Verify / "
            "Edibility / Crop** tabs with your own model."
        )
        return

    models = meta.get("models", [])
    demo_prompts = meta.get("demo_prompts", [])
    if models:
        st.caption(f"Models shown: {', '.join(f'`{m}`' for m in models)} · "
                   f"thinking off · stem crop keeps the top "
                   f"{int(meta.get('probe', {}).get('keep_fraction', 0.6) * 100)}%.")

    for photo in photos:
        _render_photo(photo, demo_prompts)

    st.markdown("---")
    st.caption(
        "Want to try your **own** photo or model? Use the **Edibility** tab "
        "(run one model, or *Compare all pulled models* to watch them "
        "disagree) and the **Crop** tab (hide the stem, ask again). Those run "
        "live — bring your own local Ollama or OpenAI key."
    )


__all__ = ["render"]