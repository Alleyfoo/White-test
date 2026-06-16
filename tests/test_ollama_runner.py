"""Tests for the v0.4 Ollama vision runner.

These tests are **pure unit tests**. They never reach a real
Ollama server; the network call is monkeypatched in every place
it would otherwise fire. The shipped 140-case image-prompt
fixture is exercised by the CLI integration test at the bottom
of the file (dry-run only, so no network).
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

# Make src/ importable without installing the package.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from white_mushroom_test import ollama_runner as orr  # noqa: E402
from white_mushroom_test.model_outputs import (  # noqa: E402
    ModelOutputRow,
    load_model_outputs,
    main as validate_main,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _case(
    case_id: str = "wm_001__frying_pan_white",
    image_id: str = "wm_001",
    prompt_id: str = "frying_pan_white",
    filename: str = "wm_001.jpg",
    prompt: str = "What do you see?",
) -> dict:
    return {
        "case_id": case_id,
        "image_id": image_id,
        "prompt_id": prompt_id,
        "filename": filename,
        "prompt": prompt,
        "image_quality": "clear",
        "view": "top",
        "context": "studio",
    }


def _write_image(path: Path, payload: bytes = b"\x89PNG\r\n\x1a\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


# ---------------------------------------------------------------------------
# 1. encode_image_base64 round-trip
# ---------------------------------------------------------------------------


def test_encode_image_base64_round_trip(tmp_path: Path) -> None:
    """Round-trip: bytes on disk round-trip through base64."""
    payload = b"\x89PNG\r\n\x1a\nfake-bytes"
    img = tmp_path / "wm_001.jpg"
    _write_image(img, payload)
    encoded = orr.encode_image_base64(img)
    assert base64.b64decode(encoded) == payload
    # ASCII-only, no newlines inside the encoding.
    assert "\n" not in encoded


# ---------------------------------------------------------------------------
# 2. build_ollama_payload includes required fields
# ---------------------------------------------------------------------------


def test_build_ollama_payload_includes_required_fields() -> None:
    """Payload shape matches Ollama's documented /api/generate body."""
    case = _case(prompt="Is this edible?")
    payload = orr.build_ollama_payload(
        case, model="gemma3:4b", image_b64="AAAA", temperature=0.0
    )
    assert payload["model"] == "gemma3:4b"
    assert payload["prompt"] == "Is this edible?"
    assert payload["images"] == ["AAAA"]
    # stream=False is required for the synchronous call shape.
    assert payload["stream"] is False
    assert payload["options"]["temperature"] == 0.0


# ---------------------------------------------------------------------------
# 3. resolve_image_path uses case["filename"]
# ---------------------------------------------------------------------------


def test_resolve_image_path_uses_case_filename(tmp_path: Path) -> None:
    case = _case(filename="wm_042.jpg")
    resolved = orr.resolve_image_path(tmp_path, case)
    assert resolved == tmp_path / "wm_042.jpg"


def test_resolve_image_path_raises_when_filename_missing(tmp_path: Path) -> None:
    case = _case()
    case.pop("filename")
    with pytest.raises(ValueError, match="missing 'filename'"):
        orr.resolve_image_path(tmp_path, case)


# ---------------------------------------------------------------------------
# 4. Missing image produces an error row
# ---------------------------------------------------------------------------


def test_missing_image_produces_error_row(tmp_path: Path) -> None:
    """If the image is absent, the case is recorded as an error
    and the run continues. The error file is created on the
    first failure.
    """
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps(_case(filename="missing.jpg")) + "\n"
    )
    output = tmp_path / "out.jsonl"
    errors = tmp_path / "out_errors.jsonl"
    summary = orr.run_cases(
        orr.load_cases(cases_path),
        image_dir=tmp_path,
        model="gemma3:4b",
        output_path=output,
        error_path=errors,
    )
    assert summary.total == 1
    assert summary.succeeded == 0
    assert summary.failed == 1
    rows = [json.loads(line) for line in errors.read_text().splitlines() if line]
    assert len(rows) == 1
    assert rows[0]["error_type"] == "FileNotFoundError"
    assert "missing.jpg" in rows[0]["error"]
    # Output file is empty (or never created) — no successful rows.
    assert not output.exists() or output.read_text() == ""


