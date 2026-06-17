"""Ollama vision runner for the White Mushroom Test.

This module is the **producer** of v0.3 ``ModelOutputRow`` files.
It walks a generated (image × prompt) cases JSONL, pairs each
case with the local image, sends the prompt and the image to a
local Ollama vision model, and writes one row per successful
case. Per-case errors do not stop the run; they are recorded in
a separate error JSONL.

**Safety principle.** The benchmark observes the model's
natural behaviour under the user prompt. The runner does **not**
inject a safety system prompt. The case ``prompt`` is sent to
Ollama verbatim. The scorer evaluates the response afterwards.

**No third-party dependencies.** All HTTP I/O is done with
``urllib.request`` from the standard library. The runner is
deliberately small: a handful of helpers, a ``RunSummary``
dataclass, a ``run_cases`` entry point, and a CLI ``main``.

The runner is local-only: it talks to a user-provided Ollama
host (default ``http://localhost:11434``) and reads image
files from a user-provided directory. No image files are
committed to the repository and no model API key is required.
"""

from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


# Module-level defaults.
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT = 120  # seconds
DEFAULT_TEMPERATURE = 0
RUNNER_NAME = "ollama"


# ---------------------------------------------------------------------------
# Helpers — small, individually testable functions
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with a Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_cases(path: Path) -> list[dict]:
    """Read all cases from a generated-cases JSONL.

    Each row must contain a ``case_id``; the rest of the
    validation is deferred to ``model_outputs.load_cases``.
    """
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                case = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}: invalid JSON on line {lineno}: {exc}"
                ) from exc
            if "case_id" not in case:
                raise ValueError(
                    f"{path}: line {lineno} missing 'case_id'; "
                    f"got {sorted(case.keys())}"
                )
            out.append(case)
    return out


def resolve_image_path(image_dir: Path, case: dict) -> Path:
    """Return the absolute image path for a case.

    The runner does not check existence — callers decide what
    to do with a missing path. ``FileNotFoundError`` is the
    expected failure mode.
    """
    filename = case.get("filename")
    if not filename:
        raise ValueError(
            f"case {case.get('case_id')!r}: missing 'filename'"
        )
    return image_dir / filename


def encode_image_base64(path: Path) -> str:
    """Read an image file and return its base64 encoding."""
    return base64.b64encode(path.read_bytes()).decode("ascii")


def build_ollama_payload(
    case: dict,
    model: str,
    image_b64: str,
    temperature: float,
    *,
    extra_options: dict | None = None,
) -> dict:
    """Build the JSON body for ``POST /api/generate``.

    The shape mirrors Ollama's documented ``/api/generate``
    request. ``stream`` is always ``False`` so the response is
    a single JSON object, not a stream of newline-delimited
    JSON chunks. ``extra_options`` (e.g. ``{"num_predict": 4096}``) is merged
    into ``options`` alongside ``temperature`` — used to cap a thinking
    model's output length so a long reasoning trace cannot hang the run
    (the urllib ``timeout`` is per-recv and does not bound total generation
    time when Ollama trickles bytes during a long generation).
    """
    options: dict = {"temperature": temperature}
    if extra_options:
        options.update(extra_options)
    return {
        "model": model,
        "prompt": case["prompt"],
        "images": [image_b64],
        "stream": False,
        "options": options,
    }


def call_ollama(host: str, payload: dict, timeout: float) -> str:
    """POST ``payload`` to ``${host}/api/generate`` and return
    the model's text response.

    Raises
    ------
    urllib.error.URLError
        If the Ollama host is unreachable (DNS, refused, etc.).
    urllib.error.HTTPError
        If Ollama returns a non-2xx status.
    TimeoutError
        If the request times out.
    json.JSONDecodeError
        If the response body is not valid JSON.
    KeyError
        If the response JSON has no ``response`` field.
    """
    url = host.rstrip("/") + "/api/generate"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    data = json.loads(raw)
    return data["response"]


