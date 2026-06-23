"""Build the curated Demo data by running the probes on the demo photos.

One-off (regenerable) build tool that produces ``data/demo/demo.json`` consumed
by the Streamlit Demo tab. For each CC-licensed demo photo whose true edibility
is known (listed in ``data/demo/photos.meta.json``), it runs the edibility
prompt on the **full** photo and on the **stem-hidden crop** for each model
(``qwen3.5:9b``, ``gemma3:4b``; thinking off — the v0.13 default), classifies
both via :func:`white_mushroom_test.edibility.classify_edibility`, and records
the full→stemcut change via :func:`white_mushroom_test.crop_probe.compare`.

It also writes the stem-hidden crop JPEGs next to the sources so the Demo tab
can show the before/after. The source photos + ``photos.meta.json`` (with CC
attribution) are committed by hand; this script only adds the model outputs.

Reuses the exact CLI probe path (``edibility.PROMPT`` + ``classify_edibility``
+ ``crop_probe.compare`` + ``images.stem_crop_bytes``) so the demo verdicts
match a CLI run on the same photo — the Demo tab is a *view* over the probes,
not a parallel implementation.

Run after the demo photos + ``photos.meta.json`` are in place::

    PYTHONPATH=src python -m white_mushroom_test.demo_curate \\
        --models qwen3.5:9b gemma3:4b --keep-fraction 0.6 --json
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Optional

from white_mushroom_test import crop_probe, edibility, images, scorer
from white_mushroom_test.llm import DEFAULT_OLLAMA_HOST, LLMError, OllamaVisionClient
from white_mushroom_test.ollama_runner import now_iso
from white_mushroom_test.streamlit_app.demo_data import (
    DEMO_DIR,
    IMAGES_DIR,
    TRUTH_DEADLY,
    TRUTH_EDIBLE,
    TRUTH_POISONOUS,
)

META_PATH = DEMO_DIR / "photos.meta.json"
PROMPTS_META_PATH = DEMO_DIR / "prompts.meta.json"
OUTPUT_PATH = DEMO_DIR / "demo.json"

# Closed-set truth labels the curator accepts (mirrors demo_data).
_KNOWN_TRUTHS = {TRUTH_DEADLY, TRUTH_POISONOUS, TRUTH_EDIBLE}


def _species_line(raw: str) -> str:
    """Best-effort species guess: 2nd non-empty line of the response.

    Mirrors ``crop_probe._species_line`` / ``pages._edibility.species_line``
    (kept local so this build tool does not reach into Streamlit-layer
    modules; the logic is identical so the demo matches the CLI probe).
    """
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    return lines[1] if len(lines) > 1 else (lines[0] if lines else "")


def load_meta(path: Path = META_PATH) -> list[dict]:
    """Read the hand-curated ``photos.meta.json`` (id/label/truth/license)."""
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found. Create it listing the demo photos "
            f"(id, label, truth, image filename, CC license/attribution)."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    photos = data.get("photos", []) if isinstance(data, dict) else []
    out: list[dict] = []
    for ph in photos:
        if not isinstance(ph, dict):
            continue
        truth = str(ph.get("truth", ""))
        if truth not in _KNOWN_TRUTHS:
            raise ValueError(
                f"photo {ph.get('id')!r} has truth {truth!r}; "
                f"expected one of {sorted(_KNOWN_TRUTHS)}"
            )
        out.append(ph)
    return out


# Prompt framings whose `prompt` is resolved at load time rather than stored
# verbatim (so the neutral baseline can reuse edibility.PROMPT without drift).
_PROMPT_REFS = {"edibility.PROMPT": lambda: edibility.PROMPT}


def load_demo_prompts(path: Path = PROMPTS_META_PATH) -> list[dict]:
    """Read the prompt-framing set for the 'same photo, different question' section.

    Each entry has ``id`` / ``category`` / ``label`` / ``prompt``. A ``prompt``
    of ``null`` with a ``prompt_ref`` (e.g. ``"edibility.PROMPT"``) is resolved
    to the referenced prompt text at load time, so the neutral baseline reuses
    :data:`edibility.PROMPT` verbatim and never drifts. Returns ``[]`` if the
    file is absent — the curator then skips the prompt section and still
    produces the edibility/crop demo (graceful on a partial setup).
    """
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_prompts = data.get("prompts", []) if isinstance(data, dict) else []
    out: list[dict] = []
    for p in raw_prompts:
        if not isinstance(p, dict):
            continue
        prompt = p.get("prompt")
        if prompt is None:
            ref = str(p.get("prompt_ref", ""))
            resolver = _PROMPT_REFS.get(ref)
            if resolver is None:
                raise ValueError(
                    f"demo prompt {p.get('id')!r} has unresolved prompt_ref {ref!r}"
                )
            prompt = resolver()
        out.append({
            "id": str(p.get("id", "")),
            "category": p.get("category"),
            "label": str(p.get("label", "")),
            "prompt": str(prompt),
        })
    return out


def _truncate(text: str, n: int) -> str:
    s = " ".join((text or "").split())
    return (s[:n].rstrip() + "...") if len(s) > n else s


def _run_prompt_set(
    client: OllamaVisionClient,
    image_b64: str,
    demo_prompts: list[dict],
) -> list[dict]:
    """Run every demo prompt on one photo for one model; score each response.

    Uses :func:`scorer.score_response` (the same rule-based scorer the Verify
    tab + CLI use) so the verdict is comparable to a CLI run. Per-prompt
    ``LLMError`` is caught and recorded as a missing verdict (``verdict=""``)
    so one failure does not abort the set — the Demo tab shows that row as
    “— (call failed)”.
    """
    rows: list[dict] = []
    for dp in demo_prompts:
        pid = dp["id"]
        try:
            raw = client.generate_text(dp["prompt"], image_b64)
        except LLMError as exc:
            rows.append({
                "prompt_id": pid, "model": client.model, "verdict": "",
                "cooking_advice": False, "refused": False,
                "excerpt": f"(call failed: {exc})",
            })
            continue
        score = scorer.score_response(pid, raw, category=dp.get("category"))
        rows.append({
            "prompt_id": pid,
            "model": client.model,
            "verdict": score.verdict.value,
            "cooking_advice": bool(score.matched_cooking_advice),
            "refused": bool(score.refused),
            "excerpt": _truncate(raw, 160),
        })
    return rows


def _run_one_model(
    client: OllamaVisionClient,
    full_b64: str,
    crop_b64: str,
) -> dict:
    """Run edibility on full + stem-hidden crop for one model; classify both.

    Per-call ``LLMError`` is caught and recorded as a missing verdict (``""``)
    so one model failing on one photo does not abort the whole curation — the
    Demo tab then shows that cell as "— (call failed)".
    """
    try:
        full_v = edibility.classify_edibility(
            client.generate_text(edibility.PROMPT, full_b64)
        )
    except LLMError as exc:
        full_v = None
        full_err = str(exc)
    else:
        full_err = ""
    try:
        cut_v = edibility.classify_edibility(
            client.generate_text(edibility.PROMPT, crop_b64)
        )
    except LLMError as exc:
        cut_v = None
        cut_err = str(exc)
    else:
        cut_err = ""

    comparison = crop_probe.compare(full_v, cut_v)
    return {
        "model": client.model,
        "edibility": full_v.verdict if full_v else "",
        "species": _species_line(full_v.raw) if full_v else "",
        "reason": (full_v.reason if full_v else "") or full_err,
        "crop": {
            "full": comparison["verdict_full"] or "",
            "stemcut": comparison["verdict_stemcut"] or "",
            "category": comparison["category"],
            "species_full": _species_line(full_v.raw) if full_v else "",
            "species_stemcut": _species_line(cut_v.raw) if cut_v else "",
            "reason_full": (full_v.reason if full_v else "") or full_err,
            "reason_stemcut": (cut_v.reason if cut_v else "") or cut_err,
        },
    }


def build_demo_doc(
    meta_photos: list[dict],
    per_photo_results: dict[str, list[dict]],
    *,
    models: list[str],
    keep_fraction: float,
    thinking: bool,
    generated_at: str,
    demo_prompts: Optional[list[dict]] = None,
    per_photo_prompt_results: Optional[dict[str, list[dict]]] = None,
) -> dict:
    """Assemble the demo.json document from meta + per-photo model results.

    Pure (no I/O, no model calls) so it is unit-testable. ``per_photo_results``
    maps ``photo_id -> [result dict per model]`` (the dicts produced by
    :func:`_run_one_model`). A photo with no results (e.g. its image file was
    missing) is still included, with an empty ``results`` list, so the meta is
    the source of truth and a partial run is visible rather than silently
    dropped.

    ``demo_prompts`` (the resolved prompt-framing set from
    :func:`load_demo_prompts`) is written at the top level, and
    ``per_photo_prompt_results`` maps ``photo_id -> [PromptResult dict per
    (prompt, model)]`` (from :func:`_run_prompt_set`) onto each photo's
    ``prompt_results``. Both default to empty so an edibility-only run still
    assembles a valid doc.
    """
    demo_prompts = list(demo_prompts or [])
    per_photo_prompt_results = per_photo_prompt_results or {}
    return {
        "generated_at": generated_at,
        "probe": {
            "prompt": "edibility",
            "keep_fraction": keep_fraction,
            "thinking": thinking,
        },
        "models": list(models),
        "demo_prompts": demo_prompts,
        "photos": [
            {
                **ph,
                "results": per_photo_results.get(str(ph.get("id", "")), []),
                "prompt_results": per_photo_prompt_results.get(str(ph.get("id", "")), []),
            }
            for ph in meta_photos
        ],
    }


def run_demo_curate(
    *,
    host: str = DEFAULT_OLLAMA_HOST,
    models: list[str],
    timeout: float = 120.0,
    temperature: float = 0.0,
    keep_fraction: float = images.DEFAULT_KEEP_FRACTION,
    think: bool = False,
    meta_path: Path = META_PATH,
    prompts_meta_path: Path = PROMPTS_META_PATH,
    output_path: Path = OUTPUT_PATH,
    images_dir: Path = IMAGES_DIR,
) -> dict:
    """Run the probes on every demo photo + model; write ``demo.json`` + crops.

    Returns the written document. Photos whose image file is missing are
    skipped (with a printed note) rather than raising — so you can run the
    curator as soon as the first photo is in place and re-run as more arrive.

    In addition to the edibility + stem-crop probe, runs the prompt-framing
    set from ``prompts_meta_path`` on each (photo, model) and scores each
    response with :func:`scorer.score_response`, recording it under each
    photo's ``prompt_results``. If the prompts meta file is absent, the
    prompt section is skipped and the doc is still valid (edibility/crop only).
    """
    meta_photos = load_meta(meta_path)
    demo_prompts = load_demo_prompts(prompts_meta_path)
    if not demo_prompts:
        print(f"[info] no demo prompts at {prompts_meta_path}; "
              f"skipping the prompt-framing section.")
    per_photo: dict[str, list[dict]] = {}
    per_photo_prompts: dict[str, list[dict]] = {}
    for ph in meta_photos:
        pid = str(ph.get("id", ""))
        src = images_dir / str(ph.get("image", ""))
        if not src.is_file():
            print(f"[skip] {pid}: image not found at {src}")
            per_photo[pid] = []
            per_photo_prompts[pid] = []
            continue
        data = src.read_bytes()
        full_b64 = base64.b64encode(data).decode("ascii")
        try:
            crop_bytes = images.stem_crop_bytes(data, keep_fraction=keep_fraction)
        except LLMError as exc:  # Pillow absent
            raise SystemExit(
                f"Pillow is required to crop the demo photos: {exc}"
            ) from exc
        crop_b64 = base64.b64encode(crop_bytes).decode("ascii")
        # Persist the crop image so the Demo tab can show the before/after.
        crop_rel = str(ph.get("crop_image", ""))
        if crop_rel:
            (images_dir / crop_rel).write_bytes(crop_bytes)

        rows: list[dict] = []
        prompt_rows: list[dict] = []
        for model in models:
            client = OllamaVisionClient(
                host, model, timeout=timeout,
                temperature=temperature, think=think,
            )
            print(f"[run ] {pid} · {model} …")
            rows.append(_run_one_model(client, full_b64, crop_b64))
            if demo_prompts:
                print(f"[prompts] {pid} · {model} …")
                prompt_rows.extend(_run_prompt_set(client, full_b64, demo_prompts))
        per_photo[pid] = rows
        per_photo_prompts[pid] = prompt_rows

    doc = build_demo_doc(
        meta_photos, per_photo,
        models=models, keep_fraction=keep_fraction,
        thinking=think, generated_at=now_iso(),
        demo_prompts=demo_prompts, per_photo_prompt_results=per_photo_prompts,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"[wrote] {output_path}")
    return doc


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="white_mushroom_test.demo_curate",
        description="Curate the Streamlit Demo tab data by running the probes.",
    )
    p.add_argument("--host", default=DEFAULT_OLLAMA_HOST,
                   help=f"Ollama host (default {DEFAULT_OLLAMA_HOST})")
    p.add_argument("--models", nargs="+", default=["qwen3.5:9b", "gemma3:4b"],
                   help="Models to fan out across (default qwen3.5:9b gemma3:4b)")
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--keep-fraction", type=float,
                   default=images.DEFAULT_KEEP_FRACTION,
                   help="Stem crop keep fraction (default 0.6)")
    p.add_argument("--think", action="store_true",
                   help="Enable model thinking (default off — the v0.13 default)")
    p.add_argument("--meta", type=Path, default=META_PATH)
    p.add_argument("--prompts-meta", type=Path, default=PROMPTS_META_PATH,
                   help="Prompt-framing set for the 'same photo, different "
                        "question' section (default: data/demo/prompts.meta.json).")
    p.add_argument("--output", type=Path, default=OUTPUT_PATH)
    p.add_argument("--images-dir", type=Path, default=IMAGES_DIR)
    p.add_argument("--json", action="store_true", help="Print the written doc")
    args = p.parse_args(argv)
    doc = run_demo_curate(
        host=args.host, models=args.models, timeout=args.timeout,
        temperature=args.temperature, keep_fraction=args.keep_fraction,
        think=args.think, meta_path=args.meta,
        prompts_meta_path=args.prompts_meta, output_path=args.output,
        images_dir=args.images_dir,
    )
    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "META_PATH", "PROMPTS_META_PATH", "OUTPUT_PATH",
    "load_meta", "load_demo_prompts",
    "build_demo_doc", "run_demo_curate", "main",
]