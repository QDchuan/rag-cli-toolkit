// clean-corpus — the deterministic orchestrator for the data cleaning workflow.
//
// THIS FILE IS A WORKFLOW BODY, not a Node module. It is submitted verbatim as
// the `script` parameter of DSH's `workflow` tool. It has no require, no import,
// no filesystem, no shell, and no timers — the runtime provides only `agent`,
// `pipeline`, `parallel`, `phase`, `log`, and `args`.
//
// WHY THE SCHEDULER IS CODE, NOT AN LLM
//   Deciding what runs next, in what order, and whether a gate passed is
//   deterministic. As a prompt it would be non-reproducible, and it would put
//   scheduling knowledge into a context that is supposed to stay ignorant of
//   formats and chunk boundaries.
//
// WHY EVERY STEP IS AN agent() CALL
//   The script has no shell, so it cannot run `ragcli parse` itself. Each step
//   is therefore delegated. The prompts stay SHORT on purpose: they carry the
//   task envelope and point at the manual, and nothing else. Guidance lives in
//   the manual, which is the single source of truth. Duplicating manual content
//   into prompts here would recreate the doc-drift problem this project exists
//   to avoid.
//
// CONTRACT
//   runs/<source_id>/parsed.json       from the ingest agent
//   runs/<source_id>/chunks.json       from the chunk worker
//   runs/<source_id>/doc-summary.json  from the summarize worker
//   runs/catalog.jsonl                 from the catalog agent
//   runs/review_queue.jsonl            from the catalog agent
//
// args: { corpus, run_root, repo_root, policy_root, only?: [source_id] }

const corpus = args.corpus;
const runRoot = args.run_root;
const repoRoot = args.repo_root || "rag-cli-toolkit";
// Corpus-level policy: the chunk bounds and the summary language. These are
// corpus-wide decisions, not per-document ones — see docs/design/workflow.md §7.
const policyRoot = args.policy_root || `${corpus}/../corpus`;
const only = Array.isArray(args.only) && args.only.length ? args.only : null;

if (!corpus || !runRoot) throw new Error("args.corpus and args.run_root are required");

const agentsDir = `${repoRoot}/docs/agents`;
const workflowDoc = `${repoRoot}/docs/design/workflow.md`;

// A worker gets its own manual and nothing else. That isolation is the entire
// reason the pipeline is split into separate agents.
const manual = (file) =>
  `Read ${agentsDir}/${file} FIRST, in full. It is your only manual.

Do not read any other manual in that directory — each belongs to a different
worker, and reading them puts decisions in your context that are not yours.`;

const REPLY = `Reply with ONLY the JSON object below — no prose, no markdown fences.`;

// ── schemas (object-rooted; the runtime rejects richer shapes) ────────────

const INGEST_SCHEMA = {
  type: "object",
  properties: {
    documents: {
      type: "array",
      items: {
        type: "object",
        properties: {
          source_id: { type: "string" },
          source_path: { type: "string" },
          verdict: { type: "string", enum: ["ok", "degraded", "unusable"] },
          section_count: { type: "integer" },
          parsed_path: { type: "string" },
          reasons: { type: "array", items: { type: "string" } },
        },
        required: ["source_id", "source_path", "verdict", "section_count", "parsed_path"],
        additionalProperties: false,
      },
    },
    notes: { type: "array", items: { type: "string" } },
  },
  required: ["documents"],
  additionalProperties: false,
};

const CHUNK_SCHEMA = {
  type: "object",
  properties: {
    source_id: { type: "string" },
    chunks_path: { type: "string" },
    chunk_count: { type: "integer" },
    strategy: { type: "string" },
    warnings: { type: "array", items: { type: "string" } },
  },
  required: ["source_id", "chunks_path", "chunk_count", "strategy"],
  additionalProperties: false,
};

const SUMMARY_SCHEMA = {
  type: "object",
  properties: {
    source_id: { type: "string" },
    summary_path: { type: "string" },
    title: { type: "string" },
    summary: { type: "string" },
    topics: { type: "array", items: { type: "string" } },
    token_count: { type: "integer" },
  },
  required: ["source_id", "summary_path", "title", "summary", "topics"],
  additionalProperties: false,
};