def make_model_output_row(
    case: dict,
    model: str,
    response: str,
    latency_ms: int,
    host: str,
) -> dict:
    """Build a v0.3 ``ModelOutputRow``-compatible dict.

    The output includes the optional ``image_quality``, ``view``,
    and ``context`` fields from the case so downstream tools can
    group by image attributes. These are silently dropped by
    ``ModelOutputRow.from_dict`` (which only reads the fields it
    knows), so the row is still valid against the v0.3 schema.
    """
    row: dict = {
        "case_id": case["case_id"],
        "image_id": case.get("image_id", ""),
        "prompt_id": case.get("prompt_id", ""),
        "model": model,
        "response": response,
        "runner": RUNNER_NAME,
        "created_at": now_iso(),
        "latency_ms": latency_ms,
        "notes": f"host={host}",
    }
    for key in ("image_quality", "view", "context"):
        if case.get(key) is not None:
            row[key] = case[key]
    return row


def make_error_row(case: dict, model: str, exc: BaseException) -> dict:
    """Build a per-case error row."""
    return {
        "case_id": case.get("case_id", ""),
        "image_id": case.get("image_id", ""),
        "prompt_id": case.get("prompt_id", ""),
        "model": model,
        "runner": RUNNER_NAME,
        "created_at": now_iso(),
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


# ---------------------------------------------------------------------------
# Run summary
# ---------------------------------------------------------------------------


@dataclass
class RunSummary:
    """The result of a runner invocation."""

    total: int
    succeeded: int
    failed: int
    skipped: int
    output_path: Path
    error_path: Path
    dry_run: bool = False

    @property
    def exit_code(self) -> int:
        """0 on success, 1 on full failure. ``skipped`` is not a failure."""
        if self.dry_run:
            # Dry run reports the state of the inputs, not the run.
            return 0 if self.failed == 0 else 1
        if self.total == 0:
            return 0
        # All cases failed -> non-zero. Otherwise OK.
        return 1 if self.succeeded == 0 else 0


# ---------------------------------------------------------------------------
# run_cases
# ---------------------------------------------------------------------------


def _read_existing_case_ids(path: Path) -> set[str]:
    """Read ``case_id`` values from an existing output file.

    Returns an empty set if the file does not exist or is empty.
    Skips rows whose ``case_id`` is missing or non-string, on
    the principle that a corrupt output file is best surfaced
    downstream, not here.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return set()
    ids: set[str] = set()
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            cid = row.get("case_id")
            if isinstance(cid, str) and cid:
                ids.add(cid)
    return ids


def _select_cases(
    cases: list[dict], start: int, limit: int | None
) -> list[dict]:
    if start < 0:
        start = 0
    if start >= len(cases):
        return []
    end = len(cases) if limit is None else min(len(cases), start + limit)
    return cases[start:end]


def run_cases(
    cases: list[dict],
    image_dir: Path,
    model: str,
    output_path: Path,
    error_path: Path,
    *,
    host: str = DEFAULT_HOST,
    timeout: float = DEFAULT_TIMEOUT,
    temperature: float = DEFAULT_TEMPERATURE,
    start: int = 0,
    limit: int | None = None,
    overwrite: bool = False,
    resume: bool = False,
    dry_run: bool = False,
    call_ollama_fn: Callable[[str, dict, float], str] | None = None,
    extra_options: dict | None = None,
) -> RunSummary:
    """Run a sequence of cases against Ollama and write results.

    Parameters
    ----------
    cases:
        All available cases. The runner applies ``start`` and
        ``limit`` itself.
    image_dir:
        Directory holding the image files referenced by each
        case's ``filename``.
    model, host, timeout, temperature:
        Passed to the Ollama call.
    output_path, error_path:
        Files to write. ``output_path`` is the
        ``ModelOutputRow`` JSONL; ``error_path`` is the
        per-case error JSONL. ``error_path`` is created
        (truncated) only when the first error is recorded.
    start, limit:
        Slice into ``cases``. ``start`` is the index of the
        first case to process; ``limit`` caps how many are
        processed.
    overwrite:
        If True, truncate ``output_path`` and ``error_path``
        at the start of the run.
    resume:
        If True, skip cases whose ``case_id`` is already in
        ``output_path`` and append new rows.
    dry_run:
        If True, do not call Ollama. Verify image paths, count
        successes / failures, and return a summary.
    call_ollama_fn:
        Injection point for tests. Defaults to the real
        :func:`call_ollama`. The signature is
        ``(host, payload, timeout) -> str``.
    extra_options:
        Optional dict merged into the Ollama request ``options`` (e.g.
        ``{"num_predict": 4096}`` to cap a thinking model's output length).
        Forwarded to :func:`build_ollama_payload`; ``None`` leaves the
        payload with only ``temperature`` (the default, unchanged behaviour).

    Returns
    -------
    RunSummary
        Counts of total, succeeded, failed, skipped cases, and
        the output paths.
    """
    selected = _select_cases(cases, start, limit)
    total = len(selected)

    skipped: set[str] = set()
    if resume:
        existing = _read_existing_case_ids(output_path)
        skipped = {c["case_id"] for c in selected if c["case_id"] in existing}
        selected = [c for c in selected if c["case_id"] not in existing]

    # Open output file. ``overwrite`` truncates; otherwise we
    # append (or create). A fresh run is just append to a
    # non-existent file. Resume is also append. Dry runs do not
    # open or create either file.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    error_path.parent.mkdir(parents=True, exist_ok=True)

    if overwrite and not dry_run:
        # Truncate both files; truncate the error file only if
        # a previous run wrote to it.
        output_path.write_text("", encoding="utf-8")
        if error_path.is_file():
            error_path.write_text("", encoding="utf-8")

    output_fh: object | None = None
    if not dry_run:
        output_fh = output_path.open("a", encoding="utf-8")
    error_fh: object | None = None  # lazily opened on first error

    succeeded = 0
    failed = 0
    call = call_ollama_fn or call_ollama

    try:
        for case in selected:
            try:
                image_path = resolve_image_path(image_dir, case)
                if not dry_run:
                    if not image_path.is_file():
                        raise FileNotFoundError(
                            f"Image file not found: {image_path}"
                        )
                    image_b64 = encode_image_base64(image_path)
                    payload = build_ollama_payload(
                        case, model, image_b64, temperature,
                        extra_options=extra_options,
                    )
                    t0 = datetime.now(timezone.utc)
                    response = call(host, payload, timeout)
                    latency_ms = int(
                        (datetime.now(timezone.utc) - t0).total_seconds() * 1000
                    )
                    row = make_model_output_row(
                        case, model, response, latency_ms, host
                    )
                    output_fh.write(json.dumps(row, ensure_ascii=False))
                    output_fh.write("\n")
                    output_fh.flush()
                # Dry run counts each present image as a success
                # and each missing image as a failure. Image
                # presence was checked above in the non-dry
                # branch; in dry-run we check here.
                if dry_run:
                    if image_path.is_file():
                        pass  # success below
                    else:
                        raise FileNotFoundError(
                            f"Image file not found: {image_path}"
                        )
                succeeded += 1
            except BaseException as exc:  # noqa: BLE001 — see below
                # We catch broadly because per-case failures
                # must not stop the run. The error row records
                # the exception type and message so the user
                # can debug from the error file. Dry runs do
                # not write to the error file — they just count.
                failed += 1
                if dry_run:
                    continue
                if error_fh is None:
                    error_fh = error_path.open("a", encoding="utf-8")
                err_row = make_error_row(case, model, exc)
                error_fh.write(json.dumps(err_row, ensure_ascii=False))
                error_fh.write("\n")
                error_fh.flush()
    finally:
        if output_fh is not None:
            output_fh.close()
        if error_fh is not None:
            error_fh.close()

    return RunSummary(
        total=total,
        succeeded=succeeded,
        failed=failed,
        skipped=len(skipped),
        output_path=output_path,
        error_path=error_path,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_errors_path(output: Path) -> Path:
    """Return the default error file path derived from an output path."""
    return output.with_name(output.stem + "_errors.jsonl")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="white-mushroom-test-run-ollama",
        description=(
            "Run a generated-cases JSONL against a local Ollama "
            "vision model and write a v0.3 ModelOutputRow JSONL. "
            "Does not inject a safety system prompt: the case "
            "prompt is sent verbatim. Does not identify mushrooms."
        ),
    )
    parser.add_argument(
        "--cases",
        type=Path,
        required=True,
        help="Path to the generated cases JSONL.",
    )
    parser.add_argument(
        "--image-dir",
        type=Path,
        required=True,
        help="Directory holding the image files referenced by each case.",
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Ollama model tag, e.g. gemma3:4b.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the model-output JSONL.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Ollama host URL (default: {DEFAULT_HOST}).",
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
        "--limit",
        type=int,
        default=None,
        help="Cap the number of cases to process (default: all).",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="Skip the first N cases (default: 0).",
    )
    parser.add_argument(
        "--errors",
        type=Path,
        default=None,
        help=(
            "Path to the per-case error JSONL. Defaults to "
            "<output stem>_errors.jsonl."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Truncate the output (and error) file at the start of the run.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip case_ids already present in the output file and "
            "append new rows. If the output file does not exist, "
            "behaves like a fresh run."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Do not call Ollama. Verify image paths and report "
            "the count of cases that would be run. Exits 0 if "
            "all images exist, 1 otherwise."
        ),
    )
    parser.add_argument(
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
    return parser


def _output_exists_nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    error_path = args.errors or _default_errors_path(args.output)

    # Output-existence policy. Dry-run never touches files.
    if not args.dry_run and not args.overwrite and not args.resume:
        if _output_exists_nonempty(args.output):
            print(
                f"error: output file {args.output} already exists; "
                f"use --overwrite to replace or --resume to append",
                file=sys.stderr,
            )
            return 2

    try:
        cases = load_cases(args.cases)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.probe_first and not args.dry_run:
        # Lazy import keeps vision_probe (and thus llm) out of the module-load
        # path of ollama_runner, avoiding any import cycle, and only loads it
        # when the guard is actually on.
        from white_mushroom_test.vision_probe import probe_ollama_model

        report = probe_ollama_model(args.host, args.model, timeout=args.timeout)
        if report.verdict != "capable":
            print(
                f"error: --probe-first: model {args.model!r} is not "
                f"vision-capable (verdict={report.verdict}). Aborting before "
                f"the run. Re-run without --probe-first to force, or pick a "
                f"'capable' model (see `white-mushroom-test probe`).",
                file=sys.stderr,
            )
            return 2
        print(f"--probe-first: {args.model!r} verdict=capable; proceeding.")

    try:
        summary = run_cases(
            cases,
            image_dir=args.image_dir,
            model=args.model,
            output_path=args.output,
            error_path=error_path,
            host=args.host,
            timeout=args.timeout,
            temperature=args.temperature,
            start=args.start,
            limit=args.limit,
            overwrite=args.overwrite,
            resume=args.resume,
            dry_run=args.dry_run,
        )
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    mode = "DRY-RUN" if summary.dry_run else "RUN"
    print(
        f"{mode} summary: total={summary.total} "
        f"succeeded={summary.succeeded} "
        f"failed={summary.failed} "
        f"skipped={summary.skipped} "
        f"output={summary.output_path} "
        f"errors={summary.error_path}"
    )
    return summary.exit_code


if __name__ == "__main__":
    sys.exit(main())
