"""Tool registry — all atomic RAG tools, grouped by pipeline stage.

Three stages, split by read/write direction rather than by "is it an index":

    ingest    — write path. Everything that happens at ingestion time, including
                embed and index. These are write operations, same as parse.
    retrieve  — read path. Everything that happens per query.
    evaluate  — cross-cutting quality measurement.

The earlier four-stage split kept `index` separate from `ingest`, which was
misleading: embed/index are write operations that only ever run at ingestion.

The `stage` field exists so each agent can query only the stage it owns, which
is what keeps the pre-processing agent and the retrieval agent separated.
"""

from ragcli.tools.cache import CACHE_TOOL
from ragcli.tools.chunk import CHUNK_TOOL
from ragcli.tools.embed import EMBED_TOOL
from ragcli.tools.evaluate import EVALUATE_TOOL
from ragcli.tools.graph import GRAPH_TOOL
from ragcli.tools.hybrid import HYBRID_TOOL
from ragcli.tools.index import INDEX_TOOL
from ragcli.tools.parse import PARSE_TOOL
from ragcli.tools.rerank import RERANK_TOOL
from ragcli.tools.search import SEARCH_TOOL
from ragcli.tools.summarize import SUMMARIZE_TOOL
from ragcli.tools.tagger import TAGGER_TOOL

# Canonical stage names, in pipeline order.
STAGES = {
    "ingest": "Write path: raw source → parsed → cleaned → chunked → enriched → embedded → indexed",
    "retrieve": "Read path: query → ranked evidence",
    "evaluate": "Quality: measurement and regression detection",
}

ALL_TOOLS = {
    # ── ingest: the write path, owned by the pre-processing agent ──
    "parse": {**PARSE_TOOL, "stage": "ingest"},
    "chunk": {**CHUNK_TOOL, "stage": "ingest"},
    "summarize": {**SUMMARIZE_TOOL, "stage": "ingest"},
    "tagger": {**TAGGER_TOOL, "stage": "ingest"},
    "embed": {**EMBED_TOOL, "stage": "ingest"},
    "index": {**INDEX_TOOL, "stage": "ingest"},
    "graph": {**GRAPH_TOOL, "stage": "ingest"},
    # ── retrieve: the read path, owned by the retrieval agent ──
    "search": {**SEARCH_TOOL, "stage": "retrieve"},
    "hybrid": {**HYBRID_TOOL, "stage": "retrieve"},
    "rerank": {**RERANK_TOOL, "stage": "retrieve"},
    "cache": {**CACHE_TOOL, "stage": "retrieve"},
    # ── evaluate: cross-cutting ──
    "evaluate": {**EVALUATE_TOOL, "stage": "evaluate"},
}


def get_tool(name: str):
    """Get a tool by name. Returns None if not found."""
    return ALL_TOOLS.get(name)


def list_tools(stage: str | None = None):
    """List tools, optionally filtered to a single pipeline stage."""
    result = []
    for name, tool in ALL_TOOLS.items():
        if stage and tool.get("stage") != stage:
            continue
        result.append(
            {
                "name": name,
                "stage": tool.get("stage", "unknown"),
                "description": tool["description"],
                "inputs": tool["inputs"],
                "outputs": tool["outputs"],
            }
        )
    return result


def list_stages():
    """Describe the pipeline stages and which tools belong to each."""
    return [
        {
            "stage": stage,
            "description": desc,
            "tools": [n for n, t in ALL_TOOLS.items() if t.get("stage") == stage],
        }
        for stage, desc in STAGES.items()
    ]
