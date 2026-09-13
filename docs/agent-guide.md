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

| 工具 | 功能 | 输入 | 输出 | 关键参数 |
| --- | --- | --- | --- | --- |
| `chunk` | 文档切块 | `-f/--input` 文件路径 | JSON chunks 数组 | `--chunk-size`, `--chunk-overlap`, `--format` |
| `embed` | 生成 embedding | `-i` JSON chunks | JSON with `embedding` field | `--model`, `--api local|openai` |
| `index` | 构建向量索引 | `-i` embedded chunks | JSON status | `--store chroma/qdrant/milvus`, `--collection` |
| `search` | 向量检索 | `-q` 查询字符串 | JSON matches | `--store`, `--collection`, `-k` top-k |
| `hybrid` | 混合检索(向量+BM25) | `-q` + `-i` embedded | JSON with RRF scores | `--bm25-weight`, `--vector-weight` |
| `rerank` | Cross-Encoder 重排序 | `-q` + `-i` results | JSON re-ranked | `--model`, `--backend flag|st` |
| `summarize` | 摘要生成 | `-i` chunks | JSON with `summary` | `--model`, `--local` |
| `tagger` | 自动标签打标 | `-i` chunks + `--schema` | JSON with `tags` | `--schema` (file or JSON string) |
| `evaluate` | RAG Triad 评估 | `-g` golden + `-r` results | JSON metrics | `--model` judge model |
| `cache` | 语义缓存 | `-q` query | hit/miss JSON | `--store`, `--threshold` |
| `graph` | 知识图谱构建 | `-i` tagged chunks | JSON nodes+edges | `--model` |

---

## 典型管线组合

### 管线 A：基础 RAG（最快上手）

```bash
# Step 1: 切块
ragcli chunk -f documents.pdf -o chunks.json

# Step 2: Embedding
ragcli embed -i chunks.json -o embedded.json

# Step 3: 建索引
ragcli index -i embedded.json -d chroma --collection my_docs

# Step 4: 检索
ragcli search -q "你的问题" -d chroma --collection my_docs -k 5 > results.json

# Step 5: 用结果生成回答（交给 LLM）
cat results.json | ragcli generate-answer --prompt "基于以下结果回答问题..."
```

### 管线 B：增强 RAG（高精度）

```bash
# 切块
ragcli chunk -f documents.pdf -o chunks.json

# 生成摘要
ragcli summarize -i chunks.json -o summarized.json

# 打标签
ragcli tagger -i chunks.json -o tagged.json \
    --schema '{"domain":["finance","legal","tech"],"doc_type":["policy","faq","tutorial"]}'

# Embedding（同时嵌入原文和摘要）
ragcli embed -i tagged.json -o embedded.json

# 混合检索
ragcli hybrid -q "你的问题" -i embedded.json -k 20 > hybrid_results.json

# 重排序
ragcli rerank -q "你的问题" -i hybrid_results.json -o reranked.json -k 5

# 检索结果给 LLM 生成回答
cat reranked.json | ... # 拼接 prompt 调用 LLM
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