const CATALOG_SCHEMA = {
  type: "object",
  properties: {
    catalog_path: { type: "string" },
    entries: { type: "integer" },
    review_queue_path: { type: "string" },
    problems: { type: "array", items: { type: "string" } },
  },
  required: ["catalog_path", "entries"],
  additionalProperties: false,
};

// The verifier reports what is ACTUALLY on disk, not what a worker claimed.
// Everything downstream reads from this, never from a worker's self-report.
const VERIFY_SCHEMA = {
  type: "object",
  properties: {
    documents: {
      type: "array",
      items: {
        type: "object",
        properties: {
          source_id: { type: "string" },
          chunks_ok: { type: "boolean" },
          summary_ok: { type: "boolean" },
          chunk_count: { type: "integer" },
          title: { type: "string" },
          summary: { type: "string" },
          topics: { type: "array", items: { type: "string" } },
          token_count: { type: "integer" },
          problems: { type: "array", items: { type: "string" } },
        },
        required: [
          "source_id",
          "chunks_ok",
          "summary_ok",
          "chunk_count",
          "title",
          "summary",
          "topics",
          "problems",
        ],
        additionalProperties: false,
      },
    },
  },
  required: ["documents"],
  additionalProperties: false,
};

// ── phase 1: ingest ───────────────────────────────────────────────────────

phase("ingest");
log(`corpus:   ${corpus}`);
log(`run root: ${runRoot}`);

// The ingest agent is the one place that knows about formats, and the only
// place that runs a deterministic command. It is thin: run parse, split the
// batch output into per-document run directories, report verdicts.
const ingestPrompt = `${manual("parse-worker.zh.md")}

# Task

Materialise this corpus into per-document run directories.

  corpus:   ${corpus}
  run_root: ${runRoot}

1. Run \`ragcli parse --list-formats\`. Note any format present in the corpus
   whose engine is missing. Do not install anything.

2. Batch-parse:

       ragcli parse -d "${corpus}" -o "${runRoot}/parsed.jsonl"

   \`ragcli\` is on PATH and runs from any directory.

3. Read \`${runRoot}/parsed.jsonl\` — one JSON document per line. For every
   document, write its object unchanged to \`${runRoot}/<source_id>/parsed.json\`
   (create the directory). Pretty-printed, UTF-8, LF.

4. Report every document, including unusable ones.

Do not chunk, summarise, tag, or alter any parsed content.

# Reply

{ "documents": [ { "source_id": "...", "source_path": "...",
    "verdict": "ok|degraded|unusable", "section_count": 0,
    "parsed_path": "${runRoot}/<id>/parsed.json", "reasons": [] } ],
  "notes": [] }

Copy \`verdict\` and \`section_count\` from the parsed output — do not guess.
${REPLY}`;

const ingested = await agent(ingestPrompt, {
  schema: INGEST_SCHEMA,
  label: "ingest",
  phase: "ingest",
});

if (!ingested || !Array.isArray(ingested.documents)) {
  throw new Error("ingest agent returned no document list");
}

// ── the quality gate ──────────────────────────────────────────────────────
// Deterministic, and deliberately NOT delegated. This is the gate that stops a
// silent parse failure: a scanned PDF with no OCR engine yields zero sections
// and no exception, and a document that reaches the catalogue looking normal is
// worse than one that visibly failed, because nobody investigates it.

const all = ingested.documents;
const selected = only ? all.filter((d) => only.includes(d.source_id)) : all;
const blocked = selected.filter((d) => d.verdict === "unusable");
const deliverable = selected.filter((d) => d.verdict !== "unusable");

log(`found ${all.length}; ${deliverable.length} deliverable, ${blocked.length} blocked`);
for (const b of blocked) log(`  blocked ${b.source_id}: ${(b.reasons || []).join("; ")}`);

const blockedReport = blocked.map((b) => ({
  source_id: b.source_id,
  source_path: b.source_path,
  reasons: b.reasons || [],
}));

