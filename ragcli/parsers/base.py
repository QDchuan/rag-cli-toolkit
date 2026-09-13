"""Base classes and the standard output contract for all parsers.

Design decision — stdlib dataclasses, not pydantic:
    The toolkit's hard dependency list is intentionally empty. A parser contract
    that requires pydantic would make `ragcli list` fail on any machine without
    it, which defeats the "one bad optional engine must not break the CLI" rule.
    Dataclasses give us the same structural typing, zero install cost, and a
    much faster import. Validation is explicit via `validate()`.

Every parser — regardless of source format — MUST return a `ParsingResult`.
That contract is what decouples upstream format complexity from downstream
agents (chunk / summarize / tagger), which then never branch on file type.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Section:
    """One semantic block extracted from a source document.

    A section maps to one logical unit a downstream chunker can consume:
    a heading, a paragraph, a table, a list item, a code block.
    """

    block_id: int = 0
    #: heading | paragraph | table | list_item | code_block | caption | image
    type: str = "paragraph"
    #: Breadcrumb of ancestor headings, e.g. ["Chapter 1", "1.1 Overview"].
    #: This is what lets a chunk stay independently answerable after retrieval.
    heading_path: list[str] = field(default_factory=list)
    #: Extracted text. Tables are rendered as Markdown to preserve columns.
    content: str = ""
    #: Heading depth 1-6 when type == "heading", else None.
    level: int | None = None
    #: Provenance, e.g. {"page": 12} / {"sheet": "Q3"} / {"slide": 4}.
    location: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParsingResult:
    """The universal output contract. Every parser returns exactly this shape."""

    source_id: str = ""
    source_path: str = ""
    format_type: str = "unknown"
    meta: dict[str, Any] = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_path": self.source_path,
            "format_type": self.format_type,
            "meta": self.meta,
            "sections": [s.to_dict() for s in self.sections],
            "stats": self.stats,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def validate(self) -> list[str]:
        """Return a list of contract violations. Empty list means healthy.

        Deliberately non-fatal: a downstream agent should be able to see exactly
        what is wrong with a parse rather than get an opaque exception.
        """
        problems: list[str] = []
        if not self.source_id:
            problems.append("source_id is empty")
        if not self.format_type or self.format_type == "unknown":
            problems.append("format_type was not set by the parser")
        for i, sec in enumerate(self.sections):
            if sec.block_id != i:
                problems.append(f"section[{i}].block_id is {sec.block_id}, expected {i}")
            if sec.type not in {
                "heading", "paragraph", "table", "list_item",
                "code_block", "caption", "image",
            }:
                problems.append(f"section[{i}] has unknown type '{sec.type}'")
            if sec.type != "heading" and not sec.content.strip():
                problems.append(f"section[{i}] ({sec.type}) has empty content")
        return problems


class ParseError(Exception):
    """Raised when a parser cannot process a file at all."""


class MissingDependencyError(ParseError):
    """Raised when an optional engine for a format is not installed.

    Carries the install command so the agent can surface an actionable message
    instead of a bare ImportError.
    """

    def __init__(self, package: str, fmt: str, install_hint: str | None = None):
        hint = install_hint or f"pip install {package}"
        super().__init__(
            f"Format '{fmt}' requires the '{package}' package, which is not installed. "
            f"Install it with: {hint}"
        )
        self.package = package
        self.format = fmt


class BaseParser:
    """Interface every format parser implements.

    Subclasses declare `supported_extensions` and implement `process()`.
    Recoverable problems must be appended to `stats['warnings']` rather than
    raised — a parser that loses one table should still return the rest.
    """

    #: Bare lowercase extensions without the dot, e.g. ["md", "txt"].
    supported_extensions: list[str] = []
    format_name: str = "unknown"
    #: Human-readable engine requirements, surfaced by `ragcli parse --list-formats`.
    requires: list[str] = []

    def supports(self, path: str) -> bool:
        ext = Path(path).suffix.lower().lstrip(".")
        return ext in self.supported_extensions

    def process(self, source: str, options: dict[str, Any] | None = None) -> ParsingResult:
        raise NotImplementedError

    # ── shared helpers ────────────────────────────────────────────────────

    @staticmethod
    def make_source_id(source: str) -> str:
        """Derive a stable, filesystem-safe id from a path or URL.

        Stable matters: re-ingesting the same document must produce the same id
        so the vector store upsert overwrites instead of duplicating.
        """
        if source.startswith(("http://", "https://")):
            stem = source.rstrip("/").split("/")[-1] or "webpage"
        else:
            stem = Path(source).stem
        stem = re.sub(r"[^\w\-.]", "_", stem, flags=re.UNICODE) or "document"
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:8]
        return f"{stem}_{digest}"

    @staticmethod
    def normalize_text(text: str) -> str:
        """Baseline normalization applied by every parser before emitting content.

        NFKC is not cosmetic for CJK corpora: PDF and OCR output routinely mixes
        full-width and half-width forms (ＡＢＣ vs ABC, １２３ vs 123), and those
        variants embed as different tokens, which silently splits one concept
        across two clusters.
        """
        if not text:
            return ""
        text = unicodedata.normalize("NFKC", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = "\n".join(line.rstrip() for line in text.split("\n"))
        return text.strip()


class ParserRouter:
    """Dispatches a source path to the parser registered for its extension."""

    def __init__(self) -> None:
        self._parsers: dict[str, BaseParser] = {}

    def register_parser(self, parser_instance: BaseParser) -> None:
        for ext in parser_instance.supported_extensions:
            self._parsers[ext] = parser_instance

    @staticmethod
    def is_url(source: str) -> bool:
        return source.startswith(("http://", "https://"))

    def get_parser(self, source: str) -> BaseParser | None:
        # URLs have no meaningful extension — Path("https://x.com").suffix is
        # ".com", which would misroute to a nonexistent parser. Match the scheme
        # explicitly and hand URLs to whichever parser claims "html".
        if self.is_url(source):
            return self._parsers.get("html")
        ext = Path(source).suffix.lower().lstrip(".")
        return self._parsers.get(ext)

    def detect_and_parse(
        self, source: str, options: dict[str, Any] | None = None
    ) -> ParsingResult:
        parser = self.get_parser(source)
        if parser is None:
            if self.is_url(source):
                raise ParseError(
                    "URL sources require the web parser. Install it with: "
                    "pip install trafilatura requests"
                )
            ext = Path(source).suffix.lower().lstrip(".") or "(no extension)"
            known = ", ".join(sorted(self._parsers))
            raise ParseError(
                f"No parser registered for '{ext}'. Supported extensions: {known}"
            )
        return parser.process(source, options or {})

    @property
    def registry(self) -> dict[str, BaseParser]:
        """Extension → parser instance. Read-only view for introspection."""
        return dict(self._parsers)
