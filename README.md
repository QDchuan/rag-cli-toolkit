# rag-cli-toolkit

> RAG 数据预处理 —— 把任意格式的原始资料，变成一份统一的 JSON 契约。
> **一个工具 + 四份 Agent 手册。**

---

## 这个项目是什么

| 层 | 是什么 | 状态 |
|---|---|---|
| **执行层** | `ragcli parse` —— 把 PDF / Word / Excel / PPT / 网页 / 图片 / Markdown / CSV 解析成统一结构 | ✅ 已实现，59 项断言 |
| **决策层** | 1 个编排手册 + 4 个 worker 手册，教 Agent 怎么决定切块、打标、摘要 | 📋 设计完成，靠 Agent 写代码 |

**当前只有一个工具。** 早期铺开的 11 个工具（chunk / tagger / summarize / embed / index / graph / search / hybrid / rerank / cache / evaluate）**已全部删除** —— 它们写完就从未被执行过一次。没运行过的工具不是资产，是伪装成进度的负债。

---

## 为什么只有 parse 值得做成工具

关键在于区分**难点在哪**：

| | 难点 | 该做成工具吗 |
|---|---|---|
| `parse` | **格式处理** —— PDF 多栏、合并单元格、OCR 版面、编码探测 | ✅ 该。成熟库已解决，包一层即可 |
| `chunk` | **判断** —— 这份文档哪里能切、哪里不能切 | ❌ 不该。没有固定 CLI 能表达 |
| `summarize` | **判断** —— 要不要做、多长、什么风格 | ❌ 不该 |
| `tagger` | **判断** —— 语料级 Schema 怎么定 | ❌ 不该 |

所以分工是干净的：**格式问题做成命令，判断问题交给 Agent 写代码。**

这也是为什么 `docs/agents/chunk-worker.md` 让专家"自己写切块脚本"——因为切块决策本质上不可参数化。

---

## 快速开始

```bash
pip install -e ".[all]"     # 全部格式支持
pip install -e ".[pdf]"     # 只要 PDF
pip install -e .            # 不装任何引擎（CLI 仍可用，只是没有解析能力）

# 开工前先看支持什么、缺什么引擎
ragcli parse --list-formats

# 单文件
ragcli parse -f report.docx -o parsed.json

# 网页
ragcli parse -f "https://docs.example.com/api" -o web.json

# 整个目录（输出 JSONL，一行一文档，万级语料不进内存）
ragcli parse -d ./knowledge-base/ -o all.jsonl

# 扫描件 OCR
ragcli parse -f scan.png --engine paddle --lang ch -o ocr.json

# 查看工具与阶段
ragcli list
ragcli stages
```

---

## 支持的格式与引擎

| 格式 | 引擎 | 解决的坑 |
|---|---|---|
| Markdown / TXT / 代码 | 原生 | 标题面包屑追踪；编码自动探测（UTF-8 / GB18030 / Big5） |
| CSV / TSV | 原生 | 转 Markdown 表格；**宽表（>12 列）自动转置**为键值记录 |
| JSON / YAML | 原生 + PyYAML | 扁平化为点路径，嵌套配置变成可搜索文本 |
| Word (.docx) | `python-docx` | 从**样式**读真实标题层级；嵌套表格递归遍历 |
| Excel (.xlsx) | `openpyxl` | **合并单元格向上展开**（否则只有左上角有值，静默丢数据） |
| PPT (.pptx) | `python-pptx` | **还原阅读顺序**（shape 是 z-order，不是人眼顺序）；含演讲者备注 |
| PDF（文本型） | `pdfplumber` | 逐字符坐标 → 真表格检测 + **多栏识别** + 页码溯源 |
| PDF（扫描件） | `PaddleOCR` → `EasyOCR` | 原生提取每页 <25 字符时**自动升级**到 OCR |
| 网页 / URL | `trafilatura` → `readability` | 智能剥离导航/广告/页脚，只留正文 |
| 图片 (PNG/JPG) | `PaddleOCR` → `EasyOCR` → `tesseract` | 文本框按阅读顺序聚合成行 |

