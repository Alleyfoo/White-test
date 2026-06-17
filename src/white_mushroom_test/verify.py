"""Single-case verify orchestrator for the White Mushroom Test.

This is the testable seam the Streamlit verifier (and any other caller) uses
to send one image + one prompt to a vision client and score the response. It
holds no Streamlit imports and no HTTP of its own — it takes a ``client`` with
a ``generate_text(prompt, image_b64) -> str`` method (from
:mod:`white_mushroom_test.llm`) and calls the scorer.

The seam lets the UI stay thin: it builds a client, calls
:func:`verify_response`, and renders the :class:`VerifyOutcome`. Transport /
parse failures raise :class:`white_mushroom_test.llm.LLMError`, which the UI
catches and surfaces.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from white_mushroom_test.scorer import ScoreResult, score_response


@dataclass
class VerifyOutcome:
    """The result of a single verify call.

    ``latency_ms`` is the wall-clock time spent in ``client.generate_text``
    (rounded to the nearest millisecond). ``score`` carries the full
    :class:`white_mushroom_test.scorer.ScoreResult`.
    """

    response: str
    score: ScoreResult
    latency_ms: int


def verify_response(
    image_b64: str,
    prompt: str,
    *,
    prompt_id: str,
    category: str | None,
    client,
    mime_type: str = "image/jpeg",
) -> VerifyOutcome:
    """Send ``prompt`` + ``image_b64`` to ``client`` and score the response.

    ``client`` must expose
    ``generate_text(prompt: str, image_b64: str) -> str`` (see
    :class:`white_mushroom_test.llm.OllamaVisionClient` /
    :class:`OpenAIVisionClient`).

    ``mime_type`` is accepted for forward-compatibility (png uploads) but the
    Ollama / OpenAI payloads currently embed the image as ``image/jpeg``; pass a
    JPEG-encoded image, or extend the clients to honour ``mime_type`` before
    relying on it.

    Lets :class:`white_mushroom_test.llm.LLMError` propagate — the UI catches it
    and surfaces the message. The scorer is always called on whatever text the
    client returns (including the empty string, which scores ``incomplete``).
    """
    start = time.monotonic()
    response = client.generate_text(prompt, image_b64)
    latency_ms = int(round((time.monotonic() - start) * 1000))
    score = score_response(prompt_id, response, category=category)
    return VerifyOutcome(response=response, score=score, latency_ms=latency_ms)