"""Command-line interface for the White Mushroom Test benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from white_mushroom_test import (
    edibility,
    generate_image_cases,
    model_outputs,
    ollama_runner,
    reporting,
    vision_probe,
)
from white_mushroom_test.scorer import Verdict, score_file


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="white-mushroom-test",
        description=(
            "Score model outputs against the White Mushroom Test benchmark "
            "prompts. This tool does not identify mushrooms and does not "
            "give eating advice."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    score = sub.add_parser("score", help="Score a JSONL of model outputs.")
    score.add_argument(
        "--prompts",
        type=Path,
        required=True,
        help="Path to prompts JSONL (must contain 'id' and 'prompt' per line).",
    )
    score.add_argument(
        "--outputs",
        type=Path,
        required=True,
        help=(
            "Path to outputs JSONL (must contain 'prompt_id' and 'response' "
            "per line)."
        ),
    )
    score.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a human summary.",
    )

    gen = sub.add_parser(
        "generate-image-cases",
        help=(
            "Generate (image, prompt) test cases for the v0.2 image "
            "pilot. Does not call any model."
        ),
    )
    gen.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Path to image manifest JSONL.",
    )
    gen.add_argument(
        "--prompts",
        type=Path,
        required=True,
        help="Path to prompts JSONL.",
    )
    gen.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the generated cases JSONL.",
    )
    gen.add_argument(
        "--image-dir",
        type=Path,
        default=None,
        help=(
            "Directory to look for image files in. If supplied, each "
            "case records whether the file is present on disk."
        ),
    )
    gen.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Fail the run if any image file is missing from "
            "--image-dir. By default, missing files are recorded but "
            "do not fail the run."
        ),
    )

    val = sub.add_parser(
        "validate-model-outputs",
        help=(
            "Validate a model-output JSONL against the case manifest. "
            "Does not call any model."
        ),
    )
    val.add_argument(
        "--cases",
        type=Path,
        required=True,
        help="Path to the generated cases JSONL.",
    )
    val.add_argument(
        "--outputs",
        type=Path,
        required=True,
        help="Path to a model-output JSONL to validate.",
    )

    ls = sub.add_parser(
        "list-cases",
        help=(
            "Print the first N (image, prompt) cases from a generated "
            "cases JSONL. Used for manual inspection."
        ),
    )
    ls.add_argument(
        "--cases",
        type=Path,
        required=True,
        help="Path to the generated cases JSONL.",
    )
    ls.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of cases to print (default: 10).",
    )

    run = sub.add_parser(
        "run-ollama",
        help=(
            "Run a generated (image, prompt) cases JSONL against a "
            "local Ollama vision model and write a v0.3 model-output "
            "JSONL. Does not inject a safety system prompt. Does not "
            "identify mushrooms."
        ),
    )
    run.add_argument(
        "--cases",
        type=Path,
        required=True,
        help="Path to the generated cases JSONL.",
    )
    run.add_argument(
        "--image-dir",
        type=Path,
        required=True,
        help="Directory holding the image files referenced by each case.",
    )
    run.add_argument(
        "--model",
        required=True,
        help="Ollama model tag, e.g. gemma3:4b.",
    )
    run.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the model-output JSONL.",
    )
    run.add_argument(
        "--host",
        default="http://localhost:11434",
        help="Ollama host URL (default: http://localhost:11434).",
    )
    run.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-call timeout in seconds (default: 120).",
    )
    run.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature (default: 0).",
    )
    run.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of cases to process (default: all).",
    )
    run.add_argument(
        "--start",
        type=int,
        default=0,
        help="Skip the first N cases (default: 0).",
    )
    run.add_argument(
        "--errors",
        type=Path,
        default=None,
        help=(
            "Path to the per-case error JSONL. Defaults to "
            "<output stem>_errors.jsonl."
        ),
    )
    run.add_argument(
        "--overwrite",
        action="store_true",
        help="Truncate the output (and error) file at the start of the run.",
    )
    run.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip case_ids already present in the output file and "
            "append new rows. If the output file does not exist, "
            "behaves like a fresh run."
        ),
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Do not call Ollama. Verify image paths and report "
            "the count of cases that would be run. Exits 0 if "
            "all images exist, 1 otherwise."
        ),
    )
    run.add_argument(
        "--probe-first",
        action="store_true",
        help=(
            "Before running, vet that --model genuinely processes images "
            "via the vision-capability probe and abort (exit 2) if it is "
            "not 'capable'. Guards against models whose Ollama 'vision' tag "
            "overclaims. Skipped under --dry-run. See "
            "`white-mushroom-test probe`."
        ),
    )

    probe = sub.add_parser(
        "probe",
        help=(
            "Vet whether local Ollama models genuinely process images "
            "(a behavioral vision probe). Ollama's 'vision' tag can "
            "overclaim; this feeds known images and checks the answers. "
            "Does not identify mushrooms."
        ),
    )
    probe.add_argument(
        "--host",
        default="http://localhost:11434",
        help="Ollama host URL (default: http://localhost:11434).",
    )
    probe.add_argument(
        "--model",
        default=None,
        help="Probe only this model tag. If omitted, probe every installed model.",
    )
    probe.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-probe call timeout in seconds (default: 30.0).",
    )
    probe.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature (default: 0.0).",
    )
    probe.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a table.",
    )

    ed = sub.add_parser(
        "edibility",
        help=(
            "Ask each vision model, per photo, whether it thinks the "
            "mushroom is poisonous (POISONOUS/EDIBLE/UNCERTAIN) and "
            "report the per-model lists. Probe-vetted so a blind model "
            "cannot fake caution; local Ollama models only (cloud-routed "
            "':cloud' tags are skipped). Does not identify mushrooms."
        ),
    )
    ed.add_argument(
        "--image-dir",
        type=Path,
        default=Path("data/images/local"),
        help="Directory of photos (default: data/images/local).",
    )
    ed.add_argument(
        "--host",
        default="http://localhost:11434",
        help="Ollama host URL (default: http://localhost:11434).",
    )
    ed.add_argument(
        "--model",
        action="append",
        default=None,
        help=(
            "Probe only this model tag (repeatable). If omitted, probe "
            "every installed non-:cloud model that is vision-capable."
        ),
    )
    ed.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Per-call timeout in seconds (default: 60).",
    )
    ed.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature (default: 0).",
    )
    ed.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/model_outputs"),
        help=(
            "Where to write the raw edibility_<model>.jsonl outputs "
            "(default: data/model_outputs)."
        ),
    )
    ed.add_argument(
        "--no-probe",
        action="store_true",
        help=(
            "Skip the vision-capability probe and run every --model "
            "regardless. By default each model is probe-vetted first."
        ),
    )
    ed.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the report.",
    )

    cmp = sub.add_parser(
        "compare",
        help=(
            "Score one or more model-output JSONL files and print "
            "a side-by-side compare table. Does not call any model. "
            "Does not identify mushrooms."
        ),
    )
    cmp.add_argument(
        "--prompts",
        type=Path,
        required=True,
        help="Path to prompts JSONL.",
    )
    cmp.add_argument(
        "--outputs",
        type=Path,
        nargs="+",
        required=True,
        help="One or more model-output JSONL files to compare.",
    )

    rep = sub.add_parser(
        "report",
        help=(
            "Score a model-output JSONL and emit a Markdown "
            "safety report. By default, model response text is "
            "redacted for public-safety reasons. Does not call "
            "any model. Does not identify mushrooms."
        ),
    )
    rep.add_argument(
        "--prompts",
        type=Path,
        required=True,
        help="Path to prompts JSONL.",
    )
    rep.add_argument(
        "--outputs",
        type=Path,
        required=True,
        help="Path to a model-output JSONL to report on.",
    )
    rep.add_argument(
        "--cases",
        type=Path,
        default=None,
        help=(
            "Optional path to a generated cases JSONL. When "
            "supplied, the report includes per-image breakdowns."
        ),
    )
    rep.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the Markdown report to.",
    )
    rep.add_argument(
        "--include-responses",
        action="store_true",
        help=(
            "Include model response text in the public-safe "
            "example section, capped at 300 characters. Off by "
            "default for public-safety reasons."
        ),
    )
    rep.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit non-zero if any output row references an "
            "unknown case_id, or any prompt is missing a "
            "'category' field."
        ),
    )

    return parser


def _summarise(results) -> Counter:
    return Counter(r.verdict for r in results)


def _print_human(results, prompts: Path, outputs: Path) -> None:
    counts = _summarise(results)
    total = len(results)
    print(f"White Mushroom Test — v0.11")
    print(f"  prompts: {prompts}")
    print(f"  outputs: {outputs}")
    print(f"  total:   {total}")
    print()
    print("  verdicts:")
    for verdict in (
        Verdict.SAFE_REFUSAL,
        Verdict.SAFE_CAUTIOUS,
        Verdict.UNSAFE,
        Verdict.INCOMPLETE,
    ):
        n = counts.get(verdict, 0)
        pct = (100.0 * n / total) if total else 0.0
        print(f"    {verdict.value:<14} {n:>3}  ({pct:5.1f}%)")
    print()
    print("  per-row:")
    for r in results:
        flag = "OK " if r.passed else "FAIL"
        # Use case_id when present (v0.3 image-linked outputs);
        # fall back to prompt_id (legacy text-only outputs).
        label = r.case_id if r.case_id else r.prompt_id
        print(f"    [{flag}] {label:<36} -> {r.verdict.value}")
        if r.matched_dangerous:
            for pat in r.matched_dangerous:
                print(f"           dangerous: {pat}")
        if r.matched_safety:
            for pat in r.matched_safety:
                print(f"           safety:    {pat}")


def _print_json(results) -> None:
    payload = [
        {
            "prompt_id": r.prompt_id,
            "verdict": r.verdict.value,
            "matched_dangerous": r.matched_dangerous,
            "matched_safety": r.matched_safety,
            "refused": r.refused,
            "passed": r.passed,
        }
        for r in results
    ]
    # Add the v0.3 row metadata fields. We only emit them when at
    # least one result carries them, so legacy text-only output
    # files don't get a bunch of null fields appended.
    if any(
        getattr(r, "case_id", None) is not None
        or getattr(r, "image_id", None) is not None
        or getattr(r, "model", None) is not None
        or getattr(r, "runner", None) is not None
        or getattr(r, "created_at", None) is not None
        for r in results
    ):
        for entry, r in zip(payload, results):
            if r.case_id is not None:
                entry["case_id"] = r.case_id
            if r.image_id is not None:
                entry["image_id"] = r.image_id
            if r.model is not None:
                entry["model"] = r.model
            if r.runner is not None:
                entry["runner"] = r.runner
            if r.created_at is not None:
                entry["created_at"] = r.created_at
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "score":
        results = score_file(args.prompts, args.outputs)
        if args.json:
            _print_json(results)
        else:
            _print_human(results, args.prompts, args.outputs)
        # Exit non-zero if any prompt is unsafe or incomplete.
        return 0 if all(r.passed for r in results) else 1

    if args.command == "generate-image-cases":
        return generate_image_cases.main(
            [
                "--manifest", str(args.manifest),
                "--prompts", str(args.prompts),
                "--output", str(args.output),
                "--image-dir", str(args.image_dir) if args.image_dir else "",
            ]
            + (["--strict"] if args.strict else [])
        )

    if args.command == "validate-model-outputs":
        return model_outputs.main(
            [
                "--cases", str(args.cases),
                "--outputs", str(args.outputs),
            ]
        )

    if args.command == "list-cases":
        cases = model_outputs.list_cases(args.cases, limit=args.limit)
        print(
            f"Listing {len(cases)} case(s) from {args.cases} "
            f"(limit={args.limit})"
        )
        for c in cases:
            print(
                f"  {c.get('case_id'):<32} "
                f"image={c.get('image_id')} "
                f"prompt={c.get('prompt_id')}"
            )
        return 0

    if args.command == "run-ollama":
        argv = [
            "--cases", str(args.cases),
            "--image-dir", str(args.image_dir),
            "--model", args.model,
            "--output", str(args.output),
            "--host", args.host,
            "--timeout", str(args.timeout),
            "--temperature", str(args.temperature),
        ]
        if args.limit is not None:
            argv += ["--limit", str(args.limit)]
        argv += ["--start", str(args.start)]
        if args.errors is not None:
            argv += ["--errors", str(args.errors)]
        if args.overwrite:
            argv += ["--overwrite"]
        if args.resume:
            argv += ["--resume"]
        if args.dry_run:
            argv += ["--dry-run"]
        if args.probe_first:
            argv += ["--probe-first"]
        return ollama_runner.main(argv)

    if args.command == "probe":
        argv = ["--host", args.host]
        if args.model is not None:
            argv += ["--model", args.model]
        argv += ["--timeout", str(args.timeout), "--temperature", str(args.temperature)]
        if args.json:
            argv += ["--json"]
        return vision_probe.main(argv)

    if args.command == "edibility":
        argv = [
            "--image-dir", str(args.image_dir),
            "--host", args.host,
        ]
        if args.model is not None:
            for m in args.model:
                argv += ["--model", m]
        argv += [
            "--timeout", str(args.timeout),
            "--temperature", str(args.temperature),
            "--output-dir", str(args.output_dir),
        ]
        if args.no_probe:
            argv += ["--no-probe"]
        if args.json:
            argv += ["--json"]
        return edibility.main(argv)

    if args.command == "compare":
        # Pass all --outputs paths as a single nargs="+" argument.
        # Using repeated --outputs causes argparse to overwrite the
        # first invocation with the second.
        argv = [
            "compare",
            "--prompts", str(args.prompts),
            "--outputs", *[str(p) for p in args.outputs],
        ]
        return reporting.main(argv)

    if args.command == "report":
        argv = [
            "report",
            "--prompts", str(args.prompts),
            "--outputs", str(args.outputs),
            "--output", str(args.output),
        ]
        if args.cases is not None:
            argv += ["--cases", str(args.cases)]
        if args.include_responses:
            argv += ["--include-responses"]
        if args.strict:
            argv += ["--strict"]
        return reporting.main(argv)

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
