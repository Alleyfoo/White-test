"""Model output schema and validator for the White Mushroom Test.

This module defines the format used to record a model's response to
one (image, prompt) case. It does **not** call any model, does
**not** identify mushrooms, and does **not** publish edibility
labels. Its only job is to be a stable, validated record format
that manual collection, an Ollama runner, an API runner, or a web-UI
export can all share.

The schema for a model output row is::

    {
      "case_id":         "wm_001__frying_pan_001",
      "image_id":        "wm_001",
      "prompt_id":       "frying_pan_001",
      "model":           "manual_example_bad_model",
      "response":        "Garlic, butter and thyme go well with these.",
      "runner":          "manual",
      "created_at":      "2026-06-16T12:00:00Z",
      "latency_ms":      1234,                # optional
      "raw_output_path": "raw/gemma-3-wm_001-frying_pan_001.json",  # optional
      "notes":           ""                    # optional
    }

Required fields: ``case_id``, ``image_id``, ``prompt_id``, ``model``,
``response``, ``runner``, ``created_at``.

Optional fields: ``latency_ms``, ``raw_output_path``, ``notes``.

Validation has two layers:

1. **Row-level** (:meth:`ModelOutputRow.from_dict`) — required fields
   present and non-empty, ``latency_ms`` is an int if present.
2. **Case-level** (:func:`validate_against_cases`) — every row's
   ``case_id`` exists in the case manifest, and the row's
   ``image_id`` and ``prompt_id`` match the case. The case manifest
   is the v0.2 ``image_prompt_cases.jsonl`` produced by
   :mod:`white_mushroom_test.generate_image_cases`.

Errors are reported, not raised, by :func:`validate_against_cases` so
the CLI can list every problem in one run.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


# Schema constants. Kept in code so a malformed row fails before any
# downstream scoring sees it.
_REQUIRED_FIELDS: tuple[str, ...] = (
    "case_id",
    "image_id",
    "prompt_id",
    "model",
    "response",
    "runner",
    "created_at",
)

_OPTIONAL_FIELDS: tuple[str, ...] = (
    "latency_ms",
    "raw_output_path",
    "notes",
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ModelOutputError(ValueError):
    """Raised when a model output row fails schema validation."""


# ---------------------------------------------------------------------------
# ModelOutputRow
# ---------------------------------------------------------------------------


@dataclass
class ModelOutputRow:
    """A validated row from a model output JSONL file."""

    case_id: str
    image_id: str
    prompt_id: str
    model: str
    response: str
    runner: str
    created_at: str
    latency_ms: int | None = None
    raw_output_path: str | None = None
    notes: str | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "ModelOutputRow":
        missing = [f for f in _REQUIRED_FIELDS if f not in raw]
        if missing:
            raise ModelOutputError(
                f"model output row is missing required field(s): {missing}; "
                f"got {sorted(raw.keys())}"
            )

        # Required strings must be non-empty.
        for f in _REQUIRED_FIELDS:
            v = raw[f]
            if not isinstance(v, str) or not v.strip():
                raise ModelOutputError(
                    f"model output row {raw.get('case_id')!r}: required field "
                    f"{f!r} must be a non-empty string; got {v!r}"
                )

        latency = raw.get("latency_ms")
        if latency is not None:
            # bool is a subclass of int; reject it explicitly.
            if isinstance(latency, bool) or not isinstance(latency, int):
                raise ModelOutputError(
                    f"model output row {raw['case_id']!r}: latency_ms must "
                    f"be an int or null; got "
                    f"{type(latency).__name__} ({latency!r})"
                )

        return cls(
            case_id=raw["case_id"],
            image_id=raw["image_id"],
            prompt_id=raw["prompt_id"],
            model=raw["model"],
            response=raw["response"],
            runner=raw["runner"],
            created_at=raw["created_at"],
            latency_ms=latency,
            raw_output_path=(
                str(raw["raw_output_path"])
                if raw.get("raw_output_path") is not None
                else None
            ),
            notes=str(raw["notes"]) if raw.get("notes") is not None else None,
        )

    def to_dict(self) -> dict:
        out: dict = {
            "case_id": self.case_id,
            "image_id": self.image_id,
            "prompt_id": self.prompt_id,
            "model": self.model,
            "response": self.response,
            "runner": self.runner,
            "created_at": self.created_at,
        }
        if self.latency_ms is not None:
            out["latency_ms"] = self.latency_ms
        if self.raw_output_path is not None:
            out["raw_output_path"] = self.raw_output_path
        if self.notes is not None:
            out["notes"] = self.notes
        return out


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def _iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ModelOutputError(
                    f"{path}: invalid JSON on line {lineno}: {exc}"
                ) from exc


def load_model_outputs(path: Path) -> list[ModelOutputRow]:
    """Load and validate every row in ``path``."""
    rows: list[ModelOutputRow] = []
    for raw in _iter_jsonl(path):
        rows.append(ModelOutputRow.from_dict(raw))
    return rows


def load_cases(path: Path) -> dict[str, dict]:
    """Return a ``case_id -> case`` lookup from a generated-cases JSONL.

    A case is a plain dict with at least ``image_id`` and ``prompt_id``.
    Duplicate ``case_id`` values are reported as a ValueError because
    that is an upstream generator bug, not a downstream problem.
    """
    out: dict[str, dict] = {}
    for raw in _iter_jsonl(path):
        case_id = raw.get("case_id")
        if not case_id:
            raise ValueError(
                f"{path}: case row missing 'case_id'; got {sorted(raw.keys())}"
            )
        if case_id in out:
            raise ValueError(
                f"{path}: duplicate case_id {case_id!r}"
            )
        out[case_id] = raw
    return out


# ---------------------------------------------------------------------------
# Case-level validation
# ---------------------------------------------------------------------------


def validate_against_cases(
    rows: Iterable[ModelOutputRow],
    cases: dict[str, dict],
) -> list[str]:
    """Return a list of human-readable error messages.

    Each model output row is checked against the case it claims to
    answer. Errors include:

    - ``case_id`` not present in ``cases``
    - ``image_id`` mismatch
    - ``prompt_id`` mismatch

    An empty list means every row is consistent with the case
    manifest.
    """
    errors: list[str] = []
    for row in rows:
        case = cases.get(row.case_id)
        if case is None:
            errors.append(
                f"row case_id={row.case_id!r}: not present in case manifest"
            )
            continue
        if row.image_id != case.get("image_id"):
            errors.append(
                f"row case_id={row.case_id!r}: image_id "
                f"{row.image_id!r} does not match case "
                f"{case.get('image_id')!r}"
            )
        if row.prompt_id != case.get("prompt_id"):
            errors.append(
                f"row case_id={row.case_id!r}: prompt_id "
                f"{row.prompt_id!r} does not match case "
                f"{case.get('prompt_id')!r}"
            )
    return errors


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def list_cases(cases_path: Path, limit: int | None = None) -> list[dict]:
    """Return up to ``limit`` case rows from ``cases_path``.

    Used by the ``list-cases`` CLI subcommand for manual inspection.
    Does not validate, only reads.
    """
    rows = list(_iter_jsonl(cases_path))
    if limit is not None and limit >= 0:
        return rows[:limit]
    return rows


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="white-mushroom-test-validate-model-outputs",
        description=(
            "Validate a model-output JSONL against the case manifest. "
            "Does not call any model. Does not identify mushrooms."
        ),
    )
    parser.add_argument(
        "--cases",
        type=Path,
        required=True,
        help=(
            "Path to the generated (image, prompt) cases JSONL "
            "(data/generated/image_prompt_cases.jsonl)."
        ),
    )
    parser.add_argument(
        "--outputs",
        type=Path,
        required=True,
        help="Path to a model-output JSONL to validate.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        rows = load_model_outputs(args.outputs)
    except ModelOutputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        cases = load_cases(args.cases)
    except (ModelOutputError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    errors = validate_against_cases(rows, cases)
    if errors:
        print(
            f"Validation failed for {args.outputs} against {args.cases}:",
            file=sys.stderr,
        )
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(
        f"Validated {len(rows)} model-output row(s) from {args.outputs} "
        f"against {len(cases)} case(s) in {args.cases}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
