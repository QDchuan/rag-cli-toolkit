"""hybrid — Hybrid search combining vector + BM25.

Input: query string + embedded chunks (JSON)
Output: JSON array of merged results with RRF scores

Usage:
    ragcli hybrid -q "你的问题" -i embedded.json -k 10
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    BM25Okapi = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None


HYBRID_TOOL = {
    "name": "hybrid",
    "description": "Hybrid search combining semantic vector search and BM25 keyword matching using Reciprocal Rank Fusion",
    "inputs": ["query string", "embedded chunks (JSON)"],
    "outputs": ["merged results with RRF scores (JSON)"],
}


def tokenize(text: str) -> list[str]:
    """Simple tokenizer for BM25."""
    # For Chinese, use simple character-level; for English, word-level
    import re
    words = re.findall(r'[\w]+|[^\w\s]', text.lower())
    return [w for w in words if w.strip()]


def load_chunks(input_source: str | None) -> list[dict]:
    """Load chunks from file or stdin."""
    if input_source is None or input_source == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(input_source).read_text(encoding="utf-8"))


def get_embedding(query: str, model_name: str) -> list[float]:
    """Get embedding for query."""
    if SentenceTransformer is None:
        raise ImportError("sentence-transformers required. pip install sentence-transformers")
    model = SentenceTransformer(model_name)
    return model.encode(query, normalize_embeddings=True).tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    import numpy as np
    va, vb = np.array(a), np.array(b)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-9))


def hybrid_search(
    query: str,
    chunks: list[dict],
    top_k: int,
    model_name: str,
    bm25_weight: float = 0.4,
    vector_weight: float = 0.6,
    k_rrf: int = 60,
) -> list[dict]:
    """Hybrid search with RRF fusion."""
    import numpy as np

    # 1. Vector search
    query_emb = get_embedding(query, model_name)
    vector_scores = []
    for chunk in chunks:
        sim = cosine_similarity(query_emb, chunk["embedding"])
        vector_scores.append(sim)

    # Rank by vector score
    vector_ranked = sorted(enumerate(vector_scores), key=lambda x: x[1], reverse=True)

    # 2. BM25 search
    if BM25Okapi is None:
        raise ImportError("rank-bm25 required. pip install rank-bm25")

    tokenized_docs = [tokenize(c.get("text", "")) for c in chunks]
    bm25 = BM25Okapi(tokenized_docs)
    query_tokens = tokenize(query)
    bm25_scores = bm25.get_scores(query_tokens)

    # Rank by BM25 score
    bm25_ranked = sorted(enumerate(bm25_scores.tolist()), key=lambda x: x[1], reverse=True)

    # 3. RRF Fusion
    rrf_scores = {}
    for rank, (idx, _) in enumerate(vector_ranked, 1):
        rrf_scores[idx] = rrf_scores.get(idx, 0) + vector_weight * (1 / (k_rrf + rank))
    for rank, (idx, _) in enumerate(bm25_ranked, 1):
        rrf_scores[idx] = rrf_scores.get(idx, 0) + bm25_weight * (1 / (k_rrf + rank))

    # Sort by RRF score
    ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    output = []
    for idx, score in ranked:
        chunk = chunks[idx]
        output.append({
            "id": f"{chunk.get('doc_id', 'doc')}_{chunk.get('chunk_index', 0)}",
            "text": chunk.get("text", ""),
            "vector_score": round(vector_scores[idx], 4),
            "bm25_score": round(float(bm25_scores[idx]), 4),
            "rrf_score": round(score, 6),
            "metadata": {k: v for k, v in chunk.items() if k not in ("embedding",)},
        })

    return output


def run(args: argparse.Namespace):
    """Main entry point for hybrid command."""
    chunks = load_chunks(args.input)
    print(f"[hybrid] Hybrid search: '{args.query}' over {len(chunks)} chunks", file=sys.stderr)

    result = hybrid_search(
        query=args.query,
        chunks=chunks,
        top_k=args.top_k,
        model_name=args.model,
        bm25_weight=args.bm25_weight,
        vector_weight=args.vector_weight,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


def register_parser(subparsers):
    parser = subparsers.add_parser("hybrid", help="Hybrid search (vector + BM25 with RRF)")
    parser.add_argument("-q", "--query", required=True, help="Search query")
    parser.add_argument("-i", "--input", required=True, help="Input embedded chunks JSON")
    parser.add_argument("-k", "--top-k", type=int, default=10, help="Number of results")
    parser.add_argument("--model", default="BAAI/bge-m3", help="Embedding model")
    parser.add_argument("--bm25-weight", type=float, default=0.4, help="BM25 weight in fusion")
    parser.add_argument("--vector-weight", type=float, default=0.6, help="Vector weight in fusion")
    parser.set_defaults(func=run)
