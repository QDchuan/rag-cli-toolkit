"""Image parser: OCR for screenshots, scans, and photos.

Engine ladder (first available wins):
    1. PaddleOCR  — best CJK accuracy, handles rotated text
    2. EasyOCR    — solid multilingual fallback, easy install
    3. tesseract  — via pytesseract, last resort

Layout awareness:
    Raw OCR returns text boxes in arbitrary order. We sort them top-to-bottom
    then left-to-right, and group boxes whose vertical centres are close into
    the same line. Without this, a two-column screenshot comes out interleaved.

Note on diagrams: OCR reads *labels*, it does not understand arrows or
relationships. For architecture diagrams and flowcharts, a vision model is the
right tool — this parser records a warning when it detects a low text density,
which is the signal that the image is probably a diagram rather than a document.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ragcli.parsers.base import BaseParser, ParseError, ParsingResult, Section

_DIAGRAM_TEXT_DENSITY_THRESHOLD = 0.0005  # chars per pixel


class ImageParser(BaseParser):
    """Extracts text from raster images via OCR."""

    format_name = "image"
    supported_extensions = ["png", "jpg", "jpeg", "bmp", "tiff", "tif", "webp"]
    requires = ["paddleocr or easyocr"]

    def process(self, source: str, options: dict[str, Any] | None = None) -> ParsingResult:
        options = options or {}
        path = Path(source)
        if not path.exists():
            raise ParseError(f"File not found: {source}")

        warnings: list[str] = []
        lang = options.get("ocr_lang", "ch")
        engine_pref = options.get("engine", "auto")  # auto | paddle | easyocr | tesseract

        boxes, engine, ocr_warnings = self._run_ocr(path, lang, engine_pref)
        warnings.extend(ocr_warnings)

        if not boxes:
            raise ParseError(
                f"OCR produced no text for {source}. "
                "Install an OCR engine: pip install paddleocr  (or) pip install easyocr"
            )

        sections = self._boxes_to_sections(boxes, engine)

        width, height = self._image_size(path)
        total_chars = sum(len(s.content) for s in sections)
        density = total_chars / max(width * height, 1)
        if density < _DIAGRAM_TEXT_DENSITY_THRESHOLD:
            warnings.append(
                "Very low text density — this image is likely a diagram or photo. "
                "OCR only reads labels; consider a vision model to capture relationships."
            )

        return ParsingResult(
            source_id=self.make_source_id(source),
            source_path=str(path),
            format_type=path.suffix.lower().lstrip("."),
            meta={
                "title": path.stem,
                "width": width,
                "height": height,
                "word_count": sum(len(s.content.split()) for s in sections),
                "text_density": round(density, 6),
            },
            sections=sections,
            stats={
                "parser_engine_used": engine,
                "total_blocks_extracted": len(sections),
                "warnings": warnings,
            },
        )

    # ── OCR engines ───────────────────────────────────────────────────────

    def _run_ocr(
        self, path: Path, lang: str, pref: str
    ) -> tuple[list[dict], str, list[str]]:
        """Return (boxes, engine_name, warnings).

        Each box: {"text": str, "top": float, "left": float, "height": float}
        """
        warnings: list[str] = []

        if pref in ("auto", "paddle"):
            try:
                from paddleocr import PaddleOCR  # type: ignore

                ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
                raw = ocr.ocr(str(path), cls=True)
                boxes = self._parse_paddle(raw)
                if boxes:
                    return boxes, "paddleocr", warnings
                warnings.append("PaddleOCR returned no text boxes")
            except ImportError:
                if pref == "paddle":
                    raise ParseError("PaddleOCR not installed: pip install paddleocr")
                warnings.append("PaddleOCR not installed")
            except Exception as e:  # noqa: BLE001
                warnings.append(f"PaddleOCR failed: {e}")

        if pref in ("auto", "easyocr"):
            try:
                import easyocr  # type: ignore

                langs = [lang, "en"] if lang != "en" else ["en"]
                reader = easyocr.Reader(langs, gpu=False, verbose=False)
                raw = reader.readtext(str(path))
                boxes = self._parse_easyocr(raw)
                if boxes:
                    return boxes, "easyocr", warnings
                warnings.append("EasyOCR returned no text boxes")
            except ImportError:
                if pref == "easyocr":
                    raise ParseError("EasyOCR not installed: pip install easyocr")
                warnings.append("EasyOCR not installed")
            except Exception as e:  # noqa: BLE001
                warnings.append(f"EasyOCR failed: {e}")

        if pref in ("auto", "tesseract"):
            try:
                import pytesseract  # type: ignore
                from PIL import Image  # type: ignore

                data = pytesseract.image_to_data(
                    Image.open(path), lang=lang, output_type=pytesseract.Output.DICT
                )
                boxes = self._parse_tesseract(data)
                if boxes:
                    return boxes, "tesseract", warnings
                warnings.append("Tesseract returned no text boxes")
            except ImportError:
                warnings.append("pytesseract/Pillow not installed")
            except Exception as e:  # noqa: BLE001
                warnings.append(f"Tesseract failed: {e}")

        return [], "none", warnings

    @staticmethod
    def _parse_paddle(raw) -> list[dict]:
        boxes: list[dict] = []
        for page in raw or []:
            for item in page or []:
                try:
                    poly, (text, _conf) = item[0], item[1]
                except (IndexError, TypeError):
                    continue
                if not text or not str(text).strip():
                    continue
                ys = [p[1] for p in poly]
                xs = [p[0] for p in poly]
                boxes.append(
                    {
                        "text": str(text).strip(),
                        "top": float(min(ys)),
                        "left": float(min(xs)),
                        "height": float(max(ys) - min(ys)) or 1.0,
                    }
                )
        return boxes

    @staticmethod
    def _parse_easyocr(raw) -> list[dict]:
        boxes: list[dict] = []
        for item in raw or []:
            try:
                poly, text = item[0], item[1]
            except (IndexError, TypeError):
                continue
            if not text or not str(text).strip():
                continue
            ys = [p[1] for p in poly]
            xs = [p[0] for p in poly]
            boxes.append(
                {
                    "text": str(text).strip(),
                    "top": float(min(ys)),
                    "left": float(min(xs)),
                    "height": float(max(ys) - min(ys)) or 1.0,
                }
            )
        return boxes

    @staticmethod
    def _parse_tesseract(data: dict) -> list[dict]:
        boxes: list[dict] = []
        n = len(data.get("text", []))
        for i in range(n):
            text = (data["text"][i] or "").strip()
            if not text:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = 0.0
            if conf < 30:  # discard low-confidence noise
                continue
            boxes.append(
                {
                    "text": text,
                    "top": float(data["top"][i]),
                    "left": float(data["left"][i]),
                    "height": float(data["height"][i]) or 1.0,
                }
            )
        return boxes

    # ── layout reconstruction ─────────────────────────────────────────────

    def _boxes_to_sections(self, boxes: list[dict], engine: str) -> list[Section]:
        """Group boxes into reading-order lines, then into paragraphs."""
        boxes = sorted(boxes, key=lambda b: (b["top"], b["left"]))

        lines: list[list[dict]] = []
        for box in boxes:
            placed = False
            for line in lines:
                ref = line[0]
                # Same line if vertical centres overlap by >50% of the smaller height
                ref_centre = ref["top"] + ref["height"] / 2
                box_centre = box["top"] + box["height"] / 2
                tolerance = max(ref["height"], box["height"]) * 0.5
                if abs(ref_centre - box_centre) <= tolerance:
                    line.append(box)
                    placed = True
                    break
            if not placed:
                lines.append([box])

        lines.sort(key=lambda ln: min(b["top"] for b in ln))
        text_lines = [
            " ".join(b["text"] for b in sorted(ln, key=lambda x: x["left"])) for ln in lines
        ]

        # Blank-line separated groups become paragraphs
        sections: list[Section] = []
        para_buf: list[str] = []
        last_top: float | None = None
        heights = [b["height"] for b in boxes] or [12.0]
        median_h = sorted(heights)[len(heights) // 2]
        gap_threshold = median_h * 1.8

        def flush():
            if para_buf:
                text = self.normalize_text("\n".join(para_buf))
                if text:
                    sections.append(
                        Section(
                            block_id=len(sections),
                            type="paragraph",
                            content=text,
                            metadata={"ocr_engine": engine},
                        )
                    )
                para_buf.clear()

        for line, ln in zip(text_lines, lines):
            top = min(b["top"] for b in ln)
            if last_top is not None and (top - last_top) > gap_threshold:
                flush()
            para_buf.append(line)
            last_top = top
        flush()

        return sections

    @staticmethod
    def _image_size(path: Path) -> tuple[int, int]:
        try:
            from PIL import Image  # type: ignore

            with Image.open(path) as im:
                return im.width, im.height
        except Exception:  # noqa: BLE001
            return 0, 0


def _register():
    return ImageParser()
