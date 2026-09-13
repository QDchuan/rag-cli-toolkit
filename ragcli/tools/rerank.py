"""rerank — Cross-Encoder re-ranking of retrieved results.

Input: query + list of (chunk, score) pairs
Output: Re-ranked results with new scores

Usage:
    ragcli rerank -q "你的问题" -i results.json -o reranked.json --model BAAI/bge-reranker-v2-m3
    cat results.json | ragcli rerank -q "你的问题" -i - -o reranked.json
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from FlagEmbedding import CrossEncoder
except ImportError:
    CrossEncoder = None

try:
    from sentence_transformers import CrossEncoder as STCrossEncoder
except ImportError:
    STCrossEncoder = None


RERANK_TOOL = {
    "name": "rerank",
    "description": "Re-rank retrieved results using a Cross-Encoder model for better precision",
    "inputs": ["query string", "retrieved results (JSON array)"],
    "outputs": ["re-ranked results with new scores (JSON)"],
}


def load_results(input_source: str | None) -> list[dict]:
    """Load search results."""
    if input_source is None or input_source == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(input_source).read_text(encoding="utf-8"))


def rerank_flagembed(query: str, results: list[dict], model_name: str, top_k: int) -> list[dict]:
    """Re-rank using FlagEmbedding Cross-Encoder."""
    if CrossEncoder is None:
        raise ImportError("FlagEmbedding required. pip install FlagEmbedding")

    print(f"[rerank] Loading model: {model_name}", file=sys.stderr)
    model = CrossEncoder(model_name)

    pairs = [(query, r.get("text", "")) for r in results]
    scores = model.compute_score(pairs, normalize=True)

    # Attach scores and sort
    for result, score in zip(results, scores):
        result["rerank_score"] = round(float(score), 6)

    ranked = sorted(results, key=lambda x: x["rerank_score"], reverse=True)[:top_k]
    return ranked


def rerank_st(query: str, results: list[dict], model_name: str, top_k: int) -> list[dict]:
    """Re-rank using sentence-transformers CrossEncoder."""
    if STCrossEncoder is None:
        raise ImportError("sentence-transformers >= 3.0 required. pip install sentence-transformers")

    print(f"[rerank] Loading model: {model_name}", file=sys.stderr)
    model = STCrossEncoder(model_name)

    pairs = [[query, r.get("text", "")] for r in results]
    scores = model.predict(pairs)

    for result, score in zip(results, scores):
        result["rerank_score"] = round(float(score), 6)

    ranked = sorted(results, key=lambda x: x["rerank_score"], reverse=True)[:top_k]
    return ranked


def run(args: argparse.Namespace):
    """Main entry point for rerank command."""
    results = load_results(args.input)
    print(f"[rerank] Re-ranking {len(results)} results for: '{args.query[:50]}...'", file=sys.stderr)

    if args.backend == "flag":
        result = rerank_flagembed(args.query, results, args.model, args.top_k)
    elif args.backend == "st":
        result = rerank_st(args.query, results, args.model, args.top_k)
    else:
        raise ValueError(f"Unknown backend: {args.backend}")

    output = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"[rerank] Wrote {len(result)} reranked results to {args.output}", file=sys.stderr)
    else:
        print(output)


def register_parser(subparsers):
    parser = subparsers.add_parser("rerank", help="Cross-Encoder re-ranking")
    parser.add_argument("-q", "--query", required=True, help="Search query")
    parser.add_argument("-i", "--input", required=True, help="Input results JSON (stdin with '-')")
    parser.add_argument("-o", "--output", help="Output file")
    parser.add_argument("--model", default="BAAI/bge-reranker-v2-m3", help="Cross-Encoder model")
    parser.add_argument("--backend", choices=["flag", "st"], default="flag", help="Backend library")
    parser.add_argument("--top-k", type=int, default=10, help="Keep top K after re-ranking")
    parser.set_defaults(func=run)
