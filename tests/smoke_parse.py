"""Smoke test for the parse module — exercises real files and the CLI contract.

Run:  python tests/smoke_parse.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ragcli.cleaners import clean_result
from ragcli.parsers import SUPPORTED_EXTENSIONS, route
from ragcli.parsers.base import MissingDependencyError, ParseError

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name: str, condition: bool, detail: str = ""):
    results.append((PASS if condition else FAIL, name, detail))


def summarize(result) -> dict:
    d = result.to_dict()
    types: dict[str, int] = {}
    for s in d["sections"]:
        types[s["type"]] = types.get(s["type"], 0) + 1
    return {
        "format": d["format_type"],
        "sections": len(d["sections"]),
        "engine": d["stats"].get("parser_engine_used"),
        "types": types,
        "warnings": len(d["stats"].get("warnings", [])),
        "problems": result.validate(),
    }


tmp = Path(tempfile.mkdtemp(prefix="ragcli_parse_"))

# ── 1. Markdown with headings, a table and a fenced code block ─────────────
md_file = tmp / "guide.md"
md_file.write_text(
    "# Chapter 1\n\n"
    "Intro paragraph before any subsection.\n\n"
    "## 1.1 Overview\n\n"
    "Some **body** text here.\n\n"
    "| Col A | Col B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n\n"
    "### 1.1.1 Detail\n\n"
    "```python\nprint('hello')\n```\n\n"
    "# Chapter 2\n\n"
    "Second chapter text.\n",
    encoding="utf-8",
)
r = route(str(md_file))
info = summarize(r)
check("md: parses without error", info["sections"] > 0, str(info))
check("md: heading detected", info["types"].get("heading", 0) >= 4, str(info["types"]))
check("md: table detected", info["types"].get("table", 0) == 1, str(info["types"]))
check("md: code block detected", info["types"].get("code_block", 0) == 1, str(info["types"]))
check("md: contract valid", not info["problems"], str(info["problems"]))

# heading_path breadcrumb: the Detail section must know its ancestors
detail = [s for s in r.sections if "print" in s.content]
check(
    "md: heading_path tracks ancestors",
    bool(detail) and detail[0].heading_path[:2] == ["Chapter 1", "1.1 Overview"],
    str(detail[0].heading_path) if detail else "not found",
)

# ── 2. CSV → Markdown table ───────────────────────────────────────────────
csv_file = tmp / "data.csv"
csv_file.write_text(
    "name,role,dept\nAlice,Engineer,Platform\nBob,Designer,Product\n", encoding="utf-8"
)
r_csv = route(str(csv_file))
info = summarize(r_csv)
check("csv: one table section", info["types"].get("table", 0) == 1, str(info["types"]))
check("csv: header in output", "| name | role | dept |" in r_csv.sections[0].content)
check("csv: row count metadata", r_csv.sections[0].metadata.get("rows") == 2)

# wide CSV must transpose instead of producing an unusable 20-column table
wide = tmp / "wide.csv"
wide.write_text(
    ",".join(f"c{i}" for i in range(20)) + "\n" + ",".join(str(i) for i in range(20)) + "\n",
    encoding="utf-8",
)
r_wide = route(str(wide))
check(
    "csv: wide table transposed",
    any(s.type == "paragraph" and "Record 1" in s.content for s in r_wide.sections),
    str([s.type for s in r_wide.sections]),
)

# ── 3. JSON flattening ────────────────────────────────────────────────────
json_file = tmp / "config.json"
json_file.write_text(
    json.dumps({"db": {"host": "localhost", "port": 5432}, "tags": ["a", "b"]}),
    encoding="utf-8",
)
r_json = route(str(json_file))
flat = r_json.sections[0].content
check("json: nested path flattened", "db.host: localhost" in flat, flat[:200])
check("json: array indexed", "tags[0]: a" in flat, flat[:200])

# ── 4. Plain text ─────────────────────────────────────────────────────────
txt = tmp / "notes.txt"
txt.write_text("Para one.\n\nPara two.\n\n\n\nPara three.", encoding="utf-8")
r_txt = route(str(txt))
check("txt: paragraphs split", len(r_txt.sections) == 3, str(len(r_txt.sections)))

# ── 5. Cleaning pipeline ──────────────────────────────────────────────────
dirty = tmp / "dirty.md"
dirty.write_text(
    "# Report\n\n"
    "Page 1 of 9\n\n"
    "CONFIDENTIAL\n\n"
    "retrie-\nval systems matter.\n\n"
    "www.example.com\n\n"
    "42\n\n"
    "The quick brown fox jumps over the lazy dog and keeps running for a while.\n",
    encoding="utf-8",
)
r_dirty = route(str(dirty))
raw_count = len(r_dirty.sections)
cleaned = clean_result(r_dirty, {"min_chars": 10})
body = " ".join(s.content for s in cleaned.sections)
check("clean: removed page artifact", "Page 1 of 9" not in body, body[:200])
check("clean: removed watermark", "CONFIDENTIAL" not in body, body[:200])
check("clean: removed bare URL", "www.example.com" not in body, body[:200])
check("clean: removed orphan page number", "\n42\n" not in body, body[:200])
check("clean: dehyphenated", "retrieval systems" in body, body[:200])
check(
    "clean: stats recorded",
    "cleaning" in cleaned.stats and cleaned.stats["cleaning"]["blocks_removed"] > 0,
    str(cleaned.stats.get("cleaning")),
)

# ── 6. Corrupt file of a supported type must raise a TYPED error ──────────
# This is the contract that keeps batch ingestion safe: one bad file yields a
# ParseError the loop can record and skip, never a raw engine exception.
for label, fname, payload in [
    ("pptx", "deck.pptx", b"PK\x03\x04not-a-real-pptx"),
    ("docx", "doc.docx", b"PK\x03\x04not-a-real-docx"),
    ("xlsx", "book.xlsx", b"PK\x03\x04not-a-real-xlsx"),
    ("pdf", "scan.pdf", b"%PDF-1.4 garbage but not parseable"),
]:
    bad = tmp / fname
    bad.write_bytes(payload)
    try:
        route(str(bad))
        check(f"corrupt {label}: raises typed error", False, "no exception raised")
    except ParseError as e:
        check(f"corrupt {label}: raises ParseError", True, str(e)[:60])
    except Exception as e:  # noqa: BLE001
        check(
            f"corrupt {label}: raises ParseError",
            False,
            f"leaked {type(e).__name__}: {e}",
        )

# ── 7. Unknown extension is rejected with a helpful message ───────────────
unknown = tmp / "archive.xyz"
unknown.write_text("nope", encoding="utf-8")
try:
    route(str(unknown))
    check("unknown ext: rejected", False, "no exception")
except ParseError as e:
    check("unknown ext: helpful error", "Supported extensions" in str(e), str(e)[:120])

# ── 8. Contract stability across every source type parsed above ───────────
required_keys = {
    "source_id", "source_path", "format_type", "meta", "sections", "stats",
    "verdict", "verdict_reasons",
}
all_ok = True
for res in (r, r_csv, r_json, r_txt, r_wide):
    if set(res.to_dict().keys()) != required_keys:
        all_ok = False
check("contract: identical top-level keys for all formats", all_ok)

# ── 9. Registry breadth ───────────────────────────────────────────────────
exts = SUPPORTED_EXTENSIONS()
check("registry: covers office + pdf + web + image",
      all(e in exts for e in ["docx", "xlsx", "pptx", "pdf", "html", "png", "md", "csv"]),
      f"{len(exts)} extensions")

# ── report ────────────────────────────────────────────────────────────────
width = max(len(n) for _, n, _ in results)
passed = sum(1 for s, _, _ in results if s == PASS)
print()
for status, name, detail in results:
    mark = "OK  " if status == PASS else "FAIL"
    line = f"[{mark}] {name.ljust(width)}"
    if status == FAIL and detail:
        line += f"  <- {detail}"
    print(line)
print(f"\n{passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
