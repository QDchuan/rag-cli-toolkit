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
REQUIRED_AGENT_DOCS = [
    "orchestrator.md",
    "parse-worker.md", "parse-worker.zh.md",
    "chunk-worker.md", "chunk-worker.zh.md",
    "summarize-worker.md", "summarize-worker.zh.md",
    "tagger-worker.md", "tagger-worker.zh.md",
]
for name in REQUIRED_AGENT_DOCS:
    check(f"agent doc exists: {name}", (AGENTS / name).is_file())

for name in ["architecture.md", "pipeline-dependencies.md"]:
    check(f"design doc in design/: {name}", (DOCS / "design" / name).is_file())

# ── 4. No agent doc links into design/ ────────────────────────────────────
polluted = [p.name for p in AGENTS.glob("*.md") if "design/" in p.read_text(encoding="utf-8")]
check("no agent doc links to design/", not polluted, f"polluted: {polluted}")

# ── 5. Orchestrator names worker manuals only to forbid them ──────────────
orch = (AGENTS / "orchestrator.md").read_text(encoding="utf-8")
manual_names = ["chunk-worker.md", "tagger-worker.md", "summarize-worker.md", "parse-worker.md"]
offending = []
for line in orch.split("\n"):
    if any(n in line for n in manual_names):
        low = line.lower()
        if "never read" in low or "不读" in line:
            continue
        offending.append(line.strip()[:80])
check(
    "orchestrator mentions worker manuals only to forbid reading them",
    not offending,
    str(offending)[:160],
)

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
