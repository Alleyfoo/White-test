"""Tests for the cropped-image probe (crop_probe.py).

Pure unit tests: ``compare`` is exercised directly with crafted verdicts;
``build_crop_cases`` against a temp image/crops dir; ``run_crop_model`` through
the real runner with ``call_ollama`` monkeypatched (keyed by the base64 image
bytes — no PIL, no real JPEG decode). No network, no Pillow required.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from white_mushroom_test import crop_probe as cp  # noqa: E402
from white_mushroom_test import edibility as ed  # noqa: E402
from white_mushroom_test import images, ollama_runner  # noqa: E402
from white_mushroom_test.llm import LLMError  # noqa: E402
from white_mushroom_test.vision_probe import CAPABLE, ProbeReport, TEXT_ONLY  # noqa: E402


def _pv(verdict: str, raw: str = "") -> ed.EdibilityVerdict:
    return ed.EdibilityVerdict(verdict, "", raw or verdict)


# ---------------------------------------------------------------------------
# compare()
# ---------------------------------------------------------------------------


def test_compare_stayed_poisonous() -> None:
    out = cp.compare(_pv(ed.POISONOUS, "POISONOUS\nAmanita\nvolva"),
                     _pv(ed.POISONOUS, "POISONOUS\nAmanita\nvolva"))
    assert out["category"] == cp.STAYED_POISONOUS
    assert out["verdict_change"] == "P->P"
    assert out["species_changed"] is False


def test_compare_flipped_p_to_u() -> None:
    out = cp.compare(_pv(ed.POISONOUS, "POISONOUS\nAmanita virosa\nvolva at base"),
                     _pv(ed.UNCERTAIN, "UNCERTAIN\ncannot identify"))
    assert out["category"] == cp.FLIPPED_P_TO_U
    assert out["verdict_change"] == "P->U"
    assert out["species_changed"] is True  # "Amanita virosa" != "cannot identify"


def test_compare_flipped_p_to_e() -> None:
    out = cp.compare(_pv(ed.POISONOUS), _pv(ed.EDIBLE))
    assert out["category"] == cp.FLIPPED_P_TO_E
    assert out["verdict_change"] == "P->E"


def test_compare_stayed_uncertain() -> None:
    out = cp.compare(_pv(ed.UNCERTAIN), _pv(ed.UNCERTAIN))
    assert out["category"] == cp.STAYED_UNCERTAIN
    assert out["verdict_change"] == "U->U"


def test_compare_stemcut_missing() -> None:
    out = cp.compare(_pv(ed.POISONOUS), None)
    assert out["category"] == cp.STEMCUT_MISSING
    assert out["verdict_change"] == "P->_"
    assert out["stemcut_present"] is False
    assert out["verdict_stemcut"] is None


def test_compare_species_same_when_lines_match() -> None:
    out = cp.compare(_pv(ed.POISONOUS, "POISONOUS\nAmanita\nx"),
                     _pv(ed.UNCERTAIN, "UNCERTAIN\nAmanita\ny"))
    # Same 2nd-line species guess ("Amanita") even though the verdict flipped.
    assert out["species_changed"] is False


# ---------------------------------------------------------------------------
# build_crop_cases
# ---------------------------------------------------------------------------


def test_build_crop_cases_pairs_full_and_stemcut(tmp_path: Path) -> None:
    image_dir = tmp_path / "imgs"
    crops_dir = tmp_path / "crops"
    image_dir.mkdir()
    crops_dir.mkdir()
    (image_dir / "wm_001.jpg").write_bytes(b"x")
    (image_dir / "wm_002.jpg").write_bytes(b"x")
    (crops_dir / "wm_001_stemcut.jpg").write_bytes(b"x")  # no wm_002 crop

    cases = cp.build_crop_cases(image_dir, crops_dir)
    fulls = [c for c in cases if c["variant"] == cp.FULL]
    cuts = [c for c in cases if c["variant"] == cp.STEMCUT]
    assert [c["image_id"] for c in fulls] == ["wm_001", "wm_002"]
    assert [c["image_id"] for c in cuts] == ["wm_001"]  # wm_002 has no crop

    f1 = next(c for c in fulls if c["image_id"] == "wm_001")
    assert f1["case_id"] == "wm_001__full__edibility"
    assert f1["filename"] == "wm_001.jpg"
    assert f1["prompt_id"] == "edibility"
    assert f1["prompt"] == ed.PROMPT

    c1 = next(c for c in cuts if c["image_id"] == "wm_001")
    assert c1["case_id"] == "wm_001__stemcut__edibility"
    assert c1["filename"] == "wm_001_stemcut.jpg"


def test_build_crop_cases_missing_crops_dir(tmp_path: Path) -> None:
    # crops_dir does not exist -> full cases only, no OSError from the glob.
    image_dir = tmp_path / "imgs"
    image_dir.mkdir()
    (image_dir / "wm_001.jpg").write_bytes(b"x")
    cases = cp.build_crop_cases(image_dir, tmp_path / "nope")
    assert [c["variant"] for c in cases] == [cp.FULL]


def test_build_crop_cases_only_stems_filter(tmp_path: Path) -> None:
    image_dir = tmp_path / "imgs"
    crops_dir = tmp_path / "crops"
    image_dir.mkdir()
    crops_dir.mkdir()
    for stem in ("wm_001", "wm_002", "wm_003"):
        (image_dir / f"{stem}.jpg").write_bytes(b"x")
        (crops_dir / f"{stem}_stemcut.jpg").write_bytes(b"x")
    cases = cp.build_crop_cases(image_dir, crops_dir, only_stems={"wm_002"})
    assert {c["image_id"] for c in cases} == {"wm_002"}


# ---------------------------------------------------------------------------
# run_crop_model (real runner, call_ollama stubbed; keyed by base64 bytes)
# ---------------------------------------------------------------------------


def test_run_crop_model_pairs_full_and_stemcut(monkeypatch, tmp_path: Path) -> None:
    image_dir = tmp_path / "imgs"
    crops_dir = tmp_path / "crops"
    out_dir = tmp_path / "out"
    image_dir.mkdir()
    crops_dir.mkdir()
    full_b = b"\xff\xd8\xffFULL_wm_001"
    cut_b = b"\xff\xd8\xffSTEMCUT_wm_001"
    (image_dir / "wm_001.jpg").write_bytes(full_b)
    (crops_dir / "wm_001_stemcut.jpg").write_bytes(cut_b)

    def fake_call(host, payload, timeout):
        data = base64.b64decode(payload["images"][0])
        if data == full_b:
            return "POISONOUS\nAmanita virosa\nvolva at the base"
        if data == cut_b:
            return "UNCERTAIN\ncannot identify"
        raise AssertionError(f"unexpected image bytes: {data!r}")

    monkeypatch.setattr(ollama_runner, "call_ollama", fake_call)

    paired = cp.run_crop_model(
        "stub:1", image_dir, crops_dir,
        host="http://x", timeout=10, temperature=0.0, output_dir=out_dir,
    )
    assert paired["wm_001"]["full"].verdict == ed.POISONOUS
    assert paired["wm_001"]["stemcut"].verdict == ed.UNCERTAIN
    # The two output JSONLs exist and are distinct.
    assert (out_dir / "crop_stub_1_full.jsonl").is_file()
    assert (out_dir / "crop_stub_1_stemcut.jsonl").is_file()


def test_run_crop_model_no_crops_raises(monkeypatch, tmp_path: Path) -> None:
    image_dir = tmp_path / "imgs"
    image_dir.mkdir()
    (image_dir / "wm_001.jpg").write_bytes(b"x")
    crops_dir = tmp_path / "crops"  # empty / missing
    with pytest.raises(LLMError, match="no crop files"):
        cp.run_crop_model(
            "stub:1", image_dir, crops_dir,
            host="http://x", timeout=10, temperature=0.0, output_dir=tmp_path / "out",
        )


def test_run_crop_model_max_tokens_caps_output(monkeypatch, tmp_path: Path) -> None:
    """``max_tokens`` flows through to the Ollama payload as ``num_predict`` so a
    thinking model's output length is capped (the urllib timeout is per-recv and
    does not bound total generation time)."""
    image_dir = tmp_path / "imgs"
    crops_dir = tmp_path / "crops"
    out_dir = tmp_path / "out"
    image_dir.mkdir()
    crops_dir.mkdir()
    full_b = b"\xff\xd8\xffFULL_wm_001"
    cut_b = b"\xff\xd8\xffSTEMCUT_wm_001"
    (image_dir / "wm_001.jpg").write_bytes(full_b)
    (crops_dir / "wm_001_stemcut.jpg").write_bytes(cut_b)
    seen: dict = {}

    def fake_call(host, payload, timeout):
        seen["options"] = payload["options"]
        data = base64.b64decode(payload["images"][0])
        return "POISONOUS\nAmanita" if data == full_b else "UNCERTAIN\n?"

    monkeypatch.setattr(ollama_runner, "call_ollama", fake_call)
    cp.run_crop_model(
        "stub:1", image_dir, crops_dir,
        host="http://x", timeout=10, temperature=0.0, output_dir=out_dir,
        max_tokens=4096,
    )
    assert seen["options"].get("num_predict") == 4096


def test_run_crop_model_think_defaults_false(monkeypatch, tmp_path: Path) -> None:
    """``think`` defaults False (thinking off — the reliable default) and reaches
    the Ollama payload's top-level ``think`` field; ``True`` opts in."""
    image_dir = tmp_path / "imgs"
    crops_dir = tmp_path / "crops"
    out_dir = tmp_path / "out"
    image_dir.mkdir()
    crops_dir.mkdir()
    full_b = b"\xff\xd8\xffFULL_wm_001"
    cut_b = b"\xff\xd8\xffSTEMCUT_wm_001"
    (image_dir / "wm_001.jpg").write_bytes(full_b)
    (crops_dir / "wm_001_stemcut.jpg").write_bytes(cut_b)
    seen: list[bool] = []

    def fake_call(host, payload, timeout):
        seen.append(payload["think"])
        data = base64.b64decode(payload["images"][0])
        return "POISONOUS\nAmanita" if data == full_b else "UNCERTAIN\n?"

    monkeypatch.setattr(ollama_runner, "call_ollama", fake_call)
    # Default -> thinking off (two calls: full + stemcut).
    cp.run_crop_model(
        "stub:1", image_dir, crops_dir,
        host="http://x", timeout=10, temperature=0.0, output_dir=out_dir,
    )
    assert seen == [False, False]
    seen.clear()
    # Opt-in -> thinking on for both calls.
    cp.run_crop_model(
        "stub:1", image_dir, crops_dir,
        host="http://x", timeout=10, temperature=0.0, output_dir=out_dir, think=True,
    )
    assert seen == [True, True]


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def test_print_report_shows_flip_and_view(capsys) -> None:
    results = {
        "m:1": {
            "wm_003": {
                "full": ed.EdibilityVerdict(ed.POISONOUS, "volva",
                                            "POISONOUS\nAmanita virosa\nvolva at base"),
                "stemcut": ed.EdibilityVerdict(ed.UNCERTAIN, "can't tell",
                                               "UNCERTAIN\ncannot identify"),
            }
        }
    }
    cp._print_report(results, ["wm_003"], {"wm_003": "full_stem_base"})
    out = capsys.readouterr().out
    assert "FLIPPED_P_TO_U" in out
    assert "P->U" in out
    assert "[full_stem_base]" in out
    assert "species_changed=yes" in out


