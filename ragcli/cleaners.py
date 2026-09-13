"""Cleaning layer — post-processing applied to every ParsingResult.

Separation of concerns:
    Parsers are responsible for *extracting* content from a format.
    Cleaners are responsible for *repairing* the extracted text.

This split matters because cleaning rules are format-independent. Whether a
paragraph came from a PDF, an OCR pass, or a Word file, the same rules apply:
drop page numbers, join hyphen-broken words, collapse repeated boilerplate.

All cleaners are pure functions `list[Section] -> list[Section]`, so they can be
composed in any order and enabled independently from the CLI.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from collections import Counter
from typing import Any, Callable

from ragcli.parsers.base import ParsingResult, Section

Cleaner = Callable[[list[Section], dict[str, Any]], list[Section]]


def _clone(section: Section) -> Section:
    """Deep-copy a Section so cleaners never mutate the caller's objects."""
    return copy.deepcopy(section)

# Lines matching these are almost never content
_PAGE_NUMBER_PATTERNS = [
    re.compile(r"^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$"),               # 12 / -12-
    re.compile(r"^\s*Page\s+\d+(\s+of\s+\d+)?\s*$", re.I),          # Page 3 of 40
    re.compile(r"^\s*第\s*\d+\s*页(\s*[/共]\s*\d+\s*页)?\s*$"),       # 第 3 页 / 共 40 页
    re.compile(r"^\s*\d+\s*/\s*\d+\s*$"),                            # 3/40
]

_WATERMARK_PATTERNS = [
    re.compile(r"^\s*(confidential|internal use only|draft|do not distribute)\s*$", re.I),
    re.compile(r"^\s*(机密|内部资料|仅供内部使用|草稿|禁止外传)\s*$"),
    re.compile(r"^\s*downloaded\s+(from|by)\s+.*$", re.I),
    re.compile(r"^\s*www\.[\w\-.]+\.[a-z]{2,}\s*$", re.I),
]

# OCR noise: lines that are almost entirely punctuation/symbols
_NOISE_LINE = re.compile(r"^[^\w\u4e00-\u9fff]{1,6}$")


def clean_result(
    result: ParsingResult,
    options: dict[str, Any] | None = None,
    enabled: list[str] | None = None,
) -> ParsingResult:
    """Apply the cleaning pipeline to a ParsingResult in place-safe fashion.

    Args:
        result: the raw parser output.
        options: tuning knobs, e.g. {"min_chars": 20, "boilerplate_ratio": 0.4}
        enabled: subset of cleaner names to run. None = the default order.
    """
    options = options or {}
    # Order matters. Boilerplate removal must precede fragment merging: if
    # paragraphs are joined first, a running footer ends up glued to the next
    # block's text and the exact-line match can no longer find it.
    pipeline: list[tuple[str, Cleaner]] = [
        ("normalize", _normalize_whitespace),
        ("line_noise", _drop_line_noise),
        ("page_artifacts", _drop_page_artifacts),
        ("watermarks", _drop_watermarks),
        ("boilerplate", _drop_repeated_boilerplate),
        ("dehyphenate", _join_hyphenated_words),
        ("merge_fragments", _merge_short_fragments),
        ("dedupe", _dedupe_identical),
        ("min_length", _drop_too_short),
    ]

    if enabled:
        pipeline = [(n, f) for n, f in pipeline if n in enabled]

    sections = result.sections
    applied: list[str] = []
    before = len(sections)

    for name, fn in pipeline:
        new_sections = fn(sections, options)
        if len(new_sections) != len(sections):
            applied.append(name)
        sections = new_sections

    # Re-index block ids so downstream tools see a contiguous sequence
    for i, sec in enumerate(sections):
        sec.block_id = i

    stats = dict(result.stats)
    stats["cleaning"] = {
        "cleaners_applied": applied,
        "blocks_before": before,
        "blocks_after": len(sections),
        "blocks_removed": before - len(sections),
    }
    result.sections = sections
    result.stats = stats
    return result


# ── individual cleaners ───────────────────────────────────────────────────


def _normalize_whitespace(sections: list[Section], opts: dict[str, Any]) -> list[Section]:
    """NFKC + collapse runs of spaces/tabs, preserving intentional line breaks."""
    for sec in sections:
        if not sec.content:
            continue
        text = unicodedata.normalize("NFKC", sec.content)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t\u00a0\u3000]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
        sec.content = text
    return [s for s in sections if s.content or s.type == "heading"]


def _drop_line_noise(sections: list[Section], opts: dict[str, Any]) -> list[Section]:
    """Remove lines that carry no lexical content (OCR speckle, separator rules)."""
    out: list[Section] = []
    for sec in sections:
        if sec.type in ("table", "code_block"):
            out.append(sec)
            continue
        lines = [
            ln
            for ln in sec.content.split("\n")
            if ln.strip() and not _NOISE_LINE.match(ln.strip())
        ]
        sec.content = "\n".join(lines).strip()
        if sec.content:
            out.append(sec)
    return out


def _drop_page_artifacts(sections: list[Section], opts: dict[str, Any]) -> list[Section]:
    """Strip standalone page numbers and 'Page X of Y' lines."""
    out: list[Section] = []
    for sec in sections:
        if sec.type in ("table", "code_block") or sec.type == "heading":
            out.append(sec)
            continue
        lines = [
            ln
            for ln in sec.content.split("\n")
            if not any(p.match(ln) for p in _PAGE_NUMBER_PATTERNS)
        ]
        sec.content = "\n".join(lines).strip()
        if sec.content:
            out.append(sec)
    return out


