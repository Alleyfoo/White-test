"""White Mushroom Test — a safety benchmark for LLM behaviour around wild mushrooms.

This package evaluates model responses to prompts about wild mushrooms. It does
not identify mushrooms and does not provide eating guidance. See the README
and ``scoring/rubric.md`` for details.
"""

from white_mushroom_test.scorer import (
    Verdict,
    ScoreResult,
    score_response,
    score_file,
)

__all__ = [
    "Verdict",
    "ScoreResult",
    "score_response",
    "score_file",
]

__version__ = "0.1.0"
