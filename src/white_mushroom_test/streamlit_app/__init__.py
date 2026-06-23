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
    TAB_DEMO,
    TAB_DEMO_B,
    TAB_DEMO_C,
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

__all__ = ["TAB_DEMO", "TAB_DEMO_B", "TAB_DEMO_C", "TAB_VERIFY", "TAB_EDIBILITY", "TAB_CROP", "PROJECT_ROOT", "render_app"]


def render_app() -> None:
    """Configure the page and render the Demo / Set B / Set C / Verify / Edibility / Crop tabs.

    The demo tabs are built from :data:`demo.DEMO_SETS` (one tab per curated
    set), so adding a set is a single registry entry — no wiring here. The live
    Verify / Edibility / Crop tabs follow. Imported lazily so importing this
    package does not pull in Streamlit (or the page modules that do); the
    repo-root ``streamlit_app.py`` script calls this, tests via AppTest.
    """
    import streamlit as st

    from white_mushroom_test.streamlit_app import state
    from white_mushroom_test.streamlit_app.components import header
    from white_mushroom_test.streamlit_app.pages import crop, demo, edibility, verify

    st.set_page_config(
        page_title="White Mushroom Test",
        page_icon="\U0001F344",  # 🍄
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    state.init()
    header.render()
    st.markdown("---")
    # One tab per curated demo set (Demo is first — the public landing tab,
    # no live model, always loads), then the live tabs. The demo sets walk the
    # thesis downhill: clean shots (A) → hard views (B) → poor quality (C).
    demo_sets = demo.DEMO_SETS
    labels = [s.tab_label for s in demo_sets] + [TAB_VERIFY, TAB_EDIBILITY, TAB_CROP]
    tabs = st.tabs(labels)
    for i, ds in enumerate(demo_sets):
        with tabs[i]:
            demo.render_set(ds)
    with tabs[len(demo_sets)]:
        verify.render()
    with tabs[len(demo_sets) + 1]:
        edibility.render()
    with tabs[len(demo_sets) + 2]:
        crop.render()