"""Tab-label constants for the Streamlit verifier.

Kept in a separate module (mirroring the sql-editor scaffolding) so an extra
tab only needs a new constant here plus a new ``pages/`` module — the entry
point's ``st.tabs([...])`` list and the package ``__init__`` re-exports stay
stable.

The app has five tabs today:
- **Demo** — the public landing tab: curated, pre-computed, no-live-model. A
  few CC-licensed photos of known edibility shown with what the models said
  (the disagreement + the stem-hidden crop flip). Always loads; needs no
  Ollama and no API key, so it works on Streamlit Community Cloud.
- **Set B** — the counterpart to Demo: the *same* five species in the hard
  views a forager meets (young 'egg', top-down, underside, alternate angle),
  curated the same way. Set A's clean pro shots are recognized; set B's
  ordinary views give very different, often worse verdicts — the further
  proof that textbook-photo recognition is not recognition.
- **Verify** — the rubric-scored safety verifier (one prompt → scored verdict).
- **Edibility** — the v0.11 belief probe: run the edibility prompt, show
  POISONOUS/EDIBLE/UNCERTAIN + species + reason, and fan out across all pulled
  Ollama models so a viewer sees the verdicts *disagree* on the same photo.
- **Crop** — the v0.12 grounding probe: hide the stem base and re-ask, showing
  whether the verdict flips (grounded in the volva) or stays (cap-pattern).
"""

from __future__ import annotations

TAB_DEMO = "Demo"
TAB_DEMO_B = "Set B"
TAB_VERIFY = "Verify"
TAB_EDIBILITY = "Edibility"
TAB_CROP = "Crop"

__all__ = ["TAB_DEMO", "TAB_VERIFY", "TAB_EDIBILITY", "TAB_CROP"]