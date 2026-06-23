"""Curated, pre-computed demo data for the Streamlit **Demo** tab.

The Demo tab shows, with **no live model call**, a handful of CC-licensed
mushroom photos whose true edibility is known, alongside what
``qwen3.5:9b`` and ``gemma3:4b`` said about each (edibility verdict + the
stem-hidden crop flip). The point is the *variation*: same photo, the models
disagree, and neither should be trusted for identification — the project
thesis, made visible without requiring the viewer to run anything.

This module is the **loader**: it reads ``data/demo/demo.json`` (a small,
committed, curated file) and resolves the image paths under
``data/demo/images/``. It does not call any model and does not import
Streamlit, so it is unit-testable on its own. The companion
:mod:`white_mushroom_test.demo_curate` produces ``demo.json`` by running the
probes; the Demo tab (:mod:`white_mushroom_test.streamlit_app.pages.demo`)
consumes it via :func:`load_demo`.

If ``demo.json`` is absent (a fresh clone before curation, or the images have
not been dropped in yet), :func:`load_demo` returns an empty list so the tab
degrades to a "not curated yet" message instead of raising.

Schema (``data/demo/demo.json``)::

    {
      "generated_at": "2026-...",
      "probe": {"prompt": "edibility", "keep_fraction": 0.6, "thinking": false},
      "models": ["qwen3.5:9b", "gemma3:4b"],
      "photos": [
        {
          "id": "destroying_angel",
          "label": "Destroying angel — Amanita virosa",
          "truth": "deadly",            // deadly | poisonous | edible
          "truth_note": "Deadly. Contains amatoxins; no safe dose.",
          "image": "destroying_angel.jpg",        // relative to data/demo/images/
          "crop_image": "destroying_angel_stemcut.jpg",
          "license": {"source": "...", "file_url": "...", "author": "...",
                      "license": "CC BY-SA 4.0", "license_url": "..."},
          "results": [                  // one entry per model
            {
              "model": "qwen3.5:9b",
              "edibility": "poisonous",   // full-photo verdict
              "species": "Amanita virosa",
              "reason": "...",
              "crop": {
                "full": "poisonous", "stemcut": "uncertain",
                "category": "FLIPPED_P_TO_U",
                "species_full": "...", "species_stemcut": "...",
                "reason_full": "...", "reason_stemcut": "..."
              }
            }
          ]
        }
      ]
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# src/white_mushroom_test/streamlit_app/demo_data.py
#   parents[0] = streamlit_app
#   parents[1] = white_mushroom_test
#   parents[2] = src
#   parents[3] = repo root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEMO_DIR = PROJECT_ROOT / "data" / "demo"
DEMO_JSON = DEMO_DIR / "demo.json"
IMAGES_DIR = DEMO_DIR / "images"

# Closed-set truth labels (mirrors what demo_curate writes; the UI colors by
# these). Kept here so the loader does not depend on the curator module.
TRUTH_DEADLY = "deadly"
TRUTH_POISONOUS = "poisonous"
TRUTH_EDIBLE = "edible"


@dataclass(frozen=True)
class License:
    """CC attribution for a demo photo (required for CC-BY-SA)."""

    source: str = ""
    file_url: str = ""
    author: str = ""
    license: str = ""
    license_url: str = ""

    def attribution_line(self) -> str:
        """One-line credit string for display under the photo."""
        parts = []
        if self.author:
            parts.append(f"Photo: {self.author}")
        if self.license:
            parts.append(self.license)
        if self.source:
            parts.append(self.source)
        return " · ".join(parts) if parts else ""


@dataclass(frozen=True)
class CropResult:
    """The full → stem-hidden edibility change for one model on one photo."""

    full: str
    stemcut: str
    category: str
    species_full: str = ""
    species_stemcut: str = ""
    reason_full: str = ""
    reason_stemcut: str = ""


@dataclass(frozen=True)
class ModelResult:
    """One model's verdicts on one demo photo (full + crop)."""

    model: str
    edibility: str          # full-photo verdict (poisonous / edible / uncertain)
    species: str
    reason: str
    crop: Optional[CropResult] = None


@dataclass(frozen=True)
class DemoPrompt:
    """One prompt framing in the 'same photo, different question' set."""

    id: str
    category: Optional[str]
    label: str
    prompt: str


@dataclass(frozen=True)
class PromptResult:
    """One (prompt, model) response on one photo, scored by the rule-based scorer.

    ``verdict`` is the scorer's ``Verdict`` value string (safe_refusal /
    safe_cautious / unsafe / incomplete), or ``""`` if the call failed.
    ``cooking_advice`` is True when the scorer's cooking-advice patterns matched
    — the dangerous signal for a food-framing prompt on a deadly mushroom.
    """

    prompt_id: str
    model: str
    verdict: str
    cooking_advice: bool = False
    refused: bool = False
    excerpt: str = ""


@dataclass(frozen=True)
class DemoPhoto:
    """One curated demo photo + every model's pre-computed verdicts."""

    id: str
    label: str
    truth: str
    truth_note: str
    image_path: Path        # absolute, may not exist on a fresh clone
    crop_image_path: Path
    license: License
    results: tuple[ModelResult, ...] = field(default_factory=tuple)
    prompt_results: tuple[PromptResult, ...] = field(default_factory=tuple)

    @property
    def image_exists(self) -> bool:
        return self.image_path.is_file()

    @property
    def crop_exists(self) -> bool:
        return self.crop_image_path.is_file()


def _parse_license(data: dict) -> License:
    if not isinstance(data, dict):
        return License()
    return License(
        source=str(data.get("source", "")),
        file_url=str(data.get("file_url", "")),
        author=str(data.get("author", "")),
        license=str(data.get("license", "")),
        license_url=str(data.get("license_url", "")),
    )


def _parse_crop(data: Optional[dict]) -> Optional[CropResult]:
    if not isinstance(data, dict):
        return None
    return CropResult(
        full=str(data.get("full", "")),
        stemcut=str(data.get("stemcut", "")),
        category=str(data.get("category", "")),
        species_full=str(data.get("species_full", "")),
        species_stemcut=str(data.get("species_stemcut", "")),
        reason_full=str(data.get("reason_full", "")),
        reason_stemcut=str(data.get("reason_stemcut", "")),
    )


def _parse_result(data: dict) -> ModelResult:
    return ModelResult(
        model=str(data.get("model", "")),
        edibility=str(data.get("edibility", "")),
        species=str(data.get("species", "")),
        reason=str(data.get("reason", "")),
        crop=_parse_crop(data.get("crop")),
    )


def _parse_demo_prompt(data: dict) -> DemoPrompt:
    return DemoPrompt(
        id=str(data.get("id", "")),
        category=data.get("category"),
        label=str(data.get("label", "")),
        prompt=str(data.get("prompt", "")),
    )


def _parse_prompt_result(data: dict) -> PromptResult:
    return PromptResult(
        prompt_id=str(data.get("prompt_id", "")),
        model=str(data.get("model", "")),
        verdict=str(data.get("verdict", "")),
        cooking_advice=bool(data.get("cooking_advice", False)),
        refused=bool(data.get("refused", False)),
        excerpt=str(data.get("excerpt", "")),
    )


def _parse_photo(data: dict, images_dir: Path) -> DemoPhoto:
    image_rel = str(data.get("image", ""))
    crop_rel = str(data.get("crop_image", ""))
    return DemoPhoto(
        id=str(data.get("id", "")),
        label=str(data.get("label", "")),
        truth=str(data.get("truth", "")),
        truth_note=str(data.get("truth_note", "")),
        image_path=images_dir / image_rel,
        crop_image_path=images_dir / crop_rel,
        license=_parse_license(data.get("license", {})),
        results=tuple(_parse_result(r) for r in data.get("results", []) if isinstance(r, dict)),
        prompt_results=tuple(
            _parse_prompt_result(r) for r in data.get("prompt_results", []) if isinstance(r, dict)
        ),
    )


def load_demo(
    path: Optional[Path] = None,
    *,
    images_dir: Path = IMAGES_DIR,
) -> tuple[list[DemoPhoto], dict]:
    """Load the curated demo.

    Returns ``(photos, meta)`` where ``meta`` carries ``generated_at``,
    ``models``, and the ``probe`` block. If the file is absent, returns
    ``([], {})`` so the Demo tab can show a "not curated yet" message. A
    malformed file raises ``ValueError`` (curation is a one-off dev action;
    fail loud rather than silently rendering a half-empty demo). ``images_dir``
    resolves each photo's relative image path; it defaults to the repo's
    ``data/demo/images/`` and is parameterized for tests.
    """
    p = path or DEMO_JSON
    if not p.is_file():
        return [], {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"demo.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("demo.json top level must be an object")
    photos = [
        _parse_photo(ph, images_dir)
        for ph in data.get("photos", []) if isinstance(ph, dict)
    ]
    meta = {
        "generated_at": str(data.get("generated_at", "")),
        "models": list(data.get("models", [])),
        "probe": data.get("probe", {}) if isinstance(data.get("probe", {}), dict) else {},
        "demo_prompts": [
            _parse_demo_prompt(p) for p in data.get("demo_prompts", []) if isinstance(p, dict)
        ],
    }
    return photos, meta


__all__ = [
    "PROJECT_ROOT", "DEMO_DIR", "DEMO_JSON", "IMAGES_DIR",
    "TRUTH_DEADLY", "TRUTH_POISONOUS", "TRUTH_EDIBLE",
    "License", "CropResult", "ModelResult", "DemoPrompt", "PromptResult", "DemoPhoto",
    "load_demo",
]