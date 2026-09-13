"""parse — unified entry point for the data pre-processing stage.

This is the ONLY command an agent needs to know about for ingestion. It hides
every format-specific detail behind one interface and always emits the same
JSON contract, so downstream tools (chunk / summarize / tagger) never branch on
file type.

Usage:
    ragcli parse -f report.pdf -o parsed.json
    ragcli parse -f https://docs.example.com/api -o web.json
    ragcli parse -d ./knowledge-base/ -o all_docs.jsonl
    ragcli parse -f scanned.png --engine paddle --lang ch -o ocr.json
    ragcli parse --list-formats
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ragcli.cleaners import clean_result
from ragcli.parsers import MissingDependencyError, ParseError, get_router, route

PARSE_TOOL = {
    "name": "parse",
    "description": (
        "Extract structured content from any source format (PDF, Word, Excel, "
        "PowerPoint, HTML/URL, Markdown, CSV, JSON, YAML, images) and emit a "
        "single normalized JSON contract for downstream tools"
    ),
    "inputs": ["file path, directory, or http(s) URL"],
    "outputs": ["ParsingResult JSON (sections with heading paths + provenance)"],
}

# Formats the router knows about, for --list-formats
_FORMAT_TABLE = [
    ("pdf", "pdfplumber → PaddleOCR/EasyOCR fallback", "pip install pdfplumber"),
    ("docx", "python-docx (nested tables, real heading levels)", "pip install python-docx"),
    ("xlsx/xlsm", "openpyxl (merged cells expanded, formula cache)", "pip install openpyxl"),
    ("pptx", "python-pptx (reading order, speaker notes)", "pip install python-pptx"),
    ("html/htm", "trafilatura → readability → tag-strip", "pip install trafilatura requests"),
    ("md/markdown", "native (heading breadcrumb tracking)", "—"),
    ("txt/log", "native (encoding auto-detect)", "—"),
    ("csv/tsv", "native (→ Markdown table, wide tables transposed)", "—"),
    ("json/jsonl", "native (flattened dotted paths)", "—"),
    ("yaml/yml", "PyYAML if present, else indented text", "pip install pyyaml"),
    ("png/jpg/webp/tiff", "PaddleOCR → EasyOCR → tesseract", "pip install paddleocr"),
    ("py/js/go/...", "native (single code block + filename heading)", "—"),
]


def _iter_sources(input_path: str, recursive: bool = True) -> list[str]:
    """Expand a directory into a sorted list of files. A file passes through."""
    p = Path(input_path)
    if p.is_file():
        return [str(p)]
    if not p.is_dir():
        return []
    pattern = "**/*" if recursive else "*"
    files = [f for f in p.glob(pattern) if f.is_file()]
    # Sort as POSIX strings, not Path objects — Path ordering differs between
    # Windows and Linux, which would make batch output non-reproducible.
    return sorted((str(f) for f in files), key=lambda s: Path(s).as_posix())


def _parse_one(source: str, args: argparse.Namespace) -> dict:
    """Parse and clean a single source, returning a serializable dict."""
    options: dict = {}

    if args.engine and args.engine != "auto":
        options["engine"] = args.engine
    if args.lang:
        options["ocr_lang"] = args.lang
    if args.strategy and args.strategy != "auto":
        options["strategy"] = args.strategy

    result = route(source, options)

    if not args.no_clean:
        clean_options = {
            "min_chars": args.min_chars,
            "merge_threshold_chars": args.merge_threshold,
            "boilerplate_ratio": args.boilerplate_ratio,
        }
        enabled = args.cleaners.split(",") if args.cleaners else None
        result = clean_result(result, clean_options, enabled)

    # Classify the extraction. This is the routing signal the orchestrator
    # branches on — a parse can fail silently (a scan with no OCR engine yields
    # zero sections and no exception), and an empty document that looks valid is
    # worse than one that visibly failed.
    result.decide_verdict()

    return json.loads(result.to_json())


def run(args: argparse.Namespace) -> None:
    # ── discovery modes ───────────────────────────────────────────────────
    if args.list_formats:
        router = get_router()
        payload = {
            "supported_formats": [
                {
                    "extensions": fmt,
                    "engine": engine,
                    "install": install,
                    "available": _is_available(fmt),
                }
                for fmt, engine, install in _FORMAT_TABLE
            ],
            "notes": [
                "Every format returns the same JSON contract: "
                "{source_id, format_type, meta, sections[], stats}",
                "sections[].heading_path carries the breadcrumb chain so downstream "
                "chunkers do not need to re-parse structure",
                "sections[].location carries provenance (page / sheet / slide)",
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not args.input and not args.file:
        print(
            "[parse] ERROR: provide -f <path|url>, -d <dir>, or --list-formats",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── collect sources ───────────────────────────────────────────────────
    sources: list[str] = []
    if args.file:
        sources.append(args.file)
    if args.input:
        sources.extend(_iter_sources(args.input, recursive=not args.no_recursive))

    if not sources:
        print(f"[parse] ERROR: no files found at {args.input}", file=sys.stderr)
        sys.exit(1)

    print(f"[parse] {len(sources)} source(s) to process", file=sys.stderr)

    # ── process ───────────────────────────────────────────────────────────
    successes: list[dict] = []
    failures: list[dict] = []

    for i, src in enumerate(sources, 1):
        label = src if len(src) < 80 else src[:77] + "..."
        print(f"[parse] ({i}/{len(sources)}) {label}", file=sys.stderr)
        try:
            payload = _parse_one(src, args)
            successes.append(payload)
            n_sections = len(payload.get("sections", []))
            engine = payload.get("stats", {}).get("parser_engine_used", "?")
            verdict = payload.get("verdict", "?")
            warn = payload.get("stats", {}).get("warnings", [])
            suffix = f" ({len(warn)} warning(s))" if warn else ""
            print(
                f"[parse]   {verdict} — {n_sections} blocks via {engine}{suffix}",
                file=sys.stderr,
            )
            for r in payload.get("verdict_reasons", []):
                print(f"[parse]   reason: {r}", file=sys.stderr)
            for w in warn:
                print(f"[parse]   warn: {w}", file=sys.stderr)
        except MissingDependencyError as e:
            failures.append({"source": src, "error": str(e), "kind": "missing_dependency"})
            print(f"[parse]   SKIP — {e}", file=sys.stderr)
        except ParseError as e:
            failures.append({"source": src, "error": str(e), "kind": "parse_error"})
            print(f"[parse]   FAIL — {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — one bad file must not kill the batch
            failures.append(
                {"source": src, "error": f"{type(e).__name__}: {e}", "kind": "unexpected"}
            )
            print(f"[parse]   FAIL — {type(e).__name__}: {e}", file=sys.stderr)

    # ── emit ──────────────────────────────────────────────────────────────
    # JSONL when batch (streams, one doc per line); pretty JSON for a single doc.
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if args.format == "jsonl" or (args.format == "auto" and len(successes) > 1):
                with out_path.open("w", encoding="utf-8", newline="\n") as fh:
                    for doc in successes:
                        fh.write(json.dumps(doc, ensure_ascii=False) + "\n")
            else:
                body = successes[0] if len(successes) == 1 else {"documents": successes}
                out_path.write_text(
                    json.dumps(body, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                    newline="\n",
                )
        except OSError as e:
            print(f"[parse] ERROR: could not write {out_path}: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"[parse] wrote {len(successes)} document(s) to {args.output}", file=sys.stderr)
    else:
        body = successes[0] if len(successes) == 1 else {"documents": successes}
        print(json.dumps(body, ensure_ascii=False, indent=2))

    # Summary to stderr so stdout stays machine-parseable
    print(
        json.dumps(
            {
                "summary": {
                    "total": len(sources),
                    "succeeded": len(successes),
                    "failed": len(failures),
                    "failures": failures,
                }
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )

    if failures and args.fail_fast:
        sys.exit(2)


def _is_available(ext_group: str) -> bool:
    """Best-effort probe of whether the engine for a format is importable."""
    probe = {
        "pdf": "pdfplumber",
        "docx": "docx",
        "xlsx/xlsm": "openpyxl",
        "pptx": "pptx",
        "html/htm": "trafilatura",
        "yaml/yml": "yaml",
        "png/jpg/webp/tiff": "paddleocr",
    }.get(ext_group)
    if probe is None:
        return True  # native engines always available
    import importlib.util

    return importlib.util.find_spec(probe) is not None


def register_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "parse",
        help="Pre-process any source format into the normalized JSON contract",
        description=PARSE_TOOL["description"],
    )
    src = p.add_argument_group("source")
    src.add_argument("-f", "--file", help="A single file path or http(s) URL")
    src.add_argument(
        "-d",
        "-i",
        "--input",
        dest="input",
        help="A directory to batch-process (recurses by default)",
    )
    src.add_argument(
        "--no-recursive", action="store_true", help="Do not descend into subdirectories"
    )

    out = p.add_argument_group("output")
    out.add_argument("-o", "--output", help="Write result here (stdout if omitted)")
    out.add_argument(
        "--format",
        choices=["auto", "json", "jsonl"],
        default="auto",
        help="jsonl streams one document per line; auto picks based on batch size",
    )

    eng = p.add_argument_group("engine selection")
    eng.add_argument(
        "--engine",
        choices=["auto", "paddle", "easyocr", "tesseract"],
        default="auto",
        help="Force a specific OCR engine for image sources",
    )
    eng.add_argument("--lang", default="ch", help="OCR language, e.g. ch / en / japan")
    eng.add_argument(
        "--strategy",
        choices=["auto", "text", "ocr"],
        default="auto",
        help="PDF extraction strategy; 'ocr' skips native extraction entirely",
    )

    cln = p.add_argument_group("cleaning")
    cln.add_argument("--no-clean", action="store_true", help="Skip the cleaning pipeline")
    cln.add_argument(
        "--cleaners",
        help="Comma-separated subset to run, e.g. 'normalize,dehyphenate,dedupe'",
    )
    cln.add_argument("--min-chars", type=int, default=2, help="Drop blocks shorter than this")
    cln.add_argument(
        "--merge-threshold",
        type=int,
        default=80,
        help="Merge paragraph fragments shorter than this many characters",
    )
    cln.add_argument(
        "--boilerplate-ratio",
        type=float,
        default=0.35,
        help="Fraction of blocks a repeated line must reach to be dropped as boilerplate",
    )

    misc = p.add_argument_group("misc")
    misc.add_argument(
        "--list-formats",
        action="store_true",
        help="Print supported formats and whether their engine is installed",
    )
    misc.add_argument(
        "--fail-fast", action="store_true", help="Exit non-zero if any source failed"
    )

    p.set_defaults(func=run)
