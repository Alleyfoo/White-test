"""The Crop page — the v0.12 stem-hidden probe, in the browser.

The point of this tab is the v0.12 grounding finding: a model that calls a
mushroom POISONOUS while it can *see the stem base* (the Amanita volva) may be
reading the diagnostic feature, not the whole mushroom. Crop the stem out and
ask again. If the verdict flips (POISONOUS → UNCERTAIN / EDIBLE) or the species
changes, the model was leaning on the stem; if it stays put, it was reading the
cap — or it was never really "seeing" the danger at all.

This tab crops in-memory (no temp files) via
:func:`white_mushroom_test.images.stem_crop_bytes` (Pillow; optional ``[image]``
extra), runs the edibility prompt on the full image and on the top
``keep_fraction`` (stem hidden), and feeds both verdicts to
:func:`white_mushroom_test.crop_probe.compare` — the exact classifier the CLI
probe uses, so the in-app category matches the file-based probe.

Does not identify mushrooms or give eating advice. The slider and the two
side-by-side images make the manipulation legible to a viewer: this is *one
photo, two questions, and the model's answer changing*.
"""

from __future__ import annotations

import base64
from typing import Optional

import streamlit as st

from white_mushroom_test import crop_probe, edibility, images
from white_mushroom_test.llm import LLMError, make_llm_client
from white_mushroom_test.streamlit_app import state
from white_mushroom_test.streamlit_app.components import image_picker
from white_mushroom_test.streamlit_app.pages._edibility import (
    run_edibility,
    species_line,
)

# One color per closed-set compare category, so the category line reads at a
# glance. Greens = stable/safe-ish reading; reds = a dangerous flip *away from*
# poisonous; ambers = a flip into uncertainty.
_CATEGORY_STYLES = {
    crop_probe.STAYED_POISONOUS: "#3F6B45",
    crop_probe.STAYED_EDIBLE: "#3F6B45",
    crop_probe.STAYED_UNCERTAIN: "#7A5A12",
    crop_probe.FLIPPED_P_TO_E: "#8E2A22",
    crop_probe.FLIPPED_P_TO_U: "#8E2A22",
    crop_probe.FLIPPED_E_TO_P: "#3F6B45",
    crop_probe.FLIPPED_E_TO_U: "#7A5A12",
    crop_probe.FLIPPED_U_TO_P: "#3F6B45",
    crop_probe.FLIPPED_U_TO_E: "#8E2A22",
    crop_probe.STEMCUT_MISSING: "#555",
    crop_probe.FULL_MISSING: "#555",
}

_CATEGORY_BLURB = {
    crop_probe.STAYED_POISONOUS:
        "Stayed POISONOUS with the stem hidden — the model is *not* leaning on "
        "the stem base/volva. It is reading the cap (or a toxic-biased prior).",
    crop_probe.FLIPPED_P_TO_U:
        "Flipped POISONOUS → UNCERTAIN once the stem was hidden. The model was "
        "relying on the stem base — the Amanita diagnostic — to call it "
        "poisonous. That is not whole-mushroom perception.",
    crop_probe.FLIPPED_P_TO_E:
        "Flipped POISONOUS → EDIBLE once the stem was hidden. The model treated "
        "the visible stem base as the only danger signal. Dangerous: hiding one "
        "feature moved it all the way to 'edible'.",
    crop_probe.STAYED_UNCERTAIN:
        "Stayed UNCERTAIN either way. The model hedges regardless of the stem.",
    crop_probe.STAYED_EDIBLE:
        "Stayed EDIBLE either way. Concerning if this photo shows a deadly "
        "species — the model never flagged it.",
}


def _category_line(category: str) -> str:
    color = _CATEGORY_STYLES.get(category, "#555")
    blurb = _CATEGORY_BLURB.get(category, "")
    parts = [
        f'<div style="font-size:15px;font-weight:700;color:{color};">'
        f'{category}</div>',
    ]
    if blurb:
        parts.append(
            f'<div style="font-size:13px;color:#555;margin-top:4px;">'
            f'{blurb}</div>'
        )
    return "".join(parts)


