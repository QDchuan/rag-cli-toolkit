"""Verify a completed run: both query paths must work, and the tiers must connect.

This is the acceptance criterion from docs/design/workflow.md §10:

    broad     -> scan catalog.jsonl only -> select the right document
    specific  -> search chunks           -> find the right passage

A document missing from either tier is lost, and lost silently. So the checks
below are about connectivity and traceability, not about answer quality.

Usage: python tests/verify_run.py <run_root> [question...]
"""

import json
import sys
from pathlib import Path

if len(sys.argv) < 2:
    print("usage: verify_run.py <run_root> [question ...]")
    sys.exit(2)

run_root = Path(sys.argv[1])
questions = sys.argv[2:]

checks: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((ok, name, detail))


catalog_path = run_root / "catalog.jsonl"
queue_path = run_root / "review_queue.jsonl"

check("catalog.jsonl exists", catalog_path.is_file(), str(catalog_path))
catalog = [
    json.loads(l) for l in catalog_path.read_text(encoding="utf-8").splitlines() if l.strip()
]
review = (
    [json.loads(l) for l in queue_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if queue_path.is_file()
    else []
)

# ── catalogue shape ───────────────────────────────────────────────────────
check("catalogue is non-empty", len(catalog) > 0, f"{len(catalog)} entries")
for e in catalog:
    missing = [k for k in ("source_id", "title", "summary", "topics", "status") if not e.get(k)]
    check(f"catalogue entry {e.get('source_id','?')} is complete", not missing, str(missing))

check(
    "every summary is non-empty",
    all(e["summary"].strip() for e in catalog),
    "an empty summary makes a document unfindable",
)

# ── collect chunks from every run directory ───────────────────────────────
chunks_by_doc: dict[str, list[dict]] = {}
chunk_problems: list[str] = []

for d in sorted(p for p in run_root.iterdir() if p.is_dir()):
    cpath = d / "chunks.json"
    spath = d / "doc-summary.json"
    if not cpath.is_file():
        continue
    art = json.loads(cpath.read_text(encoding="utf-8"))
    sid = art.get("source_id", d.name)
    check(f"{sid}: chunk artifact declares a contract version", bool(art.get("chunk_contract")))
    check(f"{sid}: chunk artifact has a decision recorded", bool((art.get("stats") or {}).get("decision")))
    chunks = art.get("chunks", [])
    check(f"{sid}: has at least one chunk", len(chunks) > 0)
    chunks_by_doc[sid] = chunks

    if spath.is_file():
        s = json.loads(spath.read_text(encoding="utf-8"))
        check(f"{sid}: summary source_id matches", s.get("source_id") == sid, str(s.get("source_id")))

    for c in chunks:
        # The per-chunk source_id is what keeps a POOLED index traceable: once
        # all documents' chunks share one vector store, the artifact-level field
        # is gone and a chunk that cannot name its source cannot be traced back.
        if c.get("source_id") != sid:
            chunk_problems.append(f"{c.get('chunk_id')} -> {c.get('source_id')!r} != {sid!r}")
        if not c.get("chunk_id"):
            chunk_problems.append(f"{sid}: a chunk has no chunk_id")
        if not isinstance(c.get("heading_path"), list):
            chunk_problems.append(f"{c.get('chunk_id')}: heading_path is not a list")

check("every chunk names its own source_id", not chunk_problems, "; ".join(chunk_problems[:3]))

# ── ACCEPTANCE 1: the tiers connect ───────────────────────────────────────
catalog_ids = {e["source_id"] for e in catalog}
chunk_ids = set(chunks_by_doc)

check(
    "every catalogued document has chunks",
    catalog_ids <= chunk_ids,
    f"catalogued but unreachable: {sorted(catalog_ids - chunk_ids)}",
)
check(
    "every chunked document is catalogued",
    chunk_ids <= catalog_ids,
    f"chunked but invisible: {sorted(chunk_ids - catalog_ids)}",
)

# A document must never appear in both the catalogue and the review queue.
queued_ids = {e["source_id"] for e in review}
check("catalogue and review queue are disjoint", not (catalog_ids & queued_ids), str(catalog_ids & queued_ids))
check("review queue entries carry a reason", all(e.get("reason") for e in review))

# ── ACCEPTANCE 2: the broad path ──────────────────────────────────────────
def broad_pick(question: str) -> list[str]:
    """What an agent can decide from catalog.jsonl alone — no chunks opened."""
    terms = {w.lower().strip("?.,") for w in question.split() if len(w) > 3}
    scored = []
    for e in catalog:
        hay = f"{e['title']} {e['summary']} {' '.join(e['topics'])}".lower()
        scored.append((sum(1 for t in terms if t in hay), e))
    scored.sort(key=lambda x: -x[0])
    return [e["source_id"] for score, e in scored if score > 0]


# ── ACCEPTANCE 3: the specific path ───────────────────────────────────────
def specific_search(question: str) -> list[dict]:
    terms = {w.lower().strip("?.,") for w in question.split() if len(w) > 3}
    hits = []
    for sid, chunks in chunks_by_doc.items():
        for c in chunks:
            score = sum(1 for t in terms if t in c["text"].lower())
            if score:
                hits.append((score, sid, c))
    hits.sort(key=lambda x: -x[0])
    return [{"source_id": sid, "chunk": c} for _, sid, c in hits]


if questions:
    for q in questions:
        picks = broad_pick(q)
        hits = specific_search(q)
        print(f"\nQ: {q}")
        print(f"   broad  -> catalogue picks: {picks or '(none)'}")
        print(f"   specific -> top chunk in: {hits[0]['source_id'] if hits else '(none)'}")
        check(f"broad path returns something for {q!r}", bool(picks), "catalogue scan found nothing")
        if hits:
            top = hits[0]["chunk"]
            check(
                f"top chunk for {q!r} carries traceable provenance",
                bool(top.get("chunk_id")) and bool(top.get("source_id")),
            )

# ── report ────────────────────────────────────────────────────────────────
width = max(len(n) for _, n, _ in checks)
passed = sum(1 for ok, _, _ in checks if ok)
print()
for ok, name, detail in checks:
    mark = "OK  " if ok else "FAIL"
    line = f"[{mark}] {name.ljust(width)}"
    if not ok and detail:
        line += f"  <- {detail}"
    print(line)
print(
    f"\n{passed}/{len(checks)} checks passed | "
    f"{len(catalog)} catalogued, {len(review)} in review queue, "
    f"{sum(len(v) for v in chunks_by_doc.values())} chunks"
)
sys.exit(0 if passed == len(checks) else 1)
