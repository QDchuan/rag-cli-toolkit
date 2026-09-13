"""Web parser: remote URLs and local HTML files.

Engine choice — `trafilatura` over BeautifulSoup:
    BeautifulSoup gives you the DOM; it does not tell you what the *article* is.
    trafilatura is trained specifically to strip navigation, sidebars, ads,
    cookie banners and footers, keeping only the main content. For a docs site
    that difference is the gap between usable retrieval and pure noise.

Fallback chain:
    1. trafilatura (best precision)
    2. readability-lxml (good on news/blog layouts)
    3. naive tag-stripping (last resort, always succeeds)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ragcli.parsers.base import BaseParser, MissingDependencyError, ParseError, ParsingResult, Section

_DEFAULT_TIMEOUT = 20
_DEFAULT_UA = (
    "Mozilla/5.0 (compatible; ragcli/0.1; +https://github.com/QDchuan/rag-data-cleaning)"
)


class WebParser(BaseParser):
    """Parses http(s) URLs and local .html/.htm files."""

    format_name = "html"
    supported_extensions = ["html", "htm", "xhtml"]
    requires = ["trafilatura", "requests"]

    def process(self, source: str, options: dict[str, Any] | None = None) -> ParsingResult:
        options = options or {}
        warnings: list[str] = []
        is_url = source.startswith(("http://", "https://"))

        if is_url:
            html, final_url = self._fetch(source, options, warnings)
            source_path = final_url
            title = self._guess_title(html) or final_url
        else:
            path = Path(source)
            if not path.exists():
                raise ParseError(f"File not found: {source}")
            html = path.read_text(encoding="utf-8", errors="replace")
            source_path = str(path)
            title = self._guess_title(html) or path.stem

        # Extract main content, most precise engine first
        content, engine = self._extract_main_content(html, options, warnings)

        sections = self._html_to_sections(content, engine)
        word_count = sum(len(s.content.split()) for s in sections)

        return ParsingResult(
            source_id=self.make_source_id(source_path),
            source_path=source_path,
            format_type="html",
            meta={
                "title": title,
                "url": final_url if is_url else None,
                "word_count": word_count,
                "char_count": len(content),
            },
            sections=sections,
            stats={
                "parser_engine_used": engine,
                "total_blocks_extracted": len(sections),
                "warnings": warnings,
            },
        )

    # ── fetching ──────────────────────────────────────────────────────────

    def _fetch(
        self, url: str, options: dict[str, Any], warnings: list[str]
    ) -> tuple[str, str]:
        try:
            import requests  # type: ignore
        except ImportError:
            raise MissingDependencyError("requests", "html", "pip install requests")

        timeout = options.get("timeout", _DEFAULT_TIMEOUT)
        headers = {"User-Agent": options.get("user_agent", _DEFAULT_UA)}

        try:
            resp = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding
            return resp.text, resp.url
        except Exception as e:  # noqa: BLE001 — surface any network failure as ParseError
            raise ParseError(f"Failed to fetch {url}: {e}") from e

    # ── extraction ────────────────────────────────────────────────────────

    def _extract_main_content(
        self, html: str, options: dict[str, Any], warnings: list[str]
    ) -> tuple[str, str]:
        """Return (markdown_text, engine_name).

        `output_format='markdown'` matters: it preserves headings and tables,
        which the downstream chunker needs for structure-aware splitting.
        """
        try:
            import trafilatura  # type: ignore

            extracted = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=options.get("include_tables", True),
                include_links=options.get("include_links", False),
                include_images=False,
                favor_precision=options.get("favor_precision", True),
                output_format="markdown",
                url=options.get("url"),
            )
            if extracted and extracted.strip():
                return self.normalize_text(extracted), "trafilatura"
            warnings.append("trafilatura returned empty; trying readability")
        except ImportError:
            warnings.append(
                "trafilatura not installed (pip install trafilatura); trying readability"
            )

        try:
            from readability import Document  # type: ignore

            doc = Document(html)
            best = doc.summary(html_partial=True)
            if best and best.strip():
                return self.normalize_text(self._strip_tags(best)), "readability-lxml"
            warnings.append("readability returned empty; falling back to tag stripping")
        except ImportError:
            warnings.append("readability-lxml not installed; falling back to tag stripping")

        return self.normalize_text(self._strip_tags(html)), "naive-strip"

    @staticmethod
    def _strip_tags(html: str) -> str:
        """Remove script/style/nav then all tags. Preserves block breaks."""
        html = re.sub(
            r"<(script|style|noscript|svg|nav|footer|header)\b[^>]*>.*?</\1>",
            " ",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        html = re.sub(r"<!--.*?-->", " ", html, flags=re.DOTALL)
        html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        html = re.sub(
            r"</(p|div|li|h[1-6]|tr|section|article)>", "\n\n", html, flags=re.IGNORECASE
        )
        text = re.sub(r"<[^>]+>", "", html)
        text = (
            text.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
        )
        return text

    # ── structuring ───────────────────────────────────────────────────────

    def _html_to_sections(self, content: str, engine: str) -> list[Section]:
        """Split extracted Markdown into heading/table/paragraph sections."""
        sections: list[Section] = []
        heading_stack: list[tuple[int, str]] = []
        buffer: list[str] = []

        def flush():
            if not buffer:
                return
            text = self.normalize_text("\n".join(buffer))
            buffer.clear()
            if not text:
                return
            # Classify each paragraph independently. A table is often preceded
            # by body text inside the same buffer, so testing only the buffer's
            # first character would misclassify the table as prose.
            for para in re.split(r"\n\s*\n", text):
                para = para.strip()
                if not para:
                    continue
                lines = para.split("\n")
                is_table = lines[0].lstrip().startswith("|") and len(lines) >= 2
                sections.append(
                    Section(
                        block_id=len(sections),
                        type="table" if is_table else "paragraph",
                        heading_path=[h for _, h in heading_stack],
                        content=para,
                        metadata=(
                            {"rows": len(lines) - 2} if is_table and len(lines) > 2 else {}
                        ),
                    )
                )

        for line in content.split("\n"):
            heading = re.match(r"^(#{1,6})\s+(.*)$", line)
            if heading:
                flush()
                level = len(heading.group(1))
                title = heading.group(2).strip()
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
            buffer.append(line)

        flush()

        if not sections and content.strip():
            sections = [
                Section(block_id=0, type="paragraph", content=self.normalize_text(content))
            ]
        return sections

    @staticmethod
    def _guess_title(html: str) -> str | None:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()[:300]
        m = re.search(
            r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.IGNORECASE
        )
        if m:
            return re.sub(r"<[^>]+>", "", m.group(1)).strip()[:300]
        return None


def _register():
    return WebParser()
