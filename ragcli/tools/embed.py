"""embed — Generate embeddings for text chunks.

Input: JSON array of chunks with 'text' field
Output: JSON array of chunks with 'embedding' field added

Usage:
    ragcli embed -i chunks.json -o embedded.json --model BAAI/bge-m3
    ragcli embed -i chunks.json -o embedded.json --api openai --model text-embedding-3-large
    ragcli embed -i chunks.json -o embedded.json --api openai --model text-embedding-3-small --base-url https://...
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


EMBED_TOOL = {
    "name": "embed",
    "description": "Generate vector embeddings for text chunks",
    "inputs": ["chunks (JSON array with 'text' field)"],
    "outputs": ["chunks with 'embedding' field"],
}


def load_chunks(input_source: str | None) -> list[dict]:
    """Load chunks from file or stdin."""
    if input_source is None or input_source == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(input_source).read_text(encoding="utf-8"))


def embed_local(chunks: list[dict], model_name: str, batch_size: int = 64) -> list[dict]:
    """Embed using local sentence-transformers model."""
    if SentenceTransformer is None:
        raise ImportError("sentence-transformers is required. Install: pip install sentence-transformers")

    print(f"[embed] Loading model: {model_name}", file=sys.stderr)
    model = SentenceTransformer(model_name)

    texts = [c.get("text", "") for c in chunks]
    embeddings_list = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        emb = model.encode(batch, normalize_embeddings=True, show_progress_bar=False)
        embeddings_list.extend(emb.tolist())

    for chunk, embedding in zip(chunks, embeddings_list):
        chunk["embedding"] = embedding
        chunk["model"] = model_name
        chunk["dimension"] = len(embedding)

    return chunks


def embed_openai(chunks: list[dict], model_name: str, base_url: str | None = None) -> list[dict]:
    """Embed using OpenAI-compatible API."""
    if OpenAI is None:
        raise ImportError("openai package is required. Install: pip install openai")

    kwargs = {}
    if base_url:
        kwargs["base_url"] = base_url

    client = OpenAI(**kwargs)
    results = []

    for chunk in chunks:
        text = chunk.get("text", "")
        response = client.embeddings.create(model=model_name, input=text)
        embedding = response.data[0].embedding

        chunk["embedding"] = embedding
        chunk["model"] = model_name
        chunk["dimension"] = len(embedding)
        results.append(chunk)

    return results


def run(args: argparse.Namespace):
    """Main entry point for embed command."""
    chunks = load_chunks(args.input)
    print(f"[embed] Processing {len(chunks)} chunks...", file=sys.stderr)

    if args.api == "local":
        result = embed_local(chunks, args.model, args.batch_size)
    elif args.api == "openai":
        result = embed_openai(chunks, args.model, args.base_url)
    else:
        raise ValueError(f"Unknown API: {args.api}")

    output = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"[embed] Wrote {len(result)} embedded chunks to {args.output}", file=sys.stderr)
    else:
        print(output)


def register_parser(subparsers):
    parser = subparsers.add_parser("embed", help="Generate embeddings for text chunks")
    parser.add_argument("-i", "--input", help="Input file (stdin if omitted)")
    parser.add_argument("-o", "--output", help="Output file (stdout if omitted)")
    parser.add_argument("--model", default="BAAI/bge-m3", help="Model name (default: BAAI/bge-m3)")
    parser.add_argument("--api", choices=["local", "openai"], default="local", help="Embedding backend")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL")
    parser.add_argument("--batch-size", type=int, default=64, help="Batch size for local embedding")
    parser.set_defaults(func=run)
