"""Verify the refactored structure is internally consistent.

Checks the things that were actually wrong before the refactor, so a regression
is caught rather than rediscovered:

1. Exactly 3 pipeline stages, with embed/index in `ingest` (write path).
2. Every agent-facing doc exists in `docs/agents/`, one per worker.
3. No agent doc references a design doc (would pollute agent context).
4. The orchestrator does not name any worker manual.
5. Every tool on disk is registered and vice versa.

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

# ── 1. Stage layout ───────────────────────────────────────────────────────
check("exactly 3 stages", len(STAGES) == 3, f"found {list(STAGES)}")
check("stages are ingest/retrieve/evaluate",
      set(STAGES) == {"ingest", "retrieve", "evaluate"}, str(list(STAGES)))
check("embed is in ingest (write path)",
      ALL_TOOLS["embed"]["stage"] == "ingest", ALL_TOOLS["embed"]["stage"])
check("index is in ingest (write path)",
      ALL_TOOLS["index"]["stage"] == "ingest", ALL_TOOLS["index"]["stage"])
check("search is in retrieve (read path)",
      ALL_TOOLS["search"]["stage"] == "retrieve", ALL_TOOLS["search"]["stage"])

# ── 2. Agent docs exist, one per worker ───────────────────────────────────
REQUIRED_AGENT_DOCS = [
    "orchestrator.md",
    "parse-worker.md", "parse-worker.zh.md",
    "chunk-worker.md", "chunk-worker.zh.md",
    "summarize-worker.md", "summarize-worker.zh.md",
    "tagger-worker.md", "tagger-worker.zh.md",
]
for name in REQUIRED_AGENT_DOCS:
    p = AGENTS / name
    check(f"agent doc exists: {name}", p.is_file(), str(p))

# ── 3. Design docs live in design/, not agents/ ───────────────────────────
for name in ["architecture.md", "pipeline-dependencies.md"]:
    check(f"design doc in design/: {name}", (DOCS / "design" / name).is_file())

# ── 4. No agent doc links into design/ (context pollution) ────────────────
polluted = []
for p in AGENTS.glob("*.md"):
    text = p.read_text(encoding="utf-8")
    if "design/" in text:
        polluted.append(p.name)
check("no agent doc links to design/", not polluted, f"polluted: {polluted}")

# ── 5. Orchestrator must not name any worker manual ───────────────────────
orch = (AGENTS / "orchestrator.md").read_text(encoding="utf-8")
# It may name them only in the explicit prohibition sentences
manual_names = ["chunk-worker.md", "tagger-worker.md", "summarize-worker.md", "parse-worker.md"]
offending = []
for line in orch.split("\n"):
    if any(n in line for n in manual_names):
        # allowed only when the line says not to read them
        if "never read" in line.lower() or "不读" in line:
            continue
        offending.append(line.strip()[:80])
check("orchestrator only mentions worker manuals to forbid reading them",
      not offending, str(offending)[:160])

# ── 6. Registry matches modules on disk ───────────────────────────────────
import ragcli.tools as pkg  # noqa: E402

disk = {p.stem for p in Path(pkg.__file__).parent.glob("*.py") if p.stem != "__init__"}
registered = set(ALL_TOOLS)
check("every tool module is registered", not (disk - registered),
      f"unregistered: {sorted(disk - registered)}")
check("every registered tool has a module", not (registered - disk),
      f"missing module: {sorted(registered - disk)}")

# ── 7. Generated CLI reference is current ─────────────────────────────────
cli_ref = DOCS / "reference" / "cli.md"
check("cli reference exists", cli_ref.is_file())
if cli_ref.is_file():
    ref_text = cli_ref.read_text(encoding="utf-8")
    missing = [n for n in ALL_TOOLS if f"### {n}" not in ref_text]
    check("cli reference documents every tool", not missing, f"missing: {missing}")
    check("cli reference is marked generated",
          "Generated from the live CLI" in ref_text)

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
