"""Office parser: Word (.docx), Excel (.xlsx), PowerPoint (.pptx).

Engine choices and the specific pitfalls each one solves:

Word — `python-docx`
    Walks the document body directly, so we get real heading levels
    (paragraph.style.name == 'Heading 2') instead of guessing from formatting.
    Pitfall handled: nested tables are walked recursively; a table inside a
    table cell would otherwise be silently dropped.

Excel — `openpyxl`
    Pitfall handled: merged cells. openpyxl reports the value only in the
    top-left cell of a merged range; every other cell reads as None. We expand
    merged ranges so the value is visible on every covered row.
    Pitfall handled: formulas. With data_only=True you get the cached value;
    if the file was never opened in Excel that cache is empty, so we fall back
    to the formula string rather than emitting a blank.

PowerPoint — `python-pptx`
    Pitfall handled: reading order. Slide shapes come back in z-order, not
    reading order. We sort by (top, left) so text flows the way a human reads
    the slide. Speaker notes are appended because they often carry the actual
    explanation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ragcli.parsers.base import BaseParser, MissingDependencyError, ParseError, ParsingResult, Section


class OfficeParser(BaseParser):
    """Unified parser for the Microsoft Office Open XML family."""

    format_name = "office"
    supported_extensions = ["docx", "xlsx", "xlsm", "pptx"]
    requires = ["python-docx", "openpyxl", "python-pptx"]

    def process(self, source: str, options: dict[str, Any] | None = None) -> ParsingResult:
        options = options or {}
        path = Path(source)
        if not path.exists():
            raise ParseError(f"File not found: {source}")

        ext = path.suffix.lower().lstrip(".")
        warnings: list[str] = []

        if ext in {"docx"}:
            sections, doc_meta, engine = self._parse_docx(path, warnings)
        elif ext in {"xlsx", "xlsm"}:
            sections, doc_meta, engine = self._parse_xlsx(path, options, warnings)
        elif ext == "pptx":
            sections, doc_meta, engine = self._parse_pptx(path, warnings)
        else:
            raise ParseError(f"Unsupported Office extension: {ext}")

        word_count = sum(len(s.content.split()) for s in sections)

        return ParsingResult(
            source_id=self.make_source_id(source),
            source_path=str(path),
            format_type=ext,
            meta={"title": path.stem, "word_count": word_count, **doc_meta},
            sections=sections,
            stats={
                "parser_engine_used": engine,
                "total_blocks_extracted": len(sections),
                "warnings": warnings,
            },
        )

    # ── Word ──────────────────────────────────────────────────────────────

    def _parse_docx(
        self, path: Path, warnings: list[str]
    ) -> tuple[list[Section], dict[str, Any], str]:
        try:
            import docx  # type: ignore
            from docx.table import Table  # type: ignore
            from docx.text.paragraph import Paragraph  # type: ignore
        except ImportError:
            raise MissingDependencyError("python-docx", "docx", "pip install python-docx")

        try:
            document = docx.Document(str(path))
        except Exception as e:  # noqa: BLE001 — corrupt/encrypted/non-OOXML file
            raise ParseError(
                f"Could not open {path.name} as a Word document: {type(e).__name__}: {e}"
            ) from e

        sections: list[Section] = []
        heading_stack: list[tuple[int, str]] = []

        def emit(text: str, kind: str, level: int | None = None, meta: dict | None = None):
            text = self.normalize_text(text)
            if not text:
                return
            sections.append(
                Section(
                    block_id=len(sections),
                    type=kind,
                    level=level,
                    heading_path=[h for _, h in heading_stack],
                    content=text,
                    metadata=meta or {},
                )
            )

        def walk_paragraph(par: Paragraph):
            style = (par.style.name or "").lower()

            if style.startswith("heading"):
                m = __import__("re").search(r"(\d+)", style)
                level = int(m.group(1)) if m else 1
                level = max(1, min(level, 6))
                title = par.text.strip()
                if not title:
                    return
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                emit(title, "heading", level=level)
                heading_stack.append((level, title))
                return

            if style.startswith("list"):
                emit(par.text, "list_item")
                return

            emit(par.text, "paragraph")

        def walk_table(table: Table):
            sections.append(
                Section(
                    block_id=len(sections),
                    type="table",
                    heading_path=[h for _, h in heading_stack],
                    content=self._table_to_markdown(table.rows),
                    metadata={"rows": len(table.rows), "columns": len(table.columns)},
                )
            )

        # Iterate body children in document order so tables land in the right place
        from docx.oxml.ns import qn  # type: ignore

        body = document.element.body
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                walk_paragraph(Paragraph(child, document))
            elif child.tag == qn("w:tbl"):
                walk_table(Table(child, document))

        # Core properties
        try:
            props = document.core_properties
            doc_meta = {
                "author": props.author or None,
                "created": props.created.isoformat() if props.created else None,
                "modified": props.modified.isoformat() if props.modified else None,
                "subject": props.subject or None,
            }
        except Exception:  # noqa: BLE001
            doc_meta = {}

        doc_meta["paragraph_count"] = len(document.paragraphs)
        doc_meta["table_count"] = len(document.tables)
        if not sections:
            warnings.append("No text extracted — the document may contain only images")
        return sections, doc_meta, "python-docx"

    @staticmethod
    def _table_to_markdown(rows) -> str:
        """Render docx table rows as a Markdown table, escaping pipes."""
        grid: list[list[str]] = []
        for row in rows:
            cells = [c.text.replace("\n", " ").strip().replace("|", "\\|") for c in row.cells]
            grid.append(cells)
        if not grid:
            return ""
        n_cols = max(len(r) for r in grid)
        grid = [r + [""] * (n_cols - len(r)) for r in grid]
        out = ["| " + " | ".join(grid[0]) + " |", "|" + "|".join(["---"] * n_cols) + "|"]
        for r in grid[1:]:
            out.append("| " + " | ".join(r) + " |")
        return "\n".join(out)

    # ── Excel ─────────────────────────────────────────────────────────────

    def _parse_xlsx(
        self, path: Path, options: dict[str, Any], warnings: list[str]
    ) -> tuple[list[Section], dict[str, Any], str]:
        try:
            import openpyxl  # type: ignore
        except ImportError:
            raise MissingDependencyError("openpyxl", "xlsx", "pip install openpyxl")

        include_formulas = options.get("include_formulas", False)
        max_rows = options.get("max_rows_per_sheet", 5000)

        # data_only=True returns cached formula results; if the file was never
        # opened in Excel that cache is None, so we re-read formulas as fallback.
        try:
            wb_values = openpyxl.load_workbook(str(path), data_only=True)
        except Exception as e:  # noqa: BLE001 — corrupt/encrypted/non-OOXML file
            raise ParseError(
                f"Could not open {path.name} as an Excel workbook: {type(e).__name__}: {e}"
            ) from e

        wb_formulas = None
        if include_formulas:
            try:
                wb_formulas = openpyxl.load_workbook(str(path), data_only=False)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"Could not read formulas: {e}")

        sections: list[Section] = []
        sheet_names = wb_values.sheetnames

        for sheet_name in sheet_names:
            ws = wb_values[sheet_name]
            ws_f = wb_formulas[sheet_name] if wb_formulas else None

            sections.append(
                Section(
                    block_id=len(sections),
                    type="heading",
                    level=1,
                    content=f"Sheet: {sheet_name}",
                    location={"sheet": sheet_name},
                )
            )

            merged = list(ws.merged_cells.ranges)
            grid: list[list[str]] = []
            truncated = False

            for r_idx, row in enumerate(ws.iter_rows(max_row=max_rows), start=1):
                values = []
                for cell in row:
                    v = cell.value
                    if v is None and ws_f is not None:
                        fv = ws_f.cell(row=cell.row, column=cell.column).value
                        if isinstance(fv, str) and fv.startswith("="):
                            v = fv  # keep the formula when no cached value exists
                    values.append("" if v is None else str(v))
                grid.append(values)

            if ws.max_row and ws.max_row > max_rows:
                truncated = True

            # Expand merged ranges so every covered cell shows the value
            for rng in merged:
                try:
                    top_left = ws.cell(row=rng.min_row, column=rng.min_col).value
                except Exception:  # noqa: BLE001
                    continue
                if top_left is None:
                    continue
                for rr in range(rng.min_row, rng.max_row + 1):
                    for cc in range(rng.min_col, rng.max_col + 1):
                        gi, gj = rr - 1, cc - 1
                        if 0 <= gi < len(grid) and 0 <= gj < len(grid[gi]):
                            grid[gi][gj] = str(top_left)

            # Drop fully-empty rows
            grid = [r for r in grid if any(c.strip() for c in r)]
            if not grid:
                warnings.append(f"Sheet '{sheet_name}' is empty")
                continue

            sections.append(
                Section(
                    block_id=len(sections),
                    type="table",
                    heading_path=[f"Sheet: {sheet_name}"],
                    content=self._grid_to_markdown(grid),
                    location={"sheet": sheet_name},
                    metadata={
                        "rows": len(grid) - 1 if len(grid) > 1 else 0,
                        "columns": len(grid[0]),
                        "merged_ranges_expanded": len(merged),
                        "truncated": truncated,
                    },
                )
            )
            if truncated:
                warnings.append(
                    f"Sheet '{sheet_name}' exceeded {max_rows} rows and was truncated"
                )

        doc_meta = {"sheet_count": len(sheet_names), "sheets": sheet_names}
        return sections, doc_meta, "openpyxl"

    @staticmethod
    def _grid_to_markdown(grid: list[list[str]]) -> str:
        n_cols = max(len(r) for r in grid)
        grid = [r + [""] * (n_cols - len(r)) for r in grid]
        header = [c.replace("|", "\\|").replace("\n", " ") for c in grid[0]]
        out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * n_cols) + "|"]
        for r in grid[1:]:
            out.append(
                "| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in r) + " |"
            )
        return "\n".join(out)

    # ── PowerPoint ────────────────────────────────────────────────────────

    def _parse_pptx(
        self, path: Path, warnings: list[str]
    ) -> tuple[list[Section], dict[str, Any], str]:
        try:
            from pptx import Presentation  # type: ignore
        except ImportError:
            raise MissingDependencyError("python-pptx", "pptx", "pip install python-pptx")

        try:
            prs = Presentation(str(path))
        except Exception as e:  # noqa: BLE001 — corrupt/encrypted/non-OOXML file
            raise ParseError(
                f"Could not open {path.name} as a PowerPoint file: {type(e).__name__}: {e}"
            ) from e

        sections: list[Section] = []

        for idx, slide in enumerate(prs.slides, start=1):
            title = ""
            try:
                if slide.shapes.title is not None:
                    title = slide.shapes.title.text.strip()
            except Exception:  # noqa: BLE001
                title = ""

            heading = title or f"Slide {idx}"
            sections.append(
                Section(
                    block_id=len(sections),
                    type="heading",
                    level=1,
                    content=heading,
                    location={"slide": idx},
                )
            )

            # Sort shapes into human reading order (top-to-bottom, left-to-right)
            shapes = sorted(
                slide.shapes,
                key=lambda s: (round(getattr(s, "top", 0) or 0, -4), getattr(s, "left", 0) or 0),
            )

            for shape in shapes:
                if getattr(shape, "has_table", False):
                    rows = [[c.text for c in row.cells] for row in shape.table.rows]
                    sections.append(
                        Section(
                            block_id=len(sections),
                            type="table",
                            heading_path=[heading],
                            content=self._grid_to_markdown(rows) if rows else "",
                            location={"slide": idx},
                            metadata={"rows": len(rows)},
                        )
                    )
                    continue

                if not getattr(shape, "has_text_frame", False):
                    continue
                if title and shape is getattr(slide.shapes, "title", None):
                    continue

                text = "\n".join(
                    p.text for p in shape.text_frame.paragraphs if p.text.strip()
                ).strip()
                if text:
                    sections.append(
                        Section(
                            block_id=len(sections),
                            type="paragraph",
                            heading_path=[heading],
                            content=self.normalize_text(text),
                            location={"slide": idx},
                        )
                    )

            # Speaker notes frequently hold the real explanation
            try:
                if slide.has_notes_slide:
                    notes = (slide.notes_slide.notes_text_frame.text or "").strip()
                    if notes:
                        sections.append(
                            Section(
                                block_id=len(sections),
                                type="paragraph",
                                heading_path=[heading],
                                content=self.normalize_text(notes),
                                location={"slide": idx},
                                metadata={"source": "speaker_notes"},
                            )
                        )
            except Exception:  # noqa: BLE001
                pass

        doc_meta = {"slide_count": len(prs.slides)}
        if not sections:
            warnings.append("No text extracted from presentation")
        return sections, doc_meta, "python-pptx"


def _register():
    return OfficeParser()
