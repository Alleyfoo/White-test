"""Streamlit interactive verifier package for the White Mushroom Test.

All ``import streamlit`` lives inside this package; the core
:mod:`white_mushroom_test` (scorer / llm / verify / runner) stays stdlib-only
and importable / testable without Streamlit installed. The default ``pytest``
run therefore skips :mod:`tests.test_streamlit_app` (gated on
``pytest.importorskip("streamlit")``) and stays dependency-free.

The canonical entry point is :func:`render_app`, called by the repo-root
``streamlit_app.py`` script (``streamlit run streamlit_app.py``).
"""

from __future__ import annotations

from pathlib import Path

from white_mushroom_test.streamlit_app._tab_labels import (
    TAB_CROP,
    TAB_EDIBILITY,
    TAB_VERIFY,
)

# Repo root derived from this file:
#   src/white_mushroom_test/streamlit_app/__init__.py
#      .parents[0] = streamlit_app
#      .parents[1] = white_mushroom_test
#      .parents[2] = src
#      .parents[3] = repo root
PROJECT_ROOT = Path(__file__).resolve().parents[3]

__all__ = ["TAB_VERIFY", "TAB_EDIBILITY", "TAB_CROP", "PROJECT_ROOT", "render_app"]


def render_app() -> None:
    """Configure the page and render the Verify / Edibility / Crop tabs.

    Imported lazily so that importing this package does not pull in Streamlit
    (or any of the page modules that do). The repo-root ``streamlit_app.py``
    script calls this; tests may call it via ``streamlit.testing.v1.AppTest``.
    """
    import streamlit as st

    from white_mushroom_test.streamlit_app import state
    from white_mushroom_test.streamlit_app.components import header
    from white_mushroom_test.streamlit_app.pages import crop, edibility, verify

    st.set_page_config(
        page_title="White Mushroom Test",
        page_icon="\U0001F344",  # 🍄
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    state.init()
    header.render()
    st.markdown("---")
    tab_verify, tab_edibility, tab_crop = st.tabs([TAB_VERIFY, TAB_EDIBILITY, TAB_CROP])
    with tab_verify:
        verify.render()
    with tab_edibility:
        edibility.render()
    with tab_crop:
        crop.render()