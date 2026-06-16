"""Generate image/prompt test cases for the v0.2 image pilot.

This module pairs every image in ``data/images/manifest.jsonl`` with
every prompt in ``data/prompts.jsonl`` and writes one row per
(image, prompt) pair to the output JSONL. The output is intended as
input to a future model runner — it does not call any model itself.

The generator is deliberately conservative:

- It does not identify mushrooms. The manifest does not record
  species or edibility, and the generator does not need to.
- It does not require image files to be present on disk. Missing
  files are recorded as ``file_present: false`` in the output so
  downstream tools can decide what to do. ``--strict`` upgrades
  this to a hard failure.
- It validates every row against the schema documented in
  ``data/images/README.md`` and fails fast on the first invalid row.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


# Schema constants. Kept in code so a malformed manifest fails before
# we write a single output row.
_ALLOWED_IMAGE_QUALITY: frozenset[str] = frozenset(
    {"clear", "medium", "blurry", "poor"}
)
_ALLOWED_VIEW: frozenset[str] = frozenset(
    {
        "cap_only",
        "underside",
        "full_stem_base",
        "side_view",
        "mixed_or_basket",
        "cooking_context",
        "unknown",
    }
)
_ALLOWED_CONTEXT: frozenset[str] = frozenset(
    {"grass", "forest", "yard", "basket", "frying_pan", "plate", "unknown"}
)

# Required fields on a manifest row. Type-validated separately below.
_MANIFEST_REQUIRED_FIELDS: tuple[str, ...] = (
    "image_id",
    "filename",
    "source",
    "license",
    "image_quality",
    "view",
    "context",
    "contains_multiple_mushrooms",
    "edibility_label_public",
)

# Required fields on a prompts row.
_PROMPT_REQUIRED_FIELDS: tuple[str, ...] = ("id", "prompt")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ManifestError(ValueError):
    """Raised when a manifest row fails schema validation."""


@dataclass
class ManifestRow:
    """A validated row from the image manifest."""

    image_id: str
    filename: str
    source: str
    license: str
    image_quality: str
    view: str
    context: str
    contains_multiple_mushrooms: bool
    edibility_label_public: str
    notes: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> "ManifestRow":
        missing = [f for f in _MANIFEST_REQUIRED_FIELDS if f not in raw]
        if missing:
            raise ManifestError(
                f"manifest row is missing required field(s): {missing}; "
                f"got {sorted(raw.keys())}"
            )

        # Enums.
        if raw["image_quality"] not in _ALLOWED_IMAGE_QUALITY:
            raise ManifestError(
                f"manifest row {raw.get('image_id')!r}: image_quality "
                f"{raw['image_quality']!r} not in "
                f"{sorted(_ALLOWED_IMAGE_QUALITY)}"
            )
        if raw["view"] not in _ALLOWED_VIEW:
            raise ManifestError(
                f"manifest row {raw.get('image_id')!r}: view "
                f"{raw['view']!r} not in {sorted(_ALLOWED_VIEW)}"
            )
        if raw["context"] not in _ALLOWED_CONTEXT:
            raise ManifestError(
                f"manifest row {raw.get('image_id')!r}: context "
                f"{raw['context']!r} not in {sorted(_ALLOWED_CONTEXT)}"
            )

        # Safety invariant: edibility_label_public MUST be withheld.
        if raw["edibility_label_public"] != "withheld":
            raise ManifestError(
                f"manifest row {raw.get('image_id')!r}: "
                f"edibility_label_public must be 'withheld' (the project "
                f"never publishes edibility labels); got "
                f"{raw['edibility_label_public']!r}"
            )

        # Type checks.
        if not isinstance(raw["contains_multiple_mushrooms"], bool):
            raise ManifestError(
                f"manifest row {raw.get('image_id')!r}: "
                f"contains_multiple_mushrooms must be a boolean; got "
                f"{type(raw['contains_multiple_mushrooms']).__name__}"
            )

        return cls(
            image_id=str(raw["image_id"]),
            filename=str(raw["filename"]),
            source=str(raw["source"]),
            license=str(raw["license"]),
            image_quality=raw["image_quality"],
            view=raw["view"],
            context=raw["context"],
            contains_multiple_mushrooms=raw["contains_multiple_mushrooms"],
            edibility_label_public=raw["edibility_label_public"],
            notes=str(raw.get("notes", "")),
        )


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
                raise ValueError(
                    f"{path}: invalid JSON on line {lineno}: {exc}"
                ) from exc


def load_manifest(path: Path) -> list[ManifestRow]:
    """Load and validate every row in ``path``."""
    rows: list[ManifestRow] = []
    for raw in _iter_jsonl(path):
        rows.append(ManifestRow.from_dict(raw))
    return rows


def load_prompts(path: Path) -> list[dict]:
    """Load prompts. Each row must have ``id`` and ``prompt``."""
    out: list[dict] = []
    for raw in _iter_jsonl(path):
        missing = [f for f in _PROMPT_REQUIRED_FIELDS if f not in raw]
        if missing:
            raise ValueError(
                f"{path}: prompt row missing field(s) {missing}; "
                f"got {sorted(raw.keys())}"
            )
        out.append(raw)
    return out


# ---------------------------------------------------------------------------
# Case generation
# ---------------------------------------------------------------------------


def build_case(
    image: ManifestRow,
    prompt: dict,
    *,
    image_dir: Path | None = None,
) -> dict:
    """Build one (image, prompt) case dict.

    The ``file_present`` field records whether the image file exists
    on disk. ``--strict`` upgrades a missing file to a hard failure
    upstream; by default it is informational.
    """
    if image_dir is not None:
        present = (image_dir / image.filename).is_file()
    else:
        present = None  # type: ignore[assignment]
    return {
        "case_id": f"{image.image_id}__{prompt['id']}",
        "image_id": image.image_id,
        "prompt_id": prompt["id"],
        "filename": image.filename,
        "prompt": prompt["prompt"],
        "prompt_category": prompt.get("category", ""),
        "image_quality": image.image_quality,
        "view": image.view,
        "context": image.context,
        "contains_multiple_mushrooms": image.contains_multiple_mushrooms,
        "edibility_label_public": image.edibility_label_public,
        "file_present": present,
    }


def generate_cases(
    manifest: Iterable[ManifestRow],
    prompts: Iterable[dict],
    *,
    image_dir: Path | None = None,
    strict: bool = False,
) -> list[dict]:
    """Generate the full (image × prompt) case list.

    If ``strict`` is True and ``image_dir`` is set, an image file that
    is missing from disk raises :class:`ManifestError` before any
    output is produced. Without ``strict``, missing files are
    recorded in the case as ``file_present: False``.
    """
    manifest_list = list(manifest)
    prompt_list = list(prompts)

    if strict and image_dir is not None:
        missing: list[str] = []
        for row in manifest_list:
            if not (image_dir / row.filename).is_file():
                missing.append(f"{row.image_id} ({row.filename})")
        if missing:
            raise ManifestError(
                f"{len(missing)} image file(s) missing under {image_dir}: "
                f"{', '.join(missing)}"
            )

    cases: list[dict] = []
    for image in manifest_list:
        for prompt in prompt_list:
            cases.append(
                build_case(image, prompt, image_dir=image_dir)
            )
    return cases


def write_cases(cases: Iterable[dict], path: Path) -> int:
    """Write cases as JSONL. Returns the number of cases written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(case, ensure_ascii=False))
            fh.write("\n")
            n += 1
    return n


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="white-mushroom-test-generate-image-cases",
        description=(
            "Generate (image, prompt) test cases for the v0.2 image "
            "pilot. Does not call any model. Does not identify mushrooms."
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Path to image manifest JSONL.",
    )
    parser.add_argument(
        "--prompts",
        type=Path,
        required=True,
        help="Path to prompts JSONL.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the generated cases JSONL.",
    )
    parser.add_argument(
        "--image-dir",
        type=Path,
        default=None,
        help=(
            "Directory to look for image files in. If supplied, each "
            "case records whether the file is present on disk."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Fail the run if any image file is missing from "
            "--image-dir. By default, missing files are recorded but "
            "do not fail the run."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        prompts = load_prompts(args.prompts)
        cases = generate_cases(
            manifest,
            prompts,
            image_dir=args.image_dir,
            strict=args.strict,
        )
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    n = write_cases(cases, args.output)
    print(
        f"Generated {n} case(s) "
        f"({len(manifest)} image(s) x {len(prompts)} prompt(s)) "
        f"-> {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
