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

Each file is injected into **exactly one** agent, in a **fresh session**. Never two at once — that is the context-pollution failure this structure exists to prevent.

| File | Held by | Language |
|---|---|---|
| [`orchestrator.md`](agents/orchestrator.md) | Orchestrator | English |
| [`parse-worker.md`](agents/parse-worker.md) · [中文](agents/parse-worker.zh.md) | Parse Worker | EN + ZH |
| [`chunk-worker.md`](agents/chunk-worker.md) · [中文](agents/chunk-worker.zh.md) | Chunk Worker | EN + ZH |
| [`summarize-worker.md`](agents/summarize-worker.md) · [中文](agents/summarize-worker.zh.md) | Summarize Worker | EN + ZH |
| [`tagger-worker.md`](agents/tagger-worker.md) · [中文](agents/tagger-worker.zh.md) | Tagger Worker | EN + ZH |

**English is the primary version** — agents follow English instructions more reliably and it costs fewer tokens. The `.zh.md` copies exist for human review of what the agent was actually told.

**The orchestrator reads only its own file.** It knows the four workers by name and by the artifact they exchange. It must never read a worker manual.

---

## `design/` — Architecture and Decisions for Humans

| File | Covers |
|---|---|
| [`architecture.md`](design/architecture.md) | Agent topology, the two-kinds-of-expert distinction, contracts, manifest, the gap list |
| [`pipeline-dependencies.md`](design/pipeline-dependencies.md) | Execution order, parallelism, concurrency hazards, scenario pipelines |

Start with `architecture.md` §1 — it explains why `parse` is a thin tool-operator while `chunk` is a thick judgment worker. Almost every other decision follows from that distinction.

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
| Who decides chunking? | **Chunk Worker writes the script.** `ragcli chunk` is a fallback for simple documents, not the primary path. |
| Who decides the tag schema? | **The corpus**, not the document. Tagger Worker applies it and proposes changes; it does not invent values. |
| Is `clean` a step? | **No.** Cleaning runs inside `parse`. There has never been a `clean` command. |
| How many stages? | **Three:** `ingest` (write path), `retrieve` (read path), `evaluate`. |
| How many agents? | **One orchestrator + four workers.** `embed`/`index` are tool steps the orchestrator runs directly. |

---

## Keeping It Clean

Three rules that prevent the confusion from returning:

1. **A new agent-facing doc goes in `agents/` and declares which single agent holds it.**
2. **A doc that only explains *why* goes in `design/`, in Chinese.**
3. **Anything derived from code is generated, not written.**
