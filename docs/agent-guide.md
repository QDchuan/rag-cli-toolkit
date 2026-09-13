# ragcli — Agent 使用指南

> **本文档面向 AI Agent**：说明如何调用 `ragcli` 工具集来构建 RAG 管线。  
> 所有工具通过 JSON stdin/stdout 通信，可自由排列组合。

---

## 快速参考

### 1. 查看所有可用工具

```bash
ragcli list
```

返回 JSON 数组，每个工具包含 `name`, `description`, `inputs`, `outputs`。

### 2. 查看某个工具的用法

```bash
ragcli <tool-name> --help
```

例如：
```bash
ragcli chunk --help
ragcli embed --help
```

### 3. 基本工作流程

```
原始文档 → chunk → embed → index → search → rerank → answer
```

每个步骤的输出是 JSON，可直接作为下一步的输入。

---

## 工具速查表

| 工具 | 阶段 | 功能 | 输入 | 输出 | 关键参数 |
| --- | --- | --- | --- | --- | --- |
| `parse` | ingest | **解析任意格式** | 文件/目录/URL | 标准 JSON 契约 | `-f` 单文件, `-d` 目录, `--list-formats` |
| `chunk` | ingest | 文档切块 | `-i` parsed JSON | JSON chunks 数组 | `--chunk-size`, `--chunk-overlap`, `--format` |
| `summarize` | ingest | 摘要生成 | `-i` chunks | JSON with `summary` | `--model`, `--local` |
| `tagger` | ingest | 自动标签打标 | `-i` chunks + `--schema` | JSON with `tags` | `--schema` (file or JSON string) |
| `embed` | index | 生成 embedding | `-i` JSON chunks | JSON with `embedding` field | `--model`, `--api local\|openai` |
| `index` | index | 构建向量索引 | `-i` embedded chunks | JSON status | `--store chroma/qdrant/milvus`, `--collection` |
| `graph` | index | 知识图谱构建 | `-i` tagged chunks | JSON nodes+edges | `--model` |
| `search` | retrieve | 向量检索 | `-q` 查询字符串 | JSON matches | `--store`, `--collection`, `-k` top-k |
| `hybrid` | retrieve | 混合检索(向量+BM25) | `-q` + `-i` embedded | JSON with RRF scores | `--bm25-weight`, `--vector-weight` |
| `rerank` | retrieve | Cross-Encoder 重排序 | `-q` + `-i` results | JSON re-ranked | `--model`, `--backend flag\|st` |
| `cache` | retrieve | 语义缓存 | `-q` query | hit/miss JSON | `--store`, `--threshold` |
| `evaluate` | evaluate | RAG Triad 评估 | `-g` golden + `-r` results | JSON metrics | `--model` judge model |

用 `ragcli list --stage ingest` 只看预处理阶段的工具，或用 `ragcli stages` 看完整阶段分组。

---

## 关于 `parse` 的输出契约

`parse` 是唯一处理文件格式的工具。它的输出被下游所有工具消费，结构永远一致：

```jsonc
{
  "source_id": "report_a1b2c3d4",       // 稳定 ID
  "format_type": "pdf",
  "meta": {"title": "...", "page_count": 12},
  "sections": [
    {
      "block_id": 2,
      "type": "paragraph",                // heading|paragraph|table|list_item|code_block
      "heading_path": ["第一章", "1.1 概述"],  // 面包屑 —— 切块时前置给 chunk
      "content": "正文...",
      "location": {"page": 3}             // 溯源
    }
  ],
  "stats": {"parser_engine_used": "pdfplumber", "warnings": [], "cleaning": {...}}
}
```

**切块时务必把 `heading_path` 前置到 chunk 里**——这是让 chunk 独立可回答的关键。
详见 [chunk-expert.md](chunk-expert.md)。

---

## 典型管线组合

### 管线 A：基础 RAG（最快上手）

```bash
# Step 0: 解析任意格式（PDF/Word/Excel/网页/图片都走这一步）
ragcli parse -f documents.pdf -o parsed.json
# 或批量：ragcli parse -d ./raw/ -o parsed.jsonl

# Step 1: 切块
ragcli chunk -i parsed.json -o chunks.json

# Step 2: Embedding
ragcli embed -i chunks.json -o embedded.json

# Step 3: 建索引
ragcli index -i embedded.json -d chroma --collection my_docs

# Step 4: 检索
ragcli search -q "你的问题" -d chroma --collection my_docs -k 5 > results.json
```

### 管线 B：增强 RAG（高精度）

```bash
# 解析
ragcli parse -d ./knowledge-base/ -o parsed.jsonl

# 切块
ragcli chunk -i parsed.jsonl -o chunks.json

# 摘要 + 打标（这两步可以并行）
ragcli summarize -i chunks.json -o summarized.json &
ragcli tagger -i chunks.json -o tagged.json \
    --schema '{"domain":["finance","legal","tech"],"doc_type":["policy","faq","tutorial"]}' &
wait

# Embedding
ragcli embed -i tagged.json -o embedded.json

# 混合检索
ragcli hybrid -q "你的问题" -i embedded.json -k 20 > hybrid_results.json

# 重排序
ragcli rerank -q "你的问题" -i hybrid_results.json -o reranked.json -k 5
```

