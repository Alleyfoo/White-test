"""Tab-label constants for the Streamlit verifier.

Kept in a separate module (mirroring the sql-editor scaffolding) so a future
extra tab only needs a new constant here plus a new ``pages/`` module — the
entry point's ``st.tabs([...])`` list and the package ``__init__`` re-exports
stay stable. The verifier is single-page today, so there is exactly one.
"""

from __future__ import annotations

TAB_VERIFY = "Verify"

__all__ = ["TAB_VERIFY"]