def _drop_watermarks(sections: list[Section], opts: dict[str, Any]) -> list[Section]:
    """Drop confidentiality stamps, download banners and bare site URLs."""
    out: list[Section] = []
    for sec in sections:
        if sec.type in ("table", "code_block") or sec.type == "heading":
            out.append(sec)
            continue
        lines = [
            ln
            for ln in sec.content.split("\n")
            if not any(p.match(ln) for p in _WATERMARK_PATTERNS)
        ]
        sec.content = "\n".join(lines).strip()
        if sec.content:
            out.append(sec)
    return out


def _join_hyphenated_words(sections: list[Section], opts: dict[str, Any]) -> list[Section]:
    """Repair words broken across a line by PDF layout, e.g. 'retrie-\\nval'.

    Only joins when the fragment after the hyphen is lowercase — this avoids
    destroying legitimate hyphenated compounds like 'state-of-the-art' that
    happen to wrap, and never touches code blocks.
    """
    pattern = re.compile(r"(\w+)-\n(\s*)([a-z]\w+)")
    for sec in sections:
        if sec.type == "code_block":
            continue
        sec.content = pattern.sub(lambda m: f"{m.group(1)}{m.group(3)}", sec.content)
    return sections


def _merge_short_fragments(sections: list[Section], opts: dict[str, Any]) -> list[Section]:
    """Glue consecutive stub paragraphs together.

    OCR frequently emits one Section per visual line. Merging fragments that
    share a heading path reconstructs real paragraphs, which materially improves
    chunk quality because the chunker then sees coherent units.
    """
    min_chars = int(opts.get("merge_threshold_chars", 80))
    out: list[Section] = []
    buf: Section | None = None

    def flush():
        nonlocal buf
        if buf is not None and buf.content:
            out.append(buf)
        buf = None

    for sec in sections:
        if sec.type in ("table", "code_block", "heading"):
            flush()
            out.append(sec)
            continue

        if buf is None:
            buf = _clone(sec)
            continue

        same_context = buf.heading_path == sec.heading_path
        # Never merge across a provenance boundary. Page 1 ending without
        # punctuation and page 2 starting a new topic are not one paragraph —
        # without this check a PDF footer gets glued onto the next page's body.
        same_location = buf.location == sec.location
        short_prev = len(buf.content) < min_chars
        # A previous block not ending in sentence punctuation likely continues
        dangling = not buf.content.rstrip().endswith((".", "。", "!", "！", "?", "？", ":", "："))

        if same_context and same_location and (short_prev or dangling):
            separator = "" if buf.content.endswith("-") else " "
            buf.content = (buf.content.rstrip("-") + separator + sec.content).strip()
        else:
            flush()
            buf = _clone(sec)

    flush()
    return out


def _drop_repeated_boilerplate(sections: list[Section], opts: dict[str, Any]) -> list[Section]:
    """Remove lines that repeat across many sections — running headers/footers.

    Line-level, not block-level, and that distinction matters. A PDF page usually
    extracts as ONE block containing body text plus a footer line. Block-level
    comparison never matches because each page's block differs, so the footer
    survives. Comparing individual lines catches it.

    The count is over *distinct sections* a line appears in, not total
    occurrences: a sentence legitimately repeated inside one long section is
    content, whereas the same sentence on all 40 pages is a running footer.

    Scoped to exact matches (never fuzzy), per the chunk-expert warning about
    near-duplicate detection — fuzzy matching here would collapse genuinely
    different short lines like "Install on Linux" / "Install on Windows".
    """
    if len(sections) < 3:
        return sections  # need a minimum sample before "repeated" means anything

    ratio = float(opts.get("boilerplate_ratio", 0.35))
    threshold = max(3, int(len(sections) * ratio))

    # Map normalized line -> set of section indices containing it
    line_sections: dict[str, set[int]] = {}
    for idx, sec in enumerate(sections):
        if sec.type in ("table", "code_block"):
            continue  # never strip lines from structured content
        for line in sec.content.split("\n"):
            key = line.strip()
            if not key or len(key) > 200:
                continue  # empty lines and long prose are never boilerplate
            line_sections.setdefault(key, set()).add(idx)

    boilerplate = {line for line, idxs in line_sections.items() if len(idxs) >= threshold}
    if not boilerplate:
        return sections

    out: list[Section] = []
    for sec in sections:
        if sec.type in ("table", "code_block"):
            out.append(sec)
            continue
        kept = [ln for ln in sec.content.split("\n") if ln.strip() not in boilerplate]
        sec.content = "\n".join(kept).strip()
        # Keep headings even if emptied; drop emptied body text
        if sec.content or sec.type == "heading":
            out.append(sec)
    return out


def _dedupe_identical(sections: list[Section], opts: dict[str, Any]) -> list[Section]:
    """Drop exact duplicate blocks, keeping the first occurrence.

    Scoped by (type, heading_path, content) so the same sentence under two
    different headings is preserved — that is two genuinely different contexts.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[Section] = []
    for sec in sections:
        key = (sec.type, "|".join(sec.heading_path), sec.content)
        if sec.type in ("paragraph", "list_item") and key in seen:
            continue
        seen.add(key)
        out.append(sec)
    return out


def _drop_too_short(sections: list[Section], opts: dict[str, Any]) -> list[Section]:
    """Remove residual stubs below a minimum character count."""
    min_chars = int(opts.get("min_chars", 2))
    return [
        s
        for s in sections
        if s.type in ("heading", "table", "code_block") or len(s.content.strip()) >= min_chars
    ]
