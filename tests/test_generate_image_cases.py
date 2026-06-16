"""Tests for the v0.2 image case generator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make src/ importable without installing the package.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from white_mushroom_test import generate_image_cases as gic  # noqa: E402
from white_mushroom_test.generate_image_cases import (  # noqa: E402
    ManifestError,
    ManifestRow,
    build_case,
    generate_cases,
    load_manifest,
    load_prompts,
    main,
    write_cases,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _good_manifest_row(image_id: str = "wm_001") -> dict:
    """One well-formed manifest row."""
    return {
        "image_id": image_id,
        "filename": f"{image_id}.jpg",
        "source": "own_photo",
        "license": "private_test_only",
        "image_quality": "clear",
        "view": "side_view",
        "context": "grass",
        "contains_multiple_mushrooms": False,
        "edibility_label_public": "withheld",
        "notes": "Do not use for identification guidance.",
    }


def _good_prompt_row(prompt_id: str = "p1") -> dict:
    return {"id": prompt_id, "category": "test", "prompt": "test prompt text"}


# ---------------------------------------------------------------------------
# ManifestRow.from_dict — validation
# ---------------------------------------------------------------------------


def test_manifest_row_accepts_well_formed_row() -> None:
    row = ManifestRow.from_dict(_good_manifest_row())
    assert row.image_id == "wm_001"
    assert row.edibility_label_public == "withheld"
    assert row.contains_multiple_mushrooms is False


def test_manifest_row_rejects_missing_field() -> None:
    bad = _good_manifest_row()
    del bad["image_quality"]
    with pytest.raises(ManifestError, match="missing required field"):
        ManifestRow.from_dict(bad)


def test_manifest_row_rejects_non_withheld_edibility_label() -> None:
    """Safety invariant: the project never publishes edibility labels."""
    bad = _good_manifest_row()
    bad["edibility_label_public"] = "edible"
    with pytest.raises(ManifestError, match="edibility_label_public must be 'withheld'"):
        ManifestRow.from_dict(bad)


def test_manifest_row_rejects_invalid_image_quality() -> None:
    bad = _good_manifest_row()
    bad["image_quality"] = "crystal_clear"
    with pytest.raises(ManifestError, match="image_quality"):
        ManifestRow.from_dict(bad)


def test_manifest_row_rejects_invalid_view() -> None:
    bad = _good_manifest_row()
    bad["view"] = "top_view"
    with pytest.raises(ManifestError, match="view"):
        ManifestRow.from_dict(bad)


def test_manifest_row_rejects_invalid_context() -> None:
    bad = _good_manifest_row()
    bad["context"] = "kitchen_sink"
    with pytest.raises(ManifestError, match="context"):
        ManifestRow.from_dict(bad)


def test_manifest_row_rejects_non_bool_multiple() -> None:
    bad = _good_manifest_row()
    bad["contains_multiple_mushrooms"] = "yes"
    with pytest.raises(ManifestError, match="contains_multiple_mushrooms must be a boolean"):
        ManifestRow.from_dict(bad)


def test_manifest_row_accepts_all_enum_values() -> None:
    """Smoke test: every allowed enum value round-trips."""
    for q in ("clear", "medium", "blurry", "poor"):
        for v in (
            "cap_only",
            "underside",
            "full_stem_base",
            "side_view",
            "mixed_or_basket",
            "cooking_context",
            "unknown",
        ):
            for c in (
                "grass",
                "forest",
                "yard",
                "basket",
                "frying_pan",
                "plate",
                "unknown",
            ):
                row = _good_manifest_row()
                row["image_quality"] = q
                row["view"] = v
                row["context"] = c
                # Should not raise.
                ManifestRow.from_dict(row)


# ---------------------------------------------------------------------------
# load_manifest / load_prompts — file I/O
# ---------------------------------------------------------------------------


def test_load_manifest_reads_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "manifest.jsonl"
    path.write_text(
        "\n".join(json.dumps(_good_manifest_row(f"wm_{i:03d}")) for i in range(1, 4))
        + "\n"
    )
    rows = load_manifest(path)
    assert [r.image_id for r in rows] == ["wm_001", "wm_002", "wm_003"]


def test_load_manifest_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "manifest.jsonl"
    path.write_text(
        json.dumps(_good_manifest_row("wm_001"))
        + "\n\n"
        + json.dumps(_good_manifest_row("wm_002"))
        + "\n"
    )
    rows = load_manifest(path)
    assert len(rows) == 2


def test_load_manifest_rejects_invalid_row(tmp_path: Path) -> None:
    path = tmp_path / "manifest.jsonl"
    row = _good_manifest_row("wm_001")
    row["edibility_label_public"] = "edible"  # safety invariant violation
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ManifestError, match="edibility_label_public"):
        load_manifest(path)


def test_load_manifest_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "manifest.jsonl"
    path.write_text("{not json}\n")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_manifest(path)


def test_load_prompts_requires_id_and_prompt(tmp_path: Path) -> None:
    path = tmp_path / "prompts.jsonl"
    path.write_text(json.dumps({"id": "p1", "prompt": "hello"}) + "\n")
    assert len(load_prompts(path)) == 1

    bad = tmp_path / "bad_prompts.jsonl"
    bad.write_text(json.dumps({"id": "p1"}) + "\n")
    with pytest.raises(ValueError, match="missing field"):
        load_prompts(bad)


# ---------------------------------------------------------------------------
# build_case / generate_cases
# ---------------------------------------------------------------------------


def test_build_case_without_image_dir_records_none() -> None:
    row = ManifestRow.from_dict(_good_manifest_row("wm_007"))
    case = build_case(row, _good_prompt_row("p1"))
    assert case["case_id"] == "wm_007__p1"
    assert case["image_id"] == "wm_007"
    assert case["prompt_id"] == "p1"
    assert case["filename"] == "wm_007.jpg"
    assert case["prompt"] == "test prompt text"
    assert case["prompt_category"] == "test"
    assert case["edibility_label_public"] == "withheld"
    assert case["file_present"] is None


def test_build_case_with_image_dir_records_presence(tmp_path: Path) -> None:
    row = ManifestRow.from_dict(_good_manifest_row("wm_010"))
    (tmp_path / "wm_010.jpg").write_bytes(b"fake-jpg")
    case = build_case(row, _good_prompt_row("p1"), image_dir=tmp_path)
    assert case["file_present"] is True


def test_build_case_with_image_dir_records_missing(tmp_path: Path) -> None:
    row = ManifestRow.from_dict(_good_manifest_row("wm_011"))
    # No file written.
    case = build_case(row, _good_prompt_row("p1"), image_dir=tmp_path)
    assert case["file_present"] is False


def test_generate_cases_count_is_images_times_prompts() -> None:
    images = [ManifestRow.from_dict(_good_manifest_row(f"wm_{i:03d}")) for i in range(1, 4)]
    prompts = [_good_prompt_row(f"p{i}") for i in range(1, 6)]
    cases = generate_cases(images, prompts)
    assert len(cases) == 15
    assert len(cases) == len(images) * len(prompts)


def test_generate_cases_strict_raises_on_missing_file(tmp_path: Path) -> None:
    images = [ManifestRow.from_dict(_good_manifest_row("wm_001"))]
    prompts = [_good_prompt_row("p1")]
    with pytest.raises(ManifestError, match="missing"):
        generate_cases(images, prompts, image_dir=tmp_path, strict=True)


def test_generate_cases_non_strict_records_missing(tmp_path: Path) -> None:
    images = [ManifestRow.from_dict(_good_manifest_row("wm_001"))]
    prompts = [_good_prompt_row("p1")]
    cases = generate_cases(images, prompts, image_dir=tmp_path, strict=False)
    assert cases[0]["file_present"] is False
    assert len(cases) == 1


def test_generate_cases_strict_passes_when_files_present(tmp_path: Path) -> None:
    images = [ManifestRow.from_dict(_good_manifest_row("wm_001"))]
    (tmp_path / "wm_001.jpg").write_bytes(b"x")
    prompts = [_good_prompt_row("p1")]
    cases = generate_cases(images, prompts, image_dir=tmp_path, strict=True)
    assert cases[0]["file_present"] is True


# ---------------------------------------------------------------------------
# write_cases
# ---------------------------------------------------------------------------


def test_write_cases_writes_one_json_per_line(tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    cases = [{"a": 1}, {"b": [1, 2]}]
    n = write_cases(cases, out)
    assert n == 2
    lines = out.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0]) == {"a": 1}
    assert json.loads(lines[1]) == {"b": [1, 2]}


def test_write_cases_creates_parent_dirs(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "dir" / "cases.jsonl"
    n = write_cases([{"x": 1}], out)
    assert n == 1
    assert out.is_file()


# ---------------------------------------------------------------------------
# CLI: end-to-end
# ---------------------------------------------------------------------------


def test_cli_writes_expected_count(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "\n".join(json.dumps(_good_manifest_row(f"wm_{i:03d}")) for i in range(1, 4))
        + "\n"
    )
    prompts = tmp_path / "prompts.jsonl"
    prompts.write_text(
        "\n".join(json.dumps(_good_prompt_row(f"p{i}")) for i in range(1, 4)) + "\n"
    )
    out = tmp_path / "cases.jsonl"
    rc = main(
        [
            "--manifest", str(manifest),
            "--prompts", str(prompts),
            "--output", str(out),
        ]
    )
    assert rc == 0
    cases = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(cases) == 3 * 3 == 9
    for c in cases:
        assert c["edibility_label_public"] == "withheld"


def test_cli_strict_fails_on_missing_file(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(_good_manifest_row("wm_001")) + "\n")
    prompts = tmp_path / "prompts.jsonl"
    prompts.write_text(json.dumps(_good_prompt_row("p1")) + "\n")
    out = tmp_path / "cases.jsonl"
    rc = main(
        [
            "--manifest", str(manifest),
            "--prompts", str(prompts),
            "--output", str(out),
            "--image-dir", str(tmp_path),
            "--strict",
        ]
    )
    assert rc == 2
    # Output file should not be written on failure.
    assert not out.exists()


def test_cli_non_strict_records_missing_file(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(_good_manifest_row("wm_001")) + "\n")
    prompts = tmp_path / "prompts.jsonl"
    prompts.write_text(json.dumps(_good_prompt_row("p1")) + "\n")
    out = tmp_path / "cases.jsonl"
    rc = main(
        [
            "--manifest", str(manifest),
            "--prompts", str(prompts),
            "--output", str(out),
            "--image-dir", str(tmp_path),
        ]
    )
    assert rc == 0
    case = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert case["file_present"] is False


def test_cli_fails_on_invalid_manifest(tmp_path: Path) -> None:
    bad = tmp_path / "manifest.jsonl"
    row = _good_manifest_row("wm_001")
    row["edibility_label_public"] = "edible"  # safety invariant
    bad.write_text(json.dumps(row) + "\n")
    prompts = tmp_path / "prompts.jsonl"
    prompts.write_text(json.dumps(_good_prompt_row("p1")) + "\n")
    out = tmp_path / "cases.jsonl"
    rc = main(
        [
            "--manifest", str(bad),
            "--prompts", str(prompts),
            "--output", str(out),
        ]
    )
    assert rc == 2
    assert not out.exists()


# ---------------------------------------------------------------------------
# Project's actual manifest: 14 rows, must validate cleanly
# ---------------------------------------------------------------------------


def test_real_manifest_validates_and_has_14_rows() -> None:
    repo = Path(__file__).resolve().parent.parent
    rows = load_manifest(repo / "data" / "images" / "manifest.jsonl")
    assert len(rows) == 14
    for r in rows:
        assert r.edibility_label_public == "withheld"
        assert r.license == "private_test_only"
