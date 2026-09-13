"""ragcli — Modular RAG CLI Toolkit.

Usage:
    ragcli list                      # List all tools
    ragcli list --stage ingest       # List only pre-processing tools
    ragcli stages                    # Show the pipeline stages
    ragcli <tool> --help             # Show tool help
    ragcli <tool> [args]             # Run a tool
"""

import argparse
import json
import sys

from ragcli.registry import list_stages, list_tools


def cmd_list(args):
    """List available tools, optionally filtered by pipeline stage."""
    tools = list_tools(stage=getattr(args, "stage", None))
    print(json.dumps(tools, ensure_ascii=False, indent=2))


def cmd_stages(args):
    """Describe the pipeline stages and their tools."""
    print(json.dumps(list_stages(), ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        prog="ragcli",
        description="Modular RAG CLI Toolkit — atomic tools, freely composable",
    )
    parser.add_argument(
        "--version", action="version", version=f"ragcli {__import__('ragcli').__version__}"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available tools")

    # 'list' command — with optional stage filter
    list_parser = subparsers.add_parser("list", help="List all available tools")
    list_parser.add_argument(
        "--stage",
        choices=["ingest", "retrieve", "evaluate"],
        help="Only show tools belonging to this pipeline stage",
    )
    list_parser.set_defaults(func=cmd_list)

    # 'stages' command
    stages_parser = subparsers.add_parser(
        "stages", help="Show pipeline stages and the tools in each"
    )
    stages_parser.set_defaults(func=cmd_stages)

    # Register each tool's parser. Imports are inside main() so that a missing
    # optional dependency in one tool cannot break `ragcli --help`.
    from ragcli.tools.cache import register_parser as cache_register
    from ragcli.tools.chunk import register_parser as chunk_register
    from ragcli.tools.embed import register_parser as embed_register
    from ragcli.tools.evaluate import register_parser as evaluate_register
    from ragcli.tools.graph import register_parser as graph_register
    from ragcli.tools.hybrid import register_parser as hybrid_register
    from ragcli.tools.index import register_parser as index_register
    from ragcli.tools.parse import register_parser as parse_register
    from ragcli.tools.rerank import register_parser as rerank_register
    from ragcli.tools.search import register_parser as search_register
    from ragcli.tools.summarize import register_parser as summarize_register
    from ragcli.tools.tagger import register_parser as tagger_register

    for reg in [
        parse_register,  # first: it is the entry point of the pipeline
        chunk_register,
        summarize_register,
        tagger_register,
        embed_register,
        index_register,
        graph_register,
        search_register,
        hybrid_register,
        rerank_register,
        cache_register,
        evaluate_register,
    ]:
        reg(subparsers)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