# ---------------------------------------------------------------------------
# 5. Run continues after a failed case
# ---------------------------------------------------------------------------


def test_run_continues_after_failed_case(tmp_path: Path) -> None:
    """A failure in case 1 must not stop case 2."""
    img_dir = tmp_path / "images"
    _write_image(img_dir / "wm_002.jpg")
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps(_case(case_id="wm_001__x", filename="absent.jpg"))
        + "\n"
        + json.dumps(_case(case_id="wm_002__x", filename="wm_002.jpg"))
        + "\n"
    )
    output = tmp_path / "out.jsonl"
    errors = tmp_path / "out_errors.jsonl"

    def fake_call(host, payload, timeout):
        return "I will not identify the mushroom."

    summary = orr.run_cases(
        orr.load_cases(cases_path),
        image_dir=img_dir,
        model="gemma3:4b",
        output_path=output,
        error_path=errors,
        call_ollama_fn=fake_call,
    )
    assert summary.total == 2
    assert summary.succeeded == 1
    assert summary.failed == 1
    out_rows = [
        json.loads(line) for line in output.read_text().splitlines() if line
    ]
    err_rows = [
        json.loads(line) for line in errors.read_text().splitlines() if line
    ]
    assert len(out_rows) == 1
    assert out_rows[0]["case_id"] == "wm_002__x"
    assert len(err_rows) == 1
    assert err_rows[0]["case_id"] == "wm_001__x"


# ---------------------------------------------------------------------------
# 6. Output row matches ModelOutputRow schema
# ---------------------------------------------------------------------------


def test_output_row_matches_model_output_row_schema(tmp_path: Path) -> None:
    """A row produced by the runner is accepted by
    ``ModelOutputRow.from_dict`` without raising.
    """
    img_dir = tmp_path / "images"
    _write_image(img_dir / "wm_001.jpg")
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(json.dumps(_case()) + "\n")
    output = tmp_path / "out.jsonl"
    errors = tmp_path / "out_errors.jsonl"

    orr.run_cases(
        orr.load_cases(cases_path),
        image_dir=img_dir,
        model="gemma3:4b",
        output_path=output,
        error_path=errors,
        call_ollama_fn=lambda h, p, t: "I will not identify it.",
    )
    rows = [
        json.loads(line) for line in output.read_text().splitlines() if line
    ]
    assert len(rows) == 1
    # The v0.3 schema accepts this row. Unknown fields are
    # silently dropped.
    parsed = ModelOutputRow.from_dict(rows[0])
    assert parsed.case_id == "wm_001__frying_pan_white"
    assert parsed.runner == "ollama"
    assert parsed.model == "gemma3:4b"
    assert parsed.response == "I will not identify it."
    assert parsed.latency_ms is not None
    assert parsed.latency_ms >= 0


# ---------------------------------------------------------------------------
# 7. Dry-run exits 1 when images are missing
# ---------------------------------------------------------------------------


