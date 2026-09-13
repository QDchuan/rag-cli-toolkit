"""tagger — Auto-tagging / classification for text chunks.

Input: JSON array of chunks + tag schema (JSON)
Output: JSON array with 'tags' field added

Usage:
    ragcli tagger -i chunks.json -o tagged.json --schema tag_schema.json
    cat chunks.json | ragcli tagger -i - -o tagged.json --schema '{"domain": ["finance", "legal"]}'
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


TAGGER_TOOL = {
    "name": "tagger",
    "description": "Auto-classify and tag text chunks using LLM based on a predefined schema",
    "inputs": ["chunks (JSON array)", "tag schema (JSON)"],
    "outputs": ["chunks with 'tags' field"],
}


def load_chunks(input_source: str | None) -> list[dict]:
    """Load chunks."""
    if input_source is None or input_source == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(input_source).read_text(encoding="utf-8"))


def load_schema(schema_input: str) -> dict:
    """Load tag schema from file or JSON string."""
    path = Path(schema_input)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(schema_input)


def tag_openai(chunks: list[dict], schema: dict, model_name: str = "gpt-4o-mini") -> list[dict]:
    """Tag using OpenAI API."""
    if OpenAI is None:
        raise ImportError("openai package required. pip install openai")

    client = OpenAI()
    schema_str = json.dumps(schema, ensure_ascii=False)

    results = []
    for chunk in chunks:
        content = chunk.get("text", "")[:2000]  # Limit length

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": (
                    f"你是一个文档分类助手。请根据以下标签 schema 为文本打标签。\n\n"
                    f"Schema：{schema_str}\n\n"
                    f"只输出 JSON 格式结果，不要其他文字。\n"
                    f"如果某个字段无法确定，返回 'unknown'。\n"
                    f"entities 字段返回识别到的所有关键实体列表。"
                )},
                {"role": "user", "content": content},
            ],
            temperature=0.1,
            max_tokens=200,
        )

        try:
            tags = json.loads(response.choices[0].message.content.strip())
        except json.JSONDecodeError:
            tags = {"_raw": response.choices[0].message.content.strip()}

        chunk["tags"] = tags
        chunk["tag_model"] = model_name
        results.append(chunk)

    return results


def run(args: argparse.Namespace):
    """Main entry point for tagger command."""
    chunks = load_chunks(args.input)
    schema = load_schema(args.schema)
    print(f"[tagger] Tagging {len(chunks)} chunks with schema: {json.dumps(schema, ensure_ascii=False)[:100]}...", file=sys.stderr)

    result = tag_openai(chunks, schema, args.model)

    output = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"[tagger] Wrote {len(result)} tagged chunks to {args.output}", file=sys.stderr)
    else:
        print(output)


def register_parser(subparsers):
    parser = subparsers.add_parser("tagger", help="Auto-tagging for text chunks")
    parser.add_argument("-i", "--input", help="Input file (stdin if omitted)")
    parser.add_argument("-o", "--output", help="Output file")
    parser.add_argument("--schema", required=True, help="Tag schema file path or JSON string")
    parser.add_argument("--model", default="gpt-4o-mini", help="Model name")
    parser.set_defaults(func=run)
