# rag-cli-toolkit — 模块化 RAG CLI 工具集

> **设计理念**：RAG 管线不是写死的流水线，而是一组可自由组合的原子工具。
> Agent 根据需要选择工具、排列组合，构建最适合当前场景的 RAG 流程。

---

## 数据预处理：不造轮子，只做路由

数据清洗/解析阶段采用 **"胖引擎 + 瘦壳子"** 架构：

```
外部世界（各种格式） → 适配层（选最好的开源引擎） → 标准 JSON 契约 → 下游 Agent
   Word/Excel/           python-docx / openpyxl /       统一结构      切块/摘要/打标
   PDF/网页/图片         pdfplumber / trafilatura /                   （不关心原始格式）
                        PaddleOCR ...
```

核心思路：**不为每种格式写解析器，而是把久经考验的开源利器包装成统一接口。**
无论输入是 Excel 还是扫描图片，输出的 JSON 结构完全一致，下游 Agent 无需知道也不关心原始格式。

### 支持格式与引擎选型

| 数据类型 | 选用引擎 | 为什么选它 |
|---|---|---|
| Markdown / TXT | Python 原生 | 零依赖，标题面包屑追踪，编码自动探测 |
| CSV / TSV | Python 原生 | 转 Markdown 表格；宽表（>12 列）自动转置为键值记录 |
| JSON / JSONL | Python 原生 | 扁平化为点路径，嵌套配置变成可搜索文本 |
| Word (.docx) | `python-docx` | 从样式读取真实标题层级，嵌套表格递归遍历 |
| Excel (.xlsx) | `openpyxl` | 合并单元格向上展开；处理公式缓存 |
| PowerPoint (.pptx) | `python-pptx` | 还原阅读顺序（按位置排序而非 z-order），含演讲者备注 |
| PDF（文本型） | `pdfplumber` | 逐字符坐标 → 真实表格检测、多栏阅读、页码溯源 |
| PDF（扫描件） | `PaddleOCR` → `EasyOCR` | 原生提取每页 <25 字符时自动升级到 OCR |
| 网页 / URL | `trafilatura` → `readability` | 智能剥离导航/广告/页脚，只保留正文 |
| 图片 (PNG/JPG) | `PaddleOCR` → `EasyOCR` | 文本框按阅读顺序聚合成行 |

### 标准输出契约

不管来源是什么格式，输出永远是同一份结构：

```jsonc
{
  "source_id": "report_a1b2c3d4",        // 稳定 ID：重复摄入覆盖而非重复
  "source_path": "docs/report.pdf",
  "format_type": "pdf",
  "meta": {"title": "季度报告", "page_count": 12, "word_count": 4500},
  "sections": [
    {
      "block_id": 2,
      "type": "paragraph",                // heading|paragraph|table|list_item|code_block
      "heading_path": ["第一章", "1.1 概述"],  // 面包屑链路 —— 让 chunk 独立可回答
      "content": "正文内容...",
      "level": null,
      "location": {"page": 3},            // 溯源：page / sheet / slide
      "metadata": {}
    }
  ],
  "stats": {
    "parser_engine_used": "pdfplumber",
    "total_blocks_extracted": 68,
    "warnings": [],
    "cleaning": {"blocks_before": 76, "blocks_after": 68, "blocks_removed": 8}
  }
}
```

**`heading_path` 是最重要的字段** —— 它让每个 section 携带自己的上下文，
下游切块时直接把面包屑前置，chunk 即使脱离原文也能独立回答。

### 清洗管线（与格式无关，顺序有意义）

| 顺序 | 清洗器 | 清除什么 |
|---|---|---|
| 1 | `normalize` | NFKC 统一码、连续空白 |
| 2 | `line_noise` | OCR 噪点、分隔线、纯标点行 |
| 3 | `page_artifacts` | 孤立页码、"Page 3 of 40"、"第 3 页" |
| 4 | `watermarks` | "CONFIDENTIAL"、"内部资料"、下载横幅 |
| 5 | `boilerplate` | **行级**跨页重复的页眉页脚 |
| 6 | `dehyphenate` | `retrie-\nval` → `retrieval` |
| 7 | `merge_fragments` | OCR 行碎片粘回段落 |
| 8 | `dedupe` | 完全重复的块 |
| 9 | `min_length` | 残留碎片 |

---

## 快速开始

```bash
# 安装（按需选择格式组，不必全装）
pip install -e ".[parse-all]"     # 全部格式支持
pip install -e ".[parse-pdf]"     # 只要 PDF
pip install -e ".[retrieve]"      # 只要检索

# 查看支持的格式和引擎安装状态 —— 开工前先跑这个
ragcli parse --list-formats

# 查看所有工具（可按阶段过滤）
ragcli list
ragcli list --stage ingest
ragcli stages

# 解析单个文件
ragcli parse -f report.docx -o parsed.json

# 解析网页
ragcli parse -f "https://docs.example.com/api" -o web.json

# 批量解析目录（输出 JSONL，一行一文档）
ragcli parse -d ./knowledge-base/ -o all_docs.jsonl

# 扫描件 OCR
ragcli parse -f scan.png --engine paddle --lang ch -o ocr.json
```

