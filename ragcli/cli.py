"""ragcli — RAG data pre-processing CLI.

Currently ships one tool: `parse`. It turns any source format into a single
normalized JSON contract, so nothing downstream has to know about file formats.

Usage:
    ragcli list                      # list tools
    ragcli stages                    # pipeline stages and their tools
    ragcli parse --list-formats      # supported formats + engine availability
    ragcli parse -f doc.pdf -o out.json
    ragcli parse -d ./corpus/ -o all.jsonl
"""

import argparse
import json
import sys

from ragcli.registry import list_stages, list_tools


def cmd_list(args):
    """List available tools, optionally filtered by stage."""
    print(json.dumps(list_tools(stage=getattr(args, "stage", None)), ensure_ascii=False, indent=2))


def cmd_stages(args):
    """Describe the pipeline stages and their tools."""
    print(json.dumps(list_stages(), ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        prog="ragcli",
        description="RAG data pre-processing — turn any source format into one JSON contract",
    )
    parser.add_argument(
        "--version", action="version", version=f"ragcli {__import__('ragcli').__version__}"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available tools")

    list_parser = subparsers.add_parser("list", help="List available tools")
    list_parser.add_argument(
        "--stage",
        choices=["ingest", "retrieve", "evaluate"],
        help="Only show tools belonging to this stage",
    )
    list_parser.set_defaults(func=cmd_list)

    stages_parser = subparsers.add_parser("stages", help="Show pipeline stages and their tools")
    stages_parser.set_defaults(func=cmd_stages)

    # Imported inside main() so a missing optional engine cannot break --help
    from ragcli.tools.parse import register_parser as parse_register

    parse_register(subparsers)

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
