"""index — Build vector index from embedded chunks.

Input: JSON array of chunks with 'embedding' field
Output: Index created in specified vector store

Usage:
    ragcli index -i embedded.json -d chroma --collection my_docs
    ragcli index -i embedded.json -d qdrant --host localhost --port 6333
    ragcli index -i embedded.json -d milvus --host localhost --port 19530
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
    from qdrant_client import QdrantClient
except ImportError:
    QdrantClient = None

try:
    from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType, utility
except ImportError:
    connections = None


INDEX_TOOL = {
    "name": "index",
    "description": "Build vector index from embedded chunks into a vector database",
    "inputs": ["embedded chunks (JSON array with 'embedding' field)"],
    "outputs": ["vector index created"],
}


def load_embedded(input_source: str | None) -> list[dict]:
    """Load embedded chunks."""
    if input_source is None or input_source == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(input_source).read_text(encoding="utf-8"))


def index_chroma(chunks: list[dict], collection_name: str, host: str | None, port: int) -> dict:
    """Index into ChromaDB."""
    if chromadb is None:
        raise ImportError("chromadb required. Install: pip install chromadb")

    client_args = {}
    if host and port:
        client_args["host"] = host
        client_args["port"] = port
    else:
        client_args["persist_directory"] = "./chroma_data"

    client = chromadb.Client(**client_args)
    collection = client.get_or_create_collection(name=collection_name)

    ids = [f"{c.get('doc_id', 'doc')}_{c.get('chunk_index', 0)}" for c in chunks]
    texts = [c.get("text", "") for c in chunks]
    embeddings = [c["embedding"] for c in chunks]
    metadatas = [{k: v for k, v in c.items() if k not in ("text", "embedding")} for c in chunks]

    collection.upsert(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)

    return {"status": "ok", "count": len(ids), "collection": collection_name, "store": "chroma"}


def index_qdrant(chunks: list[dict], collection_name: str, host: str, port: int, dimension: int) -> dict:
    """Index into Qdrant."""
    if QdrantClient is None:
        raise ImportError("qdrant-client required. Install: pip install qdrant-client")

    client = QdrantClient(host=host, port=port)

    # Create collection if not exists
    try:
        client.get_collection(collection_name)
    except Exception:
        from qdrant_client.models import Distance, VectorParams
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
        )

    from qdrant_client.models import PointStruct, PayloadSchemaType

    # Create payload indexes for common fields
    for field in ["doc_id", "source", "chunk_index"]:
        try:
            client.create_payload_index(collection_name, field, field_schema=PayloadSchemaType.KEYWORD)
        except Exception:
            pass

    points = []
    for chunk in chunks:
        doc_id = chunk.get("doc_id", "doc")
        chunk_idx = chunk.get("chunk_index", 0)
        point_id = f"{doc_id}_{chunk_idx}"

        payload = {k: v for k, v in chunk.items() if k not in ("embedding",)}

        points.append(PointStruct(id=point_id, vector=chunk["embedding"], payload=payload))

    client.upsert(collection_name, points=points)

    return {"status": "ok", "count": len(points), "collection": collection_name, "store": "qdrant"}


def index_milvus(chunks: list[dict], collection_name: str, host: str, port: int, dimension: int) -> dict:
    """Index into Milvus."""
    if connections is None:
        raise ImportError("pymilvus required. Install: pip install pymilvus")

    connections.connect(alias="default", host=host, port=port)

    # Check if collection exists
    if utility.has_collection(collection_name):
        utility.drop_collection(collection_name)

    # Create schema
    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=64, is_primary=True),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dimension),
    ]
    # Add text field
    fields.append(FieldSchema(name="text", dtype=DataType.TEXT, max_length=65535))

    schema = CollectionSchema(fields, collection_name)
    collection = Collection(collection_name, schema)

    # Create index
    index_params = {"index_type": "HNSW", "params": {"M": 16, "efConstruction": 256}, "metric_type": "COSINE"}
    collection.create_index("embedding", index_params)

    # Prepare data
    ids = [f"{c.get('doc_id', 'doc')}_{c.get('chunk_index', 0)}" for c in chunks]
    texts = [c.get("text", "") for c in chunks]
    embeddings = [c["embedding"] for c in chunks]
    payloads = [[{k: v for k, v in c.items() if k not in ("embedding", "text")} for c in chunks]]

    collection.insert([ids, texts, embeddings, payloads[0]])
    collection.load()

    return {"status": "ok", "count": len(ids), "collection": collection_name, "store": "milvus"}


def run(args: argparse.Namespace):
    """Main entry point for index command."""
    chunks = load_embedded(args.input)
    if not chunks or "embedding" not in chunks[0]:
        print("[index] ERROR: Input must contain 'embedding' field", file=sys.stderr)
        sys.exit(1)

    dimension = len(chunks[0]["embedding"])
    print(f"[index] Indexing {len(chunks)} chunks, dimension={dimension}", file=sys.stderr)

    if args.store == "chroma":
        result = index_chroma(chunks, args.collection, args.host, args.port)
    elif args.store == "qdrant":
        result = index_qdrant(chunks, args.collection, args.host or "localhost", args.port or 6333, dimension)
    elif args.store == "milvus":
        result = index_milvus(chunks, args.collection, args.host or "localhost", args.port or 19530, dimension)
    else:
        raise ValueError(f"Unknown store: {args.store}")

    print(json.dumps(result, ensure_ascii=False, indent=2))


def register_parser(subparsers):
    parser = subparsers.add_parser("index", help="Build vector index from embedded chunks")
    parser.add_argument("-i", "--input", help="Input file (stdin if omitted)")
    parser.add_argument("--store", choices=["chroma", "qdrant", "milvus"], default="chroma", help="Vector store backend")
    parser.add_argument("--collection", default="rag_collection", help="Collection name")
    parser.add_argument("--host", default=None, help="Host for remote stores")
    parser.add_argument("--port", type=int, default=None, help="Port for remote stores")
    parser.set_defaults(func=run)