---

## 架构总览

```
rag-cli-toolkit/
├── ragcli/
│   ├── cli.py                 # CLI 主入口
│   ├── registry.py            # 工具注册中心（按阶段分组）
│   ├── cleaners.py            # 格式无关的清洗管线
│   ├── parsers/               # 数据预处理引擎
│   │   ├── base.py            # 抽象基类 + 标准输出契约
│   │   ├── text_parser.py     # MD/TXT/CSV/JSON/YAML/代码
│   │   ├── office_parser.py   # DOCX/XLSX/PPTX
│   │   ├── pdf_parser.py      # PDF 双引擎 + OCR 回退
│   │   ├── web_parser.py      # URL/HTML 正文提取
│   │   └── image_parser.py    # 图片 OCR + 版面还原
│   └── tools/                 # 原子工具
│       ├── parse.py           # 统一预处理入口
│       ├── chunk.py           # 文档切块
│       ├── summarize.py       # 摘要生成
│       ├── tagger.py          # 标签打标
│       ├── embed.py           # Embedding 生成
│       ├── index.py           # 向量索引
│       ├── graph.py           # 知识图谱
│       ├── search.py          # 向量检索
│       ├── hybrid.py          # 混合检索
│       ├── rerank.py          # 重排序
│       ├── cache.py           # 语义缓存
│       └── evaluate.py        # RAG Triad 评估
├── tests/                     # 测试套件
└── docs/                      # Agent 专家手册
```

---

## 工具按阶段分组

`ragcli stages` 查看完整分组。这样两个 Agent（预处理 / 检索）各自只查询自己负责的阶段。

| 阶段 | 工具 | 归属 |
|---|---|---|
| **ingest** | `parse` `chunk` `summarize` `tagger` | 数据预处理 Agent |
| **index** | `embed` `index` `graph` | 交接边界 |
| **retrieve** | `search` `hybrid` `rerank` `cache` | 检索 Agent |
| **evaluate** | `evaluate` | 横切 |

---

## Agent 专家手册

每个阶段有独立的 prompt 手册（英文原版 + 中文副本），一个 Agent 只读自己那一份，避免注意力分散：

| 手册 | 负责的 Agent | 内容 |
|---|---|---|
| [parse-expert.md](docs/parse-expert.md) · [中文](docs/parse-expert.zh.md) | 数据预处理 | 格式路由、清洗管线、契约解读 |
| [chunk-expert.md](docs/chunk-expert.md) · [中文](docs/chunk-expert.zh.md) | 切块专家 | 切块决策、token 计数、完整性保护 |
| [summarize-expert.md](docs/summarize-expert.md) · [中文](docs/summarize-expert.zh.md) | 摘要专家 | 摘要策略、长度控制、幻觉规避 |
| [tagger-expert.md](docs/tagger-expert.md) · [中文](docs/tagger-expert.zh.md) | 标签专家 | Schema 设计、元数据前置过滤 |
| [orchestration-guide.md](docs/orchestration-guide.md) | 编排 Agent | 管线时序、并行调度 |
| [agent-guide.md](docs/agent-guide.md) | 通用 | CLI 调用速查 |

---

## 典型管线组合

```bash
# ── 数据预处理阶段（Agent A）─────────────────────────────
ragcli parse -d ./raw/ -o parsed.jsonl                    # 解析任意格式
ragcli chunk -i parsed.jsonl -o chunks.json               # 切块
ragcli summarize -i chunks.json -o summarized.json        # 摘要（可与下步并行）
ragcli tagger -i chunks.json -o tagged.json --schema s.json  # 打标（可与上步并行）

# ── 索引阶段（交接边界）──────────────────────────────────
ragcli embed -i tagged.json -o embedded.json
ragcli index -i embedded.json -d qdrant --collection docs

# ── 检索阶段（Agent B）───────────────────────────────────
ragcli hybrid -q "问题" -i embedded.json -k 20 -o hits.json
ragcli rerank -q "问题" -i hits.json -o final.json -k 5
```

---

## 测试

```bash
python tests/run_all.py
```

| 套件 | 覆盖内容 |
|---|---|
| `smoke_parse.py` | 契约一致性、清洗管线、错误处理（26 项） |
| `integration_office.py` | 真实 DOCX/XLSX/PDF 提取质量（20 项） |
| `web_check.py` | HTML/URL 提取，含实时网络请求（13 项） |

检查项都是行为断言，不是实现细节——比如 "docx: heading_path 包含完整祖先链"、
"pdf: 跨页重复页脚被清除"、"corrupt pptx: 抛出 ParseError 而非裸异常"。

---

## 设计原则

1. **每个工具独立运行** —— 不依赖其他工具，可单独使用
2. **JSON 输入输出** —— 所有工具通过 stdin/stdout 交换 JSON
3. **不造轮子** —— 解析交给成熟开源引擎，我们只做路由和规范化
4. **零硬依赖** —— 核心包无第三方依赖，可选引擎缺失时优雅降级
5. **阶段解耦** —— 预处理与检索是两个 Agent，各自只读自己的手册
