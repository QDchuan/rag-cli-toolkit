"""Verification: verdict classification and the coarse-tier mental model.

The verdict is the orchestrator's routing signal, so every branch needs a test.
It exists to catch *silent* parse failure — a scanned PDF with no OCR engine
returns zero sections and no exception, and an empty document that looks valid
is worse than one that visibly failed, because nobody investigates it.

Run: python tests/test_verdict.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ragcli.cleaners import clean_result  # noqa: E402
from ragcli.parsers import route  # noqa: E402
from ragcli.parsers.base import (  # noqa: E402
    MAX_CLEANING_REMOVAL_RATIO,
    MIN_CHARS_PER_PAGE,
    ParsingResult,
    Section,
)

results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))


tmp = Path(tempfile.mkdtemp(prefix="ragcli_verdict_"))


def verdict_of(path: str) -> tuple[str, list[str]]:
    r = clean_result(route(path)).decide_verdict()
    return r.verdict, r.verdict_reasons


# ── 1. ok: a normal document ──────────────────────────────────────────────
good = tmp / "good.md"
good.write_text(
    "# Title\n\nA substantial paragraph with real content in it.\n\n"
    "## Section\n\nMore content that clearly came through extraction intact.\n",
    encoding="utf-8",
)
v, reasons = verdict_of(str(good))
check("ok: normal document is ok", v == "ok", f"{v} {reasons}")
check("ok: no reasons recorded", not reasons, str(reasons))

# ── 2. unusable: nothing extracted ────────────────────────────────────────
blank = tmp / "blank.md"
blank.write_text("   \n\n\t\n   \n", encoding="utf-8")
v, reasons = verdict_of(str(blank))
check("unusable: empty document", v == "unusable", v)
check("unusable: reason explains why", any("no sections" in r for r in reasons), str(reasons))

# ── 3. unusable: too little text for the page count (the scan case) ───────
# A 40-page PDF yielding 120 chars is a scan whose text layer does not exist.
# Native extraction "succeeds" and produces nothing usable.
scanned_shape = ParsingResult(
    source_id="scan_1",
    format_type="pdf",
    meta={"page_count": 40},
    sections=[Section(block_id=0, type="paragraph", content="x" * 120)],
    stats={},
)
scanned_shape.decide_verdict()
check("unusable: scan detected by chars/page", scanned_shape.verdict == "unusable", scanned_shape.verdict)
check(
    "unusable: scan reason names the OCR fix",
    any("paddleocr" in r for r in scanned_shape.verdict_reasons),
    str(scanned_shape.verdict_reasons),
)

# Single-page documents must not trip the chars/page rule — there is no ratio
# to compute, and a legitimately short one-pager is not a failure.
one_pager = ParsingResult(
    source_id="short_1",
    format_type="md",
    meta={"page_count": 1},
    sections=[Section(block_id=0, type="paragraph", content="Short but real.")],
    stats={},
)
one_pager.decide_verdict()
check("ok: short single page is not flagged", one_pager.verdict == "ok", one_pager.verdict)

# No page_count at all (markdown, csv) must not trip it either
no_pages = ParsingResult(
    source_id="md_1",
    format_type="md",
    meta={},
    sections=[Section(block_id=0, type="paragraph", content="Content.")],
    stats={},
)
no_pages.decide_verdict()
check("ok: missing page_count is not treated as a scan", no_pages.verdict == "ok", no_pages.verdict)

# ── 4. degraded: parser warnings ──────────────────────────────────────────
warned = ParsingResult(
    source_id="warn_1",
    format_type="pdf",
    meta={},
    sections=[Section(block_id=0, type="paragraph", content="Content here.")],
    stats={"warnings": ["table detection failed on page 3"]},
)
warned.decide_verdict()
check("degraded: warnings cause degraded", warned.verdict == "degraded", warned.verdict)
check(
    "degraded: the warning text is carried through",
    any("table detection" in r for r in warned.verdict_reasons),
    str(warned.verdict_reasons),
)

# ── 5. degraded: OCR produced the text ────────────────────────────────────
ocrd = ParsingResult(
    source_id="ocr_1",
    format_type="pdf",
    meta={"is_scanned": True, "page_count": 10},
    sections=[Section(block_id=0, type="paragraph", content="Recovered by OCR. " * 40)],
    stats={},
)
ocrd.decide_verdict()
check("degraded: OCR output is degraded, not ok", ocrd.verdict == "degraded", ocrd.verdict)
check(
    "degraded: OCR reason asks for a spot-check",
    any("OCR" in r for r in ocrd.verdict_reasons),
    str(ocrd.verdict_reasons),
)

# ── 6. degraded: cleaning gutted the document ─────────────────────────────
# If cleaning removed most of the blocks, the source may be mostly boilerplate
# that the pipeline misread as content.
gutted = ParsingResult(
    source_id="gut_1",
    format_type="html",
    meta={},
    sections=[Section(block_id=0, type="paragraph", content="Survivor.")],
    stats={"cleaning": {"blocks_before": 100, "blocks_after": 5, "blocks_removed": 95}},
)
gutted.decide_verdict()
check("degraded: heavy cleaning removal is flagged", gutted.verdict == "degraded", gutted.verdict)

# Light cleaning must NOT be flagged
light = ParsingResult(
    source_id="light_1",
    format_type="md",
    meta={},
    sections=[Section(block_id=0, type="paragraph", content="Survivor.")],
    stats={"cleaning": {"blocks_before": 100, "blocks_after": 92, "blocks_removed": 8}},
)
light.decide_verdict()
check("ok: light cleaning is not flagged", light.verdict == "ok", f"{light.verdict} {light.verdict_reasons}")

# ── 7. Thresholds behave at the boundary ──────────────────────────────────
at_limit = ParsingResult(
    source_id="lim_1",
    format_type="pdf",
    meta={"page_count": 10},
    sections=[Section(block_id=0, type="paragraph", content="x" * (MIN_CHARS_PER_PAGE * 10))],
    stats={},
)
at_limit.decide_verdict()
check("boundary: exactly MIN_CHARS_PER_PAGE is accepted", at_limit.verdict == "ok", at_limit.verdict)

just_under = ParsingResult(
    source_id="lim_2",
    format_type="pdf",
    meta={"page_count": 10},
    sections=[Section(block_id=0, type="paragraph", content="x" * (MIN_CHARS_PER_PAGE * 10 - 1))],
    stats={},
)
just_under.decide_verdict()
check("boundary: just under MIN_CHARS_PER_PAGE is rejected", just_under.verdict == "unusable")

# ── 8. Verdict survives serialization ─────────────────────────────────────
d = scanned_shape.to_dict()
check("serialization: verdict is in to_dict", d.get("verdict") == "unusable", str(d.get("verdict")))
check(
    "serialization: verdict_reasons is in to_dict",
    isinstance(d.get("verdict_reasons"), list) and d["verdict_reasons"],
)
check("validate: no complaints about a valid verdict", not scanned_shape.validate(), str(scanned_shape.validate()))

bad_verdict = ParsingResult(source_id="x", format_type="md", sections=[])
bad_verdict.verdict = "excellent"
check(
    "validate: unknown verdict is reported",
    any("verdict" in p for p in bad_verdict.validate()),
    str(bad_verdict.validate()),
)

# ── 9. The CLI surfaces it (this is what the orchestrator reads) ──────────
out = tmp / "cli_check.json"
proc = subprocess.run(
    [
        sys.executable,
        "-c",
        "import sys; sys.path.insert(0,'.'); from ragcli.cli import main; main()",
        "parse", "-f", str(good), "-o", str(out),
    ],
    cwd=ROOT, capture_output=True, text=True,
)
check("cli: parse succeeds", proc.returncode == 0, proc.stderr[-200:])
if out.exists():
    payload = json.loads(out.read_text(encoding="utf-8"))
    check("cli: output carries verdict", "verdict" in payload, str(sorted(payload)))
    check("cli: verdict is a known value", payload.get("verdict") in {"ok", "degraded", "unusable"})
    check("cli: output carries verdict_reasons", isinstance(payload.get("verdict_reasons"), list))
    check("cli: stderr shows the verdict", "ok" in proc.stderr, proc.stderr[-200:])

# ── report ────────────────────────────────────────────────────────────────
width = max(len(n) for _, n, _ in results)
passed = sum(1 for ok, _, _ in results if ok)
print()
for ok, name, detail in results:
    mark = "OK  " if ok else "FAIL"
    line = f"[{mark}] {name.ljust(width)}"
    if not ok and detail:
        line += f"  <- {detail}"
    print(line)
print(f"\n{passed}/{len(results)} verdict checks passed")
sys.exit(0 if passed == len(results) else 1)
