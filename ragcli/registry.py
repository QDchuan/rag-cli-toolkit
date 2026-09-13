"""Tool registry — all atomic RAG tools are registered here."""

from ragcli.tools.chunk import CHUNK_TOOL
from ragcli.tools.embed import EMBED_TOOL
from ragcli.tools.index import INDEX_TOOL
from ragcli.tools.search import SEARCH_TOOL
from ragcli.tools.hybrid import HYBRID_TOOL
from ragcli.tools.rerank import RERANK_TOOL
from ragcli.tools.summarize import SUMMARIZE_TOOL
from ragcli.tools.tagger import TAGGER_TOOL
from ragcli.tools.evaluate import EVALUATE_TOOL
from ragcli.tools.cache import CACHE_TOOL
from ragcli.tools.graph import GRAPH_TOOL

ALL_TOOLS = {
    "chunk": CHUNK_TOOL,
    "embed": EMBED_TOOL,
    "index": INDEX_TOOL,
    "search": SEARCH_TOOL,
    "hybrid": HYBRID_TOOL,
    "rerank": RERANK_TOOL,
    "summarize": SUMMARIZE_TOOL,
    "tagger": TAGGER_TOOL,
    "evaluate": EVALUATE_TOOL,
    "cache": CACHE_TOOL,
    "graph": GRAPH_TOOL,
}


def get_tool(name: str):
    """Get a tool by name. Returns None if not found."""
    return ALL_TOOLS.get(name)


def list_tools():
    """List all available tools with descriptions."""
    result = []
    for name, tool in ALL_TOOLS.items():
        result.append({
            "name": name,
            "description": tool["description"],
            "inputs": tool["inputs"],
            "outputs": tool["outputs"],
        })
    return result
