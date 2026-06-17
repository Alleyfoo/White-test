"""White Mushroom Test — Streamlit interactive verifier.

Run with::

    pip install -e ".[web]"
    streamlit run streamlit_app.py

Single-page app: pick an image + a prompt + a model (local Ollama **or** your
own OpenAI key), run the model, and see the response + the scorer verdict +
which patterns fired. Mirrors the scaffolding of the reference app at
``C:\\Users\\pertt\\sql-editor``. The ``import streamlit`` is confined to the
``white_mushroom_test.streamlit_app`` package; the core package stays
stdlib-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make ``import white_mushroom_test`` work regardless of CWD / the process
# location Streamlit launches from. The package lives under ./src.
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from white_mushroom_test.streamlit_app import render_app  # noqa: E402

render_app()