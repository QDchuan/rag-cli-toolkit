"""Verify the project structure is internally consistent.

Asserts the properties that earlier revisions violated, so a regression is
caught rather than rediscovered:

1. Exactly one tool exists (`parse`) — the rest were removed as legacy.
2. The three declared stages exist, with only `ingest` populated.
3. Every agent-facing doc exists, one per worker.
4. No agent doc links into `design/` (context pollution).
5. The orchestrator names worker manuals only to forbid reading them.
6. Registry and modules on disk agree.
7. The generated CLI reference is current and covers every tool.

Run: python tests/check_structure.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ragcli.registry import ALL_TOOLS, STAGES  # noqa: E402

results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))


DOCS = ROOT / "docs"
AGENTS = DOCS / "agents"

# ── 1. Exactly one tool, and it is parse ──────────────────────────────────
check("exactly one tool exists", len(ALL_TOOLS) == 1, f"found {sorted(ALL_TOOLS)}")
check("the tool is parse", set(ALL_TOOLS) == {"parse"}, str(sorted(ALL_TOOLS)))

# ── 2. Stages ─────────────────────────────────────────────────────────────
check("three stages declared", len(STAGES) == 3, str(list(STAGES)))
check(
    "stages are ingest/retrieve/evaluate",
    set(STAGES) == {"ingest", "retrieve", "evaluate"},
    str(list(STAGES)),
)
check("parse is in the ingest stage", ALL_TOOLS["parse"]["stage"] == "ingest")
check(
    "retrieve stage is empty (read path not built)",
    not [n for n, t in ALL_TOOLS.items() if t["stage"] == "retrieve"],
)

# ── 3. Agent docs ─────────────────────────────────────────────────────────
# The manuals are Chinese-only by decision. English copies were removed because
# maintaining two versions of every manual is how the Chinese ones drifted onto
# a stale mental model once already — the copy silently stopped matching.
REQUIRED_AGENT_DOCS = [
    "parse-worker.zh.md",
    "chunk-worker.zh.md",
    "summarize-worker.zh.md",
    "tagger-worker.zh.md",
]
for name in REQUIRED_AGENT_DOCS:
    check(f"agent doc exists: {name}", (AGENTS / name).is_file())

# The orchestrator is no longer a manual: the workflow script IS the
# orchestrator, and it lives in workflows/ where it can actually be executed.
check("orchestrator manual is gone (the workflow script replaces it)",
      not (AGENTS / "orchestrator.md").exists())

for name in ["architecture.md", "pipeline-dependencies.md", "workflow.md"]:
    check(f"design doc in design/: {name}", (DOCS / "design" / name).is_file())

# ── 4. No agent doc links into design/ ────────────────────────────────────
polluted = [p.name for p in AGENTS.glob("*.md") if "design/" in p.read_text(encoding="utf-8")]
check("no agent doc links to design/", not polluted, f"polluted: {polluted}")

# ── 5. English manuals stay deleted ───────────────────────────────────────
ENGLISH_MANUALS = [
    "orchestrator.md",
    "parse-worker.md",
    "chunk-worker.md",
    "summarize-worker.md",
    "tagger-worker.md",
]
lingering = [n for n in ENGLISH_MANUALS if (AGENTS / n).exists()]
check("English manuals stay deleted", not lingering, f"present: {lingering}")


# ── 5b. The workflow script points at manuals that exist ──────────────────
# This is the functional half of the same decision: deleting the English files
# without repointing the script would break every run at prompt-build time.
workflow_script = ROOT / "workflows" / "clean-corpus.js"
check("workflow script exists", workflow_script.is_file())
if workflow_script.is_file():
    script_text = workflow_script.read_text(encoding="utf-8")
    referenced = re.findall(r'manual\("([^"]+)"\)', script_text)
    check("workflow script references at least one manual", len(referenced) > 0, str(referenced))
    for ref in referenced:
        check(f"workflow script's manual exists: {ref}", (AGENTS / ref).is_file())
    stale_refs = [m for m in ENGLISH_MANUALS if f'"{m}"' in script_text]
    check("workflow script has no stale manual references", not stale_refs, str(stale_refs))

# ── 6. Registry matches modules on disk ───────────────────────────────────
import ragcli.tools as pkg  # noqa: E402

disk = {p.stem for p in Path(pkg.__file__).parent.glob("*.py") if p.stem != "__init__"}
check("no orphan tool modules on disk", not (disk - set(ALL_TOOLS)),
      f"unregistered: {sorted(disk - set(ALL_TOOLS))}")
check("no registered tool missing a module", not (set(ALL_TOOLS) - disk),
      f"missing: {sorted(set(ALL_TOOLS) - disk)}")

# ── 7. Deleted tools stay deleted ─────────────────────────────────────────
REMOVED = [
    "chunk", "tagger", "summarize", "embed", "index",
    "graph", "search", "hybrid", "rerank", "cache", "evaluate",
]
lingering = [t for t in REMOVED if (Path(pkg.__file__).parent / f"{t}.py").exists()]
check("legacy tool modules are gone", not lingering, f"still present: {lingering}")

# ── 8. CLI reference ──────────────────────────────────────────────────────
cli_ref = DOCS / "reference" / "cli.md"
check("cli reference exists", cli_ref.is_file())
if cli_ref.is_file():
    ref = cli_ref.read_text(encoding="utf-8")
    check("cli reference covers every tool", all(f"### {n}" in ref for n in ALL_TOOLS))
    check("cli reference is marked generated", "Generated from the live CLI" in ref)
    stale = [t for t in REMOVED if f"ragcli {t} " in ref]
    check("cli reference documents no removed tool", not stale, f"stale: {stale}")

# ── 9. Field names in the manuals match what the code emits ───────────────
# Three worker manuals previously used three different names for the same
# concepts, and none matched `parse`. That broke the two-tier path silently:
# a document could be selected by its summary and then be unreachable by chunk
# search, with every individual artifact looking valid.
from ragcli.parsers.base import ParsingResult, Section  # noqa: E402

EMITTED = set(Section.__dataclass_fields__) | set(ParsingResult.__dataclass_fields__)
# Container/aggregate names the manuals legitimately use
CONTRACT_NAMES = {
    "chunks", "tags", "summary", "summaries", "topics", "title",
    "chunk_contract", "summary_contract", "manifest_version",
    "verdict", "verdict_reasons", "decision", "warnings",
    "is_full_document", "parent_id", "sibling_offsets", "oversized_atomic",
    "token_counter", "doc_type", "language", "page_count", "model_used",
    "temperature", "budget_version", "chunk_count", "processed_at", "status",
    "text", "raw_text", "token_count", "chunk_id", "chunk_index",
    "summary_type", "summary_text", "summary_raw", "strategy", "strategy_used",
    "params", "special_cases", "token_bounds", "overlap_ratio",
}
CANONICAL = EMITTED | CONTRACT_NAMES

#: Names that earlier revisions used and that must not come back.
RETIRED_FIELDS = ["doc_id", "header_path", "summarized.json", "tagged.json"]

manual_dir = DOCS / "agents"
field_regressions: list[str] = []
for manual in sorted(manual_dir.glob("*.md")):
    for lineno, line in enumerate(manual.read_text(encoding="utf-8").split("\n"), 1):
        for retired in RETIRED_FIELDS:
            if f'"{retired}"' in line or f"`{retired}`" in line:
                field_regressions.append(f"{manual.name}:{lineno} uses retired '{retired}'")

check(
    "manuals contain no retired field names",
    not field_regressions,
    "; ".join(field_regressions[:3]),
)

# ── 10. The coarse tier is described as retrieval, not as context ─────────
summ = (manual_dir / "summarize-worker.zh.md").read_text(encoding="utf-8")
check(
    "summarize manual no longer claims summaries are unused for retrieval",
    "不参与向量检索" not in summ,
    "the old (wrong) claim is still present",
)
check(
    "summarize manual describes the coarse tier",
    "粗层" in summ and "目录" in summ,
    "missing the two-tier framing",
)
check(
    "summarize manual covers every document (no skip case)",
    "没有例外" in summ or "每份文档" in summ,
    "the 'skip short documents' rule may have returned",
)

chunk = (manual_dir / "chunk-worker.zh.md").read_text(encoding="utf-8")
check(
    "chunk manual states the source_id linkage rule",
    "source_id" in chunk and ("原样" in chunk or "verbatim" in chunk.lower()),
    "missing the source_id linkage requirement",
)
check(
    "chunk manual says heading_path is an array",
    "数组" in chunk,
    "missing the heading_path type rule",
)
check(
    "chunk manual requires stating a decision",
    "stats.decision" in chunk or "decision" in chunk,
    "missing the reproducibility requirement",
)

# ── 11. The workflow doc exists and holds the two-tier design ─────────────
wf = DOCS / "design" / "workflow.md"
check("workflow doc exists", wf.is_file())
if wf.is_file():
    wtext = wf.read_text(encoding="utf-8")
    check("workflow doc names both tiers", "粗层" in wtext and "细层" in wtext)
    check("workflow doc records the summary budget arithmetic", "摘要长度" in wtext)
    check("workflow doc defines the verdict states",
          all(v in wtext for v in ("ok", "degraded", "unusable")))

# ── 12. Chinese copies stay structurally in sync with their originals ─────
# The .zh.md copies exist so a human can review what the agent was told. When
# they drift they are worse than useless: the reviewer reads a stale mental
# model and approves it. This check makes drift detectable at the heading level.
_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")


def headings(path: Path) -> list[int]:
    """Return heading levels (1-6), skipping anything inside a fenced code block.

    Fence-awareness matters: these manuals are full of Python and JSON examples
    where `#` starts a comment. A naive `^#` grep counts those as headings and
    reports a phantom mismatch between the English and Chinese files.
    """
    levels: list[int] = []
    in_fence = False
    fence_marker = ""
    for line in path.read_text(encoding="utf-8").split("\n"):
        stripped = line.lstrip()
        if not in_fence and (stripped.startswith("```") or stripped.startswith("~~~")):
            in_fence = True
            fence_marker = stripped[:3]
            continue
        if in_fence:
            if stripped.startswith(fence_marker):
                in_fence = False
            continue
        m = _HEADING_RE.match(line)
        if m:
            levels.append(len(m.group(1)))
    return levels


sync_problems: list[str] = []
for en in sorted(AGENTS.glob("*.md")):
    if en.name.endswith(".zh.md"):
        continue
    zh = en.with_name(en.stem + ".zh.md")
    if not zh.exists():
        continue
    en_lv, zh_lv = headings(en), headings(zh)
    if en_lv != zh_lv:
        sync_problems.append(
            f"{zh.name}: levels {zh_lv} != {en.name} {en_lv}"
        )

check(
    "Chinese copies match their originals' heading structure",
    not sync_problems,
    "; ".join(sync_problems[:2]),
)

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
print(f"\n{passed}/{len(results)} structure checks passed")
sys.exit(0 if passed == len(results) else 1)
