# Documentation Map

> **Read this first.** It tells you which document is for whom, so you never load the wrong one.

---

## The One Rule

There are **two audiences**, and mixing them is what made this project confusing.

| Audience | Lives in | Language | Purpose |
|---|---|---|---|
| **An agent** | `agents/` | English | Operating instructions. One file per agent. |
| **A human** | `design/` | Chinese | Architecture and decisions. Rationale, tradeoffs, gaps. |
| **Either** | `reference/` | English | Generated facts about the CLI. Never hand-edited. |

**An agent must never be given a `design/` document. A human does not need to read an `agents/` document to understand the system.**

---

## `agents/` — Operating Instructions for Agents

Each manual is injected into **exactly one** agent, in a **fresh session**. Never two at once — that is the context-pollution failure this structure exists to prevent.

| File | Held by | Language |
|---|---|---|
| [`parse-worker.zh.md`](agents/parse-worker.zh.md) | Parse Worker | 中文 |
| [`chunk-worker.zh.md`](agents/chunk-worker.zh.md) | Chunk Worker | 中文 |
| [`summarize-worker.zh.md`](agents/summarize-worker.zh.md) | Summarize Worker | 中文 |
| [`tagger-worker.zh.md`](agents/tagger-worker.zh.md) | Tagger Worker | 中文 |

**Manuals are Chinese-only, deliberately.** They were previously bilingual, and the Chinese copies silently drifted onto an outdated mental model — worse than having no copy, because a reviewer reads the translation believing they are checking the original. One version means drift is impossible.

**There is no orchestrator manual.** The orchestrator is [`workflows/clean-corpus.js`](../workflows/clean-corpus.js) — it is code, because scheduling is deterministic. Adding a prose manual back would recreate the non-reproducibility the script exists to remove.

---

## `design/` — Architecture and Decisions for Humans

| File | Covers |
|---|---|
| [`workflow.md`](design/workflow.md) | **Start here.** The end-to-end workflow: stages, the two artifact tiers, quality routing, corpus policy, summary budget arithmetic, acceptance criteria |
| [`architecture.md`](design/architecture.md) | Agent topology, the two-kinds-of-expert distinction, the gap list |
| [`pipeline-dependencies.md`](design/pipeline-dependencies.md) | Execution order, parallelism, concurrency hazards, scenario pipelines |

**Read `workflow.md` first.** It defines what the system produces and why — the other two documents explain how it is organised and executed.

The single fact almost everything else follows from: there are **two artifact tiers**. A document-level summary (one per document, scanned in bulk) answers "which documents might be relevant"; chunks answer "where exactly is the answer". A document missing from either tier is lost, and lost silently.

---

## `reference/` — Generated Facts

| File | Covers |
|---|---|
| [`cli.md`](reference/cli.md) | Every tool's full interface, generated from the live CLI |

**Never hand-edit `reference/cli.md`.** It is produced by `tests/gen_cli_reference.py`, which reads the actual argparse definitions. If it is wrong, fix the code and regenerate — that is how doc/code drift is prevented.

```bash
python tests/gen_cli_reference.py
```

---

## Where the Two Models Used to Conflict

Earlier revisions mixed a "compose atomic CLI tools" model with an "expert agent per stage" model, which produced documents that contradicted each other. The resolution:

| Question | The single answer |
|---|---|
| Is this a CLI toolkit or an agent system? | **Both, at different layers.** The CLI is the execution layer; agents are the decision layer. |
| What does the system produce? | **Two tiers:** one document summary per document (the coarse tier, scanned in bulk via `catalog.jsonl`), and chunks (the fine tier, similarity-searched). Both are mandatory for every document. |
| What is a summary *for*? | **Filtering, not context.** An agent scans all summaries to decide which documents to open. The test: could a reader who has not opened the document decide relevance from the summary alone? |
| Do chunks need summaries? | **No.** Chunks carry their own text. Summaries are document-level only. |
| How many tools exist? | **One: `parse`.** Eleven others (chunk/tagger/summarize/embed/index/search/…) were removed — written once, never executed, never tested. A tool that has never run is a liability, not an asset. |
| Who decides chunking? | **Chunk Worker writes the script.** There is no `ragcli chunk`, deliberately — no fixed CLI can express per-document boundary decisions. |
| Who decides the tag schema? | **The corpus**, not the document. Tagger Worker applies it and proposes changes; it does not invent values. |
| Is `clean` a step? | **No.** Cleaning runs inside `parse`. There has never been a `clean` command. |
| How many stages? | **Three declared:** `ingest` (write path), `retrieve` (read path), `evaluate`. Only `ingest` has a tool. |
| How many agents? | **One orchestrator + four workers** (parse / chunk / summarize / tagger). |
| Can parsing fail silently? | **Yes, and that is exactly why `verdict` exists.** A scanned PDF with no OCR engine yields zero sections and no exception. The verdict gate stops it before it becomes an empty catalogue entry that looks valid. |
| Can a summary be skipped? | **No.** Skipping saves one LLM call and makes the document permanently unfindable. If the budget cannot cover every document, stop and report — a catalogue with holes is undetectable from outside. |

**Why only `parse` is a tool:** its difficulty is *format handling*, which a library should own. The other stages are difficulty of *judgment* — no fixed command can express them, so the worker writes code. See [`design/architecture.md`](design/architecture.md) §8.

---

## Keeping It Clean

Three rules that prevent the confusion from returning:

1. **A new agent-facing doc goes in `agents/` and declares which single agent holds it.**
2. **A doc that only explains *why* goes in `design/`, in Chinese.**
3. **Anything derived from code is generated, not written.**
