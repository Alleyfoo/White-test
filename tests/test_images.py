"""Tests for images.py (the lazy-Pillow crop utilities).

PIL-gated: the whole file is skipped when Pillow is not installed (the default
dep-free ``pytest`` run), mirroring ``test_streamlit_app.py``. The tiny source
images are synthesized with ``vision_probe._solid_png`` (the existing stdlib PNG
encoder) — no committed image asset. Pillow sniffs format from file bytes on
``Image.open`` and infers the output format from the ``.jpg`` extension on
``save``, so a PNG source saved to a ``.jpg`` path works.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")  # skip whole file if Pillow missing

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from white_mushroom_test import images, vision_probe  # noqa: E402
from PIL import Image  # noqa: E402  (safe: importorskip above confirmed PIL)


def _tiny_png(path: Path, w: int, h: int, rgb: tuple[int, int, int]) -> None:
    path.write_bytes(vision_probe._solid_png(w, h, rgb))


def _size(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size  # (width, height)


def test_stem_crop_keeps_top_fraction(tmp_path: Path) -> None:
    src = tmp_path / "src.png"
    _tiny_png(src, 10, 20, (200, 200, 200))
    dst = tmp_path / "dst.jpg"
    images.stem_crop(src, dst, keep_fraction=0.6)
    assert _size(dst) == (10, 12)  # top 60% of 20; bottom 8 rows (the "stem base") gone


def test_stem_crop_half_fraction(tmp_path: Path) -> None:
    src = tmp_path / "src.png"
    _tiny_png(src, 10, 20, (200, 200, 200))
    dst = tmp_path / "dst.jpg"
    images.stem_crop(src, dst, keep_fraction=0.5)
    assert _size(dst) == (10, 10)  # cut the stem in half


def test_crop_to_box(tmp_path: Path) -> None:
    src = tmp_path / "src.png"
    _tiny_png(src, 10, 20, (200, 200, 200))
    dst = tmp_path / "dst.jpg"
    images.crop_to_box(src, dst, (0, 0, 10, 5))
    assert _size(dst) == (10, 5)


def test_generate_stem_crops_writes_files(tmp_path: Path) -> None:
    image_dir = tmp_path / "imgs"
    crops_dir = tmp_path / "crops"
    image_dir.mkdir()
    for stem in ("wm_001", "wm_002"):
        _tiny_png(image_dir / f"{stem}.jpg", 10, 20, (200, 200, 200))
    out = images.generate_stem_crops(image_dir, crops_dir, keep_fraction=0.6)
    assert {p.name for p in out} == {"wm_001_stemcut.jpg", "wm_002_stemcut.jpg"}
    for stem in ("wm_001", "wm_002"):
        assert (crops_dir / f"{stem}_stemcut.jpg").is_file()
        assert _size(crops_dir / f"{stem}_stemcut.jpg") == (10, 12)


def test_generate_stem_crops_overwrite_false_skips_existing(tmp_path: Path) -> None:
    image_dir = tmp_path / "imgs"
    crops_dir = tmp_path / "crops"
    image_dir.mkdir()
    crops_dir.mkdir()
    _tiny_png(image_dir / "wm_001.jpg", 10, 20, (200, 200, 200))
    dst = crops_dir / "wm_001_stemcut.jpg"
    dst.write_bytes(b"PREEXISTING")
    out = images.generate_stem_crops(image_dir, crops_dir, overwrite=False)
    assert out == [dst]
    assert dst.read_bytes() == b"PREEXISTING"  # not overwritten


def test_generate_stem_crops_overwrite_true_replaces(tmp_path: Path) -> None:
    image_dir = tmp_path / "imgs"
    crops_dir = tmp_path / "crops"
    image_dir.mkdir()
    crops_dir.mkdir()
    _tiny_png(image_dir / "wm_001.jpg", 10, 20, (200, 200, 200))
    dst = crops_dir / "wm_001_stemcut.jpg"
    dst.write_bytes(b"PREEXISTING")
    images.generate_stem_crops(image_dir, crops_dir, overwrite=True)
    assert _size(dst) == (10, 12)  # replaced with a real crop