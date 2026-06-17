"""Vision-capability probe for local Ollama models.

Ollama silently accepts the ``images`` field for *any* model and ignores it when
the model has no vision projector — no error, no warning. A model's "vision" tag
or modelfile can therefore overclaim, so the only reliable vet is *behavioral*:
feed the model images with known, unambiguous visual content and check the answer.

This module generates a handful of tiny synthetic images with a stdlib-only PNG
encoder (no PIL, no committed assets), asks the model image-grounded questions, and
classifies the model as ``capable`` / ``text_only`` / ``hallucinating`` /
``inconsistent`` / ``error``. Run via ``white-mushroom-test probe`` before a
benchmark run, or use ``run-ollama --probe-first`` to abort a run on a model that
cannot see. Stdlib-only; no Streamlit.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import struct
import sys
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass

from white_mushroom_test.llm import DEFAULT_OLLAMA_HOST, LLMError, OllamaVisionClient

# ---------------------------------------------------------------------------
# verdict labels
# ---------------------------------------------------------------------------

CAPABLE = "capable"
TEXT_ONLY = "text_only"
HALLUCINATING = "hallucinating"
INCONSISTENT = "inconsistent"
ERROR = "error"

DEFAULT_TIMEOUT = 30.0
DEFAULT_TEMPERATURE = 0.0


# ---------------------------------------------------------------------------
# stdlib-only PNG encoder (8-bit truecolor RGB, color type 2)
# ---------------------------------------------------------------------------

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _png(w: int, h: int, rows: list[bytes]) -> bytes:
    """Encode an RGB image. ``rows`` is h entries of w*3 bytes (left-to-right RGB)."""
    if len(rows) != h or any(len(r) != w * 3 for r in rows):
        raise ValueError("rows must be h entries of w*3 bytes")
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit truecolor RGB
    raw = b"".join(b"\x00" + r for r in rows)  # filter type 0 (none) per scanline
    idat = zlib.compress(raw, 9)
    return _PNG_SIGNATURE + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _solid_png(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
    """A solid-color rectangle."""
    row = bytes(rgb) * w
    return _png(w, h, [row] * h)


_SQUARE_COLORS = [
    (220, 50, 50),    # red
    (50, 170, 60),    # green
    (50, 90, 220),    # blue
    (235, 200, 40),   # yellow
    (170, 50, 200),   # purple
]


def _count_png(n: int, w: int, h: int) -> bytes:
    """n distinct colored vertical regions, each w//n wide, on a white field."""
    colors = _SQUARE_COLORS[:n]
    side = max(1, w // n)
    rows: list[bytes] = []
    for _ in range(h):
        row = bytearray()
        for c in colors:
            row += bytes(c) * side
        if len(row) < w * 3:
            row += bytes((255, 255, 255)) * (w - len(row) // 3)
        rows.append(bytes(row))
    return _png(w, h, rows)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# ---------------------------------------------------------------------------
# probe definitions + matchers
# ---------------------------------------------------------------------------

_COLOR_SYNONYMS = {
    "red": ("red", "crimson", "scarlet", "maroon", "cherry", "rust"),
    "blue": ("blue", "navy", "azure", "cobalt", "sapphire", "indigo"),
}

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
}

# Concrete-object nouns whose *un-negated* presence in the blank-probe answer
# means the model invented something to see in a uniform gray image.
_BLANK_OBJECT_NOUNS = frozenset({
    "mushroom", "mushrooms", "fungus", "fungi", "toadstool",
    "plant", "tree", "flower", "leaf", "leaves", "grass", "moss",
    "food", "fruit", "vegetable", "berry", "berries",
    "animal", "dog", "cat", "bird", "insect", "bug",
    "person", "people", "man", "woman", "child", "human",
    "rock", "stone", "soil", "dirt", "wood", "branch", "stick",
})

_NEGATION_WORDS = frozenset({
    "no", "not", "none", "nothing", "neither", "nor", "without", "absent",
    "absence", "lack", "lacking", "missing", "cannot", "cant", "dont",
    "doesnt", "isnt", "wasnt", "arent", "wont", "shouldnt", "couldnt",
    "wouldnt", "didnt", "hasnt", "havent",
})


def _tokens(text: str) -> list[str]:
    """Lowercase alphabetic tokens, with apostrophes stripped (don't -> dont)."""
    return re.findall(r"[a-z]+", text.lower().replace("'", ""))


def _color_match(answer: str, color: str) -> bool:
    toks = set(_tokens(answer))
    return any(syn in toks for syn in _COLOR_SYNONYMS[color])


def _count_match(answer: str, n: int) -> bool:
    if str(n) in re.findall(r"\d+", answer):
        return True
    toks = set(_tokens(answer))
    return any(word in toks and val == n for word, val in _NUMBER_WORDS.items())


def _answer_invents_object(answer: str) -> bool:
    """True if the model asserts a concrete object (not merely negates one).

    A negation word within the preceding 6 tokens clears the noun — "I see no
    mushroom" is honest, "a small white mushroom" is invented. Mirrors the
    scorer's negation-window approach.
    """
    toks = _tokens(answer)
    for i, tok in enumerate(toks):
        if tok not in _BLANK_OBJECT_NOUNS:
            continue
        window = toks[max(0, i - 6):i]
        if not any(w in _NEGATION_WORDS for w in window):
            return True
    return False


@dataclass(frozen=True)
class _ProbeSpec:
    name: str
    question: str
    expected: str
    image_b64: str
    judge: str  # "color:<color>" | "count:<n>" | "blank:honest"


# Built once at import: tiny images, cheap to encode.
_SPECS = [
    _ProbeSpec(
        "color_red",
        "What is the dominant color of this image? Reply with a single color word.",
        "red",
        _b64(_solid_png(64, 64, (220, 50, 50))),
        "color:red",
    ),
    _ProbeSpec(
        "color_blue",
        "What is the dominant color of this image? Reply with a single color word.",
        "blue",
        _b64(_solid_png(64, 64, (50, 90, 220))),
        "color:blue",
    ),
    _ProbeSpec(
        "count_3",
        "How many distinct colored regions are in this image? Reply with just the number.",
        "3",
        _b64(_count_png(3, 96, 32)),
        "count:3",
    ),
    _ProbeSpec(
        "blank",
        "Describe what you see in this image in one sentence.",
        "a blank/uniform image (no invented object)",
        _b64(_solid_png(64, 64, (128, 128, 128))),
        "blank:honest",
    ),
]


def _judge(spec: _ProbeSpec, answer: str) -> bool:
    if spec.judge.startswith("color:"):
        return _color_match(answer, spec.judge.split(":", 1)[1])
    if spec.judge.startswith("count:"):
        return _count_match(answer, int(spec.judge.split(":", 1)[1]))
    if spec.judge == "blank:honest":
        return not _answer_invents_object(answer)
    return False


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    name: str
    question: str
    expected: str
    answer: str
    passed: bool
    error: str | None = None


@dataclass
class ProbeReport:
    provider: str
    model: str
    verdict: str
    probes: list[ProbeResult]
    error: str | None = None


def _verdict(results: list[ProbeResult]) -> str:
    by = {r.name: r for r in results}
    red, blue, count, blank = by["color_red"], by["color_blue"], by["count_3"], by["blank"]
    real_errored = bool(red.error and blue.error and count.error)
    real_pass = red.passed or blue.passed or count.passed
    real_all = red.passed and blue.passed and count.passed
    # blank.passed == honest (ran + no invented object); invented == ran + not passed.
    blank_invented = blank.error is None and not blank.passed
    if real_errored:
        return ERROR
    if real_all and blank.passed:
        return CAPABLE
    if real_pass and blank_invented:
        return HALLUCINATING
    if not real_pass and not real_errored:
        return TEXT_ONLY
    return INCONSISTENT


def probe_client(client, *, model: str, provider: str = "ollama") -> ProbeReport:
    """Run all probes against one client.

    ``client`` is any object with ``generate_text(prompt, image_b64) -> str``
    (both ``llm.py`` clients satisfy this), so tests inject a stub. A probe that
    raises is recorded as errored (passed=False) rather than aborting the report.
    """
    results: list[ProbeResult] = []
    for spec in _SPECS:
        try:
            answer = client.generate_text(spec.question, spec.image_b64)
        except Exception as exc:  # noqa: BLE001 — one probe must not kill the report
            results.append(
                ProbeResult(spec.name, spec.question, spec.expected, "", False,
                            error=f"{type(exc).__name__}: {exc}")
            )
            continue
        results.append(ProbeResult(spec.name, spec.question, spec.expected, answer, _judge(spec, answer)))
    return ProbeReport(provider=provider, model=model, verdict=_verdict(results), probes=results)


# ---------------------------------------------------------------------------
# Ollama discovery
# ---------------------------------------------------------------------------


def _list_ollama_models(host: str, timeout: float) -> list[str]:
    url = host.rstrip("/") + "/api/tags"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise LLMError(f"could not list Ollama models at {host!r}: {exc}") from exc
    return sorted(m["name"] for m in data.get("models", []) if m.get("name"))


def probe_ollama_model(
    host: str,
    model: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    temperature: float = DEFAULT_TEMPERATURE,
) -> ProbeReport:
    """Probe a single Ollama model (real ``call_ollama``; tests build their own client)."""
    client = OllamaVisionClient(host, model, timeout=timeout, temperature=temperature)
    return probe_client(client, model=model, provider="ollama")


def probe_ollama_models(
    host: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    temperature: float = DEFAULT_TEMPERATURE,
) -> list[ProbeReport]:
    """List installed Ollama models and probe each."""
    models = _list_ollama_models(host, timeout)
    return [probe_ollama_model(host, m, timeout=timeout, temperature=temperature) for m in models]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _report_dict(r: ProbeReport) -> dict:
    return {
        "provider": r.provider,
        "model": r.model,
        "verdict": r.verdict,
        "error": r.error,
        "probes": [
            {
                "name": p.name,
                "question": p.question,
                "expected": p.expected,
                "answer": p.answer,
                "passed": p.passed,
                "error": p.error,
            }
            for p in r.probes
        ],
    }


def _mark(p: ProbeResult) -> str:
    if p.error is not None:
        return "ERR"
    return "PASS" if p.passed else "FAIL"


def _print_reports(reports: list[ProbeReport]) -> None:
    if not reports:
        print("no models found.", file=sys.stderr)
        return
    print(f"{'MODEL':28s} {'VERDICT':13s} PROBES (PASS/FAIL/ERR)")
    for r in reports:
        summary = "  ".join(f"{p.name}={_mark(p)}" for p in r.probes)
        print(f"{r.model:28.28s} {r.verdict:13.13s} {summary}")
        if r.verdict != CAPABLE:
            for p in r.probes:
                if p.passed:
                    continue
                if p.error is not None:
                    print(f"    {p.name}: [{p.error}]")
                else:
                    print(f"    {p.name}: {p.answer!r}")
    print()
    print("verdicts: capable = sees the image; text_only = cannot see (tag overclaims);")
    print("          hallucinating = sees real content but invents an object on a blank;")
    print("          inconsistent/error = mixed or unreachable.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="white-mushroom-test-probe",
        description=(
            "Vet whether local Ollama models genuinely process images (a "
            "behavioral vision probe). Ollama's 'vision' tag can overclaim; "
            "this feeds known images and checks the answers."
        ),
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_OLLAMA_HOST,
        help=f"Ollama host URL (default: {DEFAULT_OLLAMA_HOST}).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Probe only this model tag. If omitted, probe every installed model.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Per-probe call timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=DEFAULT_TEMPERATURE,
        help=f"Sampling temperature (default: {DEFAULT_TEMPERATURE}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a table.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.model:
            reports = [probe_ollama_model(args.host, args.model, timeout=args.timeout, temperature=args.temperature)]
        else:
            reports = probe_ollama_models(args.host, timeout=args.timeout, temperature=args.temperature)
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([_report_dict(r) for r in reports], indent=2))
    else:
        _print_reports(reports)

    if not reports:
        return 1
    return 0 if any(r.verdict == CAPABLE for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())