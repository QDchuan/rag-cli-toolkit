"""graph — Build knowledge graph from chunks for GraphRAG.

Input: JSON array of tagged chunks
Output: JSON with nodes, edges, and metadata

Usage:
    ragcli graph -i tagged.json -o knowledge_graph.json --model gpt-4o-mini
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


GRAPH_TOOL = {
    "name": "graph",
    "description": "Build a knowledge graph (nodes + edges) from text chunks for multi-hop reasoning",
    "inputs": ["tagged chunks (JSON array)"],
    "outputs": ["knowledge graph (JSON with nodes, edges)"],
}


def load_chunks(input_source: str | None) -> list[dict]:
    """Load chunks."""
    if input_source is None or input_source == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(input_source).read_text(encoding="utf-8"))


def extract_entities_openai(chunks: list[dict], model_name: str = "gpt-4o-mini") -> dict:
    """Extract entities and relationships using LLM."""
    if OpenAI is None:
        raise ImportError("openai package required. pip install openai")

    client = OpenAI()
    nodes = {}  # id -> {"label": str, "type": str, "properties": dict}
    edges = []  # [{"source": str, "target": str, "relation": str}]
    node_id_counter = 0

    for chunk in chunks:
        content = chunk.get("text", "")[:3000]
        doc_id = chunk.get("doc_id", "unknown")

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": (
                    "你是一个知识图谱构建助手。请从以下文本中提取实体和关系。\n\n"
                    "要求：\n"
                    "1. 识别所有关键实体（人物、组织、产品、概念、事件等）\n"
                    "2. 识别实体之间的关系\n"
                    "3. 输出 JSON 格式，包含 'entities' 和 'relations' 两个数组\n"
                    "4. entities: [{\"id\": \"唯一标识\", \"label\": \"名称\", \"type\": \"类型\"}]\n"
                    "5. relations: [{\"source\": \"源实体id\", \"target\": \"目标实体id\", \"relation\": \"关系描述\"}]\n"
                    "6. 只输出 JSON，不要其他文字。"
                )},
                {"role": "user", "content": f"[{doc_id}]\n{content}"},
            ],
            temperature=0.1,
            max_tokens=500,
        )

        try:
            result = json.loads(response.choices[0].message.content.strip())
        except json.JSONDecodeError:
            continue

        for entity in result.get("entities", []):
            eid = entity["id"]
            if eid not in nodes:
                nodes[eid] = {
                    "label": entity["label"],
                    "type": entity.get("type", "concept"),
                    "source_doc": doc_id,
                }
            else:
                # Merge source docs
                if isinstance(nodes[eid].get("source_doc"), str):
                    nodes[eid]["source_doc"] = [nodes[eid]["source_doc"]]
                nodes[eid]["source_doc"].append(doc_id)

        for relation in result.get("relations", []):
            edges.append({
                "source": relation["source"],
                "target": relation["target"],
                "relation": relation["relation"],
            })

    return {
        "nodes": [{"id": k, **v} for k, v in nodes.items()],
        "edges": edges,
        "stats": {
            "num_nodes": len(nodes),
            "num_edges": len(edges),
            "entity_types": list(set(n.get("type", "unknown") for n in nodes.values())),
        },
    }


def run(args: argparse.Namespace):
    """Main entry point for graph command."""
    chunks = load_chunks(args.input)
    print(f"[graph] Building knowledge graph from {len(chunks)} chunks...", file=sys.stderr)

    result = extract_entities_openai(chunks, args.model)

    output = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"[graph] Wrote graph to {args.output} ({result['stats']['num_nodes']} nodes, {result['stats']['num_edges']} edges)", file=sys.stderr)
    else:
        print(output)


def register_parser(subparsers):
    parser = subparsers.add_parser("graph", help="Build knowledge graph from chunks")
    parser.add_argument("-i", "--input", help="Input file (stdin if omitted)")
    parser.add_argument("-o", "--output", help="Output file")
    parser.add_argument("--model", default="gpt-4o-mini", help="LLM model for entity extraction")
    parser.set_defaults(func=run)
