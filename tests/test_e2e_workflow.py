"""End-to-end acceptance test for the workflow described in docs/design/workflow.md.

What this verifies — and what it deliberately does not:

    The workers are STUBS. Chunk Worker and Summarize Worker are supposed to be
    LLM agents writing bespoke code; running them here would make the test
    non-deterministic and slow. The stubs emit contract-shaped artifacts so this
    test can verify the thing that actually breaks in practice: **connectivity**.
    Does a document selected by its summary remain reachable by chunk search?
    Are the field names consistent? Does an unusable parse get stopped?

    What it does NOT verify: summary quality, chunk boundary quality. Those are
    judgement calls no test can make.

The acceptance criterion from the workflow doc is that BOTH query paths work:

    broad     -> scan catalog.jsonl only -> select the right document
    specific  -> search chunks          -> find the right passage

If either path fails, a document is lost — and lost silently.

Run: python tests/test_e2e_workflow.py
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, "-c",
            "import sys; sys.path.insert(0,'.'); from ragcli.cli import main; main()",
            *args,
        ],
        cwd=ROOT, capture_output=True, text=True,
    )


work = Path(tempfile.mkdtemp(prefix="ragcli_e2e_"))
corpus = work / "raw"
corpus.mkdir()
runs = work / "runs"
runs.mkdir()

# ── build a small heterogeneous corpus ────────────────────────────────────
(corpus / "supplier-report.md").write_text(
    "# 2024 Supplier Management Report\n\n"
    "Supplier admission standards were revised this year to add payment-term "
    "risk clauses. Twelve suppliers were audited and three were downgraded.\n\n"
    "## Admission Standards\n\n"
    "New applicants must demonstrate twelve months of settled accounts. "
    "Payment terms beyond net-60 require sign-off from the finance committee.\n\n"
    "## Audit Results\n\n"
    "Twelve suppliers audited; three downgraded for late settlement.\n",
    encoding="utf-8",
)
(corpus / "oauth-guide.md").write_text(
    "# OAuth 2.0 Integration Guide\n\n"
    "This guide covers authorization flows for our public API.\n\n"
    "## Authorization Code Flow\n\n"
    "You must include the state parameter and verify it on return.\n\n"
    "## Token Expiry\n\n"
    "Access tokens expire after 3600 seconds by default. Refresh tokens last 30 days.\n",
    encoding="utf-8",
)
(corpus / "metrics.csv").write_text(
    "quarter,revenue,headcount\nQ1,1200,45\nQ2,1350,47\n", encoding="utf-8"
)
# An empty file must be stopped by the quality gate, not flow through
(corpus / "blank.md").write_text("   \n\n  \n", encoding="utf-8")

# ── stage 2: parse the whole corpus ───────────────────────────────────────
parsed_path = work / "parsed.jsonl"
proc = run_cli("parse", "-d", str(corpus), "-o", str(parsed_path))
check("parse: batch succeeds", proc.returncode == 0, proc.stderr[-300:])

parsed = [json.loads(l) for l in parsed_path.read_text(encoding="utf-8").splitlines() if l.strip()]
check("parse: all four sources produced output", len(parsed) == 4, f"got {len(parsed)}")

by_id = {d["source_id"]: d for d in parsed}
by_name = {Path(d["source_path"]).name: d for d in parsed}
check(
    "parse: every document carries a verdict",
    all(d.get("verdict") in {"ok", "degraded", "unusable"} for d in parsed),
    str([d.get("verdict") for d in parsed]),
)

# ── stage 3: quality gate — the silent-failure catch ──────────────────────
blank = by_name.get("blank.md")
check(
    "gate: the empty document is unusable",
    blank is not None and blank["verdict"] == "unusable",
    str(blank.get("verdict") if blank else "missing"),
)
check(
    "gate: unusable carries an explanatory reason",
    blank is not None and bool(blank.get("verdict_reasons")),
    str(blank.get("verdict_reasons") if blank else ""),
)
for name in ("supplier-report.md", "oauth-guide.md", "metrics.csv"):
    doc = by_name.get(name)
    check(f"gate: {name} is usable", doc is not None and doc["verdict"] in {"ok", "degraded"},
          str(doc.get("verdict") if doc else "missing"))

# The gate's whole purpose: unusable documents must not reach the workers
deliverable = [d for d in parsed if d["verdict"] != "unusable"]
check("gate: unusable is excluded from downstream", len(deliverable) == 3, f"got {len(deliverable)}")


# ── STUB WORKERS (deterministic stand-ins for the LLM agents) ─────────────
# These emit contract-shaped artifacts so connectivity can be tested without
# an LLM. They are intentionally simple; the real workers write bespoke code.


def stub_chunk(doc: dict) -> dict:
    """Split at heading boundaries. Deterministic."""
    chunks: list[dict] = []
    heading = ""
    body: list[str] = []

    def flush():
        if body:
            text = "\n".join(body).strip()
            if text:
                cid = f"{doc['source_id']}::{len(chunks)}::" + hashlib.sha1(
                    text[:80].encode("utf-8")
                ).hexdigest()[:8]
                chunks.append({
                    "chunk_id": cid,
                    "source_id": doc["source_id"],   # per-chunk, so a pooled index stays traceable
                    "chunk_index": len(chunks),
                    "text": (f"{heading}\n\n{text}" if heading else text),
                    "raw_text": text,
                    "heading_path": [heading] if heading else [],
                    "location": {},
                    "token_count": max(1, len(text) // 4),
                    "metadata": {"is_full_document": False},
                })
            body.clear()

    for sec in doc["sections"]:
        if sec["type"] == "heading":
            flush()
            heading = sec["content"]
        else:
            body.append(sec["content"])
    flush()

    return {
        "chunk_contract": "1.0",
        "source_id": doc["source_id"],
        "source_path": doc["source_path"],
        "chunks": chunks,
        "stats": {
            "total_chunks": len(chunks),
            "decision": {"strategy": "stub-by-heading", "token_bounds": {"target": 512}},
            "token_counter": "estimate",
            "oversized_atomic": [],
        },
    }


def stub_summary(doc: dict) -> dict:
    """Title from the first heading, summary from the leading content.

    This stands in for the judgement the real worker makes. It is enough to
    prove the catalogue is discriminative in the mechanical sense: the terms
    that distinguish this document appear in its entry.

    Note it draws on EVERY content type, not just paragraphs. A CSV parses to a
    single table with no paragraphs at all — a paragraphs-only outline yields an
    empty summary for a document that is entirely data, which is a real bug that
    this stub reproduced before the manual was corrected.
    """
    title = next(
        (s["content"] for s in doc["sections"] if s["type"] == "heading"), doc["source_id"]
    )
    parts = [
        s["content"]
        for s in doc["sections"]
        if s["type"] in ("paragraph", "table", "list_item")
    ]
    summary = " ".join(parts)[:200]
    words = [w for w in summary.replace(",", " ").replace(".", " ").replace("|", " ").split()
             if len(w) > 3]
    topics = list(dict.fromkeys(words))[:4]
    return {
        "summary_contract": "1.0",
        "source_id": doc["source_id"],
        "source_path": doc["source_path"],
        "title": title,
        "summary": summary,
        "topics": topics,
        "token_count": max(1, len(summary) // 4),
        "meta": {"doc_type": "doc", "language": "en"},
    }


# ── run the stubs and write per-document run directories ─────────────────
run_dirs: dict[str, Path] = {}
for doc in deliverable:
    sid = doc["source_id"]
    rdir = runs / sid
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "parsed.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    chunked = stub_chunk(doc)
    summarized = stub_summary(doc)
    (rdir / "chunks.json").write_text(
        json.dumps(chunked, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    (rdir / "doc-summary.json").write_text(
        json.dumps(summarized, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    run_dirs[sid] = rdir

# ── stage 6: build the catalogue ──────────────────────────────────────────
catalog_lines = []
for doc in deliverable:
    s = json.loads((run_dirs[doc["source_id"]] / "doc-summary.json").read_text(encoding="utf-8"))
    c = json.loads((run_dirs[doc["source_id"]] / "chunks.json").read_text(encoding="utf-8"))
    catalog_lines.append({
        "source_id": s["source_id"],
        "title": s["title"],
        "summary": s["summary"],
        "topics": s["topics"],
        "status": doc["verdict"],
        "chunk_count": len(c["chunks"]),
        "token_count": s["token_count"],
    })
catalog_path = work / "catalog.jsonl"
catalog_path.write_text(
    "\n".join(json.dumps(e, ensure_ascii=False) for e in catalog_lines) + "\n",
    encoding="utf-8", newline="\n",
)
catalog = [json.loads(l) for l in catalog_path.read_text(encoding="utf-8").splitlines() if l.strip()]

check("catalogue: one entry per deliverable document", len(catalog) == 3, f"got {len(catalog)}")
check(
    "catalogue: every entry has title + summary + topics",
    all(e["title"] and e["summary"] and e["topics"] for e in catalog),
)
check(
    "catalogue: unusable document is absent",
    all(e["source_id"] != (blank or {}).get("source_id") for e in catalog),
)

# ── ACCEPTANCE 1: the broad path — catalogue scan only ───────────────────
# Simulate an agent answering a broad question without opening any document.
# It reads ONLY catalog.jsonl.
def broad_select(question: str, entries: list[dict]) -> list[dict]:
    terms = {w.lower().strip("?.,") for w in question.split() if len(w) > 4}
    scored = []
    for e in entries:
        haystack = (e["title"] + " " + e["summary"] + " " + " ".join(e["topics"])).lower()
        scored.append((sum(1 for t in terms if t in haystack), e))
    scored.sort(key=lambda x: -x[0])
    return [e for score, e in scored if score > 0]


selected = broad_select("What are the supplier admission and payment-term requirements?", catalog)
sel_ids = [e["source_id"] for e in selected]
supplier_id = by_name["supplier-report.md"]["source_id"]
oauth_id = by_name["oauth-guide.md"]["source_id"]

check(
    "broad: selects the supplier report",
    supplier_id in sel_ids,
    f"selected={[e['title'] for e in selected]}",
)
check(
    "broad: does NOT select the unrelated OAuth guide",
    oauth_id not in sel_ids,
    f"selected={[e['title'] for e in selected]}",
)

# ── ACCEPTANCE 2: the specific path — chunk search ───────────────────────
def chunk_search(question: str, all_chunks: list[dict]) -> list[dict]:
    terms = {w.lower().strip("?.,") for w in question.split() if len(w) > 4}
    hits = []
    for c in all_chunks:
        text = c["text"].lower()
        score = sum(1 for t in terms if t in text)
        if score:
            hits.append((score, c))
    hits.sort(key=lambda x: -x[0])
    return [c for _, c in hits]


all_chunks = []
for doc in deliverable:
    all_chunks.extend(
        json.loads((run_dirs[doc["source_id"]] / "chunks.json").read_text(encoding="utf-8"))["chunks"]
    )

hits = chunk_search("How long until access tokens expire?", all_chunks)
check("specific: finds a matching chunk", bool(hits), "no chunk matched")
if hits:
    top = hits[0]
    check(
        "specific: the top chunk contains the answer",
        "3600" in top["text"],
        top["text"][:120],
    )
    check(
        "specific: the chunk points at the right document",
        top["source_id"] == oauth_id,
        f"got {top['source_id']}",
    )
    check("specific: the chunk carries a breadcrumb", bool(top.get("heading_path")), str(top.get("heading_path")))

# ── ACCEPTANCE 3: the two tiers are connected ────────────────────────────
chunk_source_ids = {c["source_id"] for c in all_chunks}
catalog_ids = {e["source_id"] for e in catalog}
check(
    "linkage: every catalogued document has chunks",
    catalog_ids <= chunk_source_ids,
    f"catalogued but unreachable: {catalog_ids - chunk_source_ids}",
)
check(
    "linkage: every chunked document is in the catalogue",
    chunk_source_ids <= catalog_ids,
    f"chunked but invisible: {chunk_source_ids - catalog_ids}",
)

# ── ACCEPTANCE 4: reproducibility ────────────────────────────────────────
# Same source parsed twice must yield identical artifacts, or a re-ingest
# silently produces different chunks for the same document.
second = work / "parsed2.jsonl"
run_cli("parse", "-d", str(corpus), "-o", str(second))
first_docs = {json.loads(l)["source_id"]: json.loads(l)
              for l in parsed_path.read_text(encoding="utf-8").splitlines() if l.strip()}
second_docs = {json.loads(l)["source_id"]: json.loads(l)
               for l in second.read_text(encoding="utf-8").splitlines() if l.strip()}
check(
    "reproducible: same corpus yields the same source_ids",
    set(first_docs) == set(second_docs),
    f"{len(first_docs)} vs {len(second_docs)}",
)
check(
    "reproducible: verdicts are stable across runs",
    {k: v["verdict"] for k, v in first_docs.items()}
    == {k: v["verdict"] for k, v in second_docs.items()},
)
check(
    "reproducible: section counts are stable across runs",
    {k: len(v["sections"]) for k, v in first_docs.items()}
    == {k: len(v["sections"]) for k, v in second_docs.items()},
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
print(f"\n{passed}/{len(results)} end-to-end checks passed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(0 if passed == len(results) else 1)