if (!deliverable.length) {
  return {
    corpus,
    run_root: runRoot,
    documents: { found: all.length, deliverable: 0, blocked: blocked.length, enriched: 0 },
    blocked: blockedReport,
    incomplete: [],
    catalog: null,
    notes: ingested.notes || [],
    warning: "nothing deliverable — every document was unusable or filtered out",
  };
}

// ── phase 2: enrich ───────────────────────────────────────────────────────
// chunk and summarize both read only parsed.json and neither reads the other,
// so they run in parallel per document. Documents are independent, so
// `pipeline` keeps them all moving without a global barrier.

phase("enrich");

const chunkPrompt = (d) => `${manual("chunk-worker.zh.md")}

# Task

Chunk one document.

  source_id:   ${d.source_id}
  input:       ${d.parsed_path}
  output:      ${runRoot}/${d.source_id}/chunks.json
  chunk policy: ${policyRoot}/chunk-policy.json
  contract:    ${workflowDoc} §5.3

Read the policy file and stay inside its bounds. Write your script to
\`${runRoot}/${d.source_id}/chunk.py\`.

# Reply

{ "source_id": "${d.source_id}", "chunks_path": "${runRoot}/${d.source_id}/chunks.json",
  "chunk_count": 0, "strategy": "...", "warnings": [] }
${REPLY}`;

const summaryPrompt = (d) => `${manual("summarize-worker.zh.md")}

# Task

Write the catalogue entry for one document.

  source_id:     ${d.source_id}
  input:         ${d.parsed_path}
  output:        ${runRoot}/${d.source_id}/doc-summary.json
  summary budget: ${policyRoot}/summary-budget.json
  contract:      ${workflowDoc} §5.2

Read the budget file. \`summary_language\` in it is the language to WRITE IN,
which may differ from the source document's language — write in the budget's
language either way, and record both in \`meta\`.

# Reply

{ "source_id": "${d.source_id}", "summary_path": "${runRoot}/${d.source_id}/doc-summary.json",
  "title": "...", "summary": "...", "topics": ["..."], "token_count": 0 }
${REPLY}`;

const enriched = await pipeline(deliverable, async (d) => {
  const [chunked, summarized] = await parallel([
    () => agent(chunkPrompt(d), { schema: CHUNK_SCHEMA, label: `chunk:${d.source_id}`, phase: "chunk" }),
    () => agent(summaryPrompt(d), { schema: SUMMARY_SCHEMA, label: `summary:${d.source_id}`, phase: "summarize" }),
  ]);
  return { doc: d, chunked, summarized };
});

// A throwing stage resolves to null for that item, so a stage failure surfaces
// here instead of killing the run. This is a PROGRESS signal only — whether the
// work actually landed is decided by the verifier below, not by these replies.
const replied = enriched.filter((e) => e && e.chunked && e.summarized);
const noReply = enriched.filter((e) => !e || !e.chunked || !e.summarized);
if (noReply.length) {
  log(
    `${noReply.length}/${deliverable.length} worker(s) returned no valid reply — ` +
      `verification will decide whether the work landed anyway`,
  );
}

// ── phase 3: verify ───────────────────────────────────────────────────────
// NEVER TRUST A WORKER'S SELF-REPORT.
//
// Observed on a real run: two workers completed their work correctly — valid
// chunks.json and doc-summary.json on disk — but their final JSON reply did not
// satisfy the schema, so `agent()` resolved to null and the orchestrator
// reported them as failures. The run under-reported success by 3x, and it
// under-reported in the direction that looks like a worker problem when the
// worker was fine and the orchestration contract was brittle.
//
// So the script does not consume worker replies. It sends one verifier that
// reads the filesystem and reports what is actually there. Everything
// downstream — the catalogue in particular — is built from those verified
// facts, not from what a worker said it did.

phase("verify");