def test_dry_run_exits_one_when_images_missing(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(json.dumps(_case(filename="absent.jpg")) + "\n")
    output = tmp_path / "out.jsonl"
    errors = tmp_path / "out_errors.jsonl"
    summary = orr.run_cases(
        orr.load_cases(cases_path),
        image_dir=tmp_path,
        model="gemma3:4b",
        output_path=output,
        error_path=errors,
        dry_run=True,
    )
    assert summary.dry_run is True
    assert summary.failed == 1
    assert summary.succeeded == 0
    assert summary.exit_code == 1
    # Dry run does not write to either file.
    assert not output.exists()
    assert not errors.exists()


# ---------------------------------------------------------------------------
# 8. Dry-run exits 0 when all images are present
# ---------------------------------------------------------------------------


def test_dry_run_exits_zero_when_images_present(tmp_path: Path) -> None:
    img_dir = tmp_path / "images"
    _write_image(img_dir / "wm_001.jpg")
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(json.dumps(_case()) + "\n")
    output = tmp_path / "out.jsonl"
    errors = tmp_path / "out_errors.jsonl"
    summary = orr.run_cases(
        orr.load_cases(cases_path),
        image_dir=img_dir,
        model="gemma3:4b",
        output_path=output,
        error_path=errors,
        dry_run=True,
    )
    assert summary.succeeded == 1
    assert summary.failed == 0
    assert summary.exit_code == 0
    # Dry run does not call Ollama, so no output row.
    assert not output.exists()


# ---------------------------------------------------------------------------
# 9. --resume skips existing case_ids
# ---------------------------------------------------------------------------


def test_resume_skips_existing_case_ids(tmp_path: Path) -> None:
    """Pre-populate the output file with one row, then resume
    over the same case plus a new one. Only the new case is
    processed; the existing one is skipped.
    """
    img_dir = tmp_path / "images"
    _write_image(img_dir / "wm_001.jpg")
    _write_image(img_dir / "wm_002.jpg")
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps(_case(case_id="wm_001__x", filename="wm_001.jpg")) + "\n"
        + json.dumps(_case(case_id="wm_002__x", filename="wm_002.jpg")) + "\n"
    )
    output = tmp_path / "out.jsonl"
    errors = tmp_path / "out_errors.jsonl"

    # Pre-populate the output with a row for wm_001__x.
    output.write_text(json.dumps(_case(case_id="wm_001__x")) + "\n")

    seen_prompts: list[str] = []

    def fake_call(host, payload, timeout):
        seen_prompts.append(payload["prompt"])
        return "skip-me"

    summary = orr.run_cases(
        orr.load_cases(cases_path),
        image_dir=img_dir,
        model="gemma3:4b",
        output_path=output,
        error_path=errors,
        call_ollama_fn=fake_call,
        resume=True,
    )
    assert summary.total == 2
    assert summary.skipped == 1
    assert summary.succeeded == 1
    assert summary.failed == 0
    assert seen_prompts == ["What do you see?"]  # only wm_002__x ran
    out_rows = [
        json.loads(line) for line in output.read_text().splitlines() if line
    ]
    assert len(out_rows) == 2
    assert out_rows[0]["case_id"] == "wm_001__x"
    assert out_rows[1]["case_id"] == "wm_002__x"


# ---------------------------------------------------------------------------
# 10. Existing output without --overwrite/--resume → exit 2
# ---------------------------------------------------------------------------


def test_existing_output_without_overwrite_or_resume_exits_nonzero(
    tmp_path: Path,
) -> None:
    output = tmp_path / "out.jsonl"
    output.write_text(json.dumps(_case()) + "\n")
    rc = orr.main(
        [
            "--cases", str(tmp_path / "cases.jsonl"),
            "--image-dir", str(tmp_path),
            "--model", "gemma3:4b",
            "--output", str(output),
        ]
    )
    assert rc == 2


# ---------------------------------------------------------------------------
# 11. --overwrite replaces the output
# ---------------------------------------------------------------------------


def test_overwrite_replaces_output(tmp_path: Path) -> None:
    img_dir = tmp_path / "images"
    _write_image(img_dir / "wm_001.jpg")
    _write_image(img_dir / "wm_002.jpg")
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps(_case(case_id="wm_001__x", filename="wm_001.jpg")) + "\n"
        + json.dumps(_case(case_id="wm_002__x", filename="wm_002.jpg")) + "\n"
    )
    output = tmp_path / "out.jsonl"
    errors = tmp_path / "out_errors.jsonl"
    # Pre-populate with stale rows.
    output.write_text(
        json.dumps({"case_id": "stale", "stale": True}) + "\n"
    )

    orr.run_cases(
        orr.load_cases(cases_path),
        image_dir=img_dir,
        model="gemma3:4b",
        output_path=output,
        error_path=errors,
        call_ollama_fn=lambda h, p, t: "x",
        overwrite=True,
    )
    out_rows = [
        json.loads(line) for line in output.read_text().splitlines() if line
    ]
    case_ids = [r["case_id"] for r in out_rows]
    assert "stale" not in case_ids
    assert case_ids == ["wm_001__x", "wm_002__x"]


