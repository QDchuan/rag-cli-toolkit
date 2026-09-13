"""Plain-text family parser: Markdown, TXT, CSV, JSON, YAML, and source code.

No external dependencies. This is the baseline every pipeline falls back to.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from ragcli.parsers.base import BaseParser, ParseError, ParsingResult, Section

# Extensions treated as "structured data" and converted rather than read raw
_CSV_EXT = {"csv", "tsv"}
_JSON_EXT = {"json", "jsonl", "ndjson"}
_YAML_EXT = {"yaml", "yml"}
_TEXT_EXT = {"txt", "text", "log"}
_MD_EXT = {"md", "markdown", "mdx"}
_CODE_EXT = {
    "py", "js", "ts", "tsx", "jsx", "java", "go", "rs", "rb", "php", "c", "h",
    "cpp", "hpp", "cs", "kt", "swift", "scala", "sh", "bash", "ps1", "sql",
    "toml", "ini", "cfg", "conf", "env", "dockerfile", "makefile",
}


class TextParser(BaseParser):
    """Parses the plain-text family.

    Design decisions:
    - Markdown: headings are extracted as `heading` sections and used to build
      `heading_path` breadcrumbs for every following block. This is what lets
      downstream chunkers attach context without re-parsing the file.
    - CSV/TSV: converted to a Markdown table. A raw comma dump destroys column
      relationships; a Markdown table preserves them and is what LLMs read best.
      Wide tables are transposed into key/value rows to stay chunkable.
    - JSON: flattened into dotted-path leaf lines, so nested config becomes
      greppable text instead of one giant blob.
    """

    format_name = "text"
    supported_extensions = list(
        _TEXT_EXT | _MD_EXT | _CSV_EXT | _JSON_EXT | _YAML_EXT | _CODE_EXT
    )
    #: Cap for a single table before switching to transposed key/value form.
    MAX_TABLE_COLUMNS = 12

    def process(self, source: str, options: dict[str, Any] | None = None) -> ParsingResult:
        options = options or {}
        path = Path(source)
        if not path.exists():
            raise ParseError(f"File not found: {source}")

        raw = self._read_text(path)
        ext = path.suffix.lower().lstrip(".")

        warnings: list[str] = []

        if ext in _CSV_EXT:
            sections = self._parse_delimited(raw, delimiter="\t" if ext == "tsv" else ",")
        elif ext in _JSON_EXT:
            sections = self._parse_json(raw, ext, warnings)
        elif ext in _YAML_EXT:
            sections = self._parse_yaml(raw, warnings)
        elif ext in _MD_EXT:
            sections = self._parse_markdown(raw)
        elif ext in _CODE_EXT:
            sections = self._parse_code(raw, ext, path.name)
        else:
            sections = self._parse_plain(raw)

        word_count = sum(len(s.content.split()) for s in sections)

        return ParsingResult(
            source_id=self.make_source_id(source),
            source_path=str(path),
            format_type=ext,
            meta={
                "title": path.stem,
                "word_count": word_count,
                "char_count": len(raw),
                "has_headings": any(s.type == "heading" for s in sections),
            },
            sections=sections,
            stats={
                "parser_engine_used": "native-python",
                "total_blocks_extracted": len(sections),
                "warnings": warnings,
            },
        )

    # ── format handlers ───────────────────────────────────────────────────

    @staticmethod
    def _read_text(path: Path) -> str:
        """Read with encoding fallback chain — CJK files often are not UTF-8."""
        for enc in ("utf-8", "utf-8-sig", "gb18030", "big5", "latin-1"):
            try:
                return path.read_text(encoding=enc)
            except (UnicodeDecodeError, LookupError):
                continue
        raise ParseError(f"Could not decode {path} with any known encoding")

    def _parse_plain(self, raw: str) -> list[Section]:
        sections: list[Section] = []
        for block in re.split(r"\n\s*\n", raw):
            block = self.normalize_text(block)
            if block:
                sections.append(Section(block_id=len(sections), type="paragraph", content=block))
        return sections

    def _parse_markdown(self, raw: str) -> list[Section]:
        """Split Markdown into blocks while tracking the heading breadcrumb."""
        sections: list[Section] = []
        heading_stack: list[tuple[int, str]] = []  # (level, text)
        buffer: list[str] = []
        in_fence = False
        fence_lines: list[str] = []

        def flush_text():
            if not buffer:
                return
            text = self.normalize_text("\n".join(buffer))
            buffer.clear()
            if not text:
                return
            for para in re.split(r"\n\s*\n", text):
                para = para.strip()
                if para:
                    sections.append(
                        Section(
                            block_id=len(sections),
                            type="paragraph",
                            heading_path=[h for _, h in heading_stack],
                            content=para,
                        )
                    )

        def flush_code():
            if fence_lines:
                sections.append(
                    Section(
                        block_id=len(sections),
                        type="code_block",
                        heading_path=[h for _, h in heading_stack],
                        content="\n".join(fence_lines),
                        metadata={"language": code_lang},
                    )
                )
                fence_lines.clear()

        code_lang = ""
        for line in raw.split("\n"):
            fence_match = re.match(r"^\s*(```+|~~~+)\s*(\S*)", line)

            if fence_match:
                if in_fence:
                    flush_code()
                    in_fence = False
                    code_lang = ""
                else:
                    flush_text()
                    in_fence = True
                    code_lang = fence_match.group(2) or ""
                continue

            if in_fence:
                fence_lines.append(line)
                continue

            heading = re.match(r"^(#{1,6})\s+(.*)$", line)
            if heading:
                flush_text()
                level = len(heading.group(1))
                title = heading.group(2).strip()
                # Pop deeper-or-equal headings to maintain the ancestor chain
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                sections.append(
                    Section(
                        block_id=len(sections),
                        type="heading",
                        level=level,
                        heading_path=[h for _, h in heading_stack],
                        content=title,
                    )
                )
                heading_stack.append((level, title))
                continue

            # Table detection: consecutive lines starting with |
            if line.strip().startswith("|"):
                flush_text()
                table_rows = [line.strip()]
                # Peek-ahead handled by collecting into buffer is complex; emit per-row group
                # Simpler: treat the whole table as one block by scanning forward is done below
                buffer.append(line)
                continue

            buffer.append(line)

        if in_fence:
            flush_code()
        flush_text()

        # Merge consecutive paragraph blocks that are actually one Markdown table
        return self._merge_markdown_tables(sections)

    @staticmethod
    def _merge_markdown_tables(sections: list[Section]) -> list[Section]:
        """Markdown tables get split by blank-line logic; re-join them."""
        merged: list[Section] = []
        table_buf: list[str] = []
        table_hp: list[str] = []

        def flush():
            if table_buf:
                merged.append(
                    Section(
                        block_id=len(merged),
                        type="table",
                        heading_path=list(table_hp),
                        content="\n".join(table_buf),
                        metadata={"rows": len(table_buf)},
                    )
                )
                table_buf.clear()

        for sec in sections:
            if sec.type == "paragraph" and sec.content.lstrip().startswith("|"):
                if not table_buf:
                    table_hp = list(sec.heading_path)
                table_buf.extend(sec.content.split("\n"))
                continue
            flush()
            sec.block_id = len(merged)
            merged.append(sec)
        flush()

        for i, sec in enumerate(merged):
            sec.block_id = i
        return merged

    def _parse_code(self, raw: str, ext: str, filename: str) -> list[Section]:
        """Treat a source file as one code block plus a synthetic heading."""
        return [
            Section(
                block_id=0,
                type="heading",
                level=1,
                content=f"{filename}",
            ),
            Section(
                block_id=1,
                type="code_block",
                heading_path=[filename],
                content=self.normalize_text(raw),
                metadata={"language": ext, "filename": filename},
            ),
        ]

    def _parse_delimited(self, raw: str, delimiter: str) -> list[Section]:
        """Convert CSV/TSV into a Markdown table (or transposed key/value rows)."""
        reader = csv.reader(io.StringIO(raw), delimiter=delimiter)
        rows = [r for r in reader if any(cell.strip() for cell in r)]
        if not rows:
            return []

        header, *body = rows
        n_cols = len(header)
        warnings: list[str] = []

        if n_cols > self.MAX_TABLE_COLUMNS:
            # Wide table: transpose so each record becomes a readable key/value block
            sections = [
                Section(
                    block_id=0,
                    type="heading",
                    level=1,
                    content="Wide table (transposed)",
                    metadata={"columns": n_cols, "rows": len(body)},
                )
            ]
            for i, row in enumerate(body):
                lines = [
                    f"- {header[j].strip() or f'col{j}'}: {row[j].strip()}"
                    for j in range(min(n_cols, len(row)))
                    if row[j].strip()
                ]
                if lines:
                    sections.append(
                        Section(
                            block_id=len(sections),
                            type="paragraph",
                            content=f"**Record {i + 1}**\n" + "\n".join(lines),
                            metadata={"record_index": i},
                        )
                    )
            return sections

        md = ["| " + " | ".join(c.strip() for c in header) + " |"]
        md.append("|" + "|".join(["---"] * n_cols) + "|")
        for row in body:
            padded = list(row) + [""] * (n_cols - len(row))
            md.append("| " + " | ".join(c.strip().replace("|", "\\|") for c in padded[:n_cols]) + " |")

        return [
            Section(
                block_id=0,
                type="table",
                content="\n".join(md),
                metadata={"columns": n_cols, "rows": len(body)},
            )
        ]

    def _parse_json(self, raw: str, ext: str, warnings: list[str]) -> list[Section]:
        """Flatten JSON/JSONL into dotted-path lines."""
        # JSONL: one object per line
        if ext in {"jsonl", "ndjson"}:
            records = []
            for i, line in enumerate(raw.split("\n")):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    warnings.append(f"Line {i + 1} is not valid JSON: {e}")
            return [
                Section(
                    block_id=i,
                    type="paragraph",
                    content=self._flatten_json(rec, prefix=f"record[{i}]"),
                    metadata={"record_index": i},
                )
                for i, rec in enumerate(records)
            ]

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            warnings.append(f"Invalid JSON, falling back to plain text: {e}")
            return self._parse_plain(raw)

        flat = self._flatten_json(data)
        return [
            Section(
                block_id=0,
                type="paragraph",
                content=flat,
                metadata={"json_root_type": type(data).__name__},
            )
        ]

    def _flatten_json(self, obj: Any, prefix: str = "") -> str:
        lines: list[str] = []

        def walk(node: Any, path: str):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{path}.{k}" if path else str(k))
            elif isinstance(node, list):
                if not node:
                    lines.append(f"{path}: []")
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")
            else:
                lines.append(f"{path}: {node}")

        walk(obj, prefix)
        return "\n".join(lines) if lines else "(empty)"

    def _parse_yaml(self, raw: str, warnings: list[str]) -> list[Section]:
        """Parse YAML if PyYAML is available; otherwise treat as indented text."""
        try:
            import yaml  # type: ignore
        except ImportError:
            warnings.append(
                "PyYAML not installed; falling back to plain-text parsing. "
                "Install with: pip install pyyaml"
            )
            return self._parse_plain(raw)

        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as e:  # type: ignore[attr-defined]
            warnings.append(f"Invalid YAML, falling back to plain text: {e}")
            return self._parse_plain(raw)

        if isinstance(data, (dict, list)):
            flat = self._flatten_json(data)
            return [Section(block_id=0, type="paragraph", content=flat)]
        return self._parse_plain(raw)


def _register():
    return TextParser()