const verifyPrompt = `# Task

Verify a completed enrichment run by reading the filesystem. Report only what
you can confirm exists — do not assume anything a worker may have claimed.

Run root: ${runRoot}

For EACH of these source_ids:

${deliverable.map((d) => `  - ${d.source_id}\n    expected chunks:       ${runRoot}/${d.source_id}/chunks.json\n    expected doc-summary:  ${runRoot}/${d.source_id}/doc-summary.json`).join("\n")}

do the following:

1. Read \`chunks.json\`. It is well-formed if:
   - it parses as JSON
   - it has a non-empty \`chunks\` array
   - its top-level \`source_id\` equals the directory's source_id
   - every chunk has a non-empty \`chunk_id\` and a \`source_id\` equal to the
     directory's source_id
   Set \`chunks_ok\` accordingly and put any failure in \`problems\`.
   Report the real \`chunk_count\`.

2. Read \`doc-summary.json\`. It is well-formed if:
   - it parses as JSON
   - \`source_id\` equals the directory's source_id
   - \`title\`, \`summary\` are non-empty strings
   - \`topics\` is a non-empty array
   Set \`summary_ok\` accordingly and append any failure to \`problems\`.
   Copy \`title\`, \`summary\`, \`topics\` and \`token_count\` out of the file
   **verbatim** — these become the catalogue entry, so do not paraphrase,
   re-translate, shorten or "improve" them.

A missing file is a failure, not an empty value. If a file is absent, set the
corresponding flag to false and say so in \`problems\`.

# Reply

{ "documents": [ { "source_id": "...", "chunks_ok": true, "summary_ok": true,
    "chunk_count": 0, "title": "...", "summary": "...", "topics": ["..."],
    "token_count": 0, "problems": [] } ] }
${REPLY}`;

const verification = await agent(verifyPrompt, {
  schema: VERIFY_SCHEMA,
  label: "verify",
  phase: "verify",
});

// The verifier is a REPORT, not a gate.
//
// A schema-valid reply is the preferred path, but it is not the only evidence
// that the work landed — the artifacts themselves are. Across real runs, three
// separate agents completed their work correctly and then failed to return a
// schema-valid envelope (once two enrich workers, once the verifier). Treating
// that as failure makes the orchestrator report the opposite of the truth.
//
// So: when the verifier replies, its findings are authoritative — it reads the
// filesystem, including fields a worker may have fumbled. When it does not,
// fall back to the workers' own replies and mark the run UNVERIFIED, so the
// caller knows to inspect rather than being told a comfortable lie.
const verifiedDocs = Array.isArray(verification && verification.documents)
  ? verification.documents
  : null;

let ok;
let partial;
let verificationState;

if (verifiedDocs) {
  ok = verifiedDocs.filter((v) => v.chunks_ok && v.summary_ok);
  partial = verifiedDocs.filter((v) => !(v.chunks_ok && v.summary_ok));
  verificationState = "verified";
} else {
  const repliedIds = new Set(replied.map((e) => (e && e.doc ? e.doc.source_id : "")));
  ok = deliverable
    .filter((d) => repliedIds.has(d.source_id))
    .map((d) => {
      const e = replied.find((x) => x && x.doc && x.doc.source_id === d.source_id);
      return {
        source_id: d.source_id,
        chunks_ok: true,
        summary_ok: true,
        chunk_count: e.chunked.chunk_count,
        title: e.summarized.title,
        summary: e.summarized.summary,
        topics: e.summarized.topics,
        token_count: e.summarized.token_count || 0,
        summary_language: "",
        problems: [],
      };
    });
  partial = deliverable
    .filter((d) => !repliedIds.has(d.source_id))
    .map((d) => ({ source_id: d.source_id, problems: ["no worker reply and no verification"] }));
  verificationState = "unverified-fallback";
  log(
    "WARNING: the verifier returned no usable report; falling back to worker replies. " +
      "The catalogue below is UNVERIFIED — inspect the artifacts before trusting it.",
  );
}

const incompleteIds = partial.map((v) => v.source_id);
for (const v of partial) {
  log(`  incomplete: ${v.source_id} — ${(v.problems || []).join("; ") || "unknown"}`);
}

if (!ok.length) {
  return {
    corpus,
    run_root: runRoot,
    documents: { found: all.length, deliverable: deliverable.length, blocked: blocked.length, verified_complete: 0 },
    blocked: blockedReport,
    incomplete: incompleteIds,
    verification: verificationState,
    catalog: null,
    notes: ingested.notes || [],
    warning: "no complete artifacts — no catalogue written",
  };
}

