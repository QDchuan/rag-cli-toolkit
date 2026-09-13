# rag-cli-toolkit

> 一个 RAG 数据预处理工具集 + 一组 Agent 手册。
> **CLI 是执行层，Agent 是决策层** —— 两者职责分离，这是整个项目的核心结构。

---

## 这个项目是什么

分两层：

| 层 | 是什么 | 在哪 |
|---|---|---|
| **执行层** | 12 个可组合的 CLI 工具，通过 JSON stdin/stdout 通信 | `ragcli/tools/` |
| **决策层** | 1 个编排 Agent + 4 个专家 Agent 的操作手册 | `docs/agents/` |

**为什么分两层**：有些步骤需要**判断**（这份文档怎么切？标签 Schema 怎么定？），有些只需要**执行**（调 embed、调 index）。前者交给 Agent，后者直接调命令。

---

## 两种"专家"

这是理解整个设计的钥匙：

| | **工具操作型** | **判断型** |
|---|---|---|
| 代表 | `parse` | `chunk` / `summarize` / `tagger` |
| 难点 | 已由开源引擎解决 | 在**决策** |
| Agent 做什么 | 路由 + 传参 + 校验 | 分析 + 决策 + 必要时写代码 |
| 手册性质 | 操作手册 | 决策框架 |
| 模型档位 | 小模型够用 | 必须强模型 |

**推论**：`parse` 的脏活（PDF 多栏、合并单元格、OCR 版面）都被封装了，Agent 只需正确调用；而 `chunk` 没有工具能替它决定"这份 API 文档该不该切"，所以专家得自己写脚本。

详见 [docs/design/architecture.md §1](docs/design/architecture.md)。

---

## 快速开始

```bash
pip install -e ".[parse-all]"      # 全部格式支持
pip install -e ".[parse-pdf]"      # 只要 PDF

# 查看支持的格式和引擎安装状态 —— 开工前先跑
ragcli parse --list-formats

# 看工具（按阶段过滤）
ragcli list
ragcli list --stage ingest
ragcli stages

# 解析：单文件 / 网页 / 整个目录
ragcli parse -f report.docx -o parsed.json
ragcli parse -f "https://docs.example.com/api" -o web.json
ragcli parse -d ./knowledge-base/ -o all.jsonl
```

---

## 数据预处理：不造轮子，只做路由

```
各种格式 → 选最合适的开源引擎 → 统一 JSON 契约 → 下游工具
Word/Excel/  python-docx / openpyxl /      统一结构    （不关心原始格式）
PDF/网页/图  pdfplumber / trafilatura /
            PaddleOCR ...
```

| 格式 | 引擎 | 解决的坑 |
|---|---|---|
| Word | `python-docx` | 从样式读真实标题层级；嵌套表格递归遍历 |
| Excel | `openpyxl` | 合并单元格向上展开（否则只有左上角有值） |
| PPT | `python-pptx` | 还原阅读顺序（shape 是 z-order，不是人眼顺序） |
| PDF（文本） | `pdfplumber` | 逐字符坐标 → 真表格检测 + 多栏识别 |
| PDF（扫描） | `PaddleOCR` | 原生提取每页 <25 字符时自动升级 |
| 网页 | `trafilatura` | 智能剥离导航/广告/页脚 |
| 图片 | `PaddleOCR → EasyOCR` | 文本框按阅读顺序聚合 |

### 统一输出契约

```jsonc
{
  "source_id": "report_a1b2c3d4",     // 稳定 ID：重复摄入覆盖而非重复
  "format_type": "pdf",
  "meta": {"title": "...", "page_count": 12},
  "sections": [
    {
      "block_id": 2,
      "type": "paragraph",              // heading|paragraph|table|list_item|code_block
      "heading_path": ["第一章", "1.1 概述"],  // ← 面包屑，最重要的字段
      "content": "正文...",
      "location": {"page": 3}
    }
  ],
  "stats": {"parser_engine_used": "pdfplumber", "warnings": [], "cleaning": {...}}
}
```

`heading_path` 让每个 section 自带上下文，下游切块时前置到 chunk 里，**chunk 脱离原文也能独立回答**。

---

## 管线

```
raw source
   │
   ├─ [worker] parse      → parsed.json      （清洗在内部自动执行）
   ├─ [worker] chunk      → chunks.json
   ├─ ┌ [worker] summarize → summarized.json ┐  并行
   │  └ [worker] tagger    → tagged.json     ┘
   ├─ [tool]   embed      → embedded.json
   └─ [tool]   index      → 向量库
```

- **[worker]** = 需要判断，派给独立 Agent（各持一份手册、独立 session）
- **[tool]** = 确定性命令，编排 Agent 直接执行

注意：`embed` 必须等 `summarize` 和 `tagger` **都**完成——标签和摘要要写进向量 payload。

---

## Agent 拓扑

```
              Orchestrator  ← 只调度，不读任何 worker 手册
                     │
     ┌───────────┬───┴───────┬────────────┐
  Parse       Chunk     Summarize     Tagger
  Worker      Worker      Worker       Worker
     └───────────┴───────────┴────────────┘
                     │
              Artifact Store（runs/<id>/ + manifest.json）
```

**三条原则**：
1. **上下文隔离靠 session 隔离**，不靠提示词说"别读"
2. **阶段间靠文件通信**，零对话
3. **每个 Worker 无状态**，状态全在 manifest

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
│   └── tools/                 # 12 个原子工具
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

**先读 [docs/README.md](docs/README.md)** —— 它说明哪份文档给谁看，避免加载错文件。

| 文档 | 读者 |
|---|---|
| [docs/agents/orchestrator.md](docs/agents/orchestrator.md) | 编排 Agent |
| [docs/agents/parse-worker.md](docs/agents/parse-worker.md) · [中文](docs/agents/parse-worker.zh.md) | 解析 Agent |
| [docs/agents/chunk-worker.md](docs/agents/chunk-worker.md) · [中文](docs/agents/chunk-worker.zh.md) | 切块 Agent |
| [docs/agents/summarize-worker.md](docs/agents/summarize-worker.md) · [中文](docs/agents/summarize-worker.zh.md) | 摘要 Agent |
| [docs/agents/tagger-worker.md](docs/agents/tagger-worker.md) · [中文](docs/agents/tagger-worker.zh.md) | 标签 Agent |
| [docs/design/architecture.md](docs/design/architecture.md) | 人（架构与缺口） |
| [docs/design/pipeline-dependencies.md](docs/design/pipeline-dependencies.md) | 人（时序与并行） |
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

断言都是行为级的，不是实现细节——例如 "heading_path 包含完整祖先链"、
"跨页重复页脚被清除"、"损坏文件抛 ParseError 而非裸异常"。

---

## 设计原则

1. **每个工具独立运行** —— 可单独使用，不依赖其他工具
2. **JSON 输入输出** —— stdin/stdout 交换，天然可组合
3. **不造轮子** —— 解析交给成熟引擎，我们只做路由和规范化
4. **零硬依赖** —— 核心包无第三方依赖，可选引擎缺失时优雅降级
5. **文档分层** —— Agent 手册（英文）与设计文档（中文）严格分开
6. **生成而非手写** —— 任何从代码派生的文档都由脚本生成，防止漂移
