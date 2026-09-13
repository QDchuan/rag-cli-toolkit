"""Tool registry.

The project currently ships one tool: `parse` (the data pre-processing engine).
A dozen other tools existed as early scaffolding — chunk, tagger, summarize,
embed, index, search and friends — but none were ever executed, tested, or
contract-checked. They were removed rather than carried as dead weight.

That removal was deliberate. A tool that has never run is not an asset; it is a
liability that looks like progress.

When a new tool is added, register it here with its stage. Stages describe
read/write direction:

    ingest    — write path: raw source in, retrievable artifacts out
    retrieve  — read path: query in, ranked evidence out
    evaluate  — cross-cutting quality measurement
"""

from ragcli.tools.parse import PARSE_TOOL

# Canonical stage names, in pipeline order.
STAGES = {
    "ingest": "Write path: raw source → normalized extracted structure",
    "retrieve": "Read path: query → ranked evidence",
    "evaluate": "Quality: measurement and regression detection",
}

ALL_TOOLS = {
    "parse": {**PARSE_TOOL, "stage": "ingest"},
}


def get_tool(name: str):
    """Get a tool by name. Returns None if not found."""
    return ALL_TOOLS.get(name)


def list_tools(stage: str | None = None):
    """List tools, optionally filtered to a single stage."""
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
    """Describe every declared stage and which tools belong to it.

    Stages with no tools are reported with an empty list rather than omitted —
    an empty stage is information (it means the read path is not built yet).
    """
    return [
        {
            "stage": stage,
            "description": desc,
            "tools": [n for n, t in ALL_TOOLS.items() if t.get("stage") == stage],
        }
        for stage, desc in STAGES.items()
    ]
