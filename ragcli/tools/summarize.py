"""summarize — Generate summaries for text chunks.

Input: JSON array of chunks
Output: JSON array with 'summary' field added

Usage:
    ragcli summarize -i chunks.json -o summarized.json --model gpt-4o-mini
    ragcli summarize -i chunks.json -o summarized.json --local --model t5-base
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


SUMMARIZE_TOOL = {
    "name": "summarize",
    "description": "Generate short summaries for text chunks using LLM or local model",
    "inputs": ["chunks (JSON array)"],
    "outputs": ["chunks with 'summary' field"],
}


def load_chunks(input_source: str | None) -> list[dict]:
    """Load chunks."""
    if input_source is None or input_source == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(input_source).read_text(encoding="utf-8"))


def summarize_openai(chunks: list[dict], model_name: str, max_tokens: int = 80, temperature: float = 0.3) -> list[dict]:
    """Summarize using OpenAI-compatible API."""
    if OpenAI is None:
        raise ImportError("openai package required. pip install openai")

    client = OpenAI()
    results = []

    for chunk in chunks:
        content = chunk.get("text", "")
        title = chunk.get("header", chunk.get("doc_id", ""))

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": (
                    "你是一个专业的文本摘要助手。请阅读给定文档片段，"
                    "生成一句不超过 50 个字的中文摘要。\n"
                    "摘要应包含：主题 + 核心观点/结论。\n"
                    "只输出摘要内容，不要加前缀或后缀。"
                )},
                {"role": "user", "content": f"标题：{title}\n\n内容：{content}"},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )

        summary = response.choices[0].message.content.strip()
        chunk["summary"] = summary
        chunk["summary_model"] = model_name
        results.append(chunk)

    return results


def summarize_local(chunks: list[dict], model_name: str) -> list[dict]:
    """Summarize using local transformer model (T5/BART)."""
    try:
        from transformers import pipeline
    except ImportError:
        raise ImportError("transformers required. pip install transformers")

    print(f"[summarize] Loading local model: {model_name}", file=sys.stderr)
    summarizer = pipeline("summarization", model=model_name)

    results = []
    for chunk in chunks:
        content = chunk.get("text", "")
        if len(content) < 50:
            chunk["summary"] = content  # Too short, use as-is
            results.append(chunk)
            continue

        try:
            max_input = 512 - 50  # Leave room for output
            truncated = content[:max_input]
            summary_result = summarizer(truncated, max_length=60, min_length=20, do_sample=False)
            summary = summary_result[0]["summary_text"]
            chunk["summary"] = summary
            chunk["summary_model"] = model_name
        except Exception:
            chunk["summary"] = content[:100] + "..."  # Fallback
            chunk["summary_model"] = f"{model_name}_fallback"

        results.append(chunk)

    return results


def run(args: argparse.Namespace):
    """Main entry point for summarize command."""
    chunks = load_chunks(args.input)
    print(f"[summarize] Summarizing {len(chunks)} chunks...", file=sys.stderr)

    if args.local:
        result = summarize_local(chunks, args.model)
    else:
        result = summarize_openai(chunks, args.model, args.max_tokens, args.temperature)

    output = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"[summarize] Wrote {len(result)} summarized chunks to {args.output}", file=sys.stderr)
    else:
        print(output)


def register_parser(subparsers):
    parser = subparsers.add_parser("summarize", help="Generate summaries for text chunks")
    parser.add_argument("-i", "--input", help="Input file (stdin if omitted)")
    parser.add_argument("-o", "--output", help="Output file")
    parser.add_argument("--model", default="gpt-4o-mini", help="Model name")
    parser.add_argument("--local", action="store_true", help="Use local model instead of API")
    parser.add_argument("--max-tokens", type=int, default=80, help="Max tokens for summary")
    parser.add_argument("--temperature", type=float, default=0.3, help="Sampling temperature")
    parser.set_defaults(func=run)
