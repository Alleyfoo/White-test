"""Image cropping for the cropped-image probe (v0.12).

The crop-probe CORE (``crop_probe.py``) is stdlib-only and operates on
already-existing crop *files*; this module is the optional, lazy-Pillow
generation side — decode a JPEG/PNG, crop, re-save. Pillow is imported lazily
inside the crop functions only, so importing this module is stdlib-only and
the default ``pytest`` run stays dependency-free. Install the optional
``[image]`` extra (``pip install -e .[image]``) to use it; without Pillow,
callers can still feed pre-generated crop files to the probe via ``--crops-dir``.

No committed image assets; no third-party deps at module load. Does not
identify mushrooms.
"""

from __future__ import annotations

from pathlib import Path

from white_mushroom_test.llm import LLMError

# Mirrored in crop_probe.py (kept local to avoid coupling the two modules at
# import time — crop_probe stays PIL-free at module load).
DEFAULT_KEEP_FRACTION = 0.6


def _require_pil():
    """Import Pillow lazily; raise LLMError with an install hint if absent.

    Reusing ``LLMError`` (not a new exception) matches the ``edibility.py``
    precedent of raising ``LLMError`` for non-LLM runtime conditions, so the
    probe's ``main`` can catch one error type.
    """
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise LLMError(
            "Pillow is required for image cropping. Install the optional "
            "[image] extra:  pip install -e \".[image]\"  — or pre-generate "
            "the crop files out-of-band and point --crops-dir at them."
        ) from exc
    return Image


def crop_to_box(src: Path, dst: Path, box: tuple[int, int, int, int]) -> None:
    """Crop ``src`` to ``box`` (left, upper, right, lower) and save to ``dst``.

    Pillow infers the output format from ``dst``'s extension and the input
    format from the file bytes (not the extension), so a PNG source saved to a
    ``.jpg`` path works. Saving inside the ``with`` ensures the source is
    decoded before its handle closes.
    """
    Image = _require_pil()
    with Image.open(src) as im:
        im.crop(box).save(dst)


def stem_crop(
    src: Path,
    dst: Path,
    *,
    keep_fraction: float = DEFAULT_KEEP_FRACTION,
) -> None:
    """Keep the top ``keep_fraction`` of ``src`` (removing the bottom — the stem
    base / volva, the Amanita diagnostic) and save to ``dst``.

    A heuristic with no per-photo segmentation: assumes the stem base sits at
    the bottom of the frame (true for side / full-stem-base photos; a null
    operation for cap-only photos). ``keep_fraction`` must be in (0, 1).
    """
    if not 0.0 < keep_fraction < 1.0:
        raise ValueError(f"keep_fraction must be in (0, 1); got {keep_fraction}")
    Image = _require_pil()
    with Image.open(src) as im:
        w, h = im.size
        new_h = max(1, int(round(h * keep_fraction)))
        im.crop((0, 0, w, new_h)).save(dst)


def generate_stem_crops(
    image_dir: Path,
    crops_dir: Path,
    *,
    keep_fraction: float = DEFAULT_KEEP_FRACTION,
    overwrite: bool = False,
) -> list[Path]:
    """Crop every ``*.jpg`` in ``image_dir`` to ``crops_dir/<stem>_stemcut.jpg``.

    Existing crops are left untouched unless ``overwrite``. Returns the list of
    crop paths (existing + newly written). ``_crops`` is a reserved subdir: the
    edibility probe globs ``*.jpg`` non-recursively, so crops here never leak
    into a plain ``edibility`` run — keep any recursive glob out of
    ``image_dir``.
    """
    _require_pil()  # fail-fast even on an empty dir
    crops_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for src in sorted(image_dir.glob("*.jpg")):
        dst = crops_dir / f"{src.stem}_stemcut.jpg"
        if dst.is_file() and not overwrite:
            out.append(dst)
            continue
        stem_crop(src, dst, keep_fraction=keep_fraction)
        out.append(dst)
    return out