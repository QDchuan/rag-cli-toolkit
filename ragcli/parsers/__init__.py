"""Parser registry — the routing table from file type to engine.

Adding a new format is a two-line change: implement a `BaseParser` subclass and
append its `_register()` to `_PARSER_FACTORIES` below. Nothing else in the
pipeline needs to know the format exists.
"""

from __future__ import annotations

from typing import Any

from ragcli.parsers.base import (
    BaseParser,
    MissingDependencyError,
    ParseError,
    ParsingResult,
    ParserRouter,
    Section,
)

__all__ = [
    "BaseParser",
    "MissingDependencyError",
    "ParseError",
    "ParsingResult",
    "ParserRouter",
    "Section",
    "get_router",
    "route",
    "SUPPORTED_EXTENSIONS",
]


def _parser_factories():
    """Lazy imports so a missing optional dependency never breaks `ragcli list`."""
    from ragcli.parsers.image_parser import _register as image
    from ragcli.parsers.office_parser import _register as office
    from ragcli.parsers.pdf_parser import _register as pdf
    from ragcli.parsers.text_parser import _register as text
    from ragcli.parsers.web_parser import _register as web

    return [text, office, pdf, web, image]


_ROUTER: ParserRouter | None = None


def get_router() -> ParserRouter:
    """Build (once) and return the global parser router."""
    global _ROUTER
    if _ROUTER is None:
        router = ParserRouter()
        for factory in _parser_factories():
            router.register_parser(factory())
        _ROUTER = router
    return _ROUTER


def route(source: str, options: dict[str, Any] | None = None) -> ParsingResult:
    """Convenience wrapper: detect the format of `source` and parse it."""
    return get_router().detect_and_parse(source, options)


def SUPPORTED_EXTENSIONS() -> list[str]:  # noqa: N802 — exposed as a constant-like API
    """All file extensions the toolkit can parse, sorted."""
    exts: set[str] = set()
    for factory in _parser_factories():
        exts.update(factory().supported_extensions)
    return sorted(exts)
