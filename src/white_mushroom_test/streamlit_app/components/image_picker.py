"""Shared image picker for the Streamlit tabs (Verify / Edibility / Crop).

A single upload-or-known-photo control parameterised by a ``key_prefix`` so the
same picker can appear in multiple tabs on one page without Streamlit
widget-key collisions (each tab's widgets get a distinct prefix). Returns
``(image_b64, image_id)`` or ``(None, None)`` when no image is ready.

``image_id`` is the manifest id for a known photo, ``"upload"`` for an uploaded
file, or ``""`` when nothing is selected. The bytes are sent verbatim (the
Ollama / OpenAI clients decode by content, so a PNG upload embeds fine even
though the payload labels it jpeg).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Optional

import streamlit as st

from white_mushroom_test.streamlit_app import state

_UPLOAD = "Upload an image"
_KNOWN = "Use a known photo"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return rows


def _load_manifest() -> list[dict]:
    return _read_jsonl(state.MANIFEST_PATH)


def _available_known_images(manifest: list[dict]) -> list[dict]:
    """Manifest rows whose image file exists under ``data/images/local/``.

    Image files are gitignored, so a fresh clone has none — the picker then
    shows an info message and the user falls back to Upload.
    """
    out: list[dict] = []
    for row in manifest:
        filename = row.get("filename")
        if isinstance(filename, str) and (state.IMAGE_DIR / filename).is_file():
            out.append(row)
    return out


def render(key_prefix: str) -> tuple[Optional[str], Optional[str]]:
    """Render the picker; return ``(image_b64, image_id)`` or ``(None, None)``.

    ``key_prefix`` namespaces every widget key so two tabs can each show this
    control on the same page without a DuplicateWidgetId error.
    """
    source = st.radio(
        "Image source", [_UPLOAD, _KNOWN], horizontal=True,
        key=f"{key_prefix}_image_source",
    )

    if source == _UPLOAD:
        uploaded = st.file_uploader(
            "Choose a mushroom photo", type=["jpg", "jpeg", "png", "webp"],
            key=f"{key_prefix}_uploader", label_visibility="collapsed",
        )
        if uploaded is None:
            st.caption("No image selected yet.")
            return None, None
        data = uploaded.getvalue()
        return base64.b64encode(data).decode("ascii"), "upload"

    available = _available_known_images(_load_manifest())
    if not available:
        st.info(
            "No local images found under `data/images/local/`. The image files "
            "are gitignored — upload one above, or drop the manifest's `.jpg` "
            "files into `data/images/local/` to use the known-photo picker."
        )
        return None, None

    def _label_for(image_id: str) -> str:
        row = next(r for r in available if r["image_id"] == image_id)
        view = row.get("view", "")
        context = row.get("context", "")
        suffix = f" — {view} / {context}".rstrip(" /")
        return f"{image_id}{suffix if suffix != ' — ' else ''}"

    image_ids = [r["image_id"] for r in available]
    selected_id = st.selectbox(
        "Known photo", options=image_ids, format_func=_label_for,
        key=f"{key_prefix}_known_image",
    )
    row = next(r for r in available if r["image_id"] == selected_id)
    path = state.IMAGE_DIR / row["filename"]
    image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return image_b64, row["image_id"]


__all__ = ["render"]