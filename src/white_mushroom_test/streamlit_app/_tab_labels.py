"""Tab-label constants for the Streamlit verifier.

Kept in a separate module (mirroring the sql-editor scaffolding) so an extra
tab only needs a new constant here plus a new ``pages/`` module — the entry
point's ``st.tabs([...])`` list and the package ``__init__`` re-exports stay
stable.

The app has three tabs today:
- **Verify** — the rubric-scored safety verifier (one prompt → scored verdict).
- **Edibility** — the v0.11 belief probe: run the edibility prompt, show
  POISONOUS/EDIBLE/UNCERTAIN + species + reason, and fan out across all pulled
  Ollama models so a viewer sees the verdicts *disagree* on the same photo.
- **Crop** — the v0.12 grounding probe: hide the stem base and re-ask, showing
  whether the verdict flips (grounded in the volva) or stays (cap-pattern).
"""

from __future__ import annotations

TAB_VERIFY = "Verify"
TAB_EDIBILITY = "Edibility"
TAB_CROP = "Crop"

__all__ = ["TAB_VERIFY", "TAB_EDIBILITY", "TAB_CROP"]