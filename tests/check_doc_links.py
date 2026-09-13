"""Check markdown cross-references and stale filename mentions across docs.

Two classes of problem are detected:

1. Broken links — a relative `[text](path)` whose target does not exist.
2. Stale mentions — a bare filename from a previous revision that is no longer
   on disk (e.g. text still referring to `chunk-expert.md` after the rename to
   `chunk-worker.md`). These do not break rendering, which is exactly why they
   rot silently.

Run:  python tests/check_doc_links.py
Exits non-zero if anything is broken, so it works as a CI gate.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# Filenames that existed in earlier revisions and must no longer be referenced.
RETIRED_NAMES = [
    "chunk-expert.md",
    "chunk-expert.zh.md",
    "summarize-expert.md",
    "summarize-expert.zh.md",
    "tagger-expert.md",
    "tagger-expert.zh.md",
    "parse-expert.md",
    "parse-expert.zh.md",
    "orchestration-guide.md",
    "agent-guide.md",
    "data-preprocessing-expert.md",
    "preprocessing-architecture.md",
    "data-pipeline-dependencies.md",
]

# Commands or paths that never existed in code.
RETIRED_REFS = ["ragcli clean"]

# A retired name may still be mentioned legitimately when the text is *recording*
# the removal. These markers identify such historical references.
HISTORICAL_MARKERS = [
    "删除", "已删除", "反模式", "后更名", "更名为",
    "deleted", "renamed", "stale", "never been", "no longer",
]


def main() -> int:
    md_files = sorted(list(DOCS.rglob("*.md")) + [ROOT / "README.md"])
    broken_links: list[str] = []
    stale: list[str] = []
    checked_links = 0

    for md in md_files:
        rel = md.relative_to(ROOT).as_posix()
        text = md.read_text(encoding="utf-8")

        # 1. relative link targets
        for label, target in LINK_RE.findall(text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            checked_links += 1
            resolved = (md.parent / path_part).resolve()
            if not resolved.exists():
                broken_links.append(f"{rel}: [{label}]({target}) -> missing")

        # 2. retired filename mentions (skip this checker's own list)
        if md.name != Path(__file__).name:
            lines = text.split("\n")

            def is_historical(line_no: int) -> bool:
                line = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
                return any(marker in line for marker in HISTORICAL_MARKERS)

            for name in RETIRED_NAMES:
                for m in re.finditer(re.escape(name), text):
                    line_no = text[: m.start()].count("\n") + 1
                    if is_historical(line_no):
                        continue
                    stale.append(f"{rel}:{line_no}: mentions retired file '{name}'")

            for ref in RETIRED_REFS:
                for m in re.finditer(re.escape(ref), text):
                    line_no = text[: m.start()].count("\n") + 1
                    if is_historical(line_no):
                        continue
                    stale.append(f"{rel}:{line_no}: references nonexistent '{ref}'")

    print(f"checked {len(md_files)} markdown files, {checked_links} relative links\n")

    if broken_links:
        print(f"BROKEN LINKS ({len(broken_links)}):")
        for b in broken_links:
            print(f"  {b}")
        print()

    if stale:
        print(f"STALE REFERENCES ({len(stale)}):")
        for s in stale:
            print(f"  {s}")
        print()

    if not broken_links and not stale:
        print("OK — no broken links, no stale references")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