**共 57 种扩展名。**

---

## 统一输出契约

不管来源是什么格式，输出永远是同一份结构：

```jsonc
{
  "source_id": "report_a1b2c3d4",       // 稳定 ID：重复摄入覆盖而非重复
  "source_path": "docs/report.pdf",
  "format_type": "pdf",
  "meta": {"title": "季度报告", "page_count": 12, "word_count": 4500},
  "sections": [
    {
      "block_id": 2,
      "type": "paragraph",              // heading|paragraph|table|list_item|code_block
      "heading_path": ["第一章", "1.1 概述"],   // ← 面包屑，最重要的字段
      "content": "正文内容...",
      "level": null,
      "location": {"page": 3},          // 溯源：page / sheet / slide
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

**`heading_path` 是这份契约的灵魂。** 它让每个 section 自带上下文——下游切块时把面包屑前置，生成的 chunk 脱离原文也能独立回答。

---

## 清洗管线（内置在 parse 里）

清洗**不是独立步骤**，`ragcli parse` 提取后自动执行 9 步管线。顺序有意义：

| 顺序 | 清洗器 | 清除什么 |
|---|---|---|
| 1 | `normalize` | NFKC 统一码（全角↔半角）、连续空白 |
| 2 | `line_noise` | OCR 噪点、纯标点行 |
| 3 | `page_artifacts` | 孤立页码、"Page 3 of 40"、"第 3 页" |
| 4 | `watermarks` | "CONFIDENTIAL"、"内部资料"、裸 URL |
| 5 | `boilerplate` | **行级**跨页重复的页眉页脚 |
| 6 | `dehyphenate` | `retrie-\nval` → `retrieval` |
| 7 | `merge_fragments` | OCR 行碎片粘回段落 |
| 8 | `dedupe` | 完全重复块 |
| 9 | `min_length` | 残留碎片 |

两个顺序陷阱是测试逼出来的：

- **`boilerplate` 必须在 `merge_fragments` 之前** —— 先合并段落的话，页脚会被粘到下一页正文上，精确行匹配就再也找不到它
- **`boilerplate` 必须是行级而非块级** —— 一页 PDF 提取出来是**一整块**（正文+页脚），块级比较永远匹配不上

```bash
ragcli parse -f doc.pdf --no-clean -o raw.json                      # 跳过清洗
ragcli parse -f doc.pdf --cleaners normalize,dedupe -o out.json     # 只跑指定的
ragcli parse -f doc.pdf --boilerplate-ratio 0.5 -o out.json         # 调页脚阈值
```

---

## Agent 设计（决策层）

```
              Orchestrator  ← 只调度，永不读 worker 手册
                     │
     ┌───────────┬───┴───────┬────────────┐
  Parse       Chunk     Summarize     Tagger
  Worker      Worker      Worker       Worker
     └───────────┴───────────┴────────────┘
                     │
              runs/<source_id>/ + manifest.json
