"""chunk — Document chunking tool.

Input: file path or stdin JSON with documents
Output: JSON array of chunks to stdout or file

Usage:
    ragcli chunk -f doc.pdf -o chunks.json
    ragcli chunk --format markdown -f doc.md -o chunks.json
    cat docs.json | ragcli chunk -i - -o chunks.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


CHUNK_TOOL = {
    "name": "chunk",
    "description": "Split documents into chunks for embedding and retrieval",
    "inputs": ["documents (file or stdin)"],
    "outputs": ["chunks (JSON array)"],
}


def chunk_recursive(
    text: str,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    separators: list[str] | None = None,
) -> list[dict]:
    """Recursive character chunking with overlap."""
    if separators is None:
        separators = ["\n\n", "\n", " ", ""]

    chunks = []
    current_start = 0
    max_iterations = max(1, len(text) // max(1, chunk_size - chunk_overlap)) + 5

    iteration = 0
    while current_start < len(text) and iteration < max_iterations:
        iteration += 1
        current_end = min(current_start + chunk_size, len(text))

        # Prevent infinite loop when overlap >= chunk_size
        if current_end <= current_start:
            current_end = current_start + 1

        chunk_text = text[current_start:current_end]

        for sep in separators:
            if sep in chunk_text:
                last_sep = chunk_text.rfind(sep)
                if last_sep > chunk_size * 0.5:
                    chunk_text = chunk_text[:last_sep + len(sep)]
                    break

        stripped = chunk_text.strip()
        if stripped:
            chunks.append({
                "text": stripped,
                "start_pos": current_start,
                "end_pos": current_start + len(chunk_text),
            })

        # Advance: next start = current_end - overlap, but ensure forward progress
        new_start = current_end - chunk_overlap
        if new_start <= current_start:
            new_start = current_start + 1
        current_start = new_start

    return chunks


def chunk_by_headers(
    text: str, headers: list[tuple[str, str]] | None = None
) -> list[dict]:
    """Structure-aware chunking by markdown headers."""
    if headers is None:
        headers = [("###", "Header 3"), ("##", "Header 2"), ("#", "Header 1")]

    chunks = []
    lines = text.split("\n")
    current_chunk_lines = []
    current_header = ""
    header_level = 0

    for line in lines:
        matched = False
        for prefix, name in headers:
            if line.startswith(prefix):
                if current_chunk_lines:
                    chunks.append({
                        "text": "\n".join(current_chunk_lines).strip(),
                        "header": current_header,
                        "level": header_level,
                    })
                current_chunk_lines = []
                current_header = line.strip()
                header_level = len(prefix)
                matched = True
                break

        if not matched:
            current_chunk_lines.append(line)

    if current_chunk_lines:
        chunks.append({
            "text": "\n".join(current_chunk_lines).strip(),
            "header": current_header,
            "level": header_level,
        })

    return chunks


def extract_text_from_pdf(file_path: str) -> str:
    """Extract text from PDF file."""
    if PdfReader is None:
        raise ImportError("pypdf is required for PDF processing. Install with: pip install pypdf")
    reader = PdfReader(file_path)
    texts = []
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            texts.append(page_text)
    return "\n\n".join(texts)


def load_documents(input_source: str | None) -> list[dict]:
    """Load documents from file or stdin."""
    if input_source is None or input_source == "-":
        data = json.loads(sys.stdin.read())
        if isinstance(data, list):
            return [{"id": i, "content": d} if isinstance(d, str) else d for i, d in enumerate(data)]
        return [data]

    path = Path(input_source)
    if path.suffix == ".pdf" and PdfReader:
        text = extract_text_from_pdf(str(path))
        return [{"id": "doc_0", "content": text, "source": str(path)}]

    if path.suffix in (".md", ".txt", ".json"):
        content = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            data = json.loads(content)
            if isinstance(data, list):
                return [{"id": i, "content": d} if isinstance(d, str) else d for i, d in enumerate(data)]
            return [data]
        return [{"id": "doc_0", "content": content, "source": str(path)}]

    raise ValueError(f"Unsupported file format: {path.suffix}")


def run(args: argparse.Namespace):
    """Main entry point for chunk command."""
    docs = load_documents(args.input)

    chunks = []
    for doc in docs:
        content = doc.get("content", "")
        source = doc.get("source", args.input or "unknown")
        doc_id = doc.get("id", f"doc_{len(chunks)}")

        if args.format == "headers":
            result = chunk_by_headers(content)
        else:
            result = chunk_recursive(
                content,
                chunk_size=args.chunk_size,
                chunk_overlap=args.chunk_overlap,
            )

        for i, chunk in enumerate(result):
            chunk["doc_id"] = doc_id
            chunk["chunk_index"] = i
            chunk["source"] = source
            chunks.append(chunk)

    output = json.dumps(chunks, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"[chunk] Wrote {len(chunks)} chunks to {args.output}", file=sys.stderr)
    else:
        print(output)


def register_parser(subparsers):
    parser = subparsers.add_parser("chunk", help="Split documents into chunks")
    parser.add_argument("-i", "--input", help="Input file (stdin if omitted)")
    parser.add_argument("-o", "--output", help="Output file (stdout if omitted)")
    parser.add_argument("--chunk-size", type=int, default=512, help="Chunk size in characters (default: 512)")
    parser.add_argument("--chunk-overlap", type=int, default=64, help="Overlap between chunks (default: 64)")
    parser.add_argument("--format", choices=["recursive", "headers"], default="recursive", help="Chunking strategy")
    parser.set_defaults(func=run)