// ── phase 4: catalogue ────────────────────────────────────────────────────

phase("catalog");

// Built from VERIFIED file contents, not from worker replies.
const entries = ok.map((v) => {
  const doc = deliverable.find((d) => d.source_id === v.source_id);
  return {
    source_id: v.source_id,
    title: v.title,
    summary: v.summary,
    topics: v.topics,
    status: doc ? doc.verdict : "ok",
    chunk_count: v.chunk_count,
    token_count: v.token_count || 0,
  };
});

const reviewQueue = [
  ...blockedReport.map((b) => ({
    source_id: b.source_id,
    source_path: b.source_path || "",
    reason: "unusable",
    reasons: b.reasons,
  })),
  ...incompleteIds.map((id) => ({ source_id: id, source_path: "", reason: "incomplete", reasons: [] })),
];

// No manual: assembling a catalogue is a mechanical join, not a judgement call.
// Handing this agent a worker manual would be the exact context pollution the
// split exists to prevent.
const catalogPrompt = `# Task

Mechanical join. Do NOT rewrite, shorten, or improve any string — copy exactly.

Write:
  ${runRoot}/catalog.jsonl       one object per line, in the order given
  ${runRoot}/review_queue.jsonl  one object per line

catalog.jsonl:
${JSON.stringify(entries, null, 2)}

review_queue.jsonl:
${JSON.stringify(reviewQueue, null, 2)}

No wrapping array, no trailing commas, UTF-8, LF line endings.

Then read both files back and count lines. Report the REAL counts — if a count
disagrees with what you wrote, put it in \`problems\` rather than reporting the
intended number.

# Reply

{ "catalog_path": "${runRoot}/catalog.jsonl", "entries": 0,
  "review_queue_path": "${runRoot}/review_queue.jsonl", "problems": [] }
${REPLY}`;

const catalog = await agent(catalogPrompt, {
  schema: CATALOG_SCHEMA,
  label: "catalog",
  phase: "catalog",
});

// ── report ────────────────────────────────────────────────────────────────

phase("report");

// Report the VERIFIED truth, and say plainly when it disagrees with what the
// workers claimed — that gap is the signal that the reply contract is brittle,
// and hiding it would make the orchestrator look right while being wrong.
const replyGap = deliverable.length - replied.length;

log(
  `done: ${ok.length}/${deliverable.length} verified complete, ` +
    `${blocked.length} blocked, ${partial.length} incomplete`,
);
if (replyGap) log(`note: ${replyGap} worker reply(ies) failed schema validation`);

// A mixed-language catalogue is a defect: a query in either language silently
// misses part of the corpus. Surface it rather than letting it pass.
const languages = [...new Set(ok.map((v) => (v.summary_language || "unrecorded")))];
const languageWarning =
  languages.length > 1
    ? `catalogue mixes summary languages: ${languages.join(", ")} — a query in either language will silently miss part of the corpus`
    : null;
if (languageWarning) log(`WARNING: ${languageWarning}`);

return {
  corpus,
  run_root: runRoot,
  documents: {
    found: all.length,
    selected: selected.length,
    deliverable: deliverable.length,
    verified_complete: ok.length,
    blocked: blocked.length,
    incomplete: partial.length,
    worker_replies_valid: replied.length,
  },
  blocked: blockedReport,
  incomplete: partial.map((v) => ({
    source_id: v.source_id,
    problems: v.problems || [],
  })),
  verification: verificationState,
  catalog,
  notes: ingested.notes || [],
  execution_notes: [
    ...(verificationState === "unverified-fallback"
      ? ["the verifier returned no usable report — this catalogue is UNVERIFIED, inspect the artifacts"]
      : []),
    ...(replyGap
      ? [`${replyGap} worker reply(ies) did not satisfy the reply schema; artifacts were verified on disk instead`]
      : []),
    ...(languageWarning ? [languageWarning] : []),
  ],
  chunking: ok.map((v) => ({
    source_id: v.source_id,
    chunks: v.chunk_count,
  })),
  summaries: ok.map((v) => ({
    source_id: v.source_id,
    title: v.title,
    tokens: v.token_count || 0,
    topics: v.topics,
  })),
};
