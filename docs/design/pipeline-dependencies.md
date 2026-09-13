# Execution — Dependency Graph and Parallelism

> Companion to [`architecture.md`](architecture.md). That document explains *who does what*; this one explains *in what order, and what can overlap*.

---

## The Dependency Graph

```
                    raw source
                        │
                  ┌─────▼─────┐
                  │   parse   │  worker   (cleaning runs inside this step)
                  └─────┬─────┘
                        │ parsed.json
                  ┌─────▼─────┐
                  │   chunk   │  worker
                  └─────┬─────┘
                        │ chunks.json
            ┌───────────┴───────────┐
            │                       │
      ┌─────▼─────┐           ┌─────▼─────┐
      │ summarize │           │  tagger   │   workers — parallel group C
      └─────┬─────┘           └─────┬─────┘
            │ summarized.json       │ tagged.json
            └───────────┬───────────┘
                        │
                  ┌─────▼─────┐
                  │   embed   │  tool step (orchestrator runs directly)
                  └─────┬─────┘
                        │ embedded.json
                  ┌─────▼─────┐
                  │   index   │  tool step
                  └───────────┘
```

---

## There Is No Separate `clean` Step

Cleaning is **inside `parse`**, not a standalone tool. `ragcli parse` runs the cleaning pipeline (`ragcli/cleaners.py`) automatically after extraction.

To control it, use `parse`'s own flags:

```bash
ragcli parse -f doc.pdf --no-clean -o parsed.json                 # skip cleaning
ragcli parse -f doc.pdf --cleaners normalize,dedupe -o out.json   # subset
ragcli parse -f doc.pdf --boilerplate-ratio 0.5 -o out.json       # tune
```

If you see `ragcli clean ...` anywhere, that reference is stale — there has never been a `clean` command.

---

## Hard Dependencies

Each row is a *must wait*, not a preference.

| Must complete first | Before this can start | Because |
|---|---|---|
| `parse` | `chunk` | chunk needs sections |
| `chunk` | `summarize` | summarize needs chunks |
| `chunk` | `tagger` | tagger needs chunks |
| `summarize` **and** `tagger` | `embed` | tags and summaries go into the vector payload |
| `embed` | `index` | index needs vectors |

**Why `embed` waits for both C steps:** the payload written to the vector store includes the tag fields and the summary. Embedding earlier means writing twice — once without enrichment, once with. Wait.

---

## Parallel Opportunities

### Group C — summarize ∥ tagger (the big one)

Both depend only on `chunks.json`, neither depends on the other. They typically dominate wall-clock time because both make LLM calls.

```bash
# Both read chunks.json, write different outputs — safe to run concurrently
ragcli summarize -i chunks.json -o summarized.json &
ragcli tagger    -i chunks.json -o tagged.json --schema schema.json &
wait
```

### Across documents — the bigger one

Documents are fully independent. With 100 documents, the entire pipeline parallelises 100 ways at every stage.

```python
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=8) as pool:
    pool.map(ingest_one_document, document_paths)
```

**Cap the worker count.** LLM rate limits, not CPU, are the binding constraint. Start at 4–8 and raise only if you see no throttling.

### Within a document — do not bother

The per-document chain is short (6 steps) and dominated by LLM latency. Parallelising inside one document adds coordination cost for little gain. Parallelise across documents instead.

---

## Concurrency Hazards

These are the ways parallel ingestion breaks in practice.

| Hazard | Symptom | Mitigation |
|---|---|---|
| **LLM rate limits** | 429s, truncated outputs | Global semaphore on LLM calls; exponential backoff |
| **GPU memory** | OOM when embed and rerank run together | Serialise GPU-bound steps, or pin to different devices |
| **Index write contention** | Slow upserts, lock timeouts | Batch upserts per collection; one writer per collection |
| **Artifact path collision** | Two documents overwrite each other | One run directory per `source_id` — never a shared temp file |
| **Partial upsert** | Index count < embed count | `index` is idempotent: re-run it alone, do not re-embed |
| **Embedding model drift** | Dimension mismatch, nonsense retrieval | Model registry; verify dimension before every embed |

### The artifact path rule

Every parallel branch must write to a path derived from `source_id`:

```
runs/                          ← WRONG: two documents race here
runs/<source_id>/              ← RIGHT: no collision possible
```

This single rule removes an entire class of intermittent, hard-to-reproduce bugs.

---

## Idempotency and Resume

| Step | Idempotent? | Safe to re-run alone? |
|---|---|---|
| `parse` | ✅ deterministic | Yes |
| `chunk` | ❌ agent judgment varies | Only with the recorded decision |
| `summarize` | ❌ | Yes (regenerates) |
| `tagger` | ❌ | Yes (regenerates) |
| `embed` | ✅ | Yes, but wasteful |
| `index` | ✅ | Yes — this is the recovery path for partial upserts |

**Chunk is the odd one out** and the reason the manifest must record the decision. Re-running Chunk Worker without replaying its recorded decision can produce a different chunk set, which invalidates every summary and tag already computed against the old one.

Recovery procedure for a partial run:

```
1. Read runs/<source_id>/manifest.json
2. Find the first step whose status != "done"
3. Verify its input artifact exists and is valid
   - if the input is missing or invalid, step back one further
4. Resume from there
```

---

## Scenario Pipelines

### Minimal — prototype or internal tool

```
parse → chunk → embed → index
```

Skip summarize, tagger, graph. Fastest, lowest cost, least retrieval precision.

### Standard — most production corpora

```
parse → chunk → [summarize ∥ tagger] → embed → index
```

The default. Summaries improve recall on vague queries; tags enable filtered retrieval.

### High precision — regulated or expert domains

```
parse → chunk → [summarize ∥ tagger ∥ graph] → embed → index
```

Adds knowledge-graph extraction for multi-hop questions. Highest cost.

### Incremental — live corpus

```
parse → chunk → embed → index        (per changed document only)
```

Re-enrichment is deferred: run `summarize` and `tagger` in a background sweep rather than inline. Tag and summary fields are updated in place by re-indexing the affected chunks.

---

## Choosing a Pipeline

| Question | If yes |
|---|---|
| Will users run filtered queries ("only policy docs")? | Include `tagger` |
| Will users ask vague or short queries? | Include `summarize` |
| Are there multi-hop relationship questions? | Include `graph` |
| Is the corpus small and the budget tight? | Drop `summarize` first, then `graph` |

**Drop order when trimming cost:** `graph` → `summarize` → `tagger`.
Never drop `chunk` — it is the pipeline. Never drop `tagger` if any filtered query exists, because its absence is silent (queries just return worse results with no error).
