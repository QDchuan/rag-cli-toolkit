"""Tool registry — all atomic RAG tools, grouped by pipeline stage.

The `stage` field exists so an agent can ask "what tools belong to the
pre-processing stage?" without hardcoding a list. That keeps the two agents
(data-preprocessing vs retrieval) cleanly separated: each one queries only the
stage it owns.
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
    "ingest": "Pre-processing: raw source → normalized, enriched chunks",
    "index": "Persistence: chunks → vectors → vector store",
    "retrieve": "Retrieval: query → ranked evidence",
    "evaluate": "Quality: measurement and regression detection",
}

ALL_TOOLS = {
    # ── ingest: owned by the Data Pre-processing Agent ──
    "parse": {**PARSE_TOOL, "stage": "ingest"},
    "chunk": {**CHUNK_TOOL, "stage": "ingest"},
    "summarize": {**SUMMARIZE_TOOL, "stage": "ingest"},
    "tagger": {**TAGGER_TOOL, "stage": "ingest"},
    # ── index: the hand-off boundary between the two agents ──
    "embed": {**EMBED_TOOL, "stage": "index"},
    "index": {**INDEX_TOOL, "stage": "index"},
    "graph": {**GRAPH_TOOL, "stage": "index"},
    # ── retrieve: owned by the Retrieval Agent ──
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