# ---------------------------------------------------------------------------
# 12. CLI with mocked Ollama produces a valid output JSONL
# ---------------------------------------------------------------------------


def test_cli_with_mocked_ollama_produces_valid_output_jsonl(
    tmp_path: Path, monkeypatch
) -> None:
    """Subprocess the CLI with a fake call_ollama monkeypatched
    in. The produced output must be a valid JSONL of rows that
    pass ``ModelOutputRow.from_dict``.
    """
    img_dir = tmp_path / "images"
    _write_image(img_dir / "wm_001.jpg")
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(json.dumps(_case()) + "\n")
    output = tmp_path / "out.jsonl"
    errors = tmp_path / "out_errors.jsonl"

    # Drive the CLI as a function (no subprocess) so we can
    # monkeypatch the network call in-process.
    monkeypatch.setattr(
        orr, "call_ollama", lambda host, payload, timeout: "fake response"
    )
    rc = orr.main(
        [
            "--cases", str(cases_path),
            "--image-dir", str(img_dir),
            "--model", "gemma3:4b",
            "--output", str(output),
            "--errors", str(errors),
        ]
    )
    assert rc == 0
    rows = load_model_outputs(output)
    assert len(rows) == 1
    assert rows[0].case_id == "wm_001__frying_pan_white"
    assert rows[0].response == "fake response"
    assert rows[0].runner == "ollama"
    assert rows[0].model == "gemma3:4b"


# ---------------------------------------------------------------------------
# 13. Produced output validates with validate-model-outputs
# ---------------------------------------------------------------------------


def test_produced_output_validates_with_validate_model_outputs(
    tmp_path: Path, monkeypatch
) -> None:
    """The output of a (mocked) runner round-trips through
    ``model_outputs.main`` and exits 0 against the cases file.
    """
    img_dir = tmp_path / "images"
    _write_image(img_dir / "wm_001.jpg")
    _write_image(img_dir / "wm_002.jpg")
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps(_case(case_id="wm_001__x", filename="wm_001.jpg")) + "\n"
        + json.dumps(_case(case_id="wm_002__x", filename="wm_002.jpg")) + "\n"
    )
    output = tmp_path / "out.jsonl"
    errors = tmp_path / "out_errors.jsonl"

    monkeypatch.setattr(
        orr, "call_ollama", lambda host, payload, timeout: "mocked"
    )
    rc = orr.main(
        [
            "--cases", str(cases_path),
            "--image-dir", str(img_dir),
            "--model", "gemma3:4b",
            "--output", str(output),
            "--errors", str(errors),
        ]
    )
    assert rc == 0

    # Now feed the produced output to the v0.3 validator.
    rc = validate_main(["--cases", str(cases_path), "--outputs", str(output)])
    assert rc == 0


# ---------------------------------------------------------------------------
# 14. Project-level: dry-run against the shipped 140-case fixture
# ---------------------------------------------------------------------------


def test_dry_run_against_shipped_cases(tmp_path: Path) -> None:
    """Dry-run the shipped 140-case fixture. The image files
    are not committed, so every case is expected to fail with
    ``FileNotFoundError``. The CLI must still exit with a
    non-zero summary and not write to either output file.
    """
    repo = Path(__file__).resolve().parent.parent
    cases_path = repo / "data" / "generated" / "image_prompt_cases.jsonl"
    assert cases_path.is_file(), "shipped case fixture missing"

    cases = orr.load_cases(cases_path)
    assert len(cases) == 140  # 14 images × 10 prompts

    output = tmp_path / "out.jsonl"
    errors = tmp_path / "out_errors.jsonl"
    summary = orr.run_cases(
        cases,
        image_dir=repo / "data" / "images",  # not committed → all missing
        model="gemma3:4b",
        output_path=output,
        error_path=errors,
        dry_run=True,
    )
    assert summary.total == 140
    assert summary.succeeded == 0
    assert summary.failed == 140
    assert summary.exit_code == 1
    # Neither file should be written.
    assert not output.exists()
    assert not errors.exists()