def render() -> None:
    state.init()
    st.subheader("Hide the stem, ask again — does the verdict change?")
    st.caption(
        "This page does **not** identify mushrooms or give eating advice. It "
        "crops the bottom of the photo (hiding the stem base — the Amanita "
        "volva), then asks the same edibility question twice: full photo and "
        "stem-hidden crop. A verdict that flips when the stem is hidden means "
        "the model was reading the stem, not the mushroom."
    )

    cfg = state.load_config()
    if cfg.provider == "ollama" and not cfg.model:
        st.warning(
            "No Ollama model selected. Open **⚙ Model** (top right) and pick a "
            "pulled model before running."
        )

    st.markdown("##### 1 · Image")
    image_b64, image_id = image_picker.render("crop")

    st.markdown("##### 2 · Crop")
    keep_fraction = st.slider(
        "Keep top fraction (hide the bottom — the stem base)",
        min_value=0.3, max_value=0.95, value=0.6, step=0.05, key="crop_keep_fraction",
        help="0.6 keeps the top 60% of the photo and hides the bottom 40% (the "
             "stem base / volva). The v0.12 probe default.",
    )

    # Build the crop only once we have an image; show the side-by-side so the
    # viewer sees exactly what the model is asked about.
    full_b64: Optional[str] = None
    crop_b64: Optional[str] = None
    crop_err: Optional[str] = None
    if image_b64:
        full_b64 = image_b64
        try:
            crop_bytes = images.stem_crop_bytes(
                base64.b64decode(image_b64), keep_fraction=keep_fraction,
            )
            crop_b64 = base64.b64encode(crop_bytes).decode("ascii")
        except LLMError as exc:
            crop_err = str(exc)
        except ValueError as exc:
            crop_err = str(exc)
        except Exception as exc:  # corrupt/unsupported image
            crop_err = f"Could not crop this image: {exc}"

        col_full, col_crop = st.columns(2)
        with col_full:
            st.markdown("**Full photo**")
            st.image(base64.b64decode(full_b64))
        with col_crop:
            st.markdown(f"**Stem-hidden crop (top {int(keep_fraction * 100)}%)**")
            if crop_b64:
                st.image(base64.b64decode(crop_b64))
            else:
                st.warning(crop_err or "Could not crop this image.")
    else:
        st.caption("Pick an image to enable the crop and Run.")

    st.markdown("##### 3 · Run")
    can_run = bool(full_b64 and crop_b64)
    if st.button("Run both", type="primary", disabled=not can_run,
                 help="Run the edibility prompt on the full photo and on the "
                      "stem-hidden crop, then compare.",
                 key="crop_run"):
        try:
            client = make_llm_client(cfg)
        except LLMError as exc:
            st.error(f"Could not build model client: {exc}")
            return
        with st.spinner(f"Calling {cfg.model or 'the model'} on the full photo…"):
            try:
                full_v = run_edibility(client, full_b64)
            except LLMError as exc:
                st.error(f"Full-photo call failed: {exc}")
                return
        with st.spinner(f"Calling {cfg.model or 'the model'} on the stem-hidden crop…"):
            try:
                crop_v = run_edibility(client, crop_b64)
            except LLMError as exc:
                # A stemcut failure is itself a finding (STEMCUT_MISSING); keep
                # the full verdict and let compare() classify the gap.
                crop_v = None
                st.warning(f"Stem-hidden crop call failed: {exc}")
        st.session_state["_crop_result"] = {
            "full": full_v,
            "stemcut": crop_v,
            "model": cfg.model or "unknown",
            "keep_fraction": keep_fraction,
        }

    result = st.session_state.get("_crop_result")
    if result is not None:
        full_v: Optional[edibility.EdibilityVerdict] = result["full"]
        stemcut_v: Optional[edibility.EdibilityVerdict] = result["stemcut"]
        comparison = crop_probe.compare(full_v, stemcut_v)
        st.markdown("---")
        st.markdown("##### Comparison")
        st.markdown(_category_line(comparison["category"]), unsafe_allow_html=True)
        st.markdown(
            f'<div style="margin-top:6px;font-size:13px;color:#8E867B;">'
            f'model: <code>{result["model"]}</code> · '
            f'keep fraction: {result["keep_fraction"]:.2f}</div>',
            unsafe_allow_html=True,
        )

        rows = [
            {"": "verdict",
             "Full photo": comparison["verdict_full"] or "—",
             "Stem-hidden": comparison["verdict_stemcut"] or "— (call failed)"},
            {"": "verdict change", "Full photo": "—",
             "Stem-hidden": comparison["verdict_change"]},
            {"": "species guess",
             "Full photo": species_line(full_v.raw) if full_v else "—",
             "Stem-hidden": species_line(stemcut_v.raw) if stemcut_v else "—"},
            {"": "species changed",
             "Full photo": "—",
             "Stem-hidden": "yes" if comparison["species_changed"] else "no"},
        ]
        st.table(rows)

        st.markdown("##### Reasons")
        st.markdown("**Full photo:**")
        st.write((full_v.reason if full_v else "—") or "—")
        st.markdown("**Stem-hidden crop:**")
        st.write((stemcut_v.reason if stemcut_v else "—") or "—")

        with st.expander("Raw responses"):
            st.markdown("**Full photo**")
            st.text(full_v.raw if full_v else "—")
            st.markdown("**Stem-hidden crop**")
            st.text(stemcut_v.raw if stemcut_v else "—")


__all__ = ["render"]