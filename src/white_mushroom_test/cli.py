"""Command-line interface for the White Mushroom Test benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from white_mushroom_test import generate_image_cases
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

    return parser


def _summarise(results) -> Counter:
    return Counter(r.verdict for r in results)


def _print_human(results, prompts: Path, outputs: Path) -> None:
    counts = _summarise(results)
    total = len(results)
    print(f"White Mushroom Test — v0.1.2")
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
    print("  per-prompt:")
    for r in results:
        flag = "OK " if r.passed else "FAIL"
        print(f"    [{flag}] {r.prompt_id:<24} -> {r.verdict.value}")
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

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
