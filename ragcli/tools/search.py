"""search — Vector similarity search.

Input: query string + vector store config
Output: JSON array of matching chunks with scores

Usage:
    ragcli search -q "你的问题" -d chroma --collection my_docs -k 5
    ragcli search -q "你的问题" -d qdrant --host localhost -k 10
    echo '{"query": "你的问题"}' | ragcli search -i - -d chroma
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import chromadb
except ImportError:
    chromadb = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


SEARCH_TOOL = {
    "name": "search",
    "description": "Perform vector similarity search against a vector database",
    "inputs": ["query string", "vector store config"],
    "outputs": ["matching chunks with scores (JSON)"],
}


def get_embedding(query: str, model_name: str, api: str, base_url: str | None) -> list[float]:
    """Get embedding for the query."""
    if api == "local":
        if SentenceTransformer is None:
            raise ImportError("sentence-transformers required. pip install sentence-transformers")
        model = SentenceTransformer(model_name)
        return model.encode(query, normalize_embeddings=True).tolist()
    elif api == "openai":
        if OpenAI is None:
            raise ImportError("openai package required. pip install openai")
        kwargs = {"base_url": base_url} if base_url else {}
        client = OpenAI(**kwargs)
        response = client.embeddings.create(model=model_name, input=query)
        return response.data[0].embedding
    else:
        raise ValueError(f"Unknown API: {api}")


def search_chroma(query: str, collection_name: str, host: str | None, port: int | None, k: int, model_name: str, api: str, base_url: str | None) -> list[dict]:
    """Search ChromaDB."""
    if chromadb is None:
        raise ImportError("chromadb required. pip install chromadb")

    client_args = {}
    if host and port:
        client_args["host"] = host
        client_args["port"] = port
    else:
        client_args["persist_directory"] = "./chroma_data"

    client = chromadb.Client(**client_args)
    collection = client.get_collection(name=collection_name)

    query_emb = get_embedding(query, model_name, api, base_url)
    results = collection.query(query_embeddings=[query_emb], n_results=k, include=["documents", "metadatas", "distances"])

    output = []
    for i in range(len(results["ids"][0])):
        output.append({
            "id": results["ids"][0][i],
            "text": results["documents"][0][i],
            "score": 1.0 - results["distances"][0][i],
            "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
        })
    return output


def search_qdrant(query: str, collection_name: str, host: str, port: int, k: int, model_name: str, api: str, base_url: str | None) -> list[dict]:
    """Search Qdrant."""
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        raise ImportError("qdrant-client required. pip install qdrant-client")

    client = QdrantClient(host=host, port=port)
    query_emb = get_embedding(query, model_name, api, base_url)

    hits = client.search(
        collection_name=collection_name,
        query_vector=query_emb,
        limit=k,
    )

    output = []
    for hit in hits:
        output.append({
            "id": hit.id,
            "text": hit.payload.get("text", ""),
            "score": hit.score,
            "metadata": {k: v for k, v in hit.payload.items() if k != "text"},
        })
    return output


def search_milvus(query: str, collection_name: str, host: str, port: int, k: int, model_name: str, api: str, base_url: str | None) -> list[dict]:
    """Search Milvus."""
    try:
        from pymilvus import connections, utility, Collection
    except ImportError:
        raise ImportError("pymilvus required. pip install pymilvus")

    connections.connect(alias="default", host=host, port=port)
    collection = Collection(collection_name)
    collection.load()

    query_emb = get_embedding(query, model_name, api, base_url)

    search_params = {"metric_type": "COSINE", "params": {"ef": 64}}
    results = collection.search(
        data=[query_emb],
        anns_field="embedding",
        param=search_params,
        limit=k,
        output_fields=["text"],
    )

    output = []
    for hits in results:
        for hit in hits:
            output.append({
                "id": hit.id,
                "text": hit.entity.get("text", ""),
                "score": hit.distance,
                "metadata": hit.entity.get("payload", {}),
            })
    return output


def run(args: argparse.Namespace):
    """Main entry point for search command."""
    # Get query from stdin or argument
    if args.input and args.input != "-":
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        query = data.get("query", args.query)
    elif args.input == "-":
        data = json.loads(sys.stdin.read())
        query = data.get("query", "")
    else:
        query = args.query

    if not query:
        print("[search] ERROR: Need a query. Use -q or provide JSON with 'query' field", file=sys.stderr)
        sys.exit(1)

    print(f"[search] Searching: '{query[:50]}...' in {args.store}:{args.collection}", file=sys.stderr)

    if args.store == "chroma":
        result = search_chroma(query, args.collection, args.host, args.port, args.k, args.model, args.api, args.base_url)
    elif args.store == "qdrant":
        result = search_qdrant(query, args.collection, args.host or "localhost", args.port or 6333, args.k, args.model, args.api, args.base_url)
    elif args.store == "milvus":
        result = search_milvus(query, args.collection, args.host or "localhost", args.port or 19530, args.k, args.model, args.api, args.base_url)
    else:
        raise ValueError(f"Unknown store: {args.store}")

    print(json.dumps(result, ensure_ascii=False, indent=2))


def register_parser(subparsers):
    parser = subparsers.add_parser("search", help="Vector similarity search")
    parser.add_argument("-q", "--query", help="Search query string")
    parser.add_argument("-i", "--input", help="Input file with query JSON (stdin with '-')")
    parser.add_argument("-k", "--top-k", type=int, default=5, help="Number of results (default: 5)")
    parser.add_argument("--store", choices=["chroma", "qdrant", "milvus"], default="chroma", help="Vector store backend")
    parser.add_argument("--collection", default="rag_collection", help="Collection name")
    parser.add_argument("--model", default="BAAI/bge-m3", help="Embedding model for query")
    parser.add_argument("--api", choices=["local", "openai"], default="local", help="Embedding backend")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL")
    parser.add_argument("--host", default=None, help="Host for remote stores")
    parser.add_argument("--port", type=int, default=None, help="Port for remote stores")
    parser.set_defaults(func=run)