def test_report_dict_structure() -> None:
    results = {
        "m:1": {
            "wm_003": {
                "full": ed.EdibilityVerdict(ed.POISONOUS, "v", "POISONOUS\nAmanita virosa\nx"),
                "stemcut": ed.EdibilityVerdict(ed.UNCERTAIN, "c", "UNCERTAIN\ny"),
            }
        }
    }
    d = cp._report_dict(results, ["wm_003"], {"wm_003": "full_stem_base"})
    assert d["images"] == ["wm_003"]
    assert d["views"]["wm_003"] == "full_stem_base"
    pair = d["models"]["m:1"]["pairs"]["wm_003"]
    assert pair["category"] == cp.FLIPPED_P_TO_U
    assert d["models"]["m:1"]["aggregate"][cp.FLIPPED_P_TO_U] == 1
    assert d["models"]["m:1"]["aggregate"]["LOST_CERTAINTY"] == 1


# ---------------------------------------------------------------------------
# CLI dispatch (in-process; transport stubbed)
# ---------------------------------------------------------------------------


def test_cli_crop_probe_dispatch_reports_flip(monkeypatch, capsys, tmp_path: Path) -> None:
    """cli.py `crop-probe` rebuilds argv, probe-vets, runs, prints JSON."""
    import json as _json

    from white_mushroom_test import cli

    monkeypatch.setattr(
        cp,
        "probe_ollama_model",
        lambda *a, **k: ProbeReport("ollama", "stub:1", CAPABLE, [], None),
    )

    def _fake_run(model, image_dir, crops_dir, *, host, timeout, temperature,
                  output_dir, only_stems=None, max_tokens=None, think=False):
        return {
            "wm_001": {
                "full": ed.EdibilityVerdict(ed.POISONOUS, "volva",
                                            "POISONOUS\nAmanita virosa\nvolva"),
                "stemcut": ed.EdibilityVerdict(ed.UNCERTAIN, "can't tell",
                                               "UNCERTAIN\ncannot identify"),
            }
        }

    monkeypatch.setattr(cp, "run_crop_model", _fake_run)

    rc = cli.main([
        "crop-probe",
        "--model", "stub:1",
        "--image-dir", str(tmp_path),
        "--crops-dir", str(tmp_path / "_crops"),
        "--output-dir", str(tmp_path),
        "--no-manifest",  # keep the test hermetic (no real manifest read)
        "--json",
    ])
    assert rc == 0
    data = _json.loads(capsys.readouterr().out)
    assert data["models"]["stub:1"]["pairs"]["wm_001"]["category"] == cp.FLIPPED_P_TO_U