### 管线 C：带评估的迭代优化

```bash
# 构建基础管线
ragcli chunk -f docs.pdf -o chunks.json
ragcli embed -i chunks.json -o embedded.json
ragcli index -i embedded.json -d chroma --collection test

# 对测试集提问
for q in test_queries.jsonl; do
    ragcli search -q "$(jq -r '.query' $q)" -d chroma --collection test > result.json
    # 收集结果...
done

# 评估
ragcli evaluate -g golden_dataset.json -r all_results.json -o metrics.json

# 根据指标调整参数后重试
```

### 管线 D：GraphRAG（多跳推理）

```bash
# 切块 + 打标签
ragcli chunk -f knowledge_base.pdf -o chunks.json
ragcli tagger -i chunks.json -o tagged.json \
    --schema '{"entities":"auto_extract"}'

# 构建知识图谱
ragcli graph -i tagged.json -o knowledge_graph.json

# 用图谱辅助检索（Agent 需自行实现图谱遍历逻辑）
```

### 管线 E：语义缓存加速

```bash
# 检查缓存
ragcli cache -q "你的问题" --store ./cache.json > cache_result.json

# 如果是 miss，拿到 LLM 回答后保存
echo '{"query": "你的问题", "result": "LLM生成的答案"}' \
    | ragcli cache -i - --store ./cache.json --save
```

---

## 管道组合技巧

### 使用 stdin/stdout 串联工具

```bash
ragcli chunk -f doc.pdf | ragcli embed -i - -o embedded.json
```

### 使用临时文件传递数据

```bash
ragcli chunk -f doc.pdf -o /tmp/chunks.json
ragcli embed -i /tmp/chunks.json -o /tmp/embedded.json
ragcli index -i /tmp/embedded.json -d chroma
```

### 动态选择工具

Agent 应根据场景决定使用哪些工具：

```python
# 伪代码：Agent 决策逻辑
def build_rag_pipeline(scenario, data_config):
    tools = []

    # 基础管线始终需要
    tools.append("chunk")
    tools.append("embed")

    if data_config.use_vector_db:
        tools.append("index")
        tools.append("search")
    elif data_config.use_hybrid:
        tools.append("hybrid")

    if data_config.precison_required:
        tools.append("rerank")

    if data_config.add_enrichment:
        tools.append("summarize")
        tools.append("tagger")

    if data_config.evaluate:
        tools.append("evaluate")

    if data_config.graph_enabled:
        tools.append("graph")

    return tools
```

---

## 配置示例

### 嵌入模型配置

```json
{
    "embed": {
        "model": "BAAI/bge-m3",
        "api": "local",
        "batch_size": 64
    }
}
```

### 标签 Schema 配置

```json
{
    "domain": ["finance", "legal", "technical", "hr", "product"],
    "doc_type": ["policy", "faq", "tutorial", "api_ref", "release_note"],
    "time_period": ["current", "legacy", "deprecated"],
    "entities": "auto_extract"
}
```

### Golden Dataset 格式（用于评估）

```json
[
    {
        "query": "Spring Boot 3.x 迁移注意事项",
        "answer": "主要注意 Jakarta EE 命名空间变更...",
        "relevant_docs": ["doc_123_chunk_5", "doc_123_chunk_8"]
    },
    ...
]
```

---

## 环境变量

| 变量 | 用途 | 示例 |
| --- | --- | --- |
| `OPENAI_API_KEY` | OpenAI API 密钥 | `sk-...` |
| `OPENAI_BASE_URL` | OpenAI 兼容 API 地址 | `https://your-proxy/v1` |

---

## 常见错误排查

| 错误 | 原因 | 解决 |
| --- | --- | --- |
| `ImportError: xxx required` | 缺少依赖包 | `pip install xxx` |
| `No module named 'sentence_transformers'` | 未安装本地 embedding 库 | `pip install sentence-transformers` |
| `Connection refused` | 向量数据库未启动 | 启动对应服务（Chroma/Qdrant/Milvus） |
| `JSON decode error` | 输入文件格式不对 | 检查 JSON 结构 |
| `embedding dimension mismatch` | 嵌入模型维度不匹配 | 确保 embed 和 search 用同一模型 |

---

## 扩展开发

如需添加新工具：

1. 在 `ragcli/tools/` 下创建新模块 `my_tool.py`
2. 定义 `MY_TOOL` 字典（name, description, inputs, outputs）
3. 实现 `run(args)` 函数和 `register_parser(subparsers)` 函数
4. 在 `ragcli/registry.py` 中注册
5. 在 `ragcli/cli.py` 中导入注册

工具必须遵守的原则：
- 通过 stdin/stdout 交换 JSON
- 不硬编码其他工具
- 每个工具可独立运行
