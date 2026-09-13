"""evaluate — RAG Triad evaluation.

Input: golden dataset (JSON) + search results (JSON)
Output: Evaluation metrics (JSON)

Usage:
    ragcli evaluate -g golden.json -r results.json -o metrics.json
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


EVALUATE_TOOL = {
    "name": "evaluate",
    "description": "Evaluate RAG pipeline using RAG Triad metrics (context relevance, groundedness, answer relevance)",
    "inputs": ["golden dataset", "search/generation results"],
    "outputs": ["evaluation metrics (JSON)"],
}


def load_json(source: str | None, default=None) -> any:
    """Load JSON from file or stdin."""
    if source is None or source == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(source).read_text(encoding="utf-8"))


def evaluate_llm_as_judge(query: str, context: str, generated_answer: str, ground_truth: str, judge_model: str) -> dict:
    """Use LLM as judge to evaluate RAG Triad metrics."""
    if OpenAI is None:
        raise ImportError("openai package required. pip install openai")

    client = OpenAI()

    # Context Relevance: Are retrieved docs relevant to the query?
    ctx_response = client.chat.completions.create(
        model=judge_model,
        messages=[
            {"role": "system", "content": (
                "你是一个评估助手。请判断以下检索到的文档是否与用户查询相关。\n"
                "只输出一个 0-1 的分数：1.0 表示完全相关，0.0 表示完全不相关。\n"
                "只输出数字，不要其他文字。"
            )},
            {"role": "user", "content": f"查询：{query}\n\n文档：{context}"},
        ],
        temperature=0,
        max_tokens=10,
    )
    ctx_score = float(ctx_response.choices[0].message.content.strip())

    # Groundedness: Is the answer supported by the context?
    grd_response = client.chat.completions.create(
        model=judge_model,
        messages=[
            {"role": "system", "content": (
                "你是一个评估助手。请判断生成的答案是否完全基于提供的上下文。\n"
                "如果答案中有上下文不支持的内容，说明有幻觉。\n"
                "只输出一个 0-1 的分数：1.0 表示完全有据可依，0.0 表示严重幻觉。\n"
                "只输出数字，不要其他文字。"
            )},
            {"role": "user", "content": f"查询：{query}\n\n上下文：{context}\n\n答案：{generated_answer}"},
        ],
        temperature=0,
        max_tokens=10,
    )
    grd_score = float(grd_response.choices[0].message.content.strip())

    # Answer Relevance: Does the answer address the question?
    ans_response = client.chat.completions.create(
        model=judge_model,
        messages=[
            {"role": "system", "content": (
                "你是一个评估助手。请判断生成的答案是否直接回答了用户的问题。\n"
                "只输出一个 0-1 的分数：1.0 表示完美回答，0.0 表示答非所问。\n"
                "只输出数字，不要其他文字。"
            )},
            {"role": "user", "content": f"查询：{query}\n\n答案：{generated_answer}"},
        ],
        temperature=0,
        max_tokens=10,
    )
    ans_score = float(ans_response.choices[0].message.content.strip())

    return {
        "context_relevance": round(ctx_score, 4),
        "groundedness": round(grd_score, 4),
        "answer_relevance": round(ans_score, 4),
        "triad_avg": round((ctx_score + grd_score + ans_score) / 3, 4),
    }


def run(args: argparse.Namespace):
    """Main entry point for evaluate command."""
    golden = load_json(args.golden)
    results = load_json(args.results)

    print(f"[evaluate] Evaluating {len(golden)} queries...", file=sys.stderr)

    metrics_per_query = []
    total_ctx = 0
    total_grd = 0
    total_ans = 0

    for i, item in enumerate(golden):
        query = item.get("query", "")
        ground_truth = item.get("answer", "")
        expected_context = item.get("relevant_docs", [])

        # Find matching result
        result_item = results[i] if i < len(results) else {}
        retrieved_context = result_item.get("text", "")
        generated_answer = result_item.get("generated_answer", "")

        try:
            scores = evaluate_llm_as_judge(query, retrieved_context, generated_answer, ground_truth, args.model)
        except Exception as e:
            print(f"[evaluate] Query {i} failed: {e}", file=sys.stderr)
            scores = {"context_relevance": 0, "groundedness": 0, "answer_relevance": 0, "triad_avg": 0}

        metric = {
            "query_index": i,
            "query": query[:100],
            **scores,
        }
        metrics_per_query.append(metric)

        total_ctx += scores["context_relevance"]
        total_grd += scores["groundedness"]
        total_ans += scores["answer_relevance"]

    n = len(metrics_per_query)
    overall = {
        "total_queries": n,
        "avg_context_relevance": round(total_ctx / n, 4) if n > 0 else 0,
        "avg_groundedness": round(total_grd / n, 4) if n > 0 else 0,
        "avg_answer_relevance": round(total_ans / n, 4) if n > 0 else 0,
        "avg_triad": round((total_ctx + total_grd + total_ans) / (3 * n), 4) if n > 0 else 0,
        "per_query": metrics_per_query,
    }

    output = json.dumps(overall, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"[evaluate] Wrote metrics to {args.output}", file=sys.stderr)
    else:
        print(output)


def register_parser(subparsers):
    parser = subparsers.add_parser("evaluate", help="RAG Triad evaluation")
    parser.add_argument("-g", "--golden", required=True, help="Golden dataset JSON")
    parser.add_argument("-r", "--results", required=True, help="Results JSON")
    parser.add_argument("-o", "--output", help="Output file")
    parser.add_argument("--model", default="gpt-4o-mini", help="Judge model")
    parser.set_defaults(func=run)