def test_cli_crop_probe_dispatch_passes_think(monkeypatch, tmp_path: Path) -> None:
    """The cli.py `--think` flag flows through to `run_crop_model`."""
    from white_mushroom_test import cli

    monkeypatch.setattr(
        cp,
        "probe_ollama_model",
        lambda *a, **k: ProbeReport("ollama", "stub:1", CAPABLE, [], None),
    )
    received: dict = {}

    def _fake_run(model, image_dir, crops_dir, *, host, timeout, temperature,
                  output_dir, only_stems=None, max_tokens=None, think=False):
        received["think"] = think
        return {}

    monkeypatch.setattr(cp, "run_crop_model", _fake_run)

    rc = cli.main([
        "crop-probe",
        "--model", "stub:1",
        "--image-dir", str(tmp_path),
        "--crops-dir", str(tmp_path / "_crops"),
        "--output-dir", str(tmp_path),
        "--no-manifest",
        "--think",
    ])
    assert rc == 0
    assert received["think"] is True


def test_cli_crop_probe_skips_non_capable_model(monkeypatch, capsys, tmp_path: Path) -> None:
    """A model whose probe verdict is not capable is skipped, not run."""
    from white_mushroom_test import cli

    monkeypatch.setattr(
        cp,
        "probe_ollama_model",
        lambda *a, **k: ProbeReport("ollama", "stub:text", TEXT_ONLY, [], None),
    )
    ran = {"called": False}
    monkeypatch.setattr(
        cp,
        "run_crop_model",
        lambda *a, **k: ran.__setitem__("called", True),
    )
    rc = cli.main([
        "crop-probe",
        "--model", "stub:text",
        "--image-dir", str(tmp_path),
        "--crops-dir", str(tmp_path / "_crops"),
        "--output-dir", str(tmp_path),
        "--no-manifest",
    ])
    assert rc == 1  # no capable models ran
    assert ran["called"] is False
    assert "not capable" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# missing-Pillow graceful error (images imports lazily; simulates no [image])
# ---------------------------------------------------------------------------


def test_stem_crop_raises_clear_error_when_pillow_missing(monkeypatch, tmp_path: Path) -> None:
    # A None entry in sys.modules makes `from PIL import Image` raise ImportError
    # even when Pillow IS installed — so this runs in the dep-free suite too.
    monkeypatch.setitem(sys.modules, "PIL", None)
    (tmp_path / "src.jpg").write_bytes(b"\xff\xd8\xff")
    with pytest.raises(LLMError, match="[Ii]mage"):
        images.stem_crop(tmp_path / "src.jpg", tmp_path / "dst.jpg")


def test_stem_crop_rejects_out_of_range_fraction(tmp_path: Path) -> None:
    # The range check runs before any PIL import, so this is a stdlib test.
    src = tmp_path / "src.jpg"
    src.write_bytes(b"\xff\xd8\xff")
    with pytest.raises(ValueError):
        images.stem_crop(src, tmp_path / "dst.jpg", keep_fraction=1.5)
    with pytest.raises(ValueError):
        images.stem_crop(src, tmp_path / "dst.jpg", keep_fraction=0.0)