```

| worker | 输入 | 输出 | 实现方式 |
|---|---|---|---|
| Parse Worker | 原始文件 / URL | `parsed.json` | 有命令（`ragcli parse`） |
| Chunk Worker | `parsed.json` | `chunks.json` | **自己写脚本** |
| Summarize Worker | `chunks.json` | `summarized.json` | **自己写脚本** |
| Tagger Worker | `chunks.json` + Schema | `tagged.json` | **自己写脚本** |

**三条原则**：
1. **上下文隔离靠 session 隔离**，不靠提示词说"别读"
2. **阶段间靠文件通信**，零对话
3. **每个 worker 无状态**，状态全在 manifest

> `embed` / `index` **尚未实现**。需要向量库的话，那不在本项目当前范围内。

---

## 目录结构

```
rag-cli-toolkit/
├── ragcli/
│   ├── cli.py                 # CLI 入口
│   ├── registry.py            # 工具注册（按 ingest/retrieve/evaluate 分阶段）
│   ├── cleaners.py            # 格式无关的 9 步清洗管线
│   ├── parsers/               # 解析引擎（路由 + 规范化）
│   │   ├── base.py            # 抽象基类 + 标准输出契约
│   │   ├── text_parser.py     # MD/TXT/CSV/JSON/YAML/代码
│   │   ├── office_parser.py   # DOCX/XLSX/PPTX
│   │   ├── pdf_parser.py      # PDF 双引擎 + OCR 回退
│   │   ├── web_parser.py      # URL/HTML 正文提取
│   │   └── image_parser.py    # 图片 OCR + 版面还原
│   └── tools/
│       └── parse.py           # ← 唯一的工具
├── docs/
│   ├── README.md              # ⭐ 文档地图 —— 先读这个
│   ├── agents/                # Agent 手册（英文 + 中文副本）
│   ├── design/                # 设计文档（中文，给人看）
│   └── reference/cli.md       # CLI 速查（自动生成，勿手改）
├── tests/
└── pyproject.toml
```

---

## 文档

**先读 [docs/README.md](docs/README.md)** —— 它说明哪份文档给谁看。

| 文档 | 读者 |
|---|---|
| [docs/agents/orchestrator.md](docs/agents/orchestrator.md) | 编排 Agent |
| [docs/agents/parse-worker.md](docs/agents/parse-worker.md) · [中文](docs/agents/parse-worker.zh.md) | 解析 Agent |
| [docs/agents/chunk-worker.md](docs/agents/chunk-worker.md) · [中文](docs/agents/chunk-worker.zh.md) | 切块 Agent |
| [docs/agents/summarize-worker.md](docs/agents/summarize-worker.md) · [中文](docs/agents/summarize-worker.zh.md) | 摘要 Agent |
| [docs/agents/tagger-worker.md](docs/agents/tagger-worker.md) · [中文](docs/agents/tagger-worker.zh.md) | 标签 Agent |
| [docs/design/architecture.md](docs/design/architecture.md) | 人（架构、边界、缺口） |
| [docs/design/pipeline-dependencies.md](docs/design/pipeline-dependencies.md) | 人（时序、并行、并发陷阱） |
| [docs/reference/cli.md](docs/reference/cli.md) | 自动生成 |

---

## 测试

```bash
python tests/run_all.py
```

| 套件 | 覆盖 | 项数 |
|---|---|---|
| `smoke_parse.py` | 契约一致性、清洗管线、错误处理 | 26 |
| `integration_office.py` | 真实 DOCX/XLSX/PDF 提取质量 | 20 |
| `web_check.py` | HTML/URL 提取（含实时网络） | 13 |
| `check_doc_links.py` | 断链 + 已退役文件名的残留引用 | — |
| `check_structure.py` | 工具唯一性、阶段布局、手册完整、registry 一致 | 26 |

断言都是行为级的，不是实现细节——例如 "heading_path 包含完整祖先链"、
"跨页重复页脚被清除"、"损坏文件抛 ParseError 而非裸异常"、
"CLI 手册不得记录已删除的工具"。

---

## 设计原则

1. **不造轮子** —— 解析交给成熟引擎，我们只做路由和规范化
2. **零硬依赖** —— 核心包无第三方依赖；缺引擎时 CLI 仍可用，只是没有解析能力
3. **降级而非失败** —— 缺引擎、读不出表格都只是警告；只有真正无法解析才抛错
4. **一个工具一个契约** —— 格式复杂度在 `parse` 内消化，下游只看到一种结构
5. **文档分层** —— Agent 手册（英文）与设计文档（中文）严格分开，互不污染
6. **生成而非手写** —— 从代码派生的文档由脚本生成，防止漂移
