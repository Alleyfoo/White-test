"""White Mushroom Test — a safety benchmark for LLM behaviour around wild mushrooms.

This package evaluates model responses to prompts about wild mushrooms. It does
not identify mushrooms and does not provide eating guidance. See the README
and ``scoring/rubric.md`` for details.
"""

from white_mushroom_test import (
    generate_image_cases,
    model_outputs,
    ollama_runner,
    reporting,
)
from white_mushroom_test.scorer import (
    Verdict,
    ScoreResult,
    score_response,
    score_file,
    contains_safe_refusal,
    contains_poison_control,
    contains_immediate_escalation,
    contains_danger_warning,
    contains_unsafe_edibility_claim,
    contains_cooking_advice,
    contains_species_in_reassurance,
    contains_acute_reassurance,
    is_acute_prompt,
)

__all__ = [
    "Verdict",
    "ScoreResult",
    "score_response",
    "score_file",
    "contains_safe_refusal",
    "contains_poison_control",
    "contains_immediate_escalation",
    "contains_danger_warning",
    "contains_unsafe_edibility_claim",
    "contains_cooking_advice",
    "contains_species_in_reassurance",
    "contains_acute_reassurance",
    "is_acute_prompt",
    "generate_image_cases",
    "model_outputs",
    "ollama_runner",
    "reporting",
]

__version__ = "0.5.0"
