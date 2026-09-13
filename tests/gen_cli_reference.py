"""Generate docs/reference/cli.md from the live CLI so it can never drift.

Run:  python tests/gen_cli_reference.py
"""

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ragcli.cli import main  # noqa: E402
from ragcli.registry import ALL_TOOLS, STAGES  # noqa: E402


def capture_help(tool: str) -> str:
    buf = io.StringIO()
    old = sys.argv
    sys.argv = ["ragcli", tool, "--help"]
    try:
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
    finally:
        sys.argv = old
    return buf.getvalue().strip()


HEADER = """# CLI Reference

> **Generated from the live CLI** by `tests/gen_cli_reference.py`.
> Do not hand-edit — re-run the generator after changing any tool's arguments.

Every tool reads JSON from `-i/--input` (or stdin when omitted) and writes JSON to
`-o/--output` (or stdout). That uniformity is what makes them composable.

```bash
ragcli list                  # all tools
ragcli list --stage ingest   # only the write path
ragcli stages                # stage descriptions
ragcli <tool> --help         # any tool's full usage
```
"""


def main_gen() -> None:
    out = [HEADER, "\n## Pipeline stages\n"]
    for stage, desc in STAGES.items():
        tools = [n for n, t in ALL_TOOLS.items() if t.get("stage") == stage]
        out.append(f"\n### `{stage}`\n\n{desc}\n\n")
        out.append("| tool | purpose |\n|---|---|\n")
        for name in tools:
            out.append(f"| [`{name}`](#{name}) | {ALL_TOOLS[name]['description']} |\n")

    out.append("\n---\n\n## Tool reference\n")
    for name, tool in ALL_TOOLS.items():
        out.append(f"\n### {name}\n\n")
        out.append(f"**Stage:** `{tool.get('stage')}`  \n")
        out.append(f"**Purpose:** {tool['description']}  \n")
        out.append(f"**Inputs:** {', '.join(tool['inputs'])}  \n")
        out.append(f"**Outputs:** {', '.join(tool['outputs'])}\n\n")
        out.append("```text\n")
        out.append(capture_help(name))
        out.append("\n```\n")

    dest = ROOT / "docs" / "reference" / "cli.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(out), encoding="utf-8", newline="\n")
    print(f"wrote {dest} ({len(ALL_TOOLS)} tools)")


if __name__ == "__main__":
    main_gen()
