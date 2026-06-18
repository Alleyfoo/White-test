"""Pure (no-Streamlit) helpers shared by the Edibility and Crop tabs.

Kept separate from the page modules so these helpers are importable and
unit-testable without Streamlit installed: send one image to a vision client
and classify the edibility answer, and pull the best-effort species line out
of the raw response. The page modules call these inside ``render()``.

Reuses :data:`white_mushroom_test.edibility.PROMPT` and
:func:`white_mushroom_test.edibility.classify_edibility` verbatim — no new
prompt, no new classifier — so the in-app verdict matches the CLI probe exactly.
"""

from __future__ import annotations

from white_mushroom_test import edibility
from white_mushroom_test.llm import LLMError


def run_edibility(client, image_b64: str) -> edibility.EdibilityVerdict:
    """Send the edibility prompt + image to ``client``; classify the response.

    ``client`` must expose ``generate_text(prompt, image_b64) -> str`` (both
    :class:`white_mushroom_test.llm.OllamaVisionClient` and ``OpenAIVisionClient``
    do). Lets :class:`LLMError` propagate — the UI catches it per-model so one
    model failing does not abort a multi-model fan-out.
    """
    response = client.generate_text(edibility.PROMPT, image_b64)
    return edibility.classify_edibility(response)


def species_line(raw: str) -> str:
    """Best-effort species guess: the 2nd non-empty line of the response (the
    edibility prompt asks the model to name the species there). Mirrors
    ``crop_probe._species_line`` (kept local to that module for the file-based
    probe); reproduced here so the UI does not import a private name. Models
    sometimes wrap the species in a sentence, so this is a *display* heuristic,
    not a parsed binomial.
    """
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    return lines[1] if len(lines) > 1 else (lines[0] if lines else "")


__all__ = ["run_edibility", "species_line"]