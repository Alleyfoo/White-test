"""Tests for the curated Demo tab data layer.

Covers the pure, no-Streamlit parts:
- :mod:`white_mushroom_test.streamlit_app.demo_data` — the loader (``load_demo``)
  + the dataclass parsing + license/exists helpers.
- :mod:`white_mushroom_test.demo_curate` — the meta validator (``load_meta``)
  and the pure doc assembler (``build_demo_doc``).

The actual model-calling path (``run_demo_curate``) needs a live Ollama and is
not unit-tested here; its pure building blocks are.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from white_mushroom_test.streamlit_app import demo_data
from white_mushroom_test.streamlit_app.demo_data import (
    DemoPhoto,
    License,
    load_demo,
)
from white_mushroom_test import demo_curate


# ---------------------------------------------------------------------------
# demo_data.load_demo + parsing
# ---------------------------------------------------------------------------


def test_load_demo_returns_empty_when_file_absent(tmp_path: Path) -> None:
    photos, meta = load_demo(tmp_path / "nope.json", images_dir=tmp_path)
    assert photos == []
    assert meta == {}


def test_load_demo_parses_full_doc(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    (images / "destroying_angel.jpg").write_bytes(b"\xff\xd8\xffjpeg")
    (images / "destroying_angel_stemcut.jpg").write_bytes(b"\xff\xd8\xffcrop")
    doc = {
        "generated_at": "2026-01-01T00:00:00Z",
        "probe": {"prompt": "edibility", "keep_fraction": 0.6, "thinking": False},
        "models": ["qwen3.5:9b", "gemma3:4b"],
        "photos": [
            {
                "id": "destroying_angel",
                "label": "Destroying angel — Amanita virosa",
                "truth": "deadly",
                "truth_note": "Deadly. Amatoxins.",
                "image": "destroying_angel.jpg",
                "crop_image": "destroying_angel_stemcut.jpg",
                "license": {
                    "source": "Wikimedia Commons",
                    "file_url": "https://commons.wikimedia.org/wiki/File:X",
                    "author": "Jane Doe",
                    "license": "CC BY-SA 4.0",
                    "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                },
                "results": [
                    {
                        "model": "qwen3.5:9b",
                        "edibility": "poisonous",
                        "species": "Amanita virosa",
                        "reason": "white cap + volva",
                        "crop": {
                            "full": "poisonous", "stemcut": "uncertain",
                            "category": "FLIPPED_P_TO_U",
                            "species_full": "Amanita virosa",
                            "species_stemcut": "?",
                            "reason_full": "sees the volva",
                            "reason_stemcut": "no stem visible",
                        },
                    },
                ],
            },
        ],
    }
    demo_json = tmp_path / "demo.json"
    demo_json.write_text(json.dumps(doc), encoding="utf-8")

    photos, meta = load_demo(demo_json, images_dir=images)
    assert meta["models"] == ["qwen3.5:9b", "gemma3:4b"]
    assert meta["probe"]["keep_fraction"] == 0.6
    assert len(photos) == 1

    ph = photos[0]
    assert isinstance(ph, DemoPhoto)
    assert ph.id == "destroying_angel"
    assert ph.truth == "deadly"
    assert ph.image_path == images / "destroying_angel.jpg"
    assert ph.image_exists is True
    assert ph.crop_exists is True
    # The crop result + license parse into dataclasses.
    assert ph.results[0].crop.category == "FLIPPED_P_TO_U"
    assert ph.results[0].crop.full == "poisonous"
    assert ph.license.author == "Jane Doe"
    assert ph.license.license == "CC BY-SA 4.0"


def test_load_demo_raises_on_malformed_json(tmp_path: Path) -> None:
    bad = tmp_path / "demo.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_demo(bad, images_dir=tmp_path)


def test_load_demo_raises_on_non_object_top_level(tmp_path: Path) -> None:
    bad = tmp_path / "demo.json"
    bad.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_demo(bad, images_dir=tmp_path)


def test_license_attribution_line_composes_parts() -> None:
    lic = License(author="Jane Doe", license="CC BY-SA 4.0", source="Wikimedia Commons")
    line = lic.attribution_line()
    assert "Jane Doe" in line and "CC BY-SA 4.0" in line and "Wikimedia Commons" in line
    assert License().attribution_line() == ""


def test_photo_exists_flags_reflect_filesystem(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    (images / "a.jpg").write_bytes(b"x")
    ph = DemoPhoto(
        id="a", label="A", truth="edible", truth_note="",
        image_path=images / "a.jpg",
        crop_image_path=images / "a_stemcut.jpg",
        license=License(),
        results=(),
    )
    assert ph.image_exists is True
    assert ph.crop_exists is False  # the crop file was not written


# ---------------------------------------------------------------------------
# demo_curate.load_meta + build_demo_doc (pure)
# ---------------------------------------------------------------------------


def test_load_meta_validates_truth_labels(tmp_path: Path) -> None:
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps({"photos": [
        {"id": "a", "truth": "deadly", "image": "a.jpg"},
        {"id": "b", "truth": "not-a-real-label", "image": "b.jpg"},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="truth"):
        demo_curate.load_meta(meta)


def test_load_meta_skips_non_dict_entries(tmp_path: Path) -> None:
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps({"photos": [
        {"id": "a", "truth": "edible", "image": "a.jpg"},
        "garbage-entry",
        {"id": "b", "truth": "poisonous", "image": "b.jpg"},
    ]}), encoding="utf-8")
    photos = demo_curate.load_meta(meta)
    assert [p["id"] for p in photos] == ["a", "b"]


def test_build_demo_doc_assembles_meta_and_results() -> None:
    meta_photos = [
        {
            "id": "destroying_angel", "label": "Destroying angel",
            "truth": "deadly", "truth_note": "Deadly.",
            "image": "destroying_angel.jpg",
            "crop_image": "destroying_angel_stemcut.jpg",
            "license": {"author": "Jane Doe", "license": "CC BY-SA 4.0"},
        },
        {
            "id": "chanterelle", "label": "Chanterelle",
            "truth": "edible", "truth_note": "Edible.",
            "image": "chanterelle.jpg", "crop_image": "chanterelle_stemcut.jpg",
            "license": {"author": "John", "license": "CC BY-SA 4.0"},
        },
    ]
    results = {
        "destroying_angel": [
            {"model": "qwen3.5:9b", "edibility": "poisonous", "species": "A. virosa",
             "reason": "r", "crop": {"full": "poisonous", "stemcut": "uncertain",
             "category": "FLIPPED_P_TO_U", "species_full": "A", "species_stemcut": "B",
             "reason_full": "r1", "reason_stemcut": "r2"}},
        ],
        # chanterelle intentionally absent — a partial run must still list it.
    }
    doc = demo_curate.build_demo_doc(
        meta_photos, results,
        models=["qwen3.5:9b", "gemma3:4b"], keep_fraction=0.6,
        thinking=False, generated_at="2026-01-01T00:00:00Z",
    )
    assert doc["models"] == ["qwen3.5:9b", "gemma3:4b"]
    assert doc["probe"] == {"prompt": "edibility", "keep_fraction": 0.6, "thinking": False}
    assert doc["generated_at"] == "2026-01-01T00:00:00Z"
    # Meta is carried through verbatim (label, truth, license, image paths).
    da = doc["photos"][0]
    assert da["label"] == "Destroying angel"
    assert da["license"]["author"] == "Jane Doe"
    assert da["results"] == results["destroying_angel"]
    # A photo with no results is still present, with an empty results list —
    # the meta is the source of truth and a partial run is visible, not dropped.
    ch = doc["photos"][1]
    assert ch["id"] == "chanterelle"
    assert ch["results"] == []


def test_build_demo_doc_does_not_mutate_meta() -> None:
    meta_photos = [{"id": "x", "truth": "edible", "image": "x.jpg"}]
    demo_curate.build_demo_doc(
        meta_photos, {}, models=["m"], keep_fraction=0.6,
        thinking=False, generated_at="t",
    )
    # The curator must not inject `results` into the caller's meta dicts.
    assert "results" not in meta_photos[0]