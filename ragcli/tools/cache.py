"""cache — Semantic caching for RAG responses.

Input: query + cache DB (file or directory)
Output: cached response if found, or miss

Usage:
    ragcli cache -q "你的问题" --store ./semantic_cache.db -k 0.92
    # On miss, save the result back
    echo '{"result": "LLM生成的答案"}' | ragcli cache -i - -q "你的问题" --save
"""

import argparse
import json
import hashlib
import sys
from pathlib import Path

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

try:
    import numpy as np
except ImportError:
    np = None


CACHE_TOOL = {
    "name": "cache",
    "description": "Semantic cache for RAG responses — look up by similarity or save new entries",
    "inputs": ["query string", "optional response to cache"],
    "outputs": ["cached response JSON or miss"],
}


def get_embedding(text: str, model_name: str = "BAAI/bge-m3") -> list[float]:
    """Get embedding for text."""
    if SentenceTransformer is None:
        raise ImportError("sentence-transformers required. pip install sentence-transformers")
    model = SentenceTransformer(model_name)
    return model.encode(text, normalize_embeddings=True).tolist()


def cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity."""
    if np is None:
        return sum(x * y for x, y in zip(a, b)) / (
            (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5) + 1e-9
        )
    va, vb = np.array(a), np.array(b)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-9))


def load_cache(cache_file: str) -> dict:
    """Load cache from file."""
    path = Path(cache_file)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"entries": []}


def save_cache(cache_file: str, data: dict):
    """Save cache to file."""
    Path(cache_file).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run(args: argparse.Namespace):
    """Main entry point for cache command."""
    cache_file = args.store
    cache = load_cache(cache_file)

    # Get query
    if args.input and args.input != "-":
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        query = data.get("query", args.query)
        result_to_save = data.get("result")
    elif args.input == "-":
        data = json.loads(sys.stdin.read())
        query = data.get("query", args.query)
        result_to_save = data.get("result")
    else:
        query = args.query
        result_to_save = None

    emb = get_embedding(query, args.model)
    hash_key = hashlib.md5(emb.tobytes() if hasattr(emb, 'tobytes') else bytes(str(emb), 'utf-8')).hexdigest()[:16]

    # Search cache
    best_match = None
    best_score = 0
    for entry in cache["entries"]:
        sim = cosine_sim(emb, entry["embedding"])
        if sim > best_score:
            best_score = sim
            best_match = entry

    if best_match and best_score >= args.threshold:
        print(json.dumps({
            "status": "hit",
            "similarity": round(best_score, 4),
            "original_query": best_match["query"],
            "result": best_match["result"],
        }, ensure_ascii=False, indent=2))
        return

    # Save mode
    if result_to_save is not None:
        cache["entries"].append({
            "hash": hash_key,
            "query": query,
            "result": result_to_save,
            "embedding": emb,
            "created_at": args.created_at,
        })
        save_cache(cache_file, cache)
        print(json.dumps({"status": "saved", "hash": hash_key}, ensure_ascii=False, indent=2))
        return

    # Miss
    print(json.dumps({
        "status": "miss",
        "similarity": round(best_score, 4),
        "threshold": args.threshold,
    }, ensure_ascii=False, indent=2))


def register_parser(subparsers):
    parser = subparsers.add_parser("cache", help="Semantic cache for RAG responses")
    parser.add_argument("-q", "--query", help="Query string")
    parser.add_argument("-i", "--input", help="Input JSON with query/result (stdin with '-')")
    parser.add_argument("--store", default="./semantic_cache.json", help="Cache file path")
    parser.add_argument("--model", default="BAAI/bge-m3", help="Embedding model")
    parser.add_argument("--threshold", type=float, default=0.92, help="Similarity threshold for hit")
    parser.add_argument("--save", action="store_true", help="Save result to cache")
    parser.add_argument("--created-at", default=None, help="Timestamp for cache entry")
    parser.set_defaults(func=run)
