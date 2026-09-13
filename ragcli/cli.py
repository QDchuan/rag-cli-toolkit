"""ragcli — Modular RAG CLI Toolkit.

Usage:
    ragcli list                  # List all available tools
    ragcli <tool> --help         # Show tool help
    ragcli <tool> [args]         # Run a tool
"""

import argparse
import json
import sys

from ragcli.registry import ALL_TOOLS, list_tools


def cmd_list(args):
    """List all available tools."""
    tools = list_tools()
    print(json.dumps(tools, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        prog="ragcli",
        description="Modular RAG CLI Toolkit — atomic tools, freely composable",
    )
    parser.add_argument("--version", action="version", version=f"ragcli {__import__('ragcli').__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Available tools")

    # 'list' command
    list_parser = subparsers.add_parser("list", help="List all available tools")
    list_parser.set_defaults(func=cmd_list)

    # Register each tool's parser
    from ragcli.tools.chunk import register_parser as chunk_register
    from ragcli.tools.embed import register_parser as embed_register
    from ragcli.tools.index import register_parser as index_register
    from ragcli.tools.search import register_parser as search_register
    from ragcli.tools.hybrid import register_parser as hybrid_register
    from ragcli.tools.rerank import register_parser as rerank_register
    from ragcli.tools.summarize import register_parser as summarize_register
    from ragcli.tools.tagger import register_parser as tagger_register
    from ragcli.tools.evaluate import register_parser as evaluate_register
    from ragcli.tools.cache import register_parser as cache_register
    from ragcli.tools.graph import register_parser as graph_register

    for reg in [chunk_register, embed_register, index_register, search_register,
                hybrid_register, rerank_register, summarize_register, tagger_register,
                evaluate_register, cache_register, graph_register]:
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
