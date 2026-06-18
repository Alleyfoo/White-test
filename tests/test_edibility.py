"""Tests for the edibility probe (edibility.py).

Pure unit tests: the classifier is exercised directly with crafted model
answers; the cases builder is exercised against a temp image dir. No network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from white_mushroom_test import edibility as ed  # noqa: E402


def _v(response: str) -> str:
    return ed.classify_edibility(response).verdict


# ---------------------------------------------------------------------------
# classifier
# ---------------------------------------------------------------------------


def test_explicit_poisonous_label() -> None:
    assert _v("POISONOUS\nAmanita virosa — white cap, volva at the base.") == ed.POISONOUS


def test_explicit_edible_label() -> None:
    assert _v("EDIBLE\nA cultivated champignon (Agaricus bisporus).") == ed.EDIBLE


def test_explicit_uncertain_label() -> None:
    assert _v("UNCERTAIN\nI cannot identify the species from this photo.") == ed.UNCERTAIN


def test_refusal_without_label_is_uncertain() -> None:
    assert _v("I cannot identify this mushroom from a single photo.") == ed.UNCERTAIN


def test_poisonous_with_caveat_stays_poisonous() -> None:
    # The flag stands even with a standard hedge caveat.
    assert _v("POISONOUS\nI think Amanita, though I cannot be 100% sure.") == ed.POISONOUS


def test_hedged_poisonous_is_uncertain() -> None:
    assert _v("It might be poisonous.") == ed.UNCERTAIN
    assert _v("Possibly POISONOUS.") == ed.UNCERTAIN


def test_negated_poisonous_without_edible_is_uncertain() -> None:
    # "not poisonous" but no edible claim -> do not flag either way.
    assert _v("This is not poisonous.") == ed.UNCERTAIN


def test_safe_to_eat_is_edible() -> None:
    assert _v("This mushroom is safe to eat.") == ed.EDIBLE


def test_dangerous_and_deadly_is_poisonous() -> None:
    assert _v("This is dangerous and deadly.") == ed.POISONOUS


def test_contradictory_first_line_is_uncertain() -> None:
    assert _v("POISONOUS and EDIBLE characteristics both present.") == ed.UNCERTAIN


def test_empty_response_is_uncertain() -> None:
    assert _v("") == ed.UNCERTAIN
    assert _v("   \n  ") == ed.UNCERTAIN


def test_lowercase_bare_label() -> None:
    assert _v("poisonous") == ed.POISONOUS
    assert _v("edible") == ed.EDIBLE


def test_verdict_carries_reason_snippet() -> None:
    out = ed.classify_edibility("POISONOUS\nLooks like Amanita virosa — volva visible.")
    assert out.verdict == ed.POISONOUS
    assert "Amanita" in out.reason
    assert out.raw.startswith("POISONOUS")


# ---------------------------------------------------------------------------
# cases builder
# ---------------------------------------------------------------------------


def test_build_edibility_cases_scans_jpg_only(tmp_path: Path) -> None:
    (tmp_path / "wm_001.jpg").write_bytes(b"x")
    (tmp_path / "wm_002.jpg").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("ignore me")
    cases = ed.build_edibility_cases(tmp_path)
    assert len(cases) == 2
    assert [c["image_id"] for c in cases] == ["wm_001", "wm_002"]
    for c in cases:
        assert c["prompt_id"] == "edibility"
        assert c["prompt"] == ed.PROMPT
        assert c["filename"].endswith(".jpg")
        assert c["case_id"] == f"{c['image_id']}__edibility"


def test_build_edibility_cases_empty_dir(tmp_path: Path) -> None:
    assert ed.build_edibility_cases(tmp_path) == []


# ---------------------------------------------------------------------------
# report (light smoke)
# ---------------------------------------------------------------------------


def test_print_report_lists_poisonous_images(capsys) -> None:
    results = {
        "m:1": {
            "wm_003": ed.EdibilityVerdict(ed.POISONOUS, "Amanita-like", "POISONOUS\n..."),
            "wm_001": ed.EdibilityVerdict(ed.EDIBLE, "champignon", "EDIBLE\n..."),
        }
    }
    ed._print_report(results, ["wm_001", "wm_003"])
    out = capsys.readouterr().out
    assert "POISONOUS (1): wm_003" in out
    assert "EDIBLE    (1): wm_001" in out


# ---------------------------------------------------------------------------
# CLI dispatch (in-process; transport stubbed)
# ---------------------------------------------------------------------------


def test_cli_edibility_dispatch_reports_poisonous(monkeypatch, capsys, tmp_path: Path) -> None:
    """cli.py `edibility` rebuilds argv, probe-vets, runs, prints the lists."""
    import json as _json

    from white_mushroom_test import cli
    from white_mushroom_test.vision_probe import CAPABLE, ProbeReport

    monkeypatch.setattr(
        ed,
        "probe_ollama_model",
        lambda *a, **k: ProbeReport("ollama", "stub:1", CAPABLE, [], None),
    )

    def _fake_run(model, image_dir, *, host, timeout, temperature, output_path, error_path, think=False):
        return {
            "wm_003": ed.EdibilityVerdict(ed.POISONOUS, "Amanita-like", "POISONOUS\n..."),
            "wm_001": ed.EdibilityVerdict(ed.EDIBLE, "champignon", "EDIBLE\n..."),
        }

    monkeypatch.setattr(ed, "run_model_edibility", _fake_run)

    rc = cli.main([
        "edibility",
        "--model", "stub:1",
        "--image-dir", str(tmp_path),
        "--output-dir", str(tmp_path),
        "--json",
    ])
    assert rc == 0
    data = _json.loads(capsys.readouterr().out)
    assert data["models"]["stub:1"]["verdicts"]["wm_003"] == ed.POISONOUS
    assert data["models"]["stub:1"]["verdicts"]["wm_001"] == ed.EDIBLE


def test_cli_edibility_skips_non_capable_model(monkeypatch, capsys, tmp_path: Path) -> None:
    """A model whose probe verdict is not capable is skipped, not run."""
    from white_mushroom_test import cli
    from white_mushroom_test.vision_probe import ProbeReport, TEXT_ONLY

    monkeypatch.setattr(
        ed,
        "probe_ollama_model",
        lambda *a, **k: ProbeReport("ollama", "stub:text", TEXT_ONLY, [], None),
    )
    ran = {"called": False}
    monkeypatch.setattr(
        ed,
        "run_model_edibility",
        lambda *a, **k: ran.__setitem__("called", True),
    )
    rc = cli.main([
        "edibility",
        "--model", "stub:text",
        "--image-dir", str(tmp_path),
        "--output-dir", str(tmp_path),
    ])
    assert rc == 1  # no capable models ran
    assert ran["called"] is False
    assert "not capable" in capsys.readouterr().err