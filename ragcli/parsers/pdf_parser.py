"""PDF parser with a two-stage strategy: native text extraction, then OCR fallback.

Why two stages:
    Roughly half of real-world PDFs in enterprise knowledge bases are scans or
    image-only exports. A native extractor on those returns an empty string with
    no error — you silently index nothing. So we detect the empty case and
    escalate to OCR automatically instead of failing quietly.

Stage 1 — pdfplumber (native text + tables)
    Chosen over pypdf because it exposes per-character coordinates. That buys:
      - real table detection via ruling lines and text alignment
      - multi-column handling: we cluster words by x-position and read each
        column top-to-bottom, instead of interleaving left/right columns into
        gibberish (the classic two-column paper failure).
      - header/footer removal: lines that repeat at the same y-position across
        most pages are boilerplate and get dropped.

Stage 2 — OCR (scanned / image-only)
    PaddleOCR is preferred for CJK; EasyOCR is the fallback; tesseract last.
    We render pages to images with PyMuPDF when available.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from ragcli.parsers.base import BaseParser, ParseError, ParsingResult, Section

# A page yielding fewer than this many characters is treated as image-only.
_MIN_TEXT_CHARS_PER_PAGE = 25


class PdfParser(BaseParser):
    """Parses PDF files into heading/paragraph/table sections."""

    format_name = "pdf"
    supported_extensions = ["pdf"]
    requires = ["pdfplumber"]

    def process(self, source: str, options: dict[str, Any] | None = None) -> ParsingResult:
        options = options or {}
        path = Path(source)
        if not path.exists():
            raise ParseError(f"File not found: {source}")

        warnings: list[str] = []
        strategy = options.get("strategy", "auto")  # auto | text | ocr
        engine = "pdfplumber"

        sections: list[Section] = []
        page_count = 0

        if strategy in ("auto", "text"):
            sections, page_count, text_engine = self._extract_native(path, options, warnings)
            engine = text_engine
            if strategy == "auto" and self._looks_scanned(sections, page_count):
                warnings.append(
                    "Native extraction produced almost no text — likely a scanned PDF. "
                    "Escalating to OCR."
                )
                sections = []

        if strategy == "ocr" or (strategy == "auto" and not sections):
            ocr_sections, ocr_engine, ocr_warnings = self._extract_ocr(path, options)
            warnings.extend(ocr_warnings)
            if ocr_sections:
                sections = ocr_sections
                engine = ocr_engine
            elif not sections:
                raise ParseError(
                    f"Could not extract any content from {source}. "
                    "Tried native extraction and OCR. The PDF may be encrypted or corrupt."
                )

        # Note: repeated running headers/footers are removed by the cleaning
        # pipeline (`boilerplate` cleaner), not here. Keeping that logic in one
        # place means every format benefits from it, not just PDF.
        word_count = sum(len(s.content.split()) for s in sections)
        return ParsingResult(
            source_id=self.make_source_id(source),
            source_path=str(path),
            format_type="pdf",
            meta={
                "title": self._pdf_title(path) or path.stem,
                "page_count": page_count,
                "word_count": word_count,
                "is_scanned": engine != "pdfplumber",
            },
            sections=sections,
            stats={
                "parser_engine_used": engine,
                "total_blocks_extracted": len(sections),
                "warnings": warnings,
            },
        )

    # ── stage 1: native ───────────────────────────────────────────────────

    def _extract_native(
        self, path: Path, options: dict[str, Any], warnings: list[str]
    ) -> tuple[list[Section], int, str]:
        try:
            import pdfplumber  # type: ignore
        except ImportError:
            warnings.append(
                "pdfplumber not installed (pip install pdfplumber); skipping native extraction"
            )
            return [], 0, "none"

        sections: list[Section] = []
        detect_tables = options.get("detect_tables", True)
        multi_column = options.get("multi_column", "auto")

        try:
            with pdfplumber.open(str(path)) as pdf:
                page_count = len(pdf.pages)
                for pno, page in enumerate(pdf.pages, start=1):
                    page_text = page.extract_text() or ""

                    # Tables first — their text must not also appear as paragraphs
                    table_bboxes: list[tuple[float, float, float, float]] = []
                    if detect_tables and len(page_text) > 40:
                        try:
                            for tbl in page.find_tables() or []:
                                rows = tbl.extract()
                                rows = [
                                    ["" if c is None else str(c).replace("\n", " ").strip() for c in r]
                                    for r in rows
                                    if r
                                ]
                                rows = [r for r in rows if any(c for c in r)]
                                if len(rows) >= 2:
                                    sections.append(
                                        Section(
                                            block_id=len(sections),
                                            type="table",
                                            content=self._grid_to_markdown(rows),
                                            location={"page": pno},
                                            metadata={
                                                "rows": len(rows) - 1,
                                                "columns": len(rows[0]),
                                                "detected_by": "pdfplumber.find_tables",
                                            },
                                        )
                                    )
                                    table_bboxes.append(tuple(tbl.bbox))
                        except Exception as e:  # noqa: BLE001
                            warnings.append(f"Table detection failed on page {pno}: {e}")

                    # Body text, optionally column-aware
                    if page_text.strip():
                        body = self._page_body_text(page, page_text, table_bboxes, multi_column)
                        for block in re.split(r"\n\s*\n", body):
                            block = self.normalize_text(block)
                            if block:
                                sections.append(
                                    Section(
                                        block_id=len(sections),
                                        type="paragraph",
                                        content=block,
                                        location={"page": pno},
                                    )
                                )
        except Exception as e:  # noqa: BLE001
            warnings.append(f"pdfplumber failed: {e}")
            return [], 0, "none"

        return sections, page_count, "pdfplumber"

    def _page_body_text(
        self,
        page,
        fallback_text: str,
        table_bboxes: list[tuple[float, float, float, float]],
        multi_column: str,
    ) -> str:
        """Extract body text, reading columns separately when the page has them."""
        try:
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
        except Exception:  # noqa: BLE001
            return fallback_text

        if not words:
            return fallback_text

        # Drop words that fall inside a detected table's bounding box
        def in_table(w) -> bool:
            for (x0, top, x1, bottom) in table_bboxes:
                if x0 <= w["x0"] and w["x1"] <= x1 and top <= w["top"] and w["bottom"] <= bottom:
                    return True
            return False

        words = [w for w in words if not in_table(w)]
        if not words:
            return ""

        use_columns = multi_column is True or (
            multi_column == "auto" and self._has_two_columns(words, page.width)
        )

        if not use_columns:
            return self._words_to_text(words)

        mid = page.width / 2
        left = [w for w in words if w["x0"] < mid]
        right = [w for w in words if w["x0"] >= mid]
        return self._words_to_text(left) + "\n\n" + self._words_to_text(right)

    @staticmethod
    def _has_two_columns(words: list[dict], page_width: float) -> bool:
        """Heuristic: a two-column page has a wide empty vertical gutter."""
        if len(words) < 60:
            return False
        mid = page_width / 2
        band = page_width * 0.06
        in_gutter = sum(1 for w in words if mid - band <= w["x0"] <= mid + band)
        # If almost nothing crosses the centre line, it's two columns
        return (in_gutter / len(words)) < 0.03

    @staticmethod
    def _words_to_text(words: list[dict]) -> str:
        """Group words into lines by y-position, then join lines."""
        if not words:
            return ""
        lines: dict[int, list[dict]] = {}
        for w in sorted(words, key=lambda x: (x["top"], x["x0"])):
            key = int(round(w["top"] / 4))
            lines.setdefault(key, []).append(w)
        out: list[str] = []
        for key in sorted(lines):
            row = sorted(lines[key], key=lambda x: x["x0"])
            out.append(" ".join(w["text"] for w in row))
        return "\n".join(out)

    # ── stage 2: OCR ──────────────────────────────────────────────────────

    def _extract_ocr(
        self, path: Path, options: dict[str, Any]
    ) -> tuple[list[Section], str, list[str]]:
        warnings: list[str] = []
        lang = options.get("ocr_lang", "ch")

        # Preferred: PaddleOCR (best CJK accuracy)
        try:
            from paddleocr import PaddleOCR  # type: ignore

            ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
            result = ocr.ocr(str(path), cls=True)
            lines: list[str] = []
            for page in result or []:
                for item in page or []:
                    try:
                        lines.append(item[1][0])
                    except (IndexError, TypeError):
                        continue
            text = self.normalize_text("\n".join(lines))
            if text:
                return self._text_to_sections(text, engine="paddleocr"), "paddleocr", warnings
            warnings.append("PaddleOCR returned no text")
        except ImportError:
            warnings.append("PaddleOCR not installed (pip install paddleocr)")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"PaddleOCR failed: {e}")

        # Fallback: EasyOCR
        try:
            import easyocr  # type: ignore

            reader = easyocr.Reader([lang, "en"] if lang != "en" else ["en"], gpu=False)
            lines = reader.readtext(str(path), detail=0, paragraph=True)
            text = self.normalize_text("\n".join(lines))
            if text:
                return self._text_to_sections(text, engine="easyocr"), "easyocr", warnings
            warnings.append("EasyOCR returned no text")
        except ImportError:
            warnings.append("EasyOCR not installed (pip install easyocr)")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"EasyOCR failed: {e}")

        return [], "none", warnings

    def _text_to_sections(self, text: str, engine: str) -> list[Section]:
        sections: list[Section] = []
        for block in re.split(r"\n\s*\n", text):
            block = self.normalize_text(block)
            if not block:
                continue
            # Short, title-cased, unpunctuated lines are probably headings
            is_heading = (
                len(block) < 80
                and "\n" not in block
                and not block.endswith((".", "。", "!", "！", "?", "？", ";", "；"))
            )
            sections.append(
                Section(
                    block_id=len(sections),
                    type="heading" if is_heading else "paragraph",
                    level=1 if is_heading else None,
                    content=block,
                    metadata={"ocr_engine": engine},
                )
            )
        return sections

    # ── cleanup ───────────────────────────────────────────────────────────

    @staticmethod
    def _looks_scanned(sections: list[Section], page_count: int) -> bool:
        if page_count == 0:
            return True
        total_chars = sum(len(s.content) for s in sections if s.type != "table")
        return total_chars < (_MIN_TEXT_CHARS_PER_PAGE * page_count)

    @staticmethod
    def _grid_to_markdown(grid: list[list[str]]) -> str:
        n_cols = max(len(r) for r in grid)
        grid = [r + [""] * (n_cols - len(r)) for r in grid]
        header = [c.replace("|", "\\|") for c in grid[0]]
        out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * n_cols) + "|"]
        for r in grid[1:]:
            out.append("| " + " | ".join(c.replace("|", "\\|") for c in r) + " |")
        return "\n".join(out)

    @staticmethod
    def _pdf_title(path: Path) -> str | None:
        try:
            import pdfplumber  # type: ignore

            with pdfplumber.open(str(path)) as pdf:
                meta = pdf.metadata or {}
                for key in ("Title", "title"):
                    if meta.get(key):
                        return str(meta[key]).strip()[:300]
        except Exception:  # noqa: BLE001
            pass
        return None


def _register():
    return PdfParser()
