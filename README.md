# rag-cli-toolkit — 模块化 RAG CLI 工具集

> **设计理念**：RAG 管线不是写死的流水线，而是一组可自由组合的原子工具。  
> Agent 根据需要选择工具、排列组合，构建最适合当前场景的 RAG 流程。

## 架构总览

```
rag-cli-toolkit/
├── ragcli/                    # 核心包
│   ├── __init__.py            # 版本 & 统一入口
│   ├── cli.py                 # CLI 主入口（argparse）
│   ├── registry.py            # 工具注册中心
│   └── tools/                 # 原子工具模块
│       ├── __init__.py
│       ├── chunk.py           # 文档切块
│       ├── embed.py           # Embedding 生成
│       ├── index.py           # 向量索引构建
│       ├── search.py          # 向量检索
│       ├── hybrid.py          # 混合检索（BM25 + 向量）
│       ├── rerank.py          # Cross-Encoder 重排序
│       ├── summarize.py       # 摘要生成
│       ├── tagger.py          # 自动标签打标
│       ├── evaluate.py        # RAG Triad 评估
│       ├── cache.py           # 语义缓存
│       └── graph.py           # GraphRAG 知识图谱构建
├── tests/                     # 测试套件
├── examples/                  # 使用示例
├── pyproject.toml             # 项目配置
└── README.md                  # 本文件
```

## 设计原则

1. **每个工具独立运行**：不依赖其他工具，可单独使用
2. **JSON 输入输出**：所有工具通过 stdin/stdout 交换 JSON 数据
3. **可组合性**：任意工具的输出可作为另一个工具的输入
4. **配置驱动**：通过 YAML/JSON 配置文件定义管线
5. **零硬编码**：没有"默认管线"，一切由调用者决定

## 快速开始

```bash
pip install -e .

# 查看可用工具
ragcli list

# 查看某个工具的用法
ragcli chunk --help

# 基本用法：切块 → Embedding → 索引 → 检索
ragcli chunk -f doc.pdf -o chunks.json
ragcli embed -i chunks.json -o embeddings.json
ragcli index -i embeddings.json -d qdrant
ragcli search -q "你的问题" -d qdrant -k 5
```

## Agent 调用模式

Agent 应按需选择工具并排列组合。参考 [docs/agent-guide.md](docs/agent-guide.md) 获取完整调用指南。

典型组合模式：

| 场景 | 工具组合 |
| --- | --- |
| 基础 RAG | `chunk` → `embed` → `index` → `search` |
| 增强 RAG | `chunk` → `summarize` → `tagger` → `embed` → `index` → `hybrid` → `rerank` → `search` |
| 评估优化 | `chunk` → `embed` → `index` → `search` → `evaluate` |
| 知识图谱 | `chunk` → `tagger` → `graph` |
