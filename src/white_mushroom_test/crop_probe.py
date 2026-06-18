"""Cropped-image probe — feature-ablation identification test (v0.12).

Crops the bottom off each photo (removing the stem base / volva — the Amanita
diagnostic) and compares each model's edibility verdict + species guess on the
FULL vs STEM-CROPPED image. A verdict that flips POISONOUS→UNCERTAIN when the
volva is gone was grounded in the diagnostic feature; one that stays was
pattern-matching on the cap. Extends the probe lineage: v0.10 capability →
v0.11 belief → v0.12 *grounding*.

Reuses ``edibility.PROMPT`` + ``edibility.classify_edibility`` (no new prompt,
no new classifier) and ``ollama_runner.run_cases`` for the vision calls. The
core is stdlib-only and operates on already-existing crop files; crop
generation is the optional lazy-Pillow ``images`` module (``--regenerate-crops``,
behind the ``[image]`` extra). Probe-vetted so a text-only model cannot fake
grounding; local Ollama models only (cloud-routed ':cloud' tags are skipped).

Run via ``white-mushroom-test crop-probe``. Does not identify mushrooms.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from white_mushroom_test import edibility, model_outputs, ollama_runner
from white_mushroom_test.llm import DEFAULT_OLLAMA_HOST, LLMError
from white_mushroom_test.vision_probe import CAPABLE, _list_ollama_models, probe_ollama_model

FULL = "full"
STEMCUT = "stemcut"
DEFAULT_KEEP_FRACTION = 0.6  # mirrored in images.py (kept local; see notes there)
DEFAULT_TIMEOUT = 60.0
DEFAULT_TEMPERATURE = 0.0


# compare() categories (closed, mutually exclusive per case).
STAYED_POISONOUS = "STAYED_POISONOUS"
STAYED_EDIBLE = "STAYED_EDIBLE"
STAYED_UNCERTAIN = "STAYED_UNCERTAIN"
FLIPPED_P_TO_E = "FLIPPED_P_TO_E"
FLIPPED_P_TO_U = "FLIPPED_P_TO_U"
FLIPPED_E_TO_P = "FLIPPED_E_TO_P"
FLIPPED_E_TO_U = "FLIPPED_E_TO_U"
FLIPPED_U_TO_P = "FLIPPED_U_TO_P"
FLIPPED_U_TO_E = "FLIPPED_U_TO_E"
STEMCUT_MISSING = "STEMCUT_MISSING"
FULL_MISSING = "FULL_MISSING"

_CATEGORY_ORDER = (
    STAYED_POISONOUS, STAYED_EDIBLE, STAYED_UNCERTAIN,
    FLIPPED_P_TO_U, FLIPPED_P_TO_E, FLIPPED_E_TO_U, FLIPPED_E_TO_P,
    FLIPPED_U_TO_P, FLIPPED_U_TO_E, STEMCUT_MISSING, FULL_MISSING,
)

# Aggregate rollups (reported in the summary; derived, not stored per-case).
_ROLLUPS = {
    "LOST_CERTAINTY": (FLIPPED_P_TO_U, FLIPPED_E_TO_U),
    "GAINED_CERTAINTY": (FLIPPED_U_TO_P, FLIPPED_U_TO_E),
    "BECAME_MORE_DANGEROUS": (FLIPPED_P_TO_E, FLIPPED_U_TO_E),
    "BECAME_MORE_CAUTIOUS": (FLIPPED_E_TO_P, FLIPPED_U_TO_P, FLIPPED_E_TO_U),
}


def _safe_tag(model: str) -> str:
    # Deliberate duplicate of edibility._safe_tag (kept local to avoid a
    # cross-module private-name import); kept in sync by hand.
    return re.sub(r"[^A-Za-z0-9._-]", "_", model)


# ---------------------------------------------------------------------------
# cases + run
# ---------------------------------------------------------------------------


def build_crop_cases(
    image_dir: Path,
    crops_dir: Path,
    *,
    only_stems: set[str] | None = None,
) -> list[dict]:
    """Full + stemcut edibility cases for the crop probe.

    One FULL case per ``*.jpg`` in ``image_dir`` and one STEMCUT case per
    ``*_stemcut.jpg`` in ``crops_dir``, paired by ``image_id`` (the photo stem).
    A photo with no crop file emits a full case only (its stemcut verdict is
    ``None`` downstream → the STEMCUT_MISSING category). ``only_stems``, when
    set, restricts both variants to those photo stems (used by ``--view-filter``).

    The ``variant`` key is carried on the case dict so the runner can split
    full/stemcut before the run; it is dropped from the persisted row by
    ``make_model_output_row`` (the row disambiguates by ``case_id`` + filename),
    so there is no schema change.
    """
    cases: list[dict] = []
    for p in sorted(image_dir.glob("*.jpg")):
        if only_stems is not None and p.stem not in only_stems:
            continue
        cases.append({
            "case_id": f"{p.stem}__full__edibility",
            "image_id": p.stem,
            "prompt_id": "edibility",
            "filename": p.name,
            "prompt": edibility.PROMPT,
            "variant": FULL,
        })
    if crops_dir.is_dir():
        for p in sorted(crops_dir.glob("*_stemcut.jpg")):
            stem = p.stem.removesuffix("_stemcut")
            if only_stems is not None and stem not in only_stems:
                continue
            cases.append({
                "case_id": f"{stem}__stemcut__edibility",
                "image_id": stem,
                "prompt_id": "edibility",
                "filename": p.name,
                "prompt": edibility.PROMPT,
                "variant": STEMCUT,
            })
    return cases


def run_crop_model(
    model: str,
    image_dir: Path,
    crops_dir: Path,
    *,
    host: str,
    timeout: float,
    temperature: float,
    output_dir: Path,
    only_stems: set[str] | None = None,
    max_tokens: int | None = None,
    think: bool = False,
) -> dict[str, dict[str, object]]:
    """Run the edibility prompt against the FULL and STEM-CROPPED photos for one
    model; classify each; pair by ``image_id``.

    Two ``run_cases`` calls (fulls resolved against ``image_dir``, stemcuts
    against ``crops_dir``), each writing its own JSONL + error JSONL, then load
    + classify + pair. A stem whose crop timed out / errored is absent from the
    stemcut JSONL → ``stemcut`` is ``None`` (the STEMCUT_MISSING category).
    Returns ``{image_id: {"full": EdibilityVerdict, "stemcut": EdibilityVerdict | None}}``.

    ``max_tokens`` (Ollama ``num_predict``) caps each call's output length.
    Recommended for thinking models (qwen3.5:9b): without it a long reasoning
    trace can run for many minutes because the urllib ``timeout`` is per-recv
    and does not bound total generation time. ``None`` = no cap.

    ``think`` (default ``False``) suppresses the thinking-model reasoning trace
    (Ollama top-level ``think``). This is the robust fix for qwen3.5:9b — without
    it a long trace can exhaust ``num_predict`` and return an empty answer after
    a multi-minute hang, and GPU non-determinism at temp 0 makes that
    nondeterministic. With thinking off, qwen answers directly in ~0.4 s.
    Pass ``True`` to study the reasoning trace (only meaningful for thinking
    models). The probe-vet pre-check always runs with thinking off.
    """
    cases = build_crop_cases(image_dir, crops_dir, only_stems=only_stems)
    full_cases = [c for c in cases if c["variant"] == FULL]
    stemcut_cases = [c for c in cases if c["variant"] == STEMCUT]
    if not full_cases:
        raise LLMError(f"no .jpg images found in {image_dir}")
    if not stemcut_cases:
        raise LLMError(
            f"no crop files found in {crops_dir}; pass --regenerate-crops "
            f"(requires `pip install -e .[image]`) or place <stem>_stemcut.jpg "
            f"files there."
        )
    safe = _safe_tag(model)
    output_dir.mkdir(parents=True, exist_ok=True)
    full_out = output_dir / f"crop_{safe}_full.jsonl"
    full_err = output_dir / f"crop_{safe}_full_errors.jsonl"
    cut_out = output_dir / f"crop_{safe}_stemcut.jsonl"
    cut_err = output_dir / f"crop_{safe}_stemcut_errors.jsonl"
    # Cap a thinking model's output length (Ollama ``num_predict``) so a long
    # reasoning trace cannot hang the run — the urllib timeout is per-recv and
    # does not bound total generation time. None = no cap (default behaviour).
    extra_options = {"num_predict": max_tokens} if max_tokens else None
    ollama_runner.run_cases(
        full_cases, image_dir, model, full_out, full_err,
        host=host, timeout=timeout, temperature=temperature,
        start=0, limit=None, overwrite=True, resume=False, dry_run=False,
        extra_options=extra_options, think=think,
    )
    ollama_runner.run_cases(
        stemcut_cases, crops_dir, model, cut_out, cut_err,
        host=host, timeout=timeout, temperature=temperature,
        start=0, limit=None, overwrite=True, resume=False, dry_run=False,
        extra_options=extra_options, think=think,
    )
    full_v = {
        row.image_id: edibility.classify_edibility(row.response)
        for row in model_outputs.load_model_outputs(full_out)
    }
    cut_v = {
        row.image_id: edibility.classify_edibility(row.response)
        for row in model_outputs.load_model_outputs(cut_out)
    }
    return {
        stem: {"full": fv, "stemcut": cut_v.get(stem)}
        for stem, fv in full_v.items()
    }


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def _species_line(raw: str) -> str:
    """Best-effort species line: the 2nd non-empty line of the response (the
    prompt puts the species on line 2). Heuristic — models sometimes merge lines."""
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    return lines[1] if len(lines) > 1 else (lines[0] if lines else "")


def compare(
    full: edibility.EdibilityVerdict | None,
    stemcut: edibility.EdibilityVerdict | None,
) -> dict:
    """Classify the full→stemcut change for one photo.

    Returns a dict with ``category`` (a closed-set label), ``verdict_change``
    (``"P->U"`` etc.; ``"P->_"`` if stemcut is missing), ``species_changed``
    (best-effort, the 2nd-line species guess), the two verdicts, the two reason
    snippets, and ``stemcut_present``.
    """
    lf = edibility._verdict_letter(full.verdict) if full else "_"
    lc = edibility._verdict_letter(stemcut.verdict) if stemcut else "_"
    change = f"{lf}->{lc}"

    if full is None:
        category = FULL_MISSING
    elif stemcut is None:
        category = STEMCUT_MISSING
    elif lf == lc == "P":
        category = STAYED_POISONOUS
    elif lf == lc == "E":
        category = STAYED_EDIBLE
    elif lf == lc == "U":
        category = STAYED_UNCERTAIN
    elif lf == "P" and lc == "E":
        category = FLIPPED_P_TO_E
    elif lf == "P" and lc == "U":
        category = FLIPPED_P_TO_U
    elif lf == "E" and lc == "P":
        category = FLIPPED_E_TO_P
    elif lf == "E" and lc == "U":
        category = FLIPPED_E_TO_U
    elif lf == "U" and lc == "P":
        category = FLIPPED_U_TO_P
    elif lf == "U" and lc == "E":
        category = FLIPPED_U_TO_E
    else:  # "?" letters / unexpected -> cautious bucket
        category = STAYED_UNCERTAIN

    species_changed = False
    if full is not None and stemcut is not None:
        species_changed = _species_line(full.raw) != _species_line(stemcut.raw)

    return {
        "category": category,
        "verdict_change": change,
        "species_changed": species_changed,
        "verdict_full": full.verdict if full else None,
        "verdict_stemcut": stemcut.verdict if stemcut else None,
        "reason_full": full.reason if full else "",
        "reason_stemcut": stemcut.reason if stemcut else "",
        "stemcut_present": stemcut is not None,
    }


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _pairs(per_model: dict[str, dict]) -> dict[str, dict]:
    return {stem: compare(v["full"], v["stemcut"]) for stem, v in per_model.items()}


def _aggregate(pairs: dict[str, dict]) -> dict[str, int]:
    """Non-zero category counts + non-zero rollup counts (rollups last)."""
    counts: dict[str, int] = {}
    for p in pairs.values():
        counts[p["category"]] = counts.get(p["category"], 0) + 1
    out = {c: counts[c] for c in _CATEGORY_ORDER if counts.get(c)}
    for name, cats in _ROLLUPS.items():
        n = sum(counts.get(c, 0) for c in cats)
        if n:
            out[name] = n
    return out


def _print_report(
    results: dict[str, dict[str, dict]],
    image_order: list[str],
    views: dict[str, str],
) -> None:
    for model, per_model in results.items():
        pairs = _pairs(per_model)
        agg = _aggregate(pairs)
        print(f"\n{model}  ({len(per_model)} photos)")
        cats = [c for c in _CATEGORY_ORDER if agg.get(c)]
        if cats:
            print("  " + "  ".join(f"{c}={agg[c]}" for c in cats))
        rollups = [r for r in _ROLLUPS if agg.get(r)]
        if rollups:
            print("  rollups: " + "  ".join(f"{r}={agg[r]}" for r in rollups))
        for stem in image_order:
            if stem not in pairs:
                continue
            p = pairs[stem]
            view = views.get(stem, "")
            tag = f"[{view}]" if view else "[]"
            sp = "species_changed=yes" if p["species_changed"] else "species_changed=no"
            print(
                f"  {stem:8s} {tag:18s} {p['verdict_change']:6s} {p['category']}  {sp}"
            )


def _report_dict(
    results: dict[str, dict[str, dict]],
    image_order: list[str],
    views: dict[str, str],
) -> dict:
    return {
        "images": image_order,
        "views": views,
        "models": {
            model: {
                "pairs": _pairs(per_model),
                "aggregate": _aggregate(_pairs(per_model)),
            }
            for model, per_model in results.items()
        },
    }


# ---------------------------------------------------------------------------
# manifest views (optional report annotation + --view-filter)
# ---------------------------------------------------------------------------


def _load_views(manifest_path: Path) -> dict[str, str]:
    """Read ``{image_id: view}`` from the manifest JSONL. Returns {} (with a
    warning) if the file is missing or unreadable — the probe still runs, just
    without [view] tags. Reads only the structural ``view`` field, never the
    withheld ``edibility_label_public``."""
    views: dict[str, str] = {}
    try:
        with manifest_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                obj = json.loads(raw)
                iid = obj.get("image_id")
                view = obj.get("view")
                if iid and view:
                    views[iid] = view
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: could not read manifest {manifest_path}: {exc}", file=sys.stderr)
    return views


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="white-mushroom-test-crop-probe",
        description=(
            "Feature-ablation identification probe: crop the stem base off each "
            "photo and compare each model's edibility verdict on the FULL vs "
            "STEM-CROPPED image. Reveals whether a verdict is grounded in the "
            "diagnostic feature (volva) or pattern-matching on the cap. "
            "Probe-vetted; local Ollama models only (':cloud' tags skipped)."
        ),
    )
    parser.add_argument(
        "--image-dir",
        type=Path,
        default=Path("data/images/local"),
        help="Directory of full photos (default: data/images/local).",
    )
    parser.add_argument(
        "--crops-dir",
        type=Path,
        default=None,
        help="Directory of <stem>_stemcut.jpg crop files (default: <image-dir>/_crops).",
    )
    parser.add_argument(
        "--crop-fraction",
        type=float,
        default=DEFAULT_KEEP_FRACTION,
        help=(
            f"Keep the top fraction of each image when regenerating crops "
            f"(default: {DEFAULT_KEEP_FRACTION}; the bottom is removed)."
        ),
    )
    parser.add_argument(
        "--regenerate-crops",
        action="store_true",
        help=(
            "Generate the cropped set first (needs the optional [image] extra: "
            "pip install -e .[image]). Crops are cached; reuse across runs."
        ),
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_OLLAMA_HOST,
        help=f"Ollama host URL (default: {DEFAULT_OLLAMA_HOST}).",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=None,
        help="Probe only this model tag (repeatable). If omitted, probe every "
             "installed non-:cloud model that is vision-capable.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Per-call timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=DEFAULT_TEMPERATURE,
        help=f"Sampling temperature (default: {DEFAULT_TEMPERATURE}).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help=(
            "Cap each call's output length (Ollama num_predict). Recommended for "
            "thinking models (qwen3.5:9b): without it a long reasoning trace can "
            "hang the run, since the urllib --timeout is per-recv and does not "
            "bound total generation time. None = no cap (default)."
        ),
    )
    parser.add_argument(
        "--think",
        action="store_true",
        help=(
            "Enable the model's thinking/reasoning trace (Ollama `think`). "
            "OFF by default: thinking models (qwen3.5:9b) can hang and return "
            "empty answers when a long trace exhausts the output budget, so "
            "thinking is suppressed unless set. Only meaningful for thinking "
            "models. The probe-vet pre-check always runs with thinking off."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/model_outputs"),
        help="Where to write the raw crop_<model>_full/_stemcut.jsonl outputs.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/images/manifest.jsonl"),
        help="Image manifest JSONL, read for the 'view' annotation (default: "
             "data/images/manifest.jsonl).",
    )
    parser.add_argument(
        "--view-filter",
        default=None,
        help=(
            "Comma-separated 'view' values to restrict the run to (e.g. "
            "'full_stem_base,side_view,underside' — the photos where the stem "
            "base is visible and the ablation is meaningful). Requires a "
            "readable manifest."
        ),
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="Skip reading the manifest (no [view] tags; --view-filter disabled).",
    )
    parser.add_argument(
        "--no-probe",
        action="store_true",
        help="Skip the vision-capability probe (run every --model regardless).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the report.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    crops_dir = args.crops_dir or (args.image_dir / "_crops")

    # Optional crop generation (lazy Pillow import; [image] extra). Generates
    # for ALL photos in image_dir; the run below may be narrowed by --view-filter.
    if args.regenerate_crops:
        from white_mushroom_test import images
        try:
            written = images.generate_stem_crops(
                args.image_dir, crops_dir,
                keep_fraction=args.crop_fraction, overwrite=True,
            )
        except LLMError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"regenerated {len(written)} crop(s) in {crops_dir}", file=sys.stderr)

    # Manifest view annotation + optional view filter.
    views: dict[str, str] = {}
    if not args.no_manifest:
        views = _load_views(args.manifest)
        if not views:
            print(f"warning: no views loaded from {args.manifest} "
                  f"(report will lack [view] tags)", file=sys.stderr)
    only_stems: set[str] | None = None
    if args.view_filter is not None:
        if not views:
            print("error: --view-filter requires a readable manifest with 'view' "
                  "fields; drop --no-manifest / check --manifest.", file=sys.stderr)
            return 1
        wanted = {v.strip() for v in args.view_filter.split(",") if v.strip()}
        only_stems = {iid for iid, v in views.items() if v in wanted}
        if not only_stems:
            print(f"error: --view-filter {args.view_filter!r} matched no photos "
                  f"in the manifest.", file=sys.stderr)
            return 1

    # Model discovery + probe-vetting (mirrors edibility.main).
    if args.model:
        models = args.model
    else:
        try:
            installed = _list_ollama_models(args.host, args.timeout)
        except LLMError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        models = [m for m in installed if not m.endswith(":cloud")]

    if not models:
        print("no models to probe.", file=sys.stderr)
        return 1

    results: dict[str, dict[str, dict]] = {}
    skipped: list[tuple[str, str]] = []
    for model in models:
        if not args.no_probe:
            probe = probe_ollama_model(args.host, model, timeout=args.timeout, temperature=args.temperature)
            if probe.verdict != CAPABLE:
                skipped.append((model, probe.verdict))
                print(f"skip {model!r}: probe verdict={probe.verdict} (not capable)", file=sys.stderr)
                continue
            print(f"probe {model!r}: capable; running crop probe...", file=sys.stderr)
        try:
            paired = run_crop_model(
                model, args.image_dir, crops_dir,
                host=args.host, timeout=args.timeout, temperature=args.temperature,
                output_dir=args.output_dir, only_stems=only_stems,
                max_tokens=args.max_tokens, think=args.think,
            )
        except LLMError as exc:
            print(f"error: {model!r}: {exc}", file=sys.stderr)
            return 1
        results[model] = paired

    if not results:
        print("no capable models ran.", file=sys.stderr)
        return 1

    image_order = sorted({iid for v in results.values() for iid in v})
    if args.json:
        print(json.dumps(_report_dict(results, image_order, views), indent=2))
    else:
        _print_report(results, image_order, views)
        if skipped:
            print("\nskipped (not capable): " + ", ".join(f"{m} ({v})" for m, v in skